#!/usr/bin/env bash
# ============================================================================
# multi-agent-workflow init.sh
# 用途: 一键初始化多 Agent 并行开发工作流到任意项目
# 用法: bash init.sh -p <project-name>
#       bash init.sh -p <project-name> --non-interactive
# ============================================================================

set -euo pipefail

# ---- 颜色 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ---- 路径 ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE_DIR="${SCRIPT_DIR}/templates"
SCRIPTS_DIR="${SCRIPT_DIR}/scripts"
WORKSPACE_DIR="$(dirname "$SCRIPT_DIR")"  # multi-agent-workflow 的父目录

# ---- realpath fallback ----
# 某些系统（如 macOS）可能没有 realpath 命令
if command -v realpath &>/dev/null; then
    _realpath() { realpath "$1"; }
else
    _realpath() { python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$1"; }
fi

# ---- 默认值 ----
PROJECT_NAME=""
NON_INTERACTIVE=false

# ---- 使用说明 ----
usage() {
    echo -e "${BOLD}multi-agent-workflow init.sh${NC} — 初始化多 Agent 并行开发工作流"
    echo ""
    echo -e "用法: ${CYAN}bash init.sh -p <project-name>${NC} [选项]"
    echo ""
    echo "选项:"
    echo "  -p, --project <name>    项目名称（在同级目录创建/查找项目目录）"
    echo "  --non-interactive       非交互模式（使用 TODO 占位符，不询问）"
    echo "  -h, --help              显示帮助"
    echo ""
    echo "示例:"
    echo "  bash init.sh -p my-new-project          # 交互式创建新项目"
    echo "  bash init.sh -p existing-project         # 注入到已有项目"
    echo "  bash init.sh -p my-project --non-interactive  # 非交互模式"
}

# ---- 参数解析 ----
while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--project)
            PROJECT_NAME="$2"
            shift 2
            ;;
        --non-interactive)
            NON_INTERACTIVE=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo -e "${RED}未知参数: $1${NC}"
            usage
            exit 1
            ;;
    esac
done

if [ -z "$PROJECT_NAME" ]; then
    echo -e "${RED}错误: 必须指定项目名称 (-p <name>)${NC}"
    usage
    exit 1
fi

# ---- 项目目录 ----
PROJECT_DIR="${WORKSPACE_DIR}/${PROJECT_NAME}"

# ---- 工具函数 ----

log_header() {
    echo -e "\n${BLUE}═══════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
}

log_ok() {
    echo -e "  ${GREEN}✓${NC} $1"
}

log_skip() {
    echo -e "  ${YELLOW}⊘${NC} $1（已存在，跳过）"
}

log_info() {
    echo -e "  ${CYAN}ℹ${NC} $1"
}

# 安全复制：不覆盖已有文件
safe_copy() {
    local src="$1"
    local dst="$2"
    if [ -f "$dst" ]; then
        log_skip "$dst"
        return 0
    else
        mkdir -p "$(dirname "$dst")"
        cp "$src" "$dst"
        log_ok "创建 $dst"
        return 0
    fi
}

# 安全追加：检查标记避免重复（支持多个标记，任一匹配即跳过）
safe_append() {
    local src="$1"
    local dst="$2"
    shift 2
    local markers=("$@")

    if [ ! -f "$dst" ]; then
        cp "$src" "$dst"
        log_ok "创建 $dst"
    else
        for marker in "${markers[@]}"; do
            if grep -q "$marker" "$dst" 2>/dev/null; then
                log_skip "$dst 中已包含 '$marker'"
                return 0
            fi
        done
        echo "" >> "$dst"
        cat "$src" >> "$dst"
        log_ok "追加内容到 $dst"
    fi
}

# 交互式询问（带默认值）
ask() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"

    if $NON_INTERACTIVE; then
        eval "$var_name=\"$default\""
        return
    fi

    echo -en "  ${CYAN}?${NC} ${prompt}"
    if [ -n "$default" ]; then
        echo -en " ${YELLOW}[${default}]${NC}"
    fi
    echo -n ": "
    read -r input
    if [ -z "$input" ]; then
        eval "$var_name=\"$default\""
    else
        eval "$var_name=\"$input\""
    fi
}

# 多行输入
ask_multiline() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"

    if $NON_INTERACTIVE; then
        eval "$var_name=\"$default\""
        return
    fi

    echo -e "  ${CYAN}?${NC} ${prompt} ${YELLOW}(输入完成后按空行结束)${NC}"
    if [ -n "$default" ]; then
        echo -e "    ${YELLOW}默认: ${default}${NC}"
    fi
    local lines=""
    while IFS= read -r line; do
        [ -z "$line" ] && break
        lines+="${line}\n"
    done
    if [ -z "$lines" ]; then
        eval "$var_name=\"$default\""
    else
        eval "$var_name=\"$lines\""
    fi
}

