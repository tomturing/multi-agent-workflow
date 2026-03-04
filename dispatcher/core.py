"""
中央调度引擎 — Issue 状态监控 + 自动化编排

核心职责:
1. 轮询 VK REST API 获取 Issue 列表
2. 检测 Issue 状态变化（与上次轮询对比）
3. 根据状态转换触发编排动作:
   - To do       → 创建编码 Session（可选，默认关闭）
   - In review   → 创建交叉审查 Session
   - Done        → 合并编码分支到主分支
4. 持久化调度状态到 .vk/dispatcher_state.json

可观测性:
- 每个轮询周期生成 trace_id（6 位十六进制）
- 结构化日志: [时间] [级别] [trace] 消息
- 关键指标: 轮询次数、状态变化数、动作成功/失败数
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from .github import GitHubAPIError, GitHubClient
from .vk import VKMCPClient, VKRestClient

logger = logging.getLogger("dispatcher")


# ============================================================================
#  配置
# ============================================================================

@dataclass
class DispatcherConfig:
    """调度器配置 — 从 .vk/dispatcher.json 加载"""

    # ---- 必填: VK 项目标识 ----
    project_dir: str          # 目标项目根目录（绝对路径）
    organization_id: str      # VK 组织 ID（MCP list_workspaces 需要）
    project_id: str           # VK 项目 ID
    repo_id: str              # VK 仓库 ID

    # ---- 可选: 运行参数 ----
    main_branch: str = "master"
    poll_interval: int = 30   # 轮询间隔（秒）
    vk_port: int = 9527

    # ---- 可选: 自动化开关 ----
    auto_start_coding: bool = True    # To do → 自动启动编码（Issue 由 Copilot Plan Mode 生成，描述质量有保证）
    auto_create_pr: bool = True       # 编码完成 → 自动创建 PR 并推送到 GitHub
    auto_start_review: bool = True    # In review → 自动启动审查
    auto_merge: bool = True           # Done → 自动通过 GitHub API 合并 PR

    # ---- 可选: PR 配置 ----
    pr_merge_method: str = "squash"   # 合并方式: squash / merge / rebase
    pr_draft: bool = False            # 是否创建为 Draft PR
    pr_body_template: str = (
        "## {simple_id}: {title}\n\n"
        "### 变更概述\n\n{diff_stat}\n\n"
        "### 关联 Issue\n\nResolves VK Issue `{simple_id}`\n"
    )

    # ---- 可选: Agent 配置 ----
    default_coder_executor: str = "CLAUDE_CODE"
    cross_review_map: dict = field(default_factory=lambda: {
        "CLAUDE_CODE": "CODEX",
        "CODEX": "CLAUDE_CODE",
        "GEMINI": "CODEX",
    })
    coding_prompt_file: str = ".vk/prompts/coder.md"
    review_prompt_file: str = ".vk/prompts/reviewer.md"

    # ---- 运行时加载 ----
    status_map: dict = field(default_factory=dict)

    @classmethod
    def load(cls, config_path: str, project_dir: str) -> DispatcherConfig:
        """从 JSON 文件加载配置"""
        with open(config_path) as f:
            data = json.load(f)

        # 加载 status_map（status 名称 → status_id UUID）
        status_map_path = os.path.join(project_dir, ".vk", "status_map.json")
        status_map: dict = {}
        if os.path.isfile(status_map_path):
            with open(status_map_path) as f:
                status_map = json.load(f)
        else:
            logger.warning("status_map.json 不存在: %s", status_map_path)

        # 环境变量覆盖
        vk_port = int(os.environ.get("VK_PORT", data.get("vk_port", 9527)))

        return cls(
            project_dir=project_dir,
            organization_id=data["organization_id"],
            project_id=data["project_id"],
            repo_id=data["repo_id"],
            main_branch=data.get("main_branch", "master"),
            poll_interval=int(data.get("poll_interval", 30)),
            vk_port=vk_port,
            auto_start_coding=data.get("auto_start_coding", True),
            auto_create_pr=data.get("auto_create_pr", True),
            auto_start_review=data.get("auto_start_review", True),
            auto_merge=data.get("auto_merge", True),
            pr_merge_method=data.get("pr_merge_method", "squash"),
            pr_draft=data.get("pr_draft", False),
            pr_body_template=data.get("pr_body_template", cls.pr_body_template),
            default_coder_executor=data.get("default_coder_executor", "CLAUDE_CODE"),
            cross_review_map=data.get("cross_review_map", {
                "CLAUDE_CODE": "CODEX",
                "CODEX": "CLAUDE_CODE",
                "GEMINI": "CODEX",
            }),
            coding_prompt_file=data.get("coding_prompt_file", ".vk/prompts/coder.md"),
            review_prompt_file=data.get("review_prompt_file", ".vk/prompts/reviewer.md"),
            status_map=status_map,
        )


# ============================================================================
#  Issue 调度状态
# ============================================================================

@dataclass
class IssueTracker:
    """单个 Issue 的调度跟踪状态"""
    status: str
    title: str = ""
    simple_id: str = ""
    # 编码阶段
    coding_workspace_id: str | None = None
    coding_branch: str | None = None
    coder_executor: str | None = None
    # PR
    pr_number: int | None = None
    pr_url: str | None = None
    pr_merged: bool = False
    # 审查阶段
    review_workspace_id: str | None = None
    review_branch: str | None = None
    # 合并
    merged: bool = False
    # 时间戳
    updated_at: str = ""


# ============================================================================
#  中央调度器
# ============================================================================

class Dispatcher:
    """中央调度引擎

    状态机:
        To do       ──[auto_start_coding]──►  创建编码 Session → In progress
        In progress ──[等待 Agent 完成]──►    (cleanup: push + 状态 In review)
        In review   ──[auto_create_pr]─────►  创建 PR → 创建审查 Session
        Done        ──[auto_merge]──────────► GitHub merge PR → 完成
    """

    def __init__(self, config: DispatcherConfig, *, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run
        self.rest = VKRestClient(port=config.vk_port)

        # Issue 跟踪状态: issue_id → IssueTracker
        self._trackers: dict[str, IssueTracker] = {}
        self._state_file = os.path.join(
            config.project_dir, ".vk", "dispatcher_state.json"
        )

        # 指标
        self._poll_count = 0
        self._action_count = 0
        self._error_count = 0

        # 反向 status_map: status_id (UUID) → status 名称
        # VK REST API 返回 status_id，不返回状态名称
        self._status_id_to_name: dict[str, str] = {
            v: k for k, v in config.status_map.items()
        }

        self._load_state()

    # ---- 主循环 ----

    def run(self):
        """主轮询循环（阻塞，Ctrl+C 退出）"""
        logger.info(
            "调度器启动: project=%s interval=%ds dry_run=%s",
            self.config.project_id[:8],
            self.config.poll_interval,
            self.dry_run,
        )

        # 启动前健康检查
        if not self.rest.health_check():
            logger.error("VK 服务不可达 (%s)，请确认 VK 已启动", self.rest.base_url)
            return

        logger.info("VK 服务连接正常 ✓")

        # 启动后状态校验：验证内存中记录的 workspace 在外部是否真实有效
        self._validate_state_on_startup()

        try:
            while True:
                self.poll_once()
                time.sleep(self.config.poll_interval)
        except KeyboardInterrupt:
            logger.info("调度器停止 (Ctrl+C)")
            self._save_state()

    def poll_once(self):
        """执行一次轮询"""
        trace_id = uuid.uuid4().hex[:6]
        self._poll_count += 1

        try:
            issues = self.rest.list_issues(self.config.project_id)
        except Exception as e:
            self._error_count += 1
            logger.error("[%s] 轮询失败: %s", trace_id, e)
            return

        transitions = 0

        for issue in issues:
            issue_id = issue["id"]
            # VK REST API 只返回 status_id，通过反向 map 解析为状态名称
            status_id = issue.get("status_id", "")
            new_status = self._status_id_to_name.get(status_id, status_id)
            title = issue.get("title", "")
            simple_id = issue.get("simple_id", "")

            prev = self._trackers.get(issue_id)

            if prev is None:
                # 首次发现 — 记录但不触发动作（避免首次启动触发大量操作）
                self._trackers[issue_id] = IssueTracker(
                    status=new_status,
                    title=title,
                    simple_id=simple_id,
                    updated_at=issue.get("updated_at", ""),
                )
                logger.info("[%s] 发现: %s「%s」(%s)", trace_id, simple_id, title[:30], new_status)
                continue

            # 更新元数据
            prev.title = title
            prev.simple_id = simple_id

            if prev.status == new_status:
                # 状态未变 — 检查是否有未完成的补偿动作
                self._check_pending(issue_id, issue, trace_id)
                continue

            # 检测到状态变化!
            transitions += 1
            old_status = prev.status
            logger.info(
                "[%s] 状态变化: %s「%s」%s → %s",
                trace_id, simple_id, title[:30], old_status, new_status,
            )

            # 先更新状态（防止重复触发）
            prev.status = new_status
            prev.updated_at = issue.get("updated_at", "")

            # 触发相应动作
            self._handle_transition(issue_id, issue, old_status, new_status, trace_id)

        # 每轮结束都持久化，保证补偿检查跨进程生效（首次发现 + 状态变化均覆盖）
        self._save_state()

        logger.info(
            "[%s] 轮询 #%d: %d issues, %d 变化 (累计: %d 动作, %d 错误)",
            trace_id, self._poll_count, len(issues), transitions,
            self._action_count, self._error_count,
        )

    # ---- 状态转换处理 ----

    def _handle_transition(
        self,
        issue_id: str,
        issue: dict,
        old_status: str,
        new_status: str,
        trace_id: str,
    ):
        """根据状态转换触发编排动作"""
        sid = self._trackers[issue_id].simple_id

        if new_status == "To do" and self.config.auto_start_coding:
            logger.info("[%s] ▸ %s: 自动创建编码 Session", trace_id, sid)
            self._action_start_coding(issue_id, issue, trace_id)

        elif new_status == "In review":
            # In review: 先创建 PR（如果还没有），再启动审查 Session
            if self.config.auto_create_pr:
                self._action_create_pr(issue_id, issue, trace_id)
            if self.config.auto_start_review:
                logger.info("[%s] ▸ %s: 自动创建审查 Session", trace_id, sid)
                self._action_start_review(issue_id, issue, trace_id)

        elif new_status == "Done" and self.config.auto_merge:
            logger.info("[%s] ▸ %s: 自动合并 PR", trace_id, sid)
            self._action_merge_pr(issue_id, trace_id)

    def _check_pending(self, issue_id: str, issue: dict, trace_id: str):
        """检查当前状态是否有未完成的补偿动作

        场景: 调度器在动作执行中途崩溃重启，或启动时 Issue 已在某状态
        """
        t = self._trackers[issue_id]

        # To do 且无编码 Session → 补偿启动编码（冷启动时 To do 已存在的 issue）
        if (
            t.status == "To do"
            and self.config.auto_start_coding
            and not t.coding_workspace_id
        ):
            logger.info("[%s] ▸ %s: 补偿 — To do 但无编码 Session", trace_id, t.simple_id)
            self._action_start_coding(issue_id, issue, trace_id)
            return  # 已触发，不继续检查其他补偿

        # In review 但无 PR → 补偿创建 PR
        if (
            t.status == "In review"
            and self.config.auto_create_pr
            and not t.pr_number
            and t.coding_branch
        ):
            logger.info("[%s] ▸ %s: 补偿 — In review 但无 PR", trace_id, t.simple_id)
            self._action_create_pr(issue_id, issue, trace_id)

        # In review 但无审查 Session → 补偿创建
        if (
            t.status == "In review"
            and self.config.auto_start_review
            and not t.review_workspace_id
        ):
            logger.info("[%s] ▸ %s: 补偿 — In review 但无审查 Session", trace_id, t.simple_id)
            self._action_start_review(issue_id, issue, trace_id)

        # Done 但 PR 未合并 → 补偿合并
        if (
            t.status == "Done"
            and self.config.auto_merge
            and not t.pr_merged
            and t.pr_number
        ):
            logger.info("[%s] ▸ %s: 补偿 — Done 但 PR 未合并", trace_id, t.simple_id)
            self._action_merge_pr(issue_id, trace_id)

    # ---- 编排动作 ----

    def _action_start_coding(self, issue_id: str, issue: dict, trace_id: str):
        """动作: 创建编码 Session + 状态 → In progress"""
        if self.dry_run:
            logger.info("[%s] [DRY-RUN] 跳过创建编码 Session", trace_id)
            return

        t = self._trackers[issue_id]
        executor = self.config.default_coder_executor
        title = issue.get("title", t.simple_id)
        prompt = self._build_coding_prompt(issue)

        # ---- 幂等检查: 若已存在同名且已 provision 的 workspace，直接复用 ----
        # container_ref=null 表示 VK 创建了记录但从未真正启动 agent（如目录信任检查失败）
        # 这类 "死" workspace 不能复用，否则 agent 永远不会运行
        existing = self.rest.find_workspace_by_title(title)
        if existing and existing.get("container_ref"):
            ws_id = existing["id"]
            branch = existing.get("branch")
            t.coding_workspace_id = ws_id
            t.coding_branch = branch
            t.coder_executor = executor
            self._action_count += 1
            self.rest.update_issue_status(
                issue_id, "In progress", self.config.status_map
            )
            t.status = "In progress"
            self._save_state()
            logger.info(
                "[%s] ✓ 复用已有编码 Workspace: ws=%s branch=%s",
                trace_id, ws_id[:8], branch,
            )
            return
        elif existing:
            logger.info(
                "[%s] 发现未 provision 的同名编码 Workspace (ws=%s container_ref=null)，忽略并重新创建",
                trace_id, existing["id"][:8],
            )

        mcp = VKMCPClient(port=self.config.vk_port)
        if not mcp.connect():
            self._error_count += 1
            logger.error("[%s] MCP 连接失败", trace_id)
            return

        try:
            ws_id = mcp.start_session(
                title=title,
                repo_id=self.config.repo_id,
                base_branch=self.config.main_branch,
                executor=executor,
                issue_id=issue_id,
                prompt_override=prompt,
                rest_client=self.rest,   # 兜底: MCP 解析失败时从 REST 获取 ws_id
            )

            if not ws_id:
                self._error_count += 1
                logger.error("[%s] 编码 Session 创建失败", trace_id)
                return

            # 查找分支名
            branch = self._find_branch(mcp, ws_id)
            t.coding_workspace_id = ws_id
            t.coding_branch = branch
            t.coder_executor = executor
            self._action_count += 1

            # 状态 → In progress
            self.rest.update_issue_status(
                issue_id, "In progress", self.config.status_map
            )
            t.status = "In progress"
            self._save_state()

            logger.info(
                "[%s] ✓ 编码 Session: ws=%s branch=%s executor=%s",
                trace_id, ws_id[:8], branch, executor,
            )
        finally:
            mcp.close()

    def _action_start_review(self, issue_id: str, issue: dict, trace_id: str):
        """动作: 创建交叉审查 Session，prompt 中注入 PR URL 和 diff 范围"""
        if self.dry_run:
            logger.info("[%s] [DRY-RUN] 跳过创建审查 Session", trace_id)
            return

        t = self._trackers[issue_id]

        # 确定审查器（交叉审查矩阵）
        coder = t.coder_executor or self.config.default_coder_executor
        reviewer = self.config.cross_review_map.get(coder, "CODEX")

        # 确定编码分支（审查的 base_branch）
        base_branch = t.coding_branch
        if not base_branch:
            logger.info("[%s] 编码分支未知，尝试从 VK Workspace 推断...", trace_id)
            base_branch = self._discover_coding_branch(issue_id, issue, trace_id)
            if not base_branch:
                self._error_count += 1
                logger.error("[%s] 无法确定编码分支，跳过审查", trace_id)
                return
            t.coding_branch = base_branch

        title = f"Review: {base_branch} ({reviewer})"

        # 构建增强 prompt: 基础 prompt + PR 信息 + diff 范围
        prompt = self._build_review_prompt(t, trace_id)

        # ---- 幂等检查: 若已存在同名且已 provision 的 workspace，直接复用 ----
        # container_ref=null 表示 VK 创建了记录但从未真正启动 agent（如目录信任检查失败）
        existing = self.rest.find_workspace_by_title(title)
        if existing and existing.get("container_ref"):
            ws_id = existing["id"]
            branch_name = existing.get("branch")
            t.review_workspace_id = ws_id
            t.review_branch = branch_name
            self._action_count += 1
            self._save_state()
            logger.info(
                "[%s] ✓ 复用已有审查 Workspace: ws=%s",
                trace_id, ws_id[:8],
            )
            return
        elif existing:
            logger.info(
                "[%s] 发现未 provision 的同名审查 Workspace (ws=%s container_ref=null)，忽略并重新创建",
                trace_id, existing["id"][:8],
            )

        mcp = VKMCPClient(port=self.config.vk_port)
        if not mcp.connect():
            self._error_count += 1
            logger.error("[%s] MCP 连接失败", trace_id)
            return

        try:
            ws_id = mcp.start_session(
                title=title,
                repo_id=self.config.repo_id,
                base_branch=base_branch,
                executor=reviewer,
                issue_id=issue_id,
                prompt_override=prompt,
                rest_client=self.rest,  # 兜底: MCP 解析失败时从 REST 获取 ws_id
            )

            if not ws_id:
                self._error_count += 1
                logger.error("[%s] 审查 Session 创建失败", trace_id)
                return

            branch = self._find_branch(mcp, ws_id)
            t.review_workspace_id = ws_id
            t.review_branch = branch
            self._action_count += 1
            self._save_state()

            logger.info(
                "[%s] ✓ 审查 Session: ws=%s executor=%s base=%s pr=#%s",
                trace_id, ws_id[:8], reviewer, base_branch,
                t.pr_number or "N/A",
            )
        finally:
            mcp.close()

    def _action_create_pr(self, issue_id: str, issue: dict, trace_id: str):
        """动作: 在 GitHub 上创建 Pull Request

        在 In review 状态下触发，PR 是审查的容器。
        创建前先确保分支已推送到远端。
        """
        if self.dry_run:
            logger.info("[%s] [DRY-RUN] 跳过创建 PR", trace_id)
            return

        t = self._trackers[issue_id]

        # 跳过已有 PR 的情况
        if t.pr_number:
            logger.info("[%s] PR 已存在: #%d", trace_id, t.pr_number)
            return

        # 需要编码分支
        branch = t.coding_branch
        if not branch:
            branch = self._discover_coding_branch(issue_id, issue, trace_id)
            if not branch:
                self._error_count += 1
                logger.error("[%s] 无编码分支信息，无法创建 PR", trace_id)
                return
            t.coding_branch = branch

        try:
            gh = GitHubClient.from_project(self.config.project_dir)
        except GitHubAPIError as e:
            self._error_count += 1
            logger.error("[%s] GitHub 客户端初始化失败: %s", trace_id, e)
            return

        # 确保分支已推送（cleanup 可能已 push，这里是补偿）
        GitHubClient.push_branch(self.config.project_dir, branch)

        # 检查是否已有 open PR（幂等）
        try:
            existing = gh.list_open_prs(head=branch)
            if existing:
                pr = existing[0]
                t.pr_number = pr["number"]
                t.pr_url = pr["html_url"]
                self._save_state()
                logger.info("[%s] 发现已有 PR: #%d %s", trace_id, pr["number"], pr["html_url"])
                return
        except GitHubAPIError:
            pass  # 列表失败不阻塞，继续创建

        # 生成 diff 统计（用于 PR body）
        diff_stat = GitHubClient.generate_diff(
            self.config.project_dir,
            self.config.main_branch,
            branch,
            stat_only=True,
        )

        # 构建 PR body
        pr_title = f"{t.simple_id}: {t.title}" if t.simple_id else t.title
        pr_body = self.config.pr_body_template.format(
            simple_id=t.simple_id or "N/A",
            title=t.title,
            diff_stat=f"```\n{diff_stat}\n```" if diff_stat else "_无变更统计_",
        )

        try:
            pr = gh.create_pr(
                head=branch,
                base=self.config.main_branch,
                title=pr_title,
                body=pr_body,
                draft=self.config.pr_draft,
            )

            t.pr_number = pr["number"]
            t.pr_url = pr["html_url"]
            self._action_count += 1
            self._save_state()

            logger.info(
                "[%s] ✓ PR 已创建: #%d %s",
                trace_id, pr["number"], pr["html_url"],
            )
        except GitHubAPIError as e:
            self._error_count += 1
            # 422 通常是已有相同 head/base 的 PR
            if e.status == 422:
                logger.warning("[%s] PR 创建冲突 (422)，可能已存在", trace_id)
            else:
                logger.error("[%s] PR 创建失败: %s", trace_id, e)

    def _action_merge_pr(self, issue_id: str, trace_id: str):
        """动作: 通过 GitHub API 合并 Pull Request

        业界最佳实践: 通过 merge PR API（而非 git merge）确保:
        - 审计链完整（谁批准、谁合并）
        - 支持 squash merge（干净的主分支历史）
        - 尊重分支保护规则
        """
        if self.dry_run:
            logger.info("[%s] [DRY-RUN] 跳过合并 PR", trace_id)
            return

        t = self._trackers[issue_id]

        if not t.pr_number:
            # 没有 PR → 回退到本地 git merge（兼容无 GitHub 场景）
            logger.warning("[%s] 无 PR 编号，回退到本地 git merge", trace_id)
            self._action_merge_local(issue_id, trace_id)
            return

        try:
            gh = GitHubClient.from_project(self.config.project_dir)
        except GitHubAPIError as e:
            self._error_count += 1
            logger.error("[%s] GitHub 客户端初始化失败: %s", trace_id, e)
            return

        merge_title = f"{t.simple_id}: {t.title}" if t.simple_id else t.title

        try:
            result = gh.merge_pr(
                t.pr_number,
                merge_method=self.config.pr_merge_method,
                commit_title=merge_title,
            )

            if result.get("merged"):
                t.pr_merged = True
                t.merged = True
                self._action_count += 1
                self._save_state()

                logger.info(
                    "[%s] ✓ PR #%d 已合并 (%s) → %s  sha=%s",
                    trace_id, t.pr_number, self.config.pr_merge_method,
                    self.config.main_branch, result.get("sha", "?")[:8],
                )

                # 合并后拉取最新主分支到本地
                self._pull_main(trace_id)
            else:
                self._error_count += 1
                logger.error(
                    "[%s] PR #%d 合并失败: %s",
                    trace_id, t.pr_number, result.get("message", "unknown"),
                )

        except GitHubAPIError as e:
            self._error_count += 1
            if e.status == 405:
                logger.error(
                    "[%s] PR #%d 不可合并 (405) — 可能有冲突或未通过 CI",
                    trace_id, t.pr_number,
                )
            elif e.status == 409:
                logger.error(
                    "[%s] PR #%d HEAD 已移动 (409) — 需要更新分支",
                    trace_id, t.pr_number,
                )
            else:
                logger.error("[%s] PR #%d 合并失败: %s", trace_id, t.pr_number, e)

    def _action_merge_local(self, issue_id: str, trace_id: str):
        """回退动作: 本地 git merge（无 GitHub 时使用）"""
        t = self._trackers[issue_id]
        branch = t.coding_branch

        if not branch:
            self._error_count += 1
            logger.error("[%s] 无编码分支信息，无法合并", trace_id)
            return

        git = ["git", "-C", self.config.project_dir]
        merge_msg = f"merge: {t.simple_id} {t.title}"

        try:
            result = subprocess.run(
                [*git, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, check=True,
            )
            original = result.stdout.strip()

            subprocess.run(
                [*git, "checkout", self.config.main_branch],
                check=True, capture_output=True, text=True,
            )

            subprocess.run(
                [*git, "merge", "--no-ff", branch, "-m", merge_msg],
                check=True, capture_output=True, text=True,
            )

            t.merged = True
            self._action_count += 1
            self._save_state()

            logger.info("[%s] ✓ 本地合并 %s → %s", trace_id, branch, self.config.main_branch)

            if original != self.config.main_branch:
                subprocess.run([*git, "checkout", original], capture_output=True, text=True)

        except subprocess.CalledProcessError as e:
            self._error_count += 1
            stderr = e.stderr if isinstance(e.stderr, str) else ""
            logger.error("[%s] 本地合并失败: %s", trace_id, stderr.strip() or str(e))

    # ---- 辅助方法 ----

    def _build_coding_prompt(self, issue: dict) -> str | None:
        """构建编码 prompt: 工作流规范 + 项目规范 + Issue 完整上下文

        注入顺序（从宏观到具体）:
        1. coder.md     — 工作流规范和角色职责（通用）
        2. CLAUDE.md    — 项目编码规范、技术栈、约束（项目级）
        3. Issue 上下文 — simple_id / title / description（任务级）

        Agent（Claude Code / Codex 等）收到后会:
        - 根据 Issue 描述理解任务目标
        - 根据 CLAUDE.md 约束遵守项目规范
        - 自行读取代码库找到相关文件（无需 Dispatcher 推断）
        """
        parts: list[str] = []

        # 1. 工作流规范（coder.md）
        coder_prompt = self._load_prompt(self.config.coding_prompt_file)
        if coder_prompt:
            parts.append(coder_prompt)

        # 2. 项目规范（CLAUDE.md）— 项目根目录下
        claude_md = self._load_prompt("CLAUDE.md")
        if claude_md:
            parts.append(f"## 项目规范 (CLAUDE.md)\n\n{claude_md}")

        # 3. Issue 完整上下文
        simple_id = issue.get("simple_id", "")
        title = issue.get("title", "")
        description = issue.get("description") or issue.get("body", "")

        issue_section = f"## 当前任务\n\n"
        if simple_id:
            issue_section += f"**{simple_id}**: "
        issue_section += f"{title}\n"
        if description:
            issue_section += f"\n{description}\n"

        parts.append(issue_section)

        if not parts:
            return None

        return "\n\n---\n\n".join(parts)

    def _build_review_prompt(self, tracker: IssueTracker, trace_id: str) -> str | None:
        """构建增强审查 prompt: 基础 prompt + PR 信息 + diff 范围

        审查 Agent 需要知道:
        1. PR URL（直接查看）
        2. diff 范围（应该审查哪些文件）
        3. 变更统计（影响范围）
        """
        # 加载基础 prompt
        base_prompt = self._load_prompt(self.config.review_prompt_file) or ""

        # PR 信息
        pr_section = ""
        if tracker.pr_url:
            pr_section = f"\n## Pull Request\n\nPR: {tracker.pr_url}\n"

        # diff 范围
        diff_section = ""
        if tracker.coding_branch:
            diff_stat = GitHubClient.generate_diff(
                self.config.project_dir,
                self.config.main_branch,
                tracker.coding_branch,
                stat_only=True,
            )
            if diff_stat:
                diff_section = (
                    f"\n## 变更范围\n\n"
                    f"分支: `{tracker.coding_branch}` → `{self.config.main_branch}`\n\n"
                    f"```\n{diff_stat}\n```\n\n"
                    f"审查命令: `git diff {self.config.main_branch}...{tracker.coding_branch}`\n"
                )

        if not pr_section and not diff_section:
            return base_prompt if base_prompt else None

        enhanced = base_prompt + pr_section + diff_section
        return enhanced[:3000]  # 限制总长度

    def _pull_main(self, trace_id: str):
        """合并后拉取最新主分支到本地"""
        git = ["git", "-C", self.config.project_dir]
        try:
            subprocess.run(
                [*git, "fetch", "origin", self.config.main_branch],
                capture_output=True, text=True, check=True,
            )
            # 尝试 fast-forward 更新本地主分支
            subprocess.run(
                [*git, "branch", "-f", self.config.main_branch,
                 f"origin/{self.config.main_branch}"],
                capture_output=True, text=True,
            )
            logger.info("[%s] 已同步本地 %s", trace_id, self.config.main_branch)
        except subprocess.CalledProcessError as e:
            logger.warning("[%s] 同步主分支失败: %s", trace_id, e)

    def _load_prompt(self, prompt_file: str) -> str | None:
        """加载提示词文件内容

        上限 4000 字符：覆盖完整 coder.md / CLAUDE.md，
        同时避免单个文件撑爆 Session 初始 context。
        """
        path = os.path.join(self.config.project_dir, prompt_file)
        if os.path.isfile(path):
            with open(path) as f:
                return f.read()[:4000]
        return None

    def _find_branch(self, mcp: VKMCPClient, workspace_id: str) -> str | None:
        """查找 Workspace 对应的分支名（优先 REST，MCP list_workspaces 在 v0.1.22/23 失效）"""
        try:
            workspaces = self.rest.get_workspaces()
            for ws in workspaces:
                if ws.get("id") == workspace_id:
                    return ws.get("branch")
        except Exception:
            pass
        return None

    def _discover_coding_branch(
        self, issue_id: str, issue: dict, trace_id: str
    ) -> str | None:
        """从 VK Workspace 列表推断 Issue 对应的编码分支

        匹配策略（按优先级）:
        1. Workspace name 精确匹配 Issue title
        2. Workspace name 包含 Issue simple_id
        3. Workspace name 模糊匹配 Issue title 前缀
        """
        try:
            workspaces = self.rest.get_workspaces()
            if not workspaces:
                return None

            title = issue.get("title", "")
            simple_id = self._trackers[issue_id].simple_id

            # 过滤掉 review workspace
            coding_ws = [
                ws for ws in workspaces
                if "review" not in ws.get("name", "").lower()
                and "review" not in ws.get("branch", "").lower()
            ]

            # 策略 1: name 精确匹配
            for ws in coding_ws:
                if ws.get("name") == title:
                    logger.info("[%s] 分支匹配: name 精确匹配", trace_id)
                    return ws.get("branch")

            # 策略 2: name 包含 simple_id
            if simple_id:
                for ws in coding_ws:
                    if simple_id in ws.get("name", ""):
                        logger.info("[%s] 分支匹配: 包含 %s", trace_id, simple_id)
                        return ws.get("branch")

            # 策略 3: 模糊匹配
            title_lower = title[:20].lower()
            for ws in coding_ws:
                if title_lower and title_lower in ws.get("name", "").lower():
                    logger.info("[%s] 分支匹配: 模糊匹配", trace_id)
                    return ws.get("branch")

        except Exception as e:
            logger.error("[%s] Workspace 查询失败: %s", trace_id, e)

        return None

    # ---- 状态持久化 ----

    def _load_state(self):
        """从 JSON 文件加载调度状态"""
        if not os.path.isfile(self._state_file):
            return

        try:
            with open(self._state_file) as f:
                data = json.load(f)
            for issue_id, state_data in data.get("issues", {}).items():
                self._trackers[issue_id] = IssueTracker(**state_data)
            logger.info("加载调度状态: %d 个 Issue", len(self._trackers))
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("调度状态文件损坏，重新初始化: %s", e)

    def _validate_state_on_startup(self):
        """启动时校验内存中记录的 workspace ID 是否真正 provisioned。

        container_ref=null 表示 VK 创建了数据库记录但从未真正启动 agent
        （常见原因：codex trust-check 失败、网络超时等）。
        这类"死" workspace 不能复用，应清空让补偿逻辑重新创建。
        """
        try:
            all_workspaces = self.rest.get_workspaces()
        except Exception as e:
            logger.warning("启动校验: 获取 workspace 列表失败，跳过校验: %s", e)
            return

        ws_map = {w["id"]: w for w in all_workspaces}
        invalidated = 0

        for issue_id, t in self._trackers.items():
            for attr, label in [
                ("coding_workspace_id", "编码"),
                ("review_workspace_id", "审查"),
            ]:
                ws_id = getattr(t, attr)
                if not ws_id:
                    continue
                ws = ws_map.get(ws_id)
                if ws is None:
                    # VK 里找不到该 workspace（可能已被手动删除）
                    logger.warning(
                        "启动校验: %s 的%s Workspace %s 在 VK 中不存在，清空引用",
                        t.simple_id or issue_id[:8], label, ws_id[:8],
                    )
                    setattr(t, attr, None)
                    if attr == "review_workspace_id":
                        t.review_branch = None
                    elif attr == "coding_workspace_id":
                        t.coding_branch = None
                    invalidated += 1
                elif not ws.get("container_ref"):
                    # 记录存在但未被 provision（container_ref=null）
                    logger.warning(
                        "启动校验: %s 的%s Workspace %s container_ref=null（未被 provision），清空引用",
                        t.simple_id or issue_id[:8], label, ws_id[:8],
                    )
                    setattr(t, attr, None)
                    if attr == "review_workspace_id":
                        t.review_branch = None
                    elif attr == "coding_workspace_id":
                        t.coding_branch = None
                    invalidated += 1

        if invalidated:
            self._save_state()
            logger.info("启动校验完成: 清空 %d 个无效 workspace 引用 ✓", invalidated)
        else:
            logger.info("启动校验完成: 所有 workspace 引用有效 ✓")

    def _save_state(self):
        """持久化调度状态到 JSON 文件"""
        data = {
            "issues": {k: asdict(v) for k, v in self._trackers.items()},
            "updated_at": datetime.now(UTC).isoformat(),
            "poll_count": self._poll_count,
            "action_count": self._action_count,
            "error_count": self._error_count,
        }
        os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
        with open(self._state_file, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # ---- 状态查询（供 CLI status 命令使用）----

    def get_status_report(self) -> str:
        """生成人类可读的状态报告"""
        lines = [
            f"调度器状态报告 — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"项目: {self.config.project_id[:8]}...",
            f"轮询: {self._poll_count}次  动作: {self._action_count}  错误: {self._error_count}",
            "",
        ]

        if not self._trackers:
            lines.append("  (暂无跟踪的 Issue)")
            return "\n".join(lines)

        for issue_id, t in self._trackers.items():
            flags = []
            if t.coding_workspace_id:
                flags.append(f"coding={t.coding_workspace_id[:8]}")
            if t.review_workspace_id:
                flags.append(f"review={t.review_workspace_id[:8]}")
            if t.merged:
                flags.append("merged ✓")
            flag_str = f" [{', '.join(flags)}]" if flags else ""

            lines.append(f"  {t.simple_id:8s} {t.status:12s} {t.title[:40]}{flag_str}")

        return "\n".join(lines)
