# multi-agent-workflow

> 多 Agent 并行开发工作流模板 — 一键初始化，快速接入任何项目

## 概述

本仓库提供 **多 Agent 并行开发工作流** 的通用模板和初始化工具。
支持 Claude Code、Codex CLI、Gemini CLI 三种 Agent，通过 Vibe Kanban 统一调度。

### 核心特性

- **一键初始化**: `init.sh -p <project>` 快速为任何项目注入工作流
- **Agent 角色灵活**: 所有 Agent 既可编码也可 Review，动态分配
- **并行隔离**: Git Worktree 隔离各 Agent 工作分支，互不干扰
- **质量门禁**: 自动 lint + 测试 + 冲突检测，合并前必须通过
- **智能注入**: 对已有项目不覆盖现有文件，安全追加

## 架构

```
┌────────────────────────────────────────────────────┐
│              VS Code Copilot (Plan Mode)           │
│         需求分析 → Mermaid 架构图 → 任务分解         │
│           调用 VK MCP → 自动创建 Issue              │
└────────────────┬───────────────────────────────────┘
                 │ create_task
                 ▼
┌────────────────────────────────────────────────────┐
│               Vibe Kanban (任务/Workspace 管理)     │
│   看板 │ Workspace │ Git Worktree │ MCP Server      │
└───────┬────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────┐
│           Dispatcher (中央调度守护进程)              │
│  轮询 VK REST API → 检测状态变化 → 触发编排动作      │
│  To do → 编码Session │ In review → 审查Session      │
│  Done → 合并主分支   │ 交叉审查矩阵                  │
└───────┬────────────┬────────────┬──────────────────┘
        ▼            ▼            ▼
   ┌──────────┐  ┌──────────┐  ┌──────────┐
   │ Claude   │  │  Codex   │  │  Gemini  │
   │  Code    │  │   CLI    │  │   CLI    │
   ├──────────┤  ├──────────┤  ├──────────┤
   │ 编码/审查 │  │ 编码/审查│  │ 编码/审查 │
   │ worktree │  │ worktree │  │ worktree │
   └────┬─────┘  └────┬─────┘  └────┬─────┘
        │              │              │
        └── cleanup 钩子 → 质量门禁 → 状态更新 ──┐
                                                │
            Dispatcher 检测状态变化 ←────────────┘
              → 创建交叉审查 / 合并 / Done
```

## 快速开始

### 1. 初始化新项目

```bash
# 克隆模板仓库
git clone <this-repo> ~/Workflow/multi-agent-workflow

# 初始化新项目（交互式问答）
cd ~/Workflow/multi-agent-workflow
bash init.sh -p my-new-project

# 非交互模式（生成 TODO 占位符，稍后手动填写）
bash init.sh -p my-new-project --non-interactive
```

### 2. 注入到已有项目

```bash
# 对已有项目注入工作流文件（不覆盖已有文件）
bash init.sh -p existing-project
```

### 3. 初始化后的操作

```bash
cd ~/Workflow/my-new-project

# 1. 检查并完善 CLAUDE.md 中的 TODO 项
vim CLAUDE.md

# 2. 启动 Vibe Kanban
make vk    # 或 npx vibe-kanban

# 3. 配置 Agent API（通过 cc-switch）
cc-switch claude   # 切换 Claude Code API
cc-switch codex    # 切换 Codex API
cc-switch gemini   # 切换 Gemini API

# 4. 提交初始化
git add . && git commit -m "初始化多 Agent 工作流"
```

## 目录结构

```
multi-agent-workflow/
├── README.md                       # 本文件
├── init.sh                         # 初始化入口脚本
├── dispatcher/                     # 中央调度器（Python 模块）
│   ├── __init__.py
│   ├── __main__.py                 # CLI: python -m dispatcher
│   ├── core.py                     # 轮询引擎 + 状态机 + 编排动作
│   └── vk.py                       # VK REST API + MCP stdio 客户端
├── templates/
│   ├── CLAUDE.md.tmpl              # 项目 CLAUDE.md 模板（含占位符变量）
│   ├── gitignore.append            # .gitignore 追加内容
│   ├── Makefile.append             # Makefile 追加内容
│   └── vk/
│       ├── workflow.md             # 通用工作流规范（§1-§9）
│       ├── dispatcher.json         # 调度器配置模板
│       ├── prompts/
│       │   ├── coder.md            # Coder Agent 提示词
│       │   ├── reviewer.md         # Reviewer Agent 提示词
│       │   └── planner.md          # Planner Agent 提示词
│       └── reports/
│           └── .gitignore
└── scripts/
    ├── agent-quality-gate.sh       # 质量门禁脚本
    ├── vk-hooks.sh                 # VK 状态流转钩子（阶段感知）
    ├── check-worktree-conflicts.sh # Worktree 冲突检测脚本
    └── post-merge-verify.sh        # 合并后验证脚本
```

