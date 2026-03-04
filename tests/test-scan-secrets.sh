#!/usr/bin/env bash
# ============================================================================
# Secret Scanning 单元测试
#
# 用途: 测试 scan-secrets.sh 的核心功能
# 运行: bash tests/test-scan-secrets.sh
#
# 测试用例:
#   1. 命中检测 — 应检测到模拟 token
#   2. 不命中检测 — 不应误报
#   3. 二进制文件跳过 — 不应误判 JSON 等文本文件
#   4. 脱敏模式 — 不应输出完整 token
#   5. 分支不存在处理 — --diff 指定不存在的分支应优雅降级
# ============================================================================

set -euo pipefail

# ---- 颜色 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ---- 测试框架 ----
TESTS_RUN=0
TESTS_PASS=0
TESTS_FAIL=0

# 项目根目录
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCAN_SCRIPT="${PROJECT_ROOT}/scripts/scan-secrets.sh"

# 临时测试目录
TEST_DIR=""

setup() {
    TEST_DIR=$(mktemp -d)
    cd "$TEST_DIR"
    git init -q
    git config user.email "test@test.com"
    git config user.name "Test"
}

teardown() {
    [[ -n "$TEST_DIR" && -d "$TEST_DIR" ]] && rm -rf "$TEST_DIR"
}

assert_eq() {
    local expected="$1"
    local actual="$2"
    local msg="$3"

    if [[ "$expected" == "$actual" ]]; then
        echo -e "  ${GREEN}✓${NC} $msg"
        TESTS_PASS=$((TESTS_PASS + 1))
    else
        echo -e "  ${RED}✗${NC} $msg"
        echo -e "    期望: $expected"
        echo -e "    实际: $actual"
        TESTS_FAIL=$((TESTS_FAIL + 1))
    fi
    TESTS_RUN=$((TESTS_RUN + 1))
}

assert_contains() {
    local haystack="$1"
    local needle="$2"
    local msg="$3"

    if [[ "$haystack" == *"$needle"* ]]; then
        echo -e "  ${GREEN}✓${NC} $msg"
        TESTS_PASS=$((TESTS_PASS + 1))
    else
        echo -e "  ${RED}✗${NC} $msg"
        echo -e "    未找到: $needle"
        TESTS_RUN=$((TESTS_RUN + 1))
        TESTS_FAIL=$((TESTS_FAIL + 1))
    fi
    TESTS_RUN=$((TESTS_RUN + 1))
}

assert_not_contains() {
    local haystack="$1"
    local needle="$2"
    local msg="$3"

    if [[ "$haystack" != *"$needle"* ]]; then
        echo -e "  ${GREEN}✓${NC} $msg"
        TESTS_PASS=$((TESTS_PASS + 1))
    else
        echo -e "  ${RED}✗${NC} $msg"
        echo -e "    意外找到: $needle"
        TESTS_FAIL=$((TESTS_FAIL + 1))
    fi
    TESTS_RUN=$((TESTS_RUN + 1))
}

# ============================================================================
# 测试用例
# ============================================================================

test_detect_github_token() {
    echo -e "\n${YELLOW}测试: 检测 GitHub classic token${NC}"
    setup

    # 创建含模拟 token 的文件（使用合法长度）
    echo 'token = "ghp_1234567890abcdefghijklmnopqrstuvwxyz1234"' > config.py
    git add config.py
    git commit -m "test" -q

    local output
    local exit_code=0
    output=$(bash "$SCAN_SCRIPT" --all 2>&1) || exit_code=$?

    assert_eq "1" "$exit_code" "应检测到 token，exit code = 1"
    assert_contains "$output" "GitHub classic token" "应报告 token 类型"
    assert_contains "$output" "config.py" "应报告文件名"

    teardown
}

