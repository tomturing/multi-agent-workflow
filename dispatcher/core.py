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
    auto_start_coding: bool = False   # To do → 自动启动编码（默认关闭，需人工审核任务描述）
    auto_start_review: bool = True    # In review → 自动启动审查
    auto_merge: bool = True           # Done → 自动合并到主分支

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
            auto_start_coding=data.get("auto_start_coding", False),
            auto_start_review=data.get("auto_start_review", True),
            auto_merge=data.get("auto_merge", True),
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
        In progress ──[等待 Agent 完成]──►    (cleanup 自动设 In review)
        In review   ──[auto_start_review]──►  创建审查 Session
        Done        ──[auto_merge]──────────► 合并分支 → 完成
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
            new_status = issue.get("status", "")
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

        if transitions > 0:
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

        elif new_status == "In review" and self.config.auto_start_review:
            logger.info("[%s] ▸ %s: 自动创建审查 Session", trace_id, sid)
            self._action_start_review(issue_id, issue, trace_id)

        elif new_status == "Done" and self.config.auto_merge:
            logger.info("[%s] ▸ %s: 自动合并到 %s", trace_id, sid, self.config.main_branch)
            self._action_merge(issue_id, trace_id)

    def _check_pending(self, issue_id: str, issue: dict, trace_id: str):
        """检查当前状态是否有未完成的补偿动作

        场景: 调度器在动作执行中途崩溃重启，或启动时 Issue 已在某状态
        """
        t = self._trackers[issue_id]

        # In review 但无审查 Session → 补偿创建
        if (
            t.status == "In review"
            and self.config.auto_start_review
            and not t.review_workspace_id
        ):
            logger.info("[%s] ▸ %s: 补偿 — In review 但无审查 Session", trace_id, t.simple_id)
            self._action_start_review(issue_id, issue, trace_id)

        # Done 但未合并 → 补偿合并
        if (
            t.status == "Done"
            and self.config.auto_merge
            and not t.merged
            and t.coding_branch
        ):
            logger.info("[%s] ▸ %s: 补偿 — Done 但未合并", trace_id, t.simple_id)
            self._action_merge(issue_id, trace_id)

    # ---- 编排动作 ----

    def _action_start_coding(self, issue_id: str, issue: dict, trace_id: str):
        """动作: 创建编码 Session + 状态 → In progress"""
        if self.dry_run:
            logger.info("[%s] [DRY-RUN] 跳过创建编码 Session", trace_id)
            return

        t = self._trackers[issue_id]
        executor = self.config.default_coder_executor
        title = issue.get("title", t.simple_id)
        prompt = self._load_prompt(self.config.coding_prompt_file)

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
        """动作: 创建交叉审查 Session"""
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
        prompt = self._load_prompt(self.config.review_prompt_file)

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
                "[%s] ✓ 审查 Session: ws=%s executor=%s base=%s",
                trace_id, ws_id[:8], reviewer, base_branch,
            )
        finally:
            mcp.close()

    def _action_merge(self, issue_id: str, trace_id: str):
        """动作: 合并编码分支到主分支"""
        if self.dry_run:
            logger.info("[%s] [DRY-RUN] 跳过合并", trace_id)
            return

        t = self._trackers[issue_id]
        branch = t.coding_branch

        if not branch:
            self._error_count += 1
            logger.error("[%s] 无编码分支信息，无法合并", trace_id)
            return

        git = ["git", "-C", self.config.project_dir]
        merge_msg = f"merge: {t.simple_id} {t.title}"

        try:
            # 保存当前分支
            result = subprocess.run(
                [*git, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, check=True,
            )
            original = result.stdout.strip()

            # 切换到主分支
            subprocess.run(
                [*git, "checkout", self.config.main_branch],
                check=True, capture_output=True, text=True,
            )

            # 合并（--no-ff 保留历史）
            subprocess.run(
                [*git, "merge", "--no-ff", branch, "-m", merge_msg],
                check=True, capture_output=True, text=True,
            )

            t.merged = True
            self._action_count += 1
            self._save_state()

            logger.info(
                "[%s] ✓ 已合并 %s → %s",
                trace_id, branch, self.config.main_branch,
            )

            # 恢复原分支
            if original != self.config.main_branch:
                subprocess.run(
                    [*git, "checkout", original],
                    capture_output=True, text=True,
                )
        except subprocess.CalledProcessError as e:
            self._error_count += 1
            stderr = e.stderr if isinstance(e.stderr, str) else ""
            logger.error("[%s] 合并失败: %s", trace_id, stderr.strip() or str(e))

    # ---- 辅助方法 ----

    def _load_prompt(self, prompt_file: str) -> str | None:
        """加载提示词文件内容"""
        path = os.path.join(self.config.project_dir, prompt_file)
        if os.path.isfile(path):
            with open(path) as f:
                return f.read()[:2000]  # 限制长度防止占用过多 context
        return None

    def _find_branch(self, mcp: VKMCPClient, workspace_id: str) -> str | None:
        """通过 MCP 查找 Workspace 对应的分支名"""
        try:
            workspaces = mcp.list_workspaces(self.config.organization_id)
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
        mcp = VKMCPClient(port=self.config.vk_port)
        if not mcp.connect():
            return None

        try:
            workspaces = mcp.list_workspaces(self.config.organization_id)
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
        finally:
            mcp.close()

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
