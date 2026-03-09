# Dispatcher 项目设计记录

> 记录日期：2026-03-09  
> 项目：multi-agent-workflow / Dispatcher

---

## 1. 系统定位

Dispatcher 是 vibe-kanban (VK) 之上的**编排层**，填补 VK "人驱动 Agent" 模式与"Agent 驱动 Agent"目标之间的缺口。

```
VK 平台（任务管理 + Agent 执行环境）
    ↕  REST API / MCP stdio / SQLite 直读
Dispatcher（本项目）— 状态机编排器
    ↕  轮询 / 触发
Coding Agent → Reviewer Agent → GitHub PR → Merge
```

---

## 2. 状态机

```
To do
  └─[auto_start_coding]──► In progress
                                │
                          (cleanup: push 分支 + 改状态)
                                │
                           In review
                                │
                     ┌──────────┴──────────┐
                [Reviewer approved]   [Reviewer rejected]
                     │                     │
                   Done              In progress
                     │               (重新编码)
                [auto_merge]
```

**关键约束**：
- 每个状态转换必须有**基础设施级信号**作为触发条件
- Agent 自然语言输出**永远不作为信号源**

---

## 3. 信号可信度框架

| 信号来源 | 可信度 | 示例 |
|---------|-------|------|
| VK 基础设施写入 | ✅ 可信 | `exit_code`、`issue.status_id`、`started_at` |
| GitHub 基础设施 | ✅ 可信 | PR state、merge status |
| Agent 调用 MCP 工具 | ✅ 可信 | `update_issue` 修改 status/description |
| Agent 自然语言输出 | ❌ 不可信 | `coding_agent_turns.summary` 中的 APPROVED/CHANGES_REQUESTED |
| Agent 写文件 | ❌ 不可信 | 格式不一致、不执行概率高 |
| Agent HTTP 回调 | ❌ 不可信 | 同上 |

**核心原因**：LLM 在"副作用型任务"（写文件、调 API）上的可靠性远低于"生成型任务"（写代码、分析）。多 Agent 链条中，每个节点的概率误差会叠加，链条越长越不可靠。

---

## 4. 核心文件

| 文件 | 职责 |
|-----|------|
| `dispatcher/core.py` | 中央状态机；Issue 轮询、转换检测、编排动作 |
| `dispatcher/vk.py` | VK REST 客户端 + MCP stdio 客户端 |
| `dispatcher/vk_db.py` | VK SQLite 直读（只读，exit_code / timeout 检测）|
| `dispatcher/github.py` | GitHub API（创建 PR、merge、diff）|
| `.vk/dispatcher.json` | 运行时配置（项目 ID、超时、重试等）|
| `.vk/prompts/coder.md` | Coder Agent 基础提示词模板 |
| `.vk/prompts/reviewer.md` | Reviewer Agent 基础提示词模板 |

---

## 5. 悲观等待范式（核心设计原则）

### 旧范式（乐观推进）
```
信号模糊 → 猜测推进 → 大概率走错状态 → 人工救火
```

### 新范式（悲观等待）
```
信号模糊 → 停止等待 → 超时 → STUCK → 重试 → BLOCKED → 人工介入
```

### STUCK 降级链路
```
正在运行
  → 超时（max_wait_minutes）
  → STUCK（记录 stuck_since、stuck_reason）
  → 重建 session，retry_count += 1
  → retry_count >= max_retries
  → BLOCKED（update_issue_status("Blocked") + 告警日志 🚫）
  → 等待人工介入（手动改回 To do 或 In progress）
```

---

## 6. Review 阶段设计

### 问题根源
Reviewer Agent 将审查结论写在自然语言回复中（APPROVED / CHANGES_REQUESTED），Dispatcher 解析 `coding_agent_turns.summary` 关键词来判断结论。这是典型的"不可信信号"依赖。

### 解决方案
Reviewer Agent 必须调用 MCP 工具将结论**直接写入 VK 基础设施**：

```
审查通过：
  get_context()           → 获取 issue_id
  update_issue(status="Done")

审查不通过：
  get_context()           → 获取 issue_id
  get_issue()             → 获取当前 description
  update_issue(
    description = 原 description + "## Review Feedback\n...",
    status = "In progress"
  )
```

Dispatcher 只观察 `issue.status` 变化（基础设施级信号），不解析任何文本。

### Feedback 传递路径
```
issue.description（Review Feedback 段）
  → Dispatcher._extract_review_feedback()
  → IssueTracker.review_feedback
  → _build_coding_prompt() extra_context 注入
  → 下一轮 Coder Agent prompt 开头
```

---

## 7. VK MCP 工具可用性（源码验证）

基于 vibe-kanban v0.1.26 源码（`crates/mcp/src/task_server/tools/`）验证：

| 场景 | MCP Mode | 可用工具 |
|------|---------|---------|
| 普通 Workspace（Coder/Reviewer）| `global_mode_router` | 全部工具（含 `update_issue`、`get_context`、`get_issue`）|
| Orchestrator Session | `orchestrator_mode_router` | `context + workspaces + session` 子集（**不含** `update_issue`）|

**关键结论**：Reviewer Agent 在普通 Workspace 中运行，`update_issue` 工具可用。  
`McpContext.issue_id` 由 VK 自动从 Workspace 关联的 Issue 填充，Reviewer 调用 `get_context` 即可获取，无需 Dispatcher 注入 UUID。

---

## 8. 配置参数

```json
{
  "max_coding_retries": 2,
  "max_review_retries": 2,
  "max_coding_wait_minutes": 60,
  "max_review_wait_minutes": 15
}
```

| 参数 | 含义 | 建议值 |
|-----|------|-------|
| `max_coding_retries` | coding session 最大重试次数 | 2 |
| `max_review_retries` | review session 最大重试次数 | 2 |
| `max_coding_wait_minutes` | coding session 超时阈值（分钟）| 60 |
| `max_review_wait_minutes` | review session 完成后等待 issue.status 变化的超时（分钟）| 15 |
