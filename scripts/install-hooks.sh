#!/usr/bin/env bash
# ============================================================================
# Git Hooks 安装脚本
#
# 用途: 安装 pre-commit hook，在每次提交前自动执行 secret 扫描
# 场景: 项目初始化后运行一次，或 CI 环境中自动安装
#
# 安装的 hooks:
#   - pre-commit: 执行 secret 扫描，阻止含敏感信息的提交
#
# 用法:
#   ./scripts/install-hooks.sh           # 安装 hooks
#   ./scripts/install-hooks.sh --uninstall  # 卸载 hooks
#   ./scripts/install-hooks.sh --check   # 检查 hooks 状态
# ============================================================================

set -euo pipefail

# ---- 颜色 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# ---- 项目根目录 ----
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 获取实际的 Git 目录（支持 worktree）
resolve_git_dir() {
    if [[ -f "${PROJECT_ROOT}/.git" ]]; then
        # Git worktree: .git 是一个文件，内容类似 "gitdir: /path/to/.git/worktrees/xxx"
        local gitdir_line
        gitdir_line=$(head -1 "${PROJECT_ROOT}/.git" 2>/dev/null || true)
        if [[ "$gitdir_line" =~ ^gitdir:\ (.+)$ ]]; then
            echo "${BASH_REMATCH[1]}"
            return
        fi
    fi
    # 回退到 git rev-parse 或默认路径
    git rev-parse --git-dir 2>/dev/null || echo "${PROJECT_ROOT}/.git"
}

GIT_DIR=$(resolve_git_dir)
HOOKS_DIR="${GIT_DIR}/hooks"

# ============================================================================
# 帮助信息
# ============================================================================
show_help() {
    cat << 'EOF'
Git Hooks 安装脚本

用法:
  install-hooks.sh [选项]

选项:
  --install      安装 hooks（默认行为）
  --uninstall    卸载 hooks，恢复原始状态
  --check        检查 hooks 状态
  -h, --help     显示帮助信息

安装的 hooks:
  pre-commit — 执行 secret 扫描，阻止含敏感信息的提交
EOF
}

# ============================================================================
# 检查是否在 Git 仓库中
# ============================================================================
check_git_repo() {
    if [[ ! -d "$GIT_DIR" ]]; then
        echo -e "${RED}错误: 当前目录不是 Git 仓库${NC}" >&2
        exit 1
    fi

    # 确保 hooks 目录存在
    mkdir -p "$HOOKS_DIR"
}

# ============================================================================
# 安装 pre-commit hook
# ============================================================================
install_hooks() {
    local pre_commit="${HOOKS_DIR}/pre-commit"
    local pre_commit_backup="${HOOKS_DIR}/pre-commit.bak"

    # 备份现有的 pre-commit
    if [[ -f "$pre_commit" ]] && [[ ! -L "$pre_commit" ]]; then
        cp "$pre_commit" "$pre_commit_backup"
        echo -e "${YELLOW}已备份现有的 pre-commit → pre-commit.bak${NC}"
    fi

    # 创建 pre-commit hook
    cat > "$pre_commit" << 'HOOK_EOF'
#!/usr/bin/env bash
# ============================================================================
# Pre-commit Hook — Secret Scanning
# 自动生成，请勿手动编辑
# ============================================================================

# 获取脚本所在目录（支持符号链接）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# 执行 secret 扫描（只扫描 staged 文件）
if ! bash "${SCRIPT_DIR}/scripts/scan-secrets.sh" --staged --quiet; then
    echo ""
    echo "❌ 提交被阻止：发现敏感信息"
    echo "   请移除敏感信息后重新提交"
    echo "   如果需要跳过检查（不推荐），使用: git commit --no-verify"
    exit 1
fi

exit 0
HOOK_EOF

    chmod +x "$pre_commit"

    echo -e "${GREEN}✓ 已安装 pre-commit hook${NC}"
    echo -e "  位置: ${pre_commit}"
    echo -e "  功能: 提交前自动扫描 staged 文件中的敏感信息"
}

# ============================================================================
# 卸载 hooks
# ============================================================================
uninstall_hooks() {
    local pre_commit="${HOOKS_DIR}/pre-commit"
    local pre_commit_backup="${HOOKS_DIR}/pre-commit.bak"

    if [[ -f "$pre_commit" ]]; then
        # 检查是否是我们安装的
        if head -5 "$pre_commit" | grep -q "Secret Scanning"; then
            rm "$pre_commit"
            echo -e "${GREEN}✓ 已卸载 pre-commit hook${NC}"

            # 恢复备份
            if [[ -f "$pre_commit_backup" ]]; then
                mv "$pre_commit_backup" "$pre_commit"
                echo -e "${GREEN}✓ 已恢复原始 pre-commit hook${NC}"
            fi
        else
            echo -e "${YELLOW}pre-commit hook 不是由本脚本安装，跳过卸载${NC}"
        fi
    else
        echo -e "${YELLOW}pre-commit hook 不存在，无需卸载${NC}"
    fi
}

# ============================================================================
# 检查 hooks 状态
# ============================================================================
check_hooks() {
    local pre_commit="${HOOKS_DIR}/pre-commit"

    echo -e "${CYAN}Git Hooks 状态检查${NC}"
    echo ""

    if [[ -f "$pre_commit" ]]; then
        if head -5 "$pre_commit" | grep -q "Secret Scanning"; then
            echo -e "  pre-commit: ${GREEN}✓ 已安装（Secret Scanning）${NC}"
        else
            echo -e "  pre-commit: ${YELLOW}存在（非本脚本安装）${NC}"
        fi
    else
        echo -e "  pre-commit: ${RED}✗ 未安装${NC}"
    fi
}

# ============================================================================
# 参数解析
# ============================================================================
ACTION="install"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install)
            ACTION="install"
            shift
            ;;
        --uninstall)
            ACTION="uninstall"
            shift
            ;;
        --check)
            ACTION="check"
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo -e "${RED}未知选项: $1${NC}" >&2
            show_help
            exit 2
            ;;
    esac
done

# ============================================================================
# 主流程
# ============================================================================
check_git_repo

case "$ACTION" in
    install)
        install_hooks
        ;;
    uninstall)
        uninstall_hooks
        ;;
    check)
        check_hooks
        ;;
esac
