"""
VK SQLite 直读客户端

VK（Vibe Kanban）在本地维护一个 SQLite 数据库，记录所有 Workspace 的执行进程状态。
Dispatcher 通过直读此 DB 来判断 Agent 是否完成了编码（QG 通过），
替代之前不可靠的 .vk/qg_passed/<sha> 文件标记方案。

设计约束（来自 VK 源码分析）：
- run_reason 值: setupscript / codingagent / cleanupscript / archivescript / devserver
- status 值: running / completed / failed / killed
- cleanup_script 仅在 codingagent 正常完成（exit_code=0）后触发
- exit_code IS NULL = VK 重启 artifact（cleanup_orphan_executions），非真实失败
- 所有查询必须过滤 dropped = FALSE

SQLite DB 路径: ~/.local/share/vibe-kanban/db.v2.sqlite
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Optional

logger = logging.getLogger("dispatcher")

# VK 默认 SQLite DB 路径（随 XDG_DATA_HOME 变化时可通过环境变量覆盖）
_DEFAULT_DB_PATH = os.path.expanduser("~/.local/share/vibe-kanban/db.v2.sqlite")

# run_reason 常量（与 VK 源码 Rust enum 对应，全小写）
RUN_REASON_CODING_AGENT = "codingagent"
RUN_REASON_CLEANUP_SCRIPT = "cleanupscript"
RUN_REASON_SETUP_SCRIPT = "setupscript"
RUN_REASON_DEV_SERVER = "devserver"

# status 常量
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_KILLED = "killed"


class VKDatabase:
    """直读 VK 本地 SQLite 数据库，检测 Agent 执行进程状态。

    使用只读模式连接（uri=True + ?mode=ro），不会对 VK 的数据产生任何修改。
    每次调用时短暂打开连接，完成后立即关闭，避免持有锁。

    典型使用：
        vk_db = VKDatabase()
        result = vk_db.is_qg_passed("vk/123-fix-login")
        if result is True:
            # 编码完成，可以流转 In review
        elif result is False:
            # Agent 真实失败，等待人工介入
        else:  # None
            # 仍在运行或尚未开始，下轮再检查
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or os.environ.get("VK_DB_PATH", _DEFAULT_DB_PATH)

    def _connect(self) -> sqlite3.Connection:
        """以只读 URI 模式连接 SQLite（不阻塞 VK 进程）"""
        if not os.path.exists(self._db_path):
            raise FileNotFoundError(
                f"VK SQLite DB 不存在: {self._db_path}\n"
                "请确认 VK 已启动并至少运行过一次。\n"
                "如果 DB 路径不同，请设置环境变量 VK_DB_PATH。"
            )
        uri = f"file:{self._db_path}?mode=ro"
        return sqlite3.connect(uri, uri=True, check_same_thread=False)

    def get_latest_process(
        self, branch: str, run_reason: str
    ) -> Optional[dict]:
        """获取指定分支、指定 run_reason 的最新执行进程记录。

        Args:
            branch: 编码分支名，如 "vk/123-fix-login"
            run_reason: 进程类型，如 "codingagent" / "cleanupscript"

        Returns:
            dict 包含 workspace_id, status, exit_code, started_at, dropped
            若无记录则返回 None
        """
        sql = """
            SELECT
                ep.id          AS process_id,
                s.workspace_id,
                ep.run_reason,
                ep.status,
                ep.exit_code,
                ep.dropped,
                ep.started_at
            FROM execution_processes ep
            JOIN sessions s ON ep.session_id = s.id
            JOIN workspaces w ON s.workspace_id = w.id
            WHERE w.branch = ?
              AND ep.run_reason = ?
              AND ep.dropped = FALSE
            ORDER BY ep.started_at DESC
            LIMIT 1
        """
        try:
            conn = self._connect()
            try:
                cursor = conn.execute(sql, (branch, run_reason))
                row = cursor.fetchone()
                if row is None:
                    return None
                columns = [col[0] for col in cursor.description]
                return dict(zip(columns, row))
            finally:
                conn.close()
        except FileNotFoundError as e:
            logger.warning("VKDatabase: %s", e)
            return None
        except sqlite3.Error as e:
            logger.warning("VKDatabase: SQLite 查询失败 (branch=%s reason=%s): %s", branch, run_reason, e)
            return None

    def has_cleanup_script(self, branch: str) -> bool:
        """检查该分支关联的仓库是否配置了 cleanup_script。

        VK cleanup_script 仅在 codingagent 正常完成后触发。
        若仓库未配置 cleanup_script，则 Dispatcher 直接以 codingagent 完成为 QG 通过依据。
        """
        sql = """
            SELECT r.cleanup_script
            FROM workspaces w
            JOIN workspace_repos wr ON wr.workspace_id = w.id
            JOIN repos r ON wr.repo_id = r.id
            WHERE w.branch = ?
            LIMIT 1
        """
        try:
            conn = self._connect()
            try:
                cursor = conn.execute(sql, (branch,))
                row = cursor.fetchone()
                if row is None:
                    return False
                cleanup_script = row[0]
                # cleanup_script 可能是 NULL 或空字符串
                return bool(cleanup_script and cleanup_script.strip())
            finally:
                conn.close()
        except (FileNotFoundError, sqlite3.Error) as e:
            logger.debug("VKDatabase.has_cleanup_script: 查询失败 (branch=%s): %s", branch, e)
            return False

    def is_agent_running(self, branch: str) -> bool:
        """检查编码 Agent 是否仍在运行。

        Returns:
            True 表示 codingagent 进程的最新记录状态为 running
        """
        proc = self.get_latest_process(branch, RUN_REASON_CODING_AGENT)
        if proc is None:
            return False
        return proc["status"] == STATUS_RUNNING

    def is_qg_passed(self, branch: str) -> Optional[bool]:
        """判断指定分支的质量门禁是否通过。

        检测逻辑（基于 VK container.rs 源码分析）：

        VK 触发链：codingagent exit_code=0 → try_start_next_action → cleanupscript
        cleanup_script 仅在 codingagent 正常退出（exit_code=0）时才会被 VK 自动触发。

        1. 若仓库配置了 cleanup_script（authoritative QG）：
           - cleanupscript 进程存在：按其 status/exit_code 判断
             · status='completed' AND exit_code=0 → True（QG 通过）
             · status='failed/killed' AND exit_code IS NOT NULL → False（真实失败）
           - cleanupscript 尚未启动：
             · codingagent 仍在运行 → None（等待）
             · codingagent 刚以 exit_code=0 完成 → None（等待 VK 触发 cleanupscript）
             · codingagent 以非 0 失败且 exit_code IS NOT NULL → False（cleanup 不会再触发）
        2. 若仓库未配置 cleanup_script：
           - SQLite 无法判断 QG 结果；依赖 Path A（vk-hooks.sh → REST PATCH），返回 None
           - 如果需要 SQLite 检测，请在 VK 仓库设置中配置 cleanup_script
        3. 进程运行中 / 未开始 / exit_code=NULL（VK 重启 artifact） → None

        Args:
            branch: 编码分支名

        Returns:
            True   — QG 通过，Dispatcher 可以触发 finish_coding → In review
            False  — Agent 真实失败，需要人工介入
            None   — 仍在运行、尚未开始、无 cleanup_script 或无法判断（下轮再检查）
        """
        cleanup = self.has_cleanup_script(branch)

        if cleanup:
            # 优先检查 cleanupscript 进程状态
            proc = self.get_latest_process(branch, RUN_REASON_CLEANUP_SCRIPT)
            if proc is not None:
                return self._evaluate_process(proc, "cleanupscript", branch)
            # cleanupscript 尚未启动 → 回退检查 codingagent 是否完成（cleanup 可能正在等待触发）
            # 如果 codingagent 还在运行，那 cleanup 当然没启动
            agent_proc = self.get_latest_process(branch, RUN_REASON_CODING_AGENT)
            if agent_proc is None:
                logger.debug("VKDatabase: 未找到分支 %s 的任何进程记录", branch)
                return None
            if agent_proc["status"] == STATUS_RUNNING:
                logger.debug("VKDatabase: 分支 %s codingagent 仍在运行", branch)
                return None
            if agent_proc["status"] == STATUS_COMPLETED and agent_proc["exit_code"] == 0:
                # codingagent 刚完成，cleanup 尚未写入 DB，等下一轮
                logger.debug(
                    "VKDatabase: 分支 %s codingagent 完成，等待 cleanupscript 启动", branch
                )
                return None
            # codingagent 以非 0 失败（exit_code IS NOT NULL），cleanup 不会再触发
            return self._evaluate_process(agent_proc, "codingagent(fallback)", branch)
        else:
            # 无 cleanup_script：SQLite 无法判断 QG 是否通过
            # codingagent 完成 ≠ QG 通过（Agent 可能未运行测试，或测试失败后仍退出）
            # 依赖 Path A（agent-quality-gate.sh → vk-hooks.sh → REST PATCH）触发流转
            # 如需 SQLite 自动检测，请在 VK 仓库设置中配置 cleanup_script
            logger.debug(
                "VKDatabase: 分支 %s 关联仓库未配置 cleanup_script，SQLite 无法判断 QG 结果",
                branch,
            )
            return None

    def _evaluate_process(
        self, proc: dict, label: str, branch: str
    ) -> Optional[bool]:
        """根据进程记录判断成功/失败/运行中。

        VK 重启 artifact 识别：exit_code IS NULL + status='failed'
        这种情况由 VK 的 cleanup_orphan_executions() 产生，不代表真实失败。

        Returns:
            True  — 成功（status=completed, exit_code=0）
            False — 真实失败（exit_code IS NOT NULL AND exit_code != 0）
            None  — 运行中 / VK 重启 artifact / 无法判断
        """
        status = proc.get("status")
        exit_code = proc.get("exit_code")

        if status == STATUS_RUNNING:
            logger.debug("VKDatabase: 分支 %s %s 仍在运行", branch, label)
            return None

        if status == STATUS_COMPLETED and exit_code == 0:
            logger.info("VKDatabase: 分支 %s %s 成功完成 ✓", branch, label)
            return True

        if exit_code is None:
            # VK 重启 artifact：status='failed' 但 exit_code=NULL
            # cleanup_orphan_executions() 将进程标记为 Failed，不代表 Agent 真的失败
            logger.debug(
                "VKDatabase: 分支 %s %s exit_code=NULL（VK 重启 artifact），忽略",
                branch, label,
            )
            return None

        # exit_code IS NOT NULL AND (exit_code != 0 OR status in ['failed', 'killed'])
        logger.warning(
            "VKDatabase: 分支 %s %s 真实失败 (status=%s, exit_code=%s)",
            branch, label, status, exit_code,
        )
        return False
