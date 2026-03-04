#!/usr/bin/env bash
# ============================================================================
# Secret Scanning 脚本
#
# 用途: 扫描代码库中的敏感信息（API keys、tokens、secrets）
# 场景:
#   1. pre-commit hook — 阻止含 secret 的提交
#   2. 质量门禁 — CI/CD 阶段二次检查
#
# 扫描模式:
#   - GitHub classic token (ghp_)
#   - GitHub fine-grained token (github_pat_)
#   - OpenAI API key (sk-)
#   - Anthropic API key (sk-ant)
#   - Google API key (AIza)
#   - 通用 Bearer token
#
# 退出码:
#   0 — 未发现 secret
#   1 — 发现 secret
#   2 — 参数错误
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
cd "$PROJECT_ROOT"

# ============================================================================
# Secret 模式定义
# 格式: "模式名称|正则表达式"
# 注意: 使用扩展正则 (grep -E)
# ============================================================================
SECRET_PATTERNS=(
    "GitHub classic token|ghp_[A-Za-z0-9]{36}"
    "GitHub fine-grained token|github_pat_[A-Za-z0-9_]{82}"
    "OpenAI API key|sk-[A-Za-z0-9]{48}"
    "Anthropic API key|sk-ant-[A-Za-z0-9_-]{95}"
    "Google API key|AIza[A-Za-z0-9-_]{35}"
    "Generic Bearer token|Bearer [A-Za-z0-9._-]{20,}"
)

# 排除的文件/目录（相对于项目根）
EXCLUDE_PATTERNS=(
    ".git/"
    "node_modules/"
    ".venv/"
    "__pycache__/"
    "*.pyc"
    "*.min.js"
    "*.min.css"
    "package-lock.json"
    "pnpm-lock.yaml"
    "yarn.lock"
    "*.sum"  # go.sum, sha256.sum 等
)

# ============================================================================
# 帮助信息
# ============================================================================
show_help() {
    cat << 'EOF'
Secret Scanning — 扫描代码库中的敏感信息

用法:
  scan-secrets.sh [选项] [文件/目录...]

选项:
  -s, --staged      只扫描 git staged 文件（用于 pre-commit）
  -a, --all         扫描所有 tracked 文件（默认）
  -d, --diff BASE   扫描相对于 BASE 分支的变更文件
  -q, --quiet       静默模式，只输出结果
  -h, --help        显示帮助信息

示例:
  # 扫描所有 tracked 文件
  ./scripts/scan-secrets.sh

  # pre-commit 场景
  ./scripts/scan-secrets.sh --staged

  # 扫描相对于 main 分支的变更
  ./scripts/scan-secrets.sh --diff main

退出码:
  0 — 未发现 secret
  1 — 发现 secret
  2 — 参数错误
EOF
}

# ============================================================================
# 参数解析
# ============================================================================
MODE="all"
QUIET=false
TARGETS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--staged)
            MODE="staged"
            shift
            ;;
        -a|--all)
            MODE="all"
            shift
            ;;
        -d|--diff)
            MODE="diff"
            if [[ -z "${2:-}" ]]; then
                echo -e "${RED}错误: --diff 需要指定 BASE 分支${NC}" >&2
                exit 2
            fi
            DIFF_BASE="$2"
            shift 2
            ;;
        -q|--quiet)
            QUIET=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        -*)
            echo -e "${RED}未知选项: $1${NC}" >&2
            show_help
            exit 2
            ;;
        *)
            TARGETS+=("$1")
            shift
            ;;
    esac
done

