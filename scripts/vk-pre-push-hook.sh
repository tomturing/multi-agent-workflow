#!/usr/bin/env bash
# ============================================================================
# VK 质量门禁 pre-push hook
#
# 由 multi-agent-workflow Dispatcher 在启动时自动安装到：
#   <project>/.git/hooks/pre-push
#
# 设计原则:
#   - 仅对 vk/* 分支生效（编码 + 审查分支），不影响 main/master 等主干推送
#   - 若 QG 标记已存在（Dispatcher 兜底已跑过），跳过重复运行
#   - QG 通过后由 vk-hooks.sh 写入标记文件 + 更新 VK Issue 状态
#   - QG 失败则阻断 push（exit 1），Agent 必须修复后才能再次推送
#
# 调用方式:
#   git push 时由 git 自动调用，无需手动执行
# ============================================================================
set -eo pipefail

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"

# 仅对 vk/* 分支生效
[[ "$BRANCH" != vk/* ]] && exit 0

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
SHA="$(git rev-parse HEAD 2>/dev/null)"
MARKER_DIR="${PROJECT_ROOT}/.vk/qg_passed"
MARKER="${MARKER_DIR}/${SHA}"

# 若 Dispatcher 兜底已跑过并写入了标记，跳过重复运行
if [ -f "$MARKER" ]; then
    echo "  ✓ QG 标记已存在 (${SHA:0:8})，跳过重复运行"
    exit 0
fi

GATE="${PROJECT_ROOT}/scripts/agent-quality-gate.sh"
if [ ! -f "$GATE" ]; then
    echo "  ⚠ agent-quality-gate.sh 不存在，跳过质量门禁"
    exit 0
fi

echo ""
echo "  ╔══════════════════════════════════════════╗"
echo "  ║  VK pre-push: 运行质量门禁 ($BRANCH)"
echo "  ╚══════════════════════════════════════════╝"
echo ""

exec bash "$GATE"
