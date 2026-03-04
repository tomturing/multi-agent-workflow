# multi-agent-workflow — 调度器开发规范

> **本文件适用于在 multi-agent-workflow 仓库内工作的 AI Agent。**
> VK MCP 工具的详细 API 参考和踩坑记录见 `docs/04_VK_MCP手册.md`。

---

## 1. 项目结构

```
multi-agent-workflow/
├── dispatcher/
│   ├── __main__.py       # 入口：python3 -m dispatcher --project-dir <dir> run
│   ├── core.py           # 状态机引擎：轮询 + 编排动作
│   ├── vk.py             # VK 客户端：REST（轮询）+ MCP stdio（Session 创建）
│   ├── github.py         # GitHub REST v3：PR 创建 / 合并 / 列表
│   └── config.py         # DispatcherConfig 数据类 + JSON 加载
├── templates/
│   └── vk/
│       ├── dispatcher.json   # dispatcher.json 模板
│       └── prompts/          # coder.md / reviewer.md
├── docs/
│   ├── 01_需求说明.md
│   ├── 02_架构设计.md
│   ├── 03_现状调研.md
│   └── 04_VK_MCP手册.md     # ← VK MCP 使用手册（含踩坑）
└── init.sh               # 初始化目标项目的 .vk/ 目录
```

## 2. 运行与调试

```bash
# 以目标项目运行调度器
python3 -m dispatcher --project-dir /mnt/d/Workflow/hci-troubleshoot-platform run

# 单次轮询（调试用）
python3 -m dispatcher --project-dir /path/to/project poll-once

# 检查配置文件是否合法
python3 -m dispatcher --project-dir /path/to/project validate
```

**配置文件位置**：`<project-dir>/.vk/dispatcher.json`

## 3. 关键设计决策

### 状态机模型（`core.py`）

- Issue 状态变化驱动动作：`To do` → 创建编码 Session，`In review` → 创建审查 Session，`Done` → 合并 PR
- `_check_pending`：补偿机制，在 Issue 已处于某状态但动作尚未执行时触发
- 状态持久化：每次 `poll_once` 末尾必须调 `_save_state()`（即使无变化），防止重启后重复触发

### VK 客户端（`vk.py`）

- `VKRestClient`：只用于轮询 Issue 列表和更新状态，纯 HTTP，轻量
- `VKMCPClient`：只用于 `start_workspace_session` 等需要 MCP 协议的操作
- MCP 客户端短生命周期：`connect()` → 调用 → `close()`，不保持常驻

## 4. 已知 Bug 与修复历史

### v1.0：首次实现（commit `e50c796`）修复的 4 个 Bug

**Bug 1** — `list_issues` 响应路径错误：
```python
# 错误
return data.get("issues", [])
# 修复
return data.get("data", {}).get("issues", [])
```

**Bug 2** — Issue 状态字段用错：
```python
# 错误：VK REST 返回 status_id（UUID），不是 status（名称）
new_status = issue.get("status")     # 永远是空字符串
# 修复：维护反向映射
self._status_id_to_name = {v: k for k, v in config.status_map.items()}
new_status = self._status_id_to_name.get(issue.get("status_id", ""), "")
```

**Bug 3** — 缺少 "To do 无 Session" 的补偿分支：
```python
# 在 _check_pending 中，漏掉了首次发现 To do 时启动编码的逻辑
if t.status == "To do" and self.config.auto_start_coding and not t.coding_workspace_id:
    self._action_start_coding(issue_id, issue, trace_id)
    return
```

**Bug 4** — 只在有状态变化时保存：
```python
# 错误：首次发现 Issue 时不保存，重启后重复触发
if transitions > 0:
    self._save_state()
# 修复：始终保存
self._save_state()   # poll_once 末尾无条件调用
```

### v1.1：`start_workspace_session` 返回失败但实际成功（待修复）

**现象**：MCP 工具返回 `{success: false, "error": "Failed to parse VK API response"}`, 但 VK 后端已创建 workspace。

**根因**：VK MCP v0.1.22/0.1.23 中 `start_workspace_session` 的响应解析有 Bug（详见 `docs/04_VK_MCP手册.md` PIT-VK-002）。

**应对方案**：调用后用 REST API 确认 workspace 是否真实存在，存在则保存 ID 并跳过重试：
```python
def _get_existing_workspace(self, issue_id: str) -> str | None:
    """查询 REST API 确认 workspace 是否已创建"""
    import urllib.request, json
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{self.config.vk_port}/api/task-attempts")
        for ws in json.loads(resp.read()):
            if not ws.get("archived") and ws.get("task_id") == issue_id:
                return ws["id"]
    except Exception:
        pass
    return None
```

## 5. 避坑指南

在产生或审查 dispatcher 代码前，务必阅读：
- `docs/04_VK_MCP手册.md` — VK MCP API 行为 + 与官方文档差异
- `~/.claude/pitfalls/python.md` — Python 通用陷阱（ORM、异常等）
- `~/.claude/pitfalls/shell.md` — Shell 脚本陷阱

### dispatcher 专属规则

1. **不要假设 MCP 工具返回的 `success: false` 意味着操作失败** — 先用 REST 验证实际状态
2. **status_map 是双向的** — `dispatcher.json` 中存名称→UUID，代码中需要 UUID→名称时用 `{v:k for k,v in map.items()}`
3. **MCP 客户端有 warmup 延迟** — `connect()` 后需 `time.sleep(0.5)` 才能稳定发请求
4. **VK 进程不是总在跑** — 每次 poll 前先做 REST health check，VK 未启动时跳过而非崩溃
5. **worktree 清理** — 测试产生的大量废弃 workspace 可通过 VK UI 批量 Archive，或调 REST API `PATCH /api/task-attempts/<id>` 将 `archived` 设为 `true`

## 6. 测试目标项目

当前接入的测试项目：

- **项目**：`/mnt/d/Workflow/hci-troubleshoot-platform`
- **VK project_id**：`245d1072-203d-43ea-b0c1-1de12b5245d4`
- **VK repo_id**：`ca4e2250-ccf2-4de0-8bb5-bccc652fa7b6`
- **GitHub 仓库**：`tomturing/hci-troubleshoot-platform`（私有，默认分支 `master`）
- **dispatcher.json**：`hci-troubleshoot-platform/.vk/dispatcher.json`
