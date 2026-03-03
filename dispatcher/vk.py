"""
VK 客户端 — REST API + MCP stdio 协议

两种客户端服务不同场景:
- VKRestClient:  轮询 Issue 列表、更新状态（轻量，纯 HTTP）
- VKMCPClient:   创建 Session、查询 Workspace（需启动子进程）

设计原则:
- 纯标准库实现（urllib, subprocess），零外部依赖
- MCP 客户端仅在需要时短暂创建，用完即关
- 所有网络/进程错误均向上层抛出，由 engine 统一处理
"""

from __future__ import annotations

import json
import logging
import os
import select
import subprocess
import sys
import time
import urllib.error
import urllib.request

logger = logging.getLogger("dispatcher.vk")


# ============================================================================
#  VK REST API 客户端
# ============================================================================

class VKRestClient:
    """VK REST API 客户端 — 用于轮询和状态更新"""

    def __init__(self, port: int = 9527, host: str = "127.0.0.1"):
        self.base_url = f"http://{host}:{port}"

    def health_check(self) -> bool:
        """检查 VK 服务是否可达"""
        try:
            resp = urllib.request.urlopen(
                f"{self.base_url}/api/health", timeout=5
            )
            return resp.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def list_issues(self, project_id: str, limit: int = 100) -> list[dict]:
        """获取项目下所有 Issue

        Returns:
            Issue 字典列表，每个包含 id, title, status, status_id, simple_id 等字段
        """
        url = (
            f"{self.base_url}/api/remote/issues"
            f"?project_id={project_id}&limit={limit}"
        )
        resp = urllib.request.urlopen(url, timeout=10)
        data = json.loads(resp.read().decode())
        # VK REST API 响应结构: {success: true, data: {issues: [...]}}
        return data.get("data", {}).get("issues", [])

    def update_issue_status(
        self, issue_id: str, status_name: str, status_map: dict[str, str]
    ) -> bool:
        """通过 REST API 更新 Issue 状态

        注意: REST API 只接受 status_id，不接受状态名称。
        必须通过 status_map（名称→UUID）转换。
        """
        status_id = status_map.get(status_name)
        if not status_id:
            logger.error("状态映射不存在: '%s'", status_name)
            return False

        payload = json.dumps({"status_id": status_id}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/api/remote/issues/{issue_id}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="PATCH",
        )

        try:
            resp = urllib.request.urlopen(req, timeout=10)
            return resp.status == 200
        except urllib.error.HTTPError as e:
            logger.error("更新状态失败: HTTP %d", e.code)
            return False


# ============================================================================
#  VK MCP stdio 客户端
# ============================================================================

class VKMCPClient:
    """VK MCP stdio 客户端 — 用于创建 Session 等 REST 不支持的操作

    通过子进程与 VK MCP Server 通信（JSON-RPC over stdio）。
    生命周期: connect() → 调用方法 → close()

    典型用法:
        mcp = VKMCPClient(port=9527)
        if mcp.connect():
            ws_id = mcp.start_session(...)
            mcp.close()
    """

    def __init__(self, port: int = 9527):
        self.port = port
        self._proc: subprocess.Popen | None = None
        self._req_id = 0

    def connect(self) -> bool:
        """启动 MCP 子进程并完成协议握手

        握手流程:
        1. 启动 npx vibe-kanban --mcp
        2. 发送 initialize 请求
        3. 接收 initialize 响应
        4. 发送 notifications/initialized 通知
        """
        env = {**os.environ, "PORT": str(self.port)}

        try:
            self._proc = subprocess.Popen(
                ["npx", "-y", "vibe-kanban@latest", "--mcp"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError:
            logger.error("npx 未找到，请确认 Node.js 已安装")
            return False

        # 初始化握手
        resp = self._call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "vk-dispatcher", "version": "0.1"},
            },
        )
        if not resp:
            logger.error("MCP 初始化握手失败")
            self.close()
            return False

        # 发送 initialized 通知（必须，否则 tools/list 返回空）
        self._send({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        })
        time.sleep(0.3)
        return True

    def close(self):
        """关闭 MCP 子进程"""
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                self._proc.kill()
            self._proc = None

    # ---- 业务方法 ----

    def start_session(
        self,
        title: str,
        repo_id: str,
        base_branch: str,
        executor: str,
        issue_id: str | None = None,
        prompt_override: str | None = None,
    ) -> str | None:
        """创建 Workspace Session，返回 workspace_id"""
        args: dict = {
            "title": title,
            "repos": [{"repo_id": repo_id, "base_branch": base_branch}],
            "executor": executor,
        }
        if issue_id:
            args["issue_id"] = issue_id
        if prompt_override:
            args["prompt_override"] = prompt_override

        result = self._call_tool("start_workspace_session", args)
        if result and isinstance(result, dict):
            return result.get("workspace_id")
        return None

    def list_workspaces(self, organization_id: str) -> list[dict]:
        """列出组织下所有 Workspace

        Returns:
            Workspace 字典列表，每个包含 id, branch, name 等字段
        """
        result = self._call_tool(
            "list_workspaces", {"organization_id": organization_id}
        )
        if result and isinstance(result, dict):
            return result.get("workspaces", [])
        return []

    # ---- 内部方法 ----

    def _call_tool(self, tool_name: str, arguments: dict) -> dict | None:
        """调用 MCP tool 并解析返回值"""
        resp = self._call("tools/call", {"name": tool_name, "arguments": arguments})
        if not resp or "result" not in resp:
            return None

        content = resp["result"].get("content", [])
        if content and content[0].get("type") == "text":
            try:
                return json.loads(content[0]["text"])
            except (json.JSONDecodeError, KeyError):
                return {"raw": content[0].get("text", "")}
        return resp["result"]

    def _call(self, method: str, params: dict) -> dict | None:
        """发送 JSON-RPC 请求并等待响应"""
        self._req_id += 1
        self._send({
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": method,
            "params": params,
        })
        return self._recv()

    def _send(self, msg: dict):
        """发送 JSON-RPC 消息到 stdin"""
        if not self._proc or not self._proc.stdin:
            return
        self._proc.stdin.write(json.dumps(msg).encode() + b"\n")
        self._proc.stdin.flush()

    def _recv(self, timeout: int = 30) -> dict | None:
        """从 stdout 读取 JSON-RPC 响应"""
        if not self._proc or not self._proc.stdout:
            return None

        # Windows 不支持 select()，使用带超时的阻塞读取
        if sys.platform == "win32":
            try:
                line = self._proc.stdout.readline()
                if line and line.strip():
                    return json.loads(line)
            except (json.JSONDecodeError, OSError):
                pass
            return None

        ready, _, _ = select.select([self._proc.stdout], [], [], timeout)
        if ready:
            line = self._proc.stdout.readline()
            if line and line.strip():
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    pass
        return None