test_no_false_positive() {
    echo -e "\n${YELLOW}测试: 不应误报普通文本${NC}"
    setup

    # 创建不含 token 的文件
    echo 'print("Hello, World!")' > hello.py
    git add hello.py
    git commit -m "test" -q

    local output
    local exit_code=0
    output=$(bash "$SCAN_SCRIPT" --all 2>&1) || exit_code=$?

    assert_eq "0" "$exit_code" "不应检测到 token，exit code = 0"
    # 检查输出中不应包含"发现 X 个文件包含敏感信息"（X > 0）
    assert_not_contains "$output" "发现 1 个文件包含敏感信息" "不应报告发现 1 个敏感文件"
    assert_not_contains "$output" "hello.py" "不应报告文件名（因为没有问题）"

    teardown
}

test_json_not_mistaken_as_binary() {
    echo -e "\n${YELLOW}测试: JSON 文件不应被误判为二进制${NC}"
    setup

    # 创建含 token 的 JSON 文件
    # 注意：file 命令会输出 "JSON text data"，旧的 file|grep binary|data 会误判
    cat > config.json << 'JSONEOF'
{
    "api_key": "ghp_1234567890abcdefghijklmnopqrstuvwxyz1234",
    "name": "test"
}
JSONEOF
    git add config.json
    git commit -m "test" -q

    local output
    local exit_code=0
    output=$(bash "$SCAN_SCRIPT" --all 2>&1) || exit_code=$?

    assert_eq "1" "$exit_code" "JSON 文件应被正确扫描，检测到 token"
    assert_contains "$output" "config.json" "应报告 JSON 文件名"
    assert_contains "$output" "GitHub classic token" "应报告 token 类型"

    teardown
}

test_redact_mode() {
    echo -e "\n${YELLOW}测试: 脱敏模式不应输出完整 token${NC}"
    setup

    # 创建含模拟 token 的文件
    local fake_token="ghp_1234567890abcdefghijklmnopqrstuvwxyz1234"
    echo "token = \"$fake_token\"" > secret.py
    git add secret.py
    git commit -m "test" -q

    local output
    local exit_code=0
    output=$(bash "$SCAN_SCRIPT" --all --redact 2>&1) || exit_code=$?

    assert_eq "1" "$exit_code" "脱敏模式也应检测到 token"
    assert_not_contains "$output" "$fake_token" "脱敏模式不应输出完整 token"
    assert_contains "$output" "[REDACTED]" "脱敏模式应显示 [REDACTED]"

    teardown
}

test_quiet_mode() {
    echo -e "\n${YELLOW}测试: 静默模式只输出 exit code${NC}"
    setup

    echo 'token = "ghp_1234567890abcdefghijklmnopqrstuvwxyz1234"' > config.py
    git add config.py
    git commit -m "test" -q

    local output
    local exit_code=0
    output=$(bash "$SCAN_SCRIPT" --all --quiet 2>&1) || exit_code=$?

    assert_eq "1" "$exit_code" "静默模式也应检测到 token"
    assert_eq "" "$output" "静默模式不应输出任何内容"

    teardown
}

test_diff_nonexistent_branch() {
    echo -e "\n${YELLOW}测试: --diff 指定不存在的分支应优雅降级${NC}"
    setup

    echo 'token = "ghp_1234567890abcdefghijklmnopqrstuvwxyz1234"' > config.py
    git add config.py
    git commit -m "test" -q

    local output
    local exit_code=0
    # 指定一个不存在的分支
    output=$(bash "$SCAN_SCRIPT" --diff nonexistent-branch-xyz 2>&1) || exit_code=$?

    # 应该优雅降级到扫描当前文件，而不是崩溃
    assert_eq "1" "$exit_code" "应检测到 token（优雅降级后正常扫描）"

    teardown
}