# ---- 主流程 ----

main() {
    log_header "multi-agent-workflow 初始化"
    echo -e "  模板仓库: ${SCRIPT_DIR}"
    echo -e "  目标项目: ${PROJECT_DIR}"

    # ---- Step 1: 创建/确认项目目录 ----
    log_header "Step 1: 项目目录"

    if [ -d "$PROJECT_DIR" ]; then
        log_info "项目目录已存在: ${PROJECT_DIR}"
        log_info "将以注入模式运行（不覆盖已有文件）"
    else
        mkdir -p "$PROJECT_DIR"
        log_ok "创建项目目录: ${PROJECT_DIR}"

        # 初始化 git（如果还没有）
        if [ ! -d "${PROJECT_DIR}/.git" ]; then
            (cd "$PROJECT_DIR" && git init && git branch -m main 2>/dev/null || true)
            log_ok "git init（默认分支: main）"
        fi
    fi

    cd "$PROJECT_DIR"

    # ---- Step 2: 交互式收集项目信息 ----
    log_header "Step 2: 项目信息（用于生成 CLAUDE.md）"

    local project_description backend_stack frontend_stack database_stack
    local deploy_stack package_managers directory_structure
    local coding_conventions build_commands vk_setup_script vk_dev_server
    local forbidden_operations project_version

    ask "项目描述（一句话）" "TODO: 填写项目描述" project_description
    ask "当前版本" "0.1.0" project_version
    ask "后端技术栈" "TODO: 如 Python 3.12, FastAPI" backend_stack
    ask "前端技术栈" "TODO: 如 Vue 3, TypeScript, Vite" frontend_stack
    ask "数据库" "TODO: 如 PostgreSQL 15, Redis 7" database_stack
    ask "部署方式" "TODO: 如 Docker Compose, K8s" deploy_stack
    ask "包管理工具" "TODO: 如 uv (Python), pnpm (前端)" package_managers
    ask "VK Setup Script（安装依赖命令）" "TODO: 如 npm install" vk_setup_script
    ask "VK Dev Server（启动开发服务命令）" "TODO: 如 npm run dev" vk_dev_server

    ask_multiline "目录结构概述（每行一个目录）" "├── src/           # 源代码\n├── tests/         # 测试\n└── docs/          # 文档" directory_structure
    ask_multiline "编码规范（每行一条）" "- TODO: 填写编码规范" coding_conventions
    ask_multiline "构建/测试命令（每行一条）" "# TODO: 填写构建和测试命令" build_commands
    ask_multiline "禁止操作（格式：| 操作 | 原因 |）" "| TODO: 禁止操作 | 原因 |" forbidden_operations

    # ---- Step 3: 复制通用文件 ----
    log_header "Step 3: 复制通用工作流文件"

    # .vk/ 目录
    mkdir -p .vk/prompts .vk/reports .vk/logs
    safe_copy "${TEMPLATE_DIR}/vk/workflow.md" ".vk/workflow.md"
    safe_copy "${TEMPLATE_DIR}/vk/prompts/coder.md" ".vk/prompts/coder.md"
    safe_copy "${TEMPLATE_DIR}/vk/prompts/reviewer.md" ".vk/prompts/reviewer.md"
    safe_copy "${TEMPLATE_DIR}/vk/prompts/planner.md" ".vk/prompts/planner.md"
    safe_copy "${TEMPLATE_DIR}/vk/reports/.gitignore" ".vk/reports/.gitignore"
    # dev.sh — 一键启动守护脚本（含 VK 健康等待 + Dispatcher 异常通知）
    safe_copy "${TEMPLATE_DIR}/dev.sh" ".vk/dev.sh"
    chmod +x .vk/dev.sh 2>/dev/null || true
    # maw_dir — 记录 MAW 安装路径，供 dev.sh 在 dispatcher 未复制时回退使用
    echo "${SCRIPT_DIR}" > ".vk/maw_dir"
    log_ok "写入 .vk/maw_dir → ${SCRIPT_DIR}"

    # scripts/
    mkdir -p scripts
    safe_copy "${SCRIPTS_DIR}/agent-quality-gate.sh" "scripts/agent-quality-gate.sh"
    safe_copy "${SCRIPTS_DIR}/check-worktree-conflicts.sh" "scripts/check-worktree-conflicts.sh"
    safe_copy "${SCRIPTS_DIR}/post-merge-verify.sh" "scripts/post-merge-verify.sh"
    safe_copy "${SCRIPTS_DIR}/vk-hooks.sh" "scripts/vk-hooks.sh"
    chmod +x scripts/agent-quality-gate.sh scripts/check-worktree-conflicts.sh scripts/post-merge-verify.sh 2>/dev/null || true

    # dispatcher/ — 中央调度器（Python 模块）
    local DISPATCHER_SRC="${SCRIPT_DIR}/dispatcher"
    if [ -d "$DISPATCHER_SRC" ]; then
        mkdir -p dispatcher
        for py_file in "$DISPATCHER_SRC"/*.py; do
            [ -f "$py_file" ] && safe_copy "$py_file" "dispatcher/$(basename "$py_file")"
        done
        log_ok "复制 dispatcher/ 模块"
    fi

    # .vk/dispatcher.json 配置模板
    safe_copy "${TEMPLATE_DIR}/vk/dispatcher.json" ".vk/dispatcher.json"

    # ---- Step 4: 生成 CLAUDE.md ----
    log_header "Step 4: 生成 CLAUDE.md"

    if [ -f "CLAUDE.md" ]; then
        log_skip "CLAUDE.md（已存在，不覆盖。如需重新生成，请先删除）"
    else
        # 从模板生成，替换占位符
        sed \
            -e "s|{{PROJECT_NAME}}|${PROJECT_NAME}|g" \
            -e "s|{{PROJECT_DESCRIPTION}}|${project_description}|g" \
            -e "s|{{PROJECT_VERSION}}|${project_version}|g" \
            -e "s|{{BACKEND_STACK}}|${backend_stack}|g" \
            -e "s|{{FRONTEND_STACK}}|${frontend_stack}|g" \
            -e "s|{{DATABASE_STACK}}|${database_stack}|g" \
            -e "s|{{DEPLOY_STACK}}|${deploy_stack}|g" \
            -e "s|{{PACKAGE_MANAGERS}}|${package_managers}|g" \
            -e "s|{{VK_SETUP_SCRIPT}}|${vk_setup_script}|g" \
            -e "s|{{VK_DEV_SERVER}}|${vk_dev_server}|g" \
            "${TEMPLATE_DIR}/CLAUDE.md.tmpl" > "CLAUDE.md"

        # 多行字段需要特殊处理（sed 不好处理多行）
        # 使用 python 或 awk 替换（如果有的话）
        if command -v python3 &>/dev/null; then
            python3 -c "
import sys
content = open('CLAUDE.md').read()
replacements = {
    '{{DIRECTORY_STRUCTURE}}': '''$(echo -e "$directory_structure")''',
    '{{CODING_CONVENTIONS}}': '''$(echo -e "$coding_conventions")''',
    '{{BUILD_COMMANDS}}': '''$(echo -e "$build_commands")''',
    '{{FORBIDDEN_OPERATIONS}}': '''$(echo -e "$forbidden_operations")''',
}
for k, v in replacements.items():
    content = content.replace(k, v)
open('CLAUDE.md', 'w').write(content)
"
        fi

        log_ok "生成 CLAUDE.md"
    fi

    # ---- Step 4.5: 注入 Dispatcher 健康检查到 CLAUDE.md ----
    log_header "Step 4.5: Dispatcher 健康检查"

    # 检查是否已注入（通过标记检测）
    if grep -q "Dispatcher 健康检查" "CLAUDE.md" 2>/dev/null; then
        log_skip "CLAUDE.md 中已包含 Dispatcher 健康检查"
    else
        # 使用 Python 进行安全的模板渲染（避免 sed 转义问题）
        if command -v python3 &>/dev/null; then
            local PROJECT_DIR_ABS="$(_realpath "$PROJECT_DIR")"
            local DISPATCHER_DIR_ABS="$(_realpath "$SCRIPT_DIR")"
            local PROJECT_NAME_SAFE="$(basename "$PROJECT_DIR")"

            python3 -c "
import sys

# 读取模板
with open('${TEMPLATE_DIR}/claude_dispatcher_check.md', 'r') as f:
    template = f.read()

# 安全替换占位符（无需转义，Python 字符串替换自动处理特殊字符）
content = template.replace('{{PROJECT_DIR}}', '''$PROJECT_DIR_ABS''')
content = content.replace('{{DISPATCHER_DIR}}', '''$DISPATCHER_DIR_ABS''')
content = content.replace('{{PROJECT_NAME}}', '''$PROJECT_NAME_SAFE''')

# 追加到 CLAUDE.md
with open('CLAUDE.md', 'a') as f:
    f.write('\n')
    f.write(content)

print('  ✓ 注入 Dispatcher 健康检查到 CLAUDE.md')
"
        else
            log_skip "Python3 不可用，跳过 Dispatcher 健康检查注入"
        fi
    fi

    # ---- Step 5: 创建 AGENTS.md 符号链接 ----
    log_header "Step 5: AGENTS.md 符号链接"

    if [ -f "AGENTS.md" ] || [ -L "AGENTS.md" ]; then
        log_skip "AGENTS.md"
    else
        ln -s CLAUDE.md AGENTS.md
        log_ok "创建 AGENTS.md → CLAUDE.md"
    fi

    # ---- Step 6: 创建 CLAUDE.local.md ----
    log_header "Step 6: CLAUDE.local.md（本地配置）"

    if [ -f "CLAUDE.local.md" ]; then
        log_skip "CLAUDE.local.md"
    else
        cat > CLAUDE.local.md << 'LOCALEOF'
# 本地个人配置（不提交 git）

## 个人偏好

- 默认 Coder Agent: Claude Code
- 默认 Reviewer Agent: Codex CLI

## Agent API 管理

所有 Agent 的 API 密钥通过 cc-switch 统一管理。

```bash
cc-switch claude    # 切换到 Claude Code API
cc-switch codex     # 切换到 Codex API
cc-switch gemini    # 切换到 Gemini API
```

## 备注

本文件仅供个人使用，不提交 git。
LOCALEOF
        log_ok "创建 CLAUDE.local.md"
    fi

    # ---- Step 7: 更新 .gitignore ----
    log_header "Step 7: .gitignore"

    safe_append "${TEMPLATE_DIR}/gitignore.append" ".gitignore" "CLAUDE.local.md"

    # ---- Step 8: 更新 Makefile ----
    log_header "Step 8: Makefile"

    safe_append "${TEMPLATE_DIR}/Makefile.append" "Makefile" "multi-agent-workflow" "quality-gate"

    # ---- Step 9: 创建 .vscode/mcp.json（VS Code MCP 配置）----
    log_header "Step 9: VS Code MCP 配置"

    # 在目标项目目录创建 .vscode/mcp.json
    mkdir -p .vscode
    safe_copy "${TEMPLATE_DIR}/vscode-mcp.json" ".vscode/mcp.json"

    # 同时在工作区根目录创建（多项目工作区场景）
    # VS Code 只读取打开的工作区根目录下的 .vscode/mcp.json
    if [ "$PROJECT_DIR" != "$WORKSPACE_DIR" ]; then
        mkdir -p "${WORKSPACE_DIR}/.vscode"
        if [ -f "${WORKSPACE_DIR}/.vscode/mcp.json" ]; then
            log_skip "${WORKSPACE_DIR}/.vscode/mcp.json（工作区根目录已存在）"
        else
            cp "${TEMPLATE_DIR}/vscode-mcp.json" "${WORKSPACE_DIR}/.vscode/mcp.json"
            log_ok "创建 ${WORKSPACE_DIR}/.vscode/mcp.json（工作区根目录）"
        fi
    fi

    # ---- 完成 ----
    log_header "✅ 初始化完成"
    echo ""
    echo -e "  已初始化多 Agent 工作流到: ${GREEN}${PROJECT_DIR}${NC}"
    echo ""
    echo -e "  ${BOLD}创建的文件:${NC}"
    echo "    CLAUDE.md              — 项目规范（请检查并完善 TODO 标注处）"
    echo "    AGENTS.md              — 符号链接 → CLAUDE.md"
    echo "    CLAUDE.local.md        — 个人本地配置（不提交 git）"
    echo "    .vk/workflow.md        — 通用工作流规范"
    echo "    .vk/prompts/           — Agent 提示词模板"
    echo "    .vk/dispatcher.json    — 中央调度器配置（填写 project/repo ID）"
    echo "    .vscode/mcp.json       — VS Code MCP Server 配置（VK 连接）"
    echo "    scripts/               — 自动化脚本（质量门禁、VK 钩子等）"
    echo "    dispatcher/            — 中央调度器模块（自动化编排引擎）"
    echo ""
    echo -e "  ${BOLD}下一步:${NC}"
    echo "    1. 检查 CLAUDE.md，完善所有 TODO 标注的项目定制内容"
    echo "    2. 按需修改 scripts/agent-quality-gate.sh 中的 lint/test 命令"
    echo "    3. 填写 .vk/dispatcher.json 中的 organization_id / project_id / repo_id"
    echo "    4. 填写 .vk/status_map.json（从 VK MCP list_issue_priorities 获取）"
    echo "    5. 在 VS Code 中 Reload Window 以激活 MCP Server"
    echo "    6. 一键启动 VK + Dispatcher: make dev-up"
    echo "       （含健康检查等待、异常 Toast 通知；日志在 .vk/logs/）"
    echo "    7. 停止: make dev-down | 查看日志: make dev-logs"
    echo "    8. 提交到 git: git add . && git commit -m '初始化多 Agent 工作流'"
    echo ""
}

main "$@"
