# Vibe Kanban MCP Server 使用手册

> **本手册记录 VK MCP Server 的工具 API、真实行为（含与官方文档的偏差）和踩坑记录。**
> 版本参考: v0.1.22 / v0.1.23

---

## 一、MCP 服务器配置

### 启动方式

VK 内置 MCP Server，VK 运行时自动可用。外部客户端（Claude Code、Copilot 等）通过 stdio 子进程接入：

```json
{
  "mcpServers": {
    "vibe_kanban": {
      "command": "npx",
      "args": ["-y", "vibe-kanban@latest", "--mcp"]
    }
  }
}
```

或使用本地安装的二进制（更快）：

```json
{
  "mcpServers": {
    "vibe_kanban": {
      "command": "/home/<user>/.vibe-kanban/bin/v0.1.23-<date>/linux-x64/vibe-kanban-mcp",
      "args": [],
      "env": {
        "BACKEND_PORT": "9527"
      }
    }
  }
}
```

> **重要**: MCP Server 通过环境变量 `BACKEND_PORT` 知道 VK 后端地址，默认 `9527`。

---

## 二、工具全览

### 2.1 项目 & 组织

| 工具 | 说明 | 必填参数 | 返回 |
|------|------|---------|------|
| `list_organizations` | 列出所有组织 | — | `[{id, name, ...}]` |
| `list_org_members` | 列出组织成员 | `organization_id`（可选，workspace 内自动） | `[{id, name, email}]` |
| `list_projects` | 列出所有项目 | `organization_id`（可选） | `[{id, name, ...}]` |

### 2.2 Issue 管理

| 工具 | 说明 | 必填参数 | 常用可选参数 |
|------|------|---------|------------|
| `list_issues` | 列出项目 Issues | `project_id`（可选，workspace 内自动） | `status`, `limit`, `offset`, `search`, `simple_id` |
| `get_issue` | 获取 Issue 详情（含 tags、关联、子 issue） | `issue_id` | — |
| `create_issue` | 创建 Issue | `project_id`, `title` | `description`, `priority`, `parent_issue_id` |
| `update_issue` | 更新 Issue | `issue_id` | `title`, `description`, `status`（状态名称，非 ID） |
| `delete_issue` | 删除 Issue | `issue_id` | — |
| `list_issue_priorities` | 列出允许的优先级值 | — | — |

**`update_issue` 状态参数**：传状态**名称**（如 `"In progress"`），不是 UUID！

### 2.3 Issue 标签和分配

| 工具 | 说明 | 必填参数 |
|------|------|---------|
| `list_tags` | 列出项目标签 | `project_id`（可选） |
| `list_issue_tags` | 列出 Issue 已有标签 | `issue_id` |
| `add_issue_tag` | 给 Issue 打标签 | `issue_id`, `tag_id` |
| `remove_issue_tag` | 删除 Issue 标签 | `issue_tag_id`（注意：是关联记录 ID，非 tag_id） |
| `assign_issue` | 分配负责人 | `issue_id`, `user_id` |
| `unassign_issue` | 取消分配 | `issue_assignee_id`（关联记录 ID） |
| `list_issue_assignees` | 列出 Issue 负责人 | `issue_id` |

### 2.4 Issue 关联关系

| 工具 | 说明 | 必填参数 |
|------|------|---------|
| `create_issue_relationship` | 创建关联 | `issue_id`, `related_issue_id`, `relationship_type`（`blocking`/`related`/`has_duplicate`） |
| `delete_issue_relationship` | 删除关联 | `relationship_id` |

### 2.5 仓库管理

| 工具 | 说明 | 必填参数 |
|------|------|---------|
| `list_repos` | 列出仓库 | — | 
| `get_repo` | 获取仓库详情（含脚本） | `repo_id` |
| `update_setup_script` | 更新 Setup 脚本（初始化 workspace 时运行） | `repo_id`, `script` |
| `update_cleanup_script` | 更新 Cleanup 脚本（workspace 销毁时运行） | `repo_id`, `script` |
| `update_dev_server_script` | 更新开发服务器脚本 | `repo_id`, `script` |