test_staged_mode() {
    echo -e "\n${YELLOW}测试: --staged 只扫描暂存文件${NC}"
    setup

    # 创建两个文件，一个暂存，一个不暂存
    echo 'token = "ghp_1234567890abcdefghijklmnopqrstuvwxyz1234"' > staged.py
    echo 'token = "ghp_9999999999999999999999999999999999999999"' > unstaged.py

    git add staged.py
    # unstaged.py 不 add

    local output
    local exit_code=0
    output=$(bash "$SCAN_SCRIPT" --staged 2>&1) || exit_code=$?

    assert_eq "1" "$exit_code" "应检测到 staged 文件中的 token"
    assert_contains "$output" "staged.py" "应报告 staged 文件"
    assert_not_contains "$output" "unstaged.py" "不应报告 unstaged 文件"

    teardown
}

test_worktree_hook_path() {
    echo -e "\n${YELLOW}测试: worktree 场景下 hook 路径解析${NC}"
    setup

    # 模拟 worktree 场景：创建 .git 文件（而非目录）
    # 这个测试验证 pre-commit hook 中的 git rev-parse --show-toplevel 逻辑

    # 创建一个临时 worktree
    local main_repo="$TEST_DIR"
    local worktree_dir
    worktree_dir=$(mktemp -d)

    # 在主仓库添加初始提交
    echo "test" > README.md
    git add README.md
    git commit -m "init" -q

    # 创建 worktree
    git worktree add "$worktree_dir" -b test-branch -q

    # 在 worktree 中测试
    cd "$worktree_dir"

    # 验证 git rev-parse --show-toplevel 能正确返回 worktree 目录
    local toplevel
    toplevel=$(git rev-parse --show-toplevel 2>/dev/null || echo "FAILED")

    # toplevel 应该指向 worktree 目录，而不是主仓库
    if [[ "$toplevel" == "$worktree_dir" ]]; then
        echo -e "  ${GREEN}✓${NC} git rev-parse --show-toplevel 在 worktree 中正常工作"
        TESTS_PASS=$((TESTS_PASS + 1))
    else
        echo -e "  ${RED}✗${NC} git rev-parse --show-toplevel 返回错误路径"
        echo "    期望: $worktree_dir"
        echo "    实际: $toplevel"
        TESTS_FAIL=$((TESTS_FAIL + 1))
    fi
    TESTS_RUN=$((TESTS_RUN + 1))

    # 清理 worktree
    cd "$main_repo"
    git worktree remove "$worktree_dir" --force 2>/dev/null || true
    rm -rf "$worktree_dir"

    teardown
}

# ============================================================================
# 主流程
# ============================================================================

main() {
    echo -e "${YELLOW}═══════════════════════════════════════════════════${NC}"
    echo -e "${YELLOW}  Secret Scanning 单元测试${NC}"
    echo -e "${YELLOW}═══════════════════════════════════════════════════${NC}"
    echo -e "  脚本: $SCAN_SCRIPT"
    echo ""

    # 检查被测脚本存在
    if [[ ! -f "$SCAN_SCRIPT" ]]; then
        echo -e "${RED}错误: scan-secrets.sh 不存在于 $SCAN_SCRIPT${NC}"
        exit 1
    fi

    # 运行测试
    test_detect_github_token
    test_no_false_positive
    test_json_not_mistaken_as_binary
    test_redact_mode
    test_quiet_mode
    test_diff_nonexistent_branch
    test_staged_mode
    test_worktree_hook_path

    # 汇总
    echo -e "\n${YELLOW}═══════════════════════════════════════════════════${NC}"
    echo -e "${YELLOW}  测试汇总${NC}"
    echo -e "${YELLOW}═══════════════════════════════════════════════════${NC}"
    echo -e "  运行: ${TESTS_RUN}"
    echo -e "  ${GREEN}通过: ${TESTS_PASS}${NC}"
    echo -e "  ${RED}失败: ${TESTS_FAIL}${NC}"

    if [[ $TESTS_FAIL -gt 0 ]]; then
        echo -e "\n${RED}存在失败的测试${NC}"
        exit 1
    else
        echo -e "\n${GREEN}所有测试通过 ✓${NC}"
        exit 0
    fi
}

main "$@"
