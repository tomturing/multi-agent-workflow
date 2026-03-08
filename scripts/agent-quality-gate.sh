#!/usr/bin/env bash
# ============================================================================
# Agent 质量门禁脚本 — 自动探测版
#
# 用途: VK Workspace Cleanup Script 自动触发，或手动运行 make quality-gate
# 功能: 自动探测项目结构，零配置适配任意项目
#
# 探测逻辑优先级:
#   Python: pyproject.toml > setup.py > *.py 文件存在
#   源码目录: src/ dispatcher/ backend/ app/ lib/ + 含 __init__.py 的子目录
#   测试目录: tests/ test/ + 任意子服务的 tests/
#   前端: frontend/*/package.json + node_modules/.pnpm/（非空安装标记）
# ============================================================================

set -euo pipefail

# ---- 颜色 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# ---- 项目根目录 ----
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# ---- 计数器 ----
PASS=0
FAIL=0
SKIP=0

# ---- 加载 VK 工作流钩子 ----
VK_HOOKS="${PROJECT_ROOT}/scripts/vk-hooks.sh"
if [ -f "$VK_HOOKS" ]; then
    source "$VK_HOOKS"
fi

# ---- 报告文件 ----
REPORT_DIR="${PROJECT_ROOT}/.vk/reports"
mkdir -p "$REPORT_DIR"
REPORT_FILE="${REPORT_DIR}/quality-gate-$(date +%Y%m%d-%H%M%S).txt"

# ============================================================================
# 工具函数
# ============================================================================

log_header() {
    echo -e "\n${BLUE}═══════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
}

log_info() { echo -e "  ${CYAN}ℹ${NC} $1"; }
log_pass() { echo -e "  ${GREEN}✓ PASS${NC}: $1"; PASS=$((PASS + 1)); }
log_fail() { echo -e "  ${RED}✗ FAIL${NC}: $1"; FAIL=$((FAIL + 1)); }
log_skip() { echo -e "  ${YELLOW}⊘ SKIP${NC}: $1"; SKIP=$((SKIP + 1)); }

# ============================================================================
# 自动探测项目结构
# ============================================================================

IS_PYTHON=false
IS_FRONTEND=false
PYTHON_RUNNER=""
PYTHON_SRC_DIRS=()
PYTHON_TEST_DIRS=()
FRONTEND_DIRS=()