### 初始化后目标项目的文件结构

```
my-project/
├── CLAUDE.md              # 项目规范（Agent 阅读的核心文件）
├── AGENTS.md              # 符号链接 → CLAUDE.md
├── CLAUDE.local.md        # 个人本地配置（不提交 git）
├── .vscode/
│   └── mcp.json           # VS Code MCP Server 配置（VK 连接）
├── .vk/
│   ├── workflow.md        # 通用工作流规范
│   ├── prompts/
│   │   ├── coder.md
│   │   ├── reviewer.md
│   │   └── planner.md
│   └── reports/
│       └── .gitignore
├── scripts/
│   ├── agent-quality-gate.sh
│   ├── check-worktree-conflicts.sh
│   └── post-merge-verify.sh
├── .gitignore             # 已追加工作流排除规则
└── Makefile               # 已追加工作流 Make 命令
```

## 工作流概览

详见初始化后项目中的 `.vk/workflow.md`，核心流程：

| 阶段 | 执行者 | 动作 | 自动化 |
|------|--------|------|--------|
| 需求分析 | Copilot Plan Mode | 分析需求 → 架构图 → 任务分解 | 人工 |
| 任务创建 | Copilot (VK MCP) | `create_task` → Issue To do | 人工/半自动 |
| 编码启动 | **Dispatcher** | 检测 To do → 创建编码 Session | 可选自动 |
| 并行编码 | Claude/Codex/Gemini | 各自 worktree 独立开发 | Agent 自动 |
| 质量门禁 | vk-hooks.sh | lint + 测试 → 状态 In review | **全自动** |
| 交叉审查 | **Dispatcher** | 检测 In review → 创建审查 Session | **全自动** |
| 审查执行 | 交叉 Agent | 审查 PR diff → 通过/驳回 | Agent 自动 |
| 合并上线 | **Dispatcher** | 检测 Done → merge → main | **全自动** |

## 配置说明

### VK MCP Server

在 **VS Code 工作区根目录** 的 `.vscode/mcp.json` 中配置（`init.sh` 会自动创建）：

```json
{
  "servers": {
    "vibe_kanban": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "vibe-kanban@latest", "--mcp"],
      "env": {
        "PORT": "9527"
      }
    }
  }
}
```

