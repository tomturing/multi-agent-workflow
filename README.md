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
                 │ create_task / start_workspace_session
                 ▼
┌────────────────────────────────────────────────────┐
│               Vibe Kanban (调度中心)                │
│   管理任务 │ 分配 Workspace │ 自动 Git Worktree      │
│   Agent Profiles │ MCP Server                      │
└───────┬────────────┬────────────┬──────────────────┘
        ▼            ▼            ▼
   ┌──────────┐  ┌──────────┐  ┌──────────┐
   │ Claude   │  │  Codex   │  │  Gemini  │
   │  Code    │  │   CLI    │  │   CLI    │
   ├──────────┤  ├──────────┤  ├──────────┤
   │ 编码/审查 │  │ 编码/审查│  │ 编码/审查 │
   │ worktree │  │ worktree │  │ worktree │
   └────┬─────┘  └────┬─────┘  └────┬─────┘
        └─────────────┼─────────────┘
                      ▼
            质量门禁 → PR → 合并 main
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
├── templates/
│   ├── CLAUDE.md.tmpl              # 项目 CLAUDE.md 模板（含占位符变量）
│   ├── gitignore.append            # .gitignore 追加内容
│   ├── Makefile.append             # Makefile 追加内容
│   └── vk/
│       ├── workflow.md             # 通用工作流规范（§1-§9）
│       ├── prompts/
│       │   ├── coder.md            # Coder Agent 提示词
│       │   ├── reviewer.md         # Reviewer Agent 提示词
│       │   └── planner.md          # Planner Agent 提示词
│       └── reports/
│           └── .gitignore
└── scripts/
    ├── agent-quality-gate.sh       # 质量门禁脚本
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

| 阶段 | 执行者 | 动作 |
|------|--------|------|
| 需求分析 | Copilot Plan Mode | 分析需求 → 画架构图 → 生成任务分解 |
| 任务创建 | Copilot (VK MCP) | 调用 `create_task` 自动创建 Issues |
| 并行编码 | Claude/Codex/Gemini | 各自 worktree 独立开发 |
| 质量门禁 | agent-quality-gate.sh | lint + 单测 + 冲突检测 |
| 代码审查 | 任意 Agent | 交叉审查，生成结构化报告 |
| 合并上线 | 人工确认 | PR → main → post-merge-verify |

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
make vk              # 启动 Vibe Kanban
make quality-gate    # 运行质量门禁
make conflict-check  # 检测 worktree 冲突
make post-merge      # 合并后验证
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

### 5. init.sh 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| 脚本执行到一半中断 | `set -e` 下某步返回非零 | 所有 `safe_copy` / `safe_append` 已确保返回 0 |
| Makefile 重复追加 | 标记检测不够健壮 | `safe_append` 支持多标记检测（任一匹配即跳过） |
| 符号链接 AGENTS.md 报错 | Windows 文件系统不支持符号链接 | 在 WSL 内运行，或手动复制代替 |

## License

MIT