detect_project_structure() {
    log_header "项目结构自动探测"

    # ---- Python 项目检测 ----
    if [ -f "pyproject.toml" ] || [ -f "setup.py" ] || [ -f "setup.cfg" ]; then
        IS_PYTHON=true
    elif find . -maxdepth 2 -name "*.py" \
             -not -path "./.git/*" -not -path "*/.venv/*" \
             -not -path "*/node_modules/*" 2>/dev/null | grep -q .; then
        IS_PYTHON=true
    fi

    if $IS_PYTHON; then
        # 确定运行器
        if command -v uv &>/dev/null && [ -f "pyproject.toml" ]; then
            PYTHON_RUNNER="uv run"
        elif command -v python3 &>/dev/null; then
            PYTHON_RUNNER="python3 -m"
        fi

        # 发现源码目录：先查已知惯用名
        for d in src dispatcher backend app lib core api server; do
            if [ -d "$d" ] && find "$d" -maxdepth 1 -name "*.py" 2>/dev/null | grep -q .; then
                PYTHON_SRC_DIRS+=("$d")
            fi
        done

        # 若未找到，扫描含 __init__.py 的顶层包
        if [ ${#PYTHON_SRC_DIRS[@]} -eq 0 ]; then
            while IFS= read -r pkg; do
                local d="${pkg%/__init__.py}"
                d="${d#./}"
                [[ "$d" == */* ]] && continue   # 只取顶层
                PYTHON_SRC_DIRS+=("$d")
            done < <(find . -maxdepth 2 -name "__init__.py" \
                         -not -path "./.git/*" -not -path "*/.venv/*" \
                         -not -path "*/node_modules/*" 2>/dev/null | head -20)
        fi

        readarray -t PYTHON_SRC_DIRS < <(printf '%s\n' "${PYTHON_SRC_DIRS[@]}" | sort -u)

        # 发现测试目录
        # 优先读取 pyproject.toml 中 [tool.quality-gate] test_dirs 配置
        local qg_test_dirs_override
        qg_test_dirs_override=$(python3 -c "
try:
    import tomllib
except ImportError:
    try: import tomli as tomllib
    except ImportError: tomllib = None
if tomllib:
    with open('pyproject.toml', 'rb') as f:
        d = tomllib.load(f)
    dirs = d.get('tool', {}).get('quality-gate', {}).get('test_dirs', [])
    if dirs: print('\\n'.join(dirs))
" 2>/dev/null || true)

        if [ -n "$qg_test_dirs_override" ]; then
            readarray -t PYTHON_TEST_DIRS <<< "$qg_test_dirs_override"
            log_info "测试目录 (来自 pyproject.toml [tool.quality-gate]): ${PYTHON_TEST_DIRS[*]}"
        else
            for d in tests test; do
                [ -d "$d" ] && PYTHON_TEST_DIRS+=("$d")
            done
            while IFS= read -r tdir; do
                local rel="${tdir#./}"
                [[ "$rel" == "tests" || "$rel" == "test" ]] && continue
                PYTHON_TEST_DIRS+=("$rel")
            done < <(find . -maxdepth 3 -type d -name "tests" \
                         -not -path "./.git/*" -not -path "*/.venv/*" \
                         2>/dev/null | grep -v '^\./tests$' | head -20)

            readarray -t PYTHON_TEST_DIRS < <(printf '%s\n' "${PYTHON_TEST_DIRS[@]}" | sort -u)
            log_info "测试目录: ${PYTHON_TEST_DIRS[*]:-（未发现）}"
        fi
    else
        log_info "Python 项目: 未检测到"
    fi

    # ---- 前端项目检测 ----
    for app_dir in frontend/customer frontend/admin; do
        if [ -f "${app_dir}/package.json" ] && [ -d "${app_dir}/node_modules/.pnpm" ]; then
            IS_FRONTEND=true
            FRONTEND_DIRS+=("$app_dir")
        fi
    done
    if [ -f "package.json" ] && [ -d "node_modules" ]; then
        IS_FRONTEND=true
        FRONTEND_DIRS+=(".")
    fi

    if $IS_FRONTEND; then
        log_info "前端: ✓  目录=${FRONTEND_DIRS[*]}"
    else
        log_info "前端项目: 未检测到（或依赖未安装）"
    fi
}

# ============================================================================
# 变更检测
# ============================================================================

HAS_PYTHON_CHANGES=false
HAS_FRONTEND_CHANGES=false

detect_changes() {
    local base_branch="${VK_BASE_BRANCH:-main}"
    git rev-parse --verify "$base_branch" &>/dev/null || base_branch="master"

    local changed
    # 在 Git worktree 里，三点 diff 最可靠；merge-base 检查只是可选的兜底
    # 不再依赖 merge-base --is-ancestor，因为在 worktree 下图谱未同步时会误判
    changed=$(git diff --name-only "${base_branch}...HEAD" 2>/dev/null || true)
    if [ -z "$changed" ]; then
        # 兜底：对比暂存区 + 未暂存变更（未 commit 的情形）
        changed=$(git diff --name-only 2>/dev/null || true)
        changed+=$'\n'
        changed+=$(git diff --name-only --cached 2>/dev/null || true)
    fi

    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        [[ "$f" == *.py ]] && HAS_PYTHON_CHANGES=true
        [[ "$f" == frontend/* || "$f" == *.ts || "$f" == *.vue || "$f" == *.tsx ]] \
            && HAS_FRONTEND_CHANGES=true
    done <<< "$changed"
    
    return 0
}
# ============================================================================
# Python 检查
# ============================================================================

_ruff_cmd() {
    # 优先级: uv run ruff > uvx ruff > 全局 ruff
    if [ -n "$PYTHON_RUNNER" ] && $PYTHON_RUNNER ruff --version &>/dev/null 2>&1; then
        echo "$PYTHON_RUNNER ruff"
    elif command -v uvx &>/dev/null && uvx ruff --version &>/dev/null 2>&1; then
        echo "uvx ruff"
    elif command -v ruff &>/dev/null; then
        echo "ruff"
    fi
}

check_python_lint() {
    log_header "Python 静态检查 (ruff check)"
    local cmd; cmd="$(_ruff_cmd)"
    if [ -z "$cmd" ]; then log_skip "ruff 未安装"; return; fi
    if [ ${#PYTHON_SRC_DIRS[@]} -eq 0 ]; then log_skip "未发现源码目录"; return; fi

    if $cmd check "${PYTHON_SRC_DIRS[@]}" 2>/dev/null; then
        log_pass "ruff check — 无 error  (${PYTHON_SRC_DIRS[*]})"
    else
        log_fail "ruff check — 有问题 (运行 '$cmd check --fix ${PYTHON_SRC_DIRS[*]}' 修复)"
    fi
}

check_python_format() {
    log_header "Python 格式检查 (ruff format)"
    local cmd; cmd="$(_ruff_cmd)"
    if [ -z "$cmd" ]; then log_skip "ruff 未安装"; return; fi
    if [ ${#PYTHON_SRC_DIRS[@]} -eq 0 ]; then log_skip "未发现源码目录"; return; fi

    if $cmd format --check "${PYTHON_SRC_DIRS[@]}" 2>/dev/null; then
        log_pass "ruff format — 格式一致"
    else
        log_fail "ruff format — 不一致 (运行 '$cmd format ${PYTHON_SRC_DIRS[*]}' 修复)"
    fi
}

check_python_test() {
    log_header "Python 单元测试 (pytest)"
    if [ ${#PYTHON_TEST_DIRS[@]} -eq 0 ]; then
        log_skip "未发现测试目录 (tests/ / test/)"; return
    fi

    local pytest_cmd
    if [ -n "$PYTHON_RUNNER" ] && $PYTHON_RUNNER pytest --version &>/dev/null 2>&1; then
        pytest_cmd="$PYTHON_RUNNER pytest"
    elif command -v pytest &>/dev/null; then
        pytest_cmd="pytest"
    else
        log_skip "pytest 未安装"; return
    fi

    # 超时配置：优先环境变量，其次 pyproject.toml [tool.quality-gate] test_timeout，默认 120s
    local per_test_timeout="${QG_TEST_TIMEOUT:-}"
    if [ -z "$per_test_timeout" ]; then
        per_test_timeout=$(python3 -c "
try:
    import tomllib
except ImportError:
    try: import tomli as tomllib
    except ImportError: tomllib = None
if tomllib:
    with open('pyproject.toml', 'rb') as f:
        d = tomllib.load(f)
    print(d.get('tool', {}).get('quality-gate', {}).get('test_timeout', 120))
else:
    print(120)
" 2>/dev/null || echo 120)
    fi
    log_info "pytest 单测超时限制: ${per_test_timeout}s/套件 (可通过 QG_TEST_TIMEOUT 或 pyproject.toml 覆盖)"

    local all_pass=true
    for tdir in "${PYTHON_TEST_DIRS[@]}"; do
        [[ -z "$tdir" ]] && continue   # 过滤空元素（readarray 可能产生）
        local ignore_flags=()
        [ -d "${tdir}/integration" ] && ignore_flags+=("--ignore=${tdir}/integration")
        # exit code 5 = no tests collected，不算失败
        local ec=0
        timeout "${per_test_timeout}" \
            $pytest_cmd "$tdir" "${ignore_flags[@]}" -q --tb=short 2>/dev/null || ec=$?
        if [ $ec -eq 0 ] || [ $ec -eq 5 ]; then
            log_pass "${tdir} — 通过"
        elif [ $ec -eq 124 ]; then
            log_fail "${tdir} — 超时（>${per_test_timeout}s），疑似 import 卡在 DB 连接。建议在 pyproject.toml [tool.quality-gate] 的 test_dirs 中只保留纯单元测试目录。"
            all_pass=false
        else
            log_fail "${tdir} — 部分失败 (exit=$ec)"
            all_pass=false
        fi
    done
    $all_pass && log_pass "Python 测试全部通过"
}

# ============================================================================
# 前端检查
# ============================================================================

check_frontend_lint() {
    log_header "前端 Lint 检查"
    if [ ${#FRONTEND_DIRS[@]} -eq 0 ]; then
        log_skip "未发现已安装依赖的前端目录"; return
    fi

    local lint_cmd="pnpm lint"
    command -v pnpm &>/dev/null || lint_cmd="npm run lint"

    local all_pass=true
    for app_dir in "${FRONTEND_DIRS[@]}"; do
        local pkg="${app_dir}/package.json"
        [ "$app_dir" = "." ] && pkg="package.json"
        if ! grep -q '"lint"' "$pkg" 2>/dev/null; then
            log_skip "${app_dir} — 未定义 lint 脚本"; continue
        fi
        if (cd "$app_dir" && $lint_cmd 2>/dev/null); then
            log_pass "${app_dir} lint — 通过"
        else
            log_fail "${app_dir} lint — 发现问题"
            all_pass=false
        fi
    done
    $all_pass && log_pass "前端 lint 全部通过"
}

# ============================================================================
# 主流程
# ============================================================================

main() {
    log_header "Agent 质量门禁 — $(date '+%Y-%m-%d %H:%M:%S')"
    echo -e "  项目: ${PROJECT_ROOT}"
    echo -e "  分支: $(git branch --show-current 2>/dev/null || echo 'unknown')"

    # ---- 逃生窗：如果提供项目自定义脚本，直接执行并退出 ----
    if [ -f "${PROJECT_ROOT}/.vk/quality-gate.conf" ]; then
        log_info "发现 .vk/quality-gate.conf，跳过默认执行，运行自定义门禁逻辑。"
        bash "${PROJECT_ROOT}/.vk/quality-gate.conf"
        local exit_code=$?
        if [ $exit_code -eq 0 ]; then
            log_pass "自定义质量门禁通过"
            type vk_on_cleanup_success &>/dev/null && vk_on_cleanup_success
            exit 0
        else
            log_fail "自定义质量门禁失败 (exit=$exit_code)"
            type vk_on_cleanup_failure &>/dev/null && vk_on_cleanup_failure
            exit $exit_code
        fi
    fi

    detect_project_structure
    detect_changes

    echo -e "\n  变更检测: Python=$([ "$HAS_PYTHON_CHANGES" = true ] && echo 是 || echo 否)  前端=$([ "$HAS_FRONTEND_CHANGES" = true ] && echo 是 || echo 否)"

    # 决定检查范围
    local run_python=$IS_PYTHON
    local run_frontend=$IS_FRONTEND

    if [ "$HAS_PYTHON_CHANGES" = false ] && [ "$HAS_FRONTEND_CHANGES" = false ]; then
        echo -e "\n  ${YELLOW}未检测到变更，基于探测结构运行全部适用检查${NC}"
    else
        $IS_PYTHON && ! $HAS_PYTHON_CHANGES && run_python=false
        $IS_FRONTEND && ! $HAS_FRONTEND_CHANGES && run_frontend=false
    fi

    $run_python && { check_python_lint; check_python_format; check_python_test; }
    $run_frontend && check_frontend_lint
    ! $run_python && ! $run_frontend && log_skip "未检测到需要检查的项目类型"

    # ---- 汇总 ----
    log_header "质量门禁汇总"
    echo -e "  ${GREEN}通过: ${PASS}${NC}  ${RED}失败: ${FAIL}${NC}  ${YELLOW}跳过: ${SKIP}${NC}"

    {
        echo "质量门禁报告 — $(date '+%Y-%m-%d %H:%M:%S')"
        echo "分支: $(git branch --show-current 2>/dev/null || echo 'unknown')"
        echo "Python 源码: ${PYTHON_SRC_DIRS[*]:-无}  测试: ${PYTHON_TEST_DIRS[*]:-无}"
        echo "前端: ${FRONTEND_DIRS[*]:-无}"
        echo "通过: ${PASS}, 失败: ${FAIL}, 跳过: ${SKIP}"
        echo "结果: $([ $FAIL -eq 0 ] && echo 'PASSED' || echo 'FAILED')"
    } > "$REPORT_FILE"

    echo -e "  报告: ${REPORT_FILE}"

    if [ $FAIL -gt 0 ]; then
        echo -e "\n  ${RED}质量门禁未通过！${NC}"
        type vk_on_cleanup_failure &>/dev/null && vk_on_cleanup_failure
        exit 1
    else
        echo -e "\n  ${GREEN}质量门禁全部通过 ✓${NC}"
        type vk_on_cleanup_success &>/dev/null && vk_on_cleanup_success
        exit 0
    fi
}

main "$@"