# ============================================================================
# 获取待扫描的文件列表
# ============================================================================
get_scan_files() {
    local files=()

    case "$MODE" in
        staged)
            # 只扫描 staged 文件
            while IFS= read -r f; do
                [[ -n "$f" ]] && files+=("$f")
            done < <(git diff --cached --name-only --diff-filter=ACMR 2>/dev/null || true)
            ;;
        diff)
            # 扫描相对于 BASE 的变更
            local base="${DIFF_BASE:-main}"
            # 确保 base 分支存在
            if ! git rev-parse --verify "$base" &>/dev/null; then
                base="master"
            fi
            while IFS= read -r f; do
                [[ -n "$f" ]] && files+=("$f")
            done < <(git diff --name-only "$base"...HEAD 2>/dev/null || true)
            ;;
        all)
            if [[ ${#TARGETS[@]} -gt 0 ]]; then
                # 使用指定的目标
                files=("${TARGETS[@]}")
            else
                # 扫描所有 tracked 文件
                while IFS= read -r f; do
                    [[ -n "$f" ]] && files+=("$f")
                done < <(git ls-files 2>/dev/null || find . -type f -not -path "./.git/*" | sed 's|^\./||')
            fi
            ;;
    esac

    # 过滤排除模式
    local filtered=()
    for f in "${files[@]}"; do
        local skip=false
        for excl in "${EXCLUDE_PATTERNS[@]}"; do
            # 将通配符转换为正则
            local pattern="${excl//\*/.*}"
            pattern="${pattern//\?/.}"
            if [[ "$f" =~ $pattern ]]; then
                skip=true
                break
            fi
        done
        $skip || filtered+=("$f")
    done

    printf '%s\n' "${filtered[@]}"
}

# ============================================================================
# 扫描单个文件
# ============================================================================
scan_file() {
    local file="$1"
    local found_secrets=()

    # 检查文件是否存在且可读
    [[ -f "$file" && -r "$file" ]] || return 0

    # 跳过二进制文件
    if file "$file" 2>/dev/null | grep -qE 'binary|data'; then
        return 0
    fi

    for pattern_def in "${SECRET_PATTERNS[@]}"; do
        local name="${pattern_def%%|*}"
        local pattern="${pattern_def#*|}"

        # 使用 grep 扫描，输出行号
        local matches
        matches=$(grep -nE "$pattern" "$file" 2>/dev/null || true)

        if [[ -n "$matches" ]]; then
            while IFS= read -r match; do
                local line_num="${match%%:*}"
                local line_content="${match#*:}"
                # 截断过长的行
                [[ ${#line_content} -gt 100 ]] && line_content="${line_content:0:100}..."
                found_secrets+=("  ${CYAN}L${line_num}${NC}: ${line_content} ${YELLOW}[${name}]${NC}")
            done <<< "$matches"
        fi
    done

    if [[ ${#found_secrets[@]} -gt 0 ]]; then
        $QUIET || echo -e "${RED}✗ ${file}${NC}"
        for s in "${found_secrets[@]}"; do
            $QUIET || echo -e "$s"
        done
        return 1
    fi

    return 0
}

# ============================================================================
# 主流程
# ============================================================================
main() {
    local files
    files=$(get_scan_files)

    if [[ -z "$files" ]]; then
        $QUIET || echo -e "${GREEN}✓ 无待扫描文件${NC}"
        exit 0
    fi

    local file_count
    file_count=$(echo "$files" | wc -l)

    $QUIET || {
        echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
        echo -e "${CYAN}  Secret Scanning — $(date '+%Y-%m-%d %H:%M:%S')${NC}"
        echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
        echo -e "  模式: ${MODE}"
        echo -e "  文件数: ${file_count}"
        echo ""
    }

    local has_secrets=false
    local scanned=0
    local found=0

    while IFS= read -r file; do
        [[ -z "$file" ]] && continue
        ((scanned++)) || true
        if ! scan_file "$file"; then
            has_secrets=true
            ((found++)) || true
        fi
    done <<< "$files"

    $QUIET || echo ""

    if $has_secrets; then
        $QUIET || {
            echo -e "${RED}═══════════════════════════════════════════════════${NC}"
            echo -e "${RED}  ✗ 发现 ${found} 个文件包含敏感信息！${NC}"
            echo -e "${RED}═══════════════════════════════════════════════════${NC}"
            echo ""
            echo -e "  ${YELLOW}建议:${NC}"
            echo -e "    1. 将敏感信息移至环境变量或密钥管理服务"
            echo -e "    2. 如果已泄露，立即撤销并重新生成 token"
            echo -e "    3. 使用 git filter-branch 或 BFG 清理历史记录"
        }
        exit 1
    else
        $QUIET || {
            echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
            echo -e "${GREEN}  ✓ 未发现敏感信息 (扫描 ${scanned} 个文件)${NC}"
            echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
        }
        exit 0
    fi
}

main "$@"