> **⚠️ 注意**: VS Code MCP 配置使用 `"servers"` 键（非 `"mcpServers"`），且必须包含 `"type": "stdio"`。
> `"mcpServers"` 是 Claude Desktop 的格式，VS Code 不识别。
> `"env"` 中的 `PORT` 固定了 VK 端口，避免每次重启后端口变化导致需要 Reload Window。
```

### cc-switch

所有 Agent 的 API Key 通过 `cc-switch` 统一管理，切换时自动配置环境变量：

```bash
cc-switch claude    # ANTHROPIC_API_KEY
cc-switch codex     # OPENAI_API_KEY
cc-switch gemini    # GOOGLE_API_KEY
```

### Make 命令

初始化后项目支持以下 Make 命令：

```bash
make vk                # 启动 Vibe Kanban
make dispatcher        # 启动中央调度器（轮询模式，Ctrl+C 停止）
make dispatcher-once   # 单次轮询（测试用）
make dispatcher-status # 查看调度状态
make quality-gate      # 运行质量门禁
make conflict-check    # 检测 worktree 冲突
make post-merge        # 合并后验证
```

## 自定义

### 修改质量门禁

编辑 `scripts/agent-quality-gate.sh`，调整 lint/test 命令以适配你的项目。

### 修改 Agent 提示词

编辑 `.vk/prompts/` 下的 prompt 文件，添加项目特定的上下文和规则。

### 文件层级覆盖

```
~/.claude/CLAUDE.md          ← 全局个人规范（全局生效）
./CLAUDE.md                  ← 项目规范（提交 git，团队共享）
./CLAUDE.local.md            ← 个人定制（不提交 git）
./.vk/workflow.md            ← 通用工作流（提交 git）
./.vk/prompts/*.md           ← Agent 提示词（提交 git）
```

## 常见问题 / Troubleshooting

### 1. VS Code 找不到 MCP Server

**症状**: Copilot Chat 中 MCP 工具列表为空，或 VK MCP Server 显示 Not Running。

**原因**: `.vscode/mcp.json` 格式不正确或文件位置不对。

**排查清单**:
- ✅ 使用 `"servers"` 键（**不是** `"mcpServers"`，后者是 Claude Desktop 格式）
- ✅ 每个 server 必须包含 `"type": "stdio"`
- ✅ 文件必须在 **VS Code 工作区根目录** 的 `.vscode/mcp.json`
  - 如果 VS Code 打开的是 `~/Workflow/`，则放在 `~/Workflow/.vscode/mcp.json`
  - 子项目中的 `.vscode/mcp.json` 不会被读取（除非单独打开该子项目）
- ✅ 修改后需执行 **Reload Window**（`Ctrl+Shift+P` → `Developer: Reload Window`）

**正确格式**:
```json
{
  "servers": {
    "vibe_kanban": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "vibe-kanban@latest", "--mcp"],
      "env": {
        "PORT": "9527"
      }
    }
  }
}
```

### 2. VK MCP 连接超时 / 工具调用失败

**症状**: MCP Server 显示 Running 但调用 `list_issues` 等工具超时或报错。

**原因**: VK 后端未运行，或端口文件已过期。

**排查步骤**:
```bash
# 1. 确认 VK 后端是否运行
pgrep -f "vibe-kanban" || echo "VK 未运行"

# 2. 检查端口文件
cat /tmp/vibe-kanban/vibe-kanban.port

# 3. 确认端口是否可达
curl -s http://localhost:$(cat /tmp/vibe-kanban/vibe-kanban.port)/api/health

# 4. 如果端口不通，重启 VK
npx vibe-kanban  # 或 make vk

# 5. VK 重启后端口会变化，必须 Reload Window 让 MCP 读取新端口
# VS Code: Ctrl+Shift+P → Developer: Reload Window
```

### 3. VK 重启后 MCP 不工作

**症状**: VK 后端重启后，MCP 工具调用失败。

**原因**: MCP Server 在启动时读取 `/tmp/vibe-kanban/vibe-kanban.port` 并缓存端口。VK 重启后端口变化，但 MCP 仍使用旧端口。

**解决**: VK 重启后，**必须** 执行 VS Code Reload Window 以重启 MCP Server。

### 4. 多项目工作区的 mcp.json 位置

**症状**: 在项目子目录中创建了 `.vscode/mcp.json`，但 VS Code 不识别。

**原因**: VS Code 只读取 **当前打开的工作区根目录** 下的 `.vscode/mcp.json`。

**解决方案**:
- **单项目**: 项目根目录下的 `.vscode/mcp.json` 即可
- **多项目工作区**: 在 VS Code 打开的最外层目录创建 `.vscode/mcp.json`
- `init.sh` 会同时在目标项目和工作区根目录都创建 mcp.json

### 5. VK Worktree 与 Master 不同步

**症状**: Master 上修复了质量门禁脚本 bug，但 VK Worktree 中的 cleanup 仍然失败。

**原因**: VK Worktree 在 `start_workspace_session` 时从当时的 master（或指定 base_branch）创建分支。如果 master 在 worktree 创建后有修复提交，worktree 不会自动同步。

**解决方案**:
```bash
# 方案 1: 直接复制修复文件到 worktree（推荐，避免冲突）
cp master/scripts/agent-quality-gate.sh worktree/scripts/
cp master/pyproject.toml worktree/
cd worktree && git add -A && git commit -m "fix: 同步 master 质量门禁修复"

# 方案 2: rebase（⚠️ 如果 master 有大量格式化变更会产生大量冲突）
cd worktree && git rebase master  # 不推荐：ruff format 等批量变更会导致大量冲突
```

### 6. Ruff 格式化引发 Rebase 冲突

**症状**: `git rebase master` 产生大量冲突，每个 Python 文件都有 conflict marker。

**原因**: Master 上运行了 `ruff format`（批量重新格式化几十个文件），Agent 分支修改了其中部分文件的逻辑。Git 无法区分"格式变更"和"逻辑变更"。

**解决**: 不要 rebase。改用 cherry-pick 或直接复制需要同步的特定文件。大批量格式化应在 Agent 分支创建前完成。

### 7. VK MCP `start_workspace_session` 参数名

**症状**: 调用 `start_workspace_session` 时报 `missing field executor` 或 `missing field title`。

**原因**: API 参数命名与直觉不同。

**正确参数**:
```
title:            Session 名称（必填）
executor:         运行时类型，可选值: CODEX / CLAUDE / GEMINI（不是 runtime）
issue_id:         关联的 Issue ID
repos:            [{"repo_id": "...", "base_branch": "..."}]
prompt_override:  自定义 system prompt
```

### 8. 质量门禁前端检查误判

**症状**: 前端 lint 报 `Command "lint" not found`，但脚本报为 FAIL 而不是 SKIP。

**原因**: 两个问题叠加：
1. `frontend/node_modules/` 存在（只有 `.pnpm-workspace-state` 文件），脚本判断为"已安装"
2. `package.json` 没有定义 `lint` 脚本

**修复**: 质量门禁脚本需要：
- 检查 `frontend/node_modules/.pnpm/` 而非 `frontend/node_modules/`
- 运行 lint 前检查 `grep -q '"lint"' package.json`

### 9. init.sh 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| 脚本执行到一半中断 | `set -e` 下某步返回非零 | 所有 `safe_copy` / `safe_append` 已确保返回 0 |
| Makefile 重复追加 | 标记检测不够健壮 | `safe_append` 支持多标记检测（任一匹配即跳过） |
| 符号链接 AGENTS.md 报错 | Windows 文件系统不支持符号链接 | 在 WSL 内运行，或手动复制代替 |

## 中央调度器（Dispatcher）

### 设计思路

由于 Vibe Kanban 目前**不支持 Webhook**，无法在 Issue 状态变化时主动通知外部系统。
Dispatcher 通过轮询 VK REST API 弥补这一缺口，实现全自动化流水线。

**核心理念：cleanup 脚本只负责"发信号"（更新状态），Dispatcher 负责"收信号并编排"。**

```
Issue 在 VK 看板上的流转:

 ┌────────┐     ┌────────────┐     ┌───────────┐     ┌──────┐
 │ To do  │ ──► │In progress │ ──► │ In review │ ──► │ Done │
 └────────┘     └────────────┘     └───────────┘     └──────┘
      │              ▲                   │ ▲               │
      │              │                   │ │               │
 Dispatcher    编码 Agent 的          Dispatcher      Dispatcher
 创建编码      cleanup 自动设置       创建审查        合并到 main
 Session                             Session
```

### 信号机制

| 生命周期阶段 | 信号来源 | 信号内容 | Dispatcher 动作 |
|------------|---------|---------|----------------|
| Issue → To do | 用户/Copilot | VK Issue 状态 | 创建编码 Session (可选) |
| 编码完成 | cleanup (vk-hooks.sh) | 状态 → In review | 创建交叉审查 Session |
| 审查完成 | cleanup (vk-hooks.sh) | 状态 → Done | 合并编码分支到 main |
| 审查驳回 | cleanup (vk-hooks.sh) | 状态 → In progress | 无（等待修复后重跑） |

### 配置

`.vk/dispatcher.json`:

```json
{
    "organization_id": "xxx",
    "project_id": "xxx",
    "repo_id": "xxx",
    "main_branch": "master",
    "poll_interval": 30,
    "vk_port": 9527,
    "auto_start_coding": false,
    "auto_start_review": true,
    "auto_merge": true,
    "default_coder_executor": "CLAUDE_CODE",
    "cross_review_map": {
        "CLAUDE_CODE": "CODEX",
        "CODEX": "CLAUDE_CODE"
    }
}
```

> `auto_start_coding` 默认关闭——Issue 描述质量直接影响编码质量，建议人工审核后再启动。

### 使用

```bash
# 启动守护进程（在另一个终端窗口运行）
make dispatcher

# 单次轮询（测试/调试用）
make dispatcher-once

# 查看调度状态
make dispatcher-status

# 高级用法
python -m dispatcher -d /path/to/project run --dry-run  # 仅检测不执行
python -m dispatcher -d /path/to/project -v run          # 调试日志
```

### 阶段感知机制

`vk-hooks.sh` 通过两种方式判断当前 worktree 是编码阶段还是审查阶段：

1. **`.vk/phase` 文件**（优先级高）— Dispatcher 创建 Session 时写入
2. **分支名约定**（回退方案）— VK 审查分支名包含 `review`

根据阶段:
- 编码阶段 cleanup 成功 → 状态 "In review"
- 审查阶段 cleanup 成功 → 状态 "Done"

### 状态持久化

Dispatcher 将追踪状态持久化到 `.vk/dispatcher_state.json`，支持:
- 重启后恢复状态，避免重复触发
- 补偿机制：首次启动时检测已有 Issue 的遗漏动作
- 分支信息记录：自动关联 Issue → workspace → 分支名

## License

MIT