### 2.6 Workspace 管理

| 工具 | 说明 | 必填参数 | 注意 |
|------|------|---------|------|
| `list_workspaces` | 列出本地 workspaces | — | ⚠️ 见 PIT-VK-001 |
| `update_workspace` | 更新 workspace 属性 | `workspace_id`（可选） | 可改 `name`, `archived`, `pinned` |
| `delete_workspace` | 删除 workspace | `workspace_id`（可选） | `delete_remote` 和 `delete_branches` 可选 |
| `link_workspace` | 关联 workspace 到 Issue | `workspace_id`, `issue_id` | 建立追踪关联 |
| `start_workspace_session` | 启动新编码 Session | `title`, `repos`, `executor` + `issue_id` 或 `prompt_override` | ⚠️ 见 PIT-VK-002 |

#### `start_workspace_session` 参数详情

```json
{
  "title": "任务标题",
  "repos": [
    {
      "repo_id": "ca4e2250-...",
      "base_branch": "master"
    }
  ],
  "executor": "CLAUDE_CODE",
  "issue_id": "86c5e2e4-...",
  "prompt_override": "可选：覆盖自动生成的 prompt",
  "variant": "可选：executor 变体"
}
```

支持的 executor 值（大小写不敏感，支持连字符或下划线）：
- `claude-code` / `CLAUDE_CODE`
- `codex` / `CODEX`  
- `gemini` / `GEMINI`
- `amp` / `AMP`
- `opencode` / `OPENCODE`
- `cursor_agent` / `CURSOR_AGENT`
- `qwen-code` / `QWEN_CODE`
- `copilot` / `COPILOT`

### 2.7 上下文工具（仅在 workspace session 内可用）

| 工具 | 说明 |
|------|------|
| `get_context` | 获取当前 workspace 的项目/Issue/workspace 元数据 |

---

## 三、踩坑记录

### PIT-VK-001 — `list_workspaces` MCP 工具失败但 REST 正常

**症状**：调用 `list_workspaces` 返回 `{"success": false, "error": "Failed to parse VK API response", "details": "error decoding response body"}`

**根因**：VK MCP 二进制内的 HTTP 客户端对 `/api/task-attempts` 响应解析失败（原因未知，可能是特定版本 Bug）。

**绕过方式**：直接用 REST API 替代 MCP 工具：

```python
# 直接 REST 调用（响应格式: {success, data: [...]}）
resp = urllib.request.urlopen("http://127.0.0.1:9527/api/task-attempts")
envelope = json.loads(resp.read())
workspaces = envelope.get("data", [])  # 注意要解包 data 字段
```

**已报告版本**：v0.1.22、v0.1.23

---

### PIT-VK-002 — `start_workspace_session` 返回错误但 workspace 实际已创建

**症状**：调用 `start_workspace_session` 返回 `{"success": false, "error": "Failed to parse VK API response"}`，但 VK 后端实际上**已成功创建**了 workspace。

**根因**：`start_workspace_session` 对应的后端端点 `/api/task-attempts/create-and-start` 在 workspace 创建并启动后返回的响应格式，与 MCP 客户端期望的格式不匹配，导致 MCP 报解析错误，但操作本身是成功的。

**副作用**：如果调度器不检测"已存在"就重试，每次轮询都会创建一个新的 workspace，最终产生大量重复 workspace（测试中产生了 24+ 个）。

**正确处理方式**：

```python
# 调用 start_workspace_session 后，不管 success 字段如何
# 都去 REST API 确认 workspace 是否实际创建成功
def _workspace_already_exists(issue_id: str) -> str | None:
    """查找与 issue 相关的未归档 workspace，返回 workspace_id 或 None"""
    resp = urllib.request.urlopen("http://127.0.0.1:9527/api/task-attempts")
    workspaces = json.loads(resp.read())
    for ws in workspaces:
        if not ws.get("archived") and ws.get("task_id") == issue_id:
            return ws["id"]
    return None
```

**或者**：调用一次后，不管返回什么，立即将 Issue 状态记录为"已尝试创建"，下次轮询跳过。

---

### PIT-VK-003 — REST API 的状态字段是 `status_id`（UUID），不是状态名称

**症状**：`list_issues` 返回的 Issue 对象中 `status` 字段为空字符串或不存在，用 `issue.get("status")` 总是 `None`。

**根因**：VK REST API（`/api/remote/issues`）返回的 Issue 对象中，状态用 `status_id`（UUID）字段表示，不是 `status`（名称）字段。

**响应示例**：
```json
{
  "id": "86c5e2e4-...",
  "title": "ST-A2: 替换 datetime.utcnow()",
  "status_id": "4c5525d8-6608-481a-a090-07ab42eb3ae1",
  "simple_id": "TOM-3"
}
```

**修复**：维护反向映射表：

```python
# 从 dispatcher.json 的 status_map 构建反向表
status_id_to_name = {v: k for k, v in config.status_map.items()}
# 使用
status_name = status_id_to_name.get(issue.get("status_id", ""), "Unknown")
```

**MCP 例外**：通过 `update_issue` MCP 工具更新状态时，传**名称**（`"In progress"`），不传 UUID。

---

### PIT-VK-004 — `list_issues` REST 响应结构嵌套两层

**症状**：从 `list_issues` REST 响应用 `data.get("issues", [])` 取不到数据。

**根因**：响应结构是 `{success, data: {issues: [...], count: N}}`，需要两次解包。

```python
# 错误 ❌
return data.get("issues", [])

# 正确 ✅
return data.get("data", {}).get("issues", [])
```

---

### PIT-VK-005 — `remove_issue_tag` 需要 `issue_tag_id`，不是 `tag_id`

**症状**：想删除 Issue 标签时搞不清楚传哪个 ID。

**说明**：`remove_issue_tag` 接受的是"Issue-Tag 关联记录"的 ID（`issue_tag_id`），不是标签本身的 `tag_id`。需要先调 `list_issue_tags` 获取关联记录 ID。

```python
# 流程
tags = mcp.call("list_issue_tags", {"issue_id": "..."})
for tag in tags:
    if tag["name"] == "target-tag":
        mcp.call("remove_issue_tag", {"issue_tag_id": tag["issue_tag_id"]})
```

---

## 四、REST API 备忘（MCP 工具的底层端点）

VK 后端默认运行在 `http://127.0.0.1:9527`，以下是常用端点：

| 用途 | 方法 | 路径 |
|------|------|------|
| 列出 Issues | GET | `/api/remote/issues?project_id=<id>` |
| 更新 Issue | PATCH | `/api/remote/issues/<issue_id>` |
| 列出 Workspaces | GET | `/api/task-attempts` |
| 创建 Workspace | POST | `/api/task-attempts/create-and-start` |
| 列出项目 | GET | `/api/remote/projects?organization_id=<id>` |
| 列出仓库 | GET | `/api/repos` |

**响应格式规律**：
- `/api/remote/*` 端点：`{"success": true, "data": {...}}` 或 `{"success": true, "data": [...]}`
- `/api/task-attempts`：裸 JSON 数组 `[...]`（无信封）
- `/api/task-attempts/create-and-start`：422 时返回 `text/plain` 错误字符串

---

## 五、与官方文档的差异

| 官方文档说法 | 实际行为 | 影响 |
|------------|---------|------|
| `repos[].base_branch` | 实际字段名 `target_branch` | create-and-start 直接调用时 422 |
| `start_workspace_session` 返回 `{success, workspace_id}` | MCP 解析失败返回 `{success: false}` | 需另行检验 workspace 是否创建 |
| `list_workspaces` 正常可用 | v0.1.22-23 中解析 response 失败 | 需绕过到 REST API |
