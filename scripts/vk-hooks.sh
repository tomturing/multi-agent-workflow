#!/usr/bin/env bash
# ============================================================================
# VK 工作流自动化钩子
# 用途: 在质量门禁通过/失败后自动更新 VK Issue 状态
#
# 设计:
#   - 由 agent-quality-gate.sh 在退出前调用
#   - 通过 VK REST API (PATCH /api/remote/issues/{id}) 更新状态
#   - REST API 要求 status_id（非状态名称），通过 .vk/status_map.json 解析
#   - Issue ID 来源优先级:
#     1. .vk/issue_id 文件（编排者在 start_workspace_session 时写入）
#     2. 环境变量 VK_ISSUE_ID
#   - VK 地址来源: 环境变量 VK_API_URL 或默认 http://127.0.0.1:9527
#   - 阶段检测:
#     1. .vk/phase 文件（dispatcher 写入 "coding" 或 "review"）
#     2. 分支名约定: 含 "review" 则为审查阶段，否则为编码阶段
#
# 状态转换:
#   编码阶段（coding）:
#     成功 → "In review"   — dispatcher 检测到后自动创建审查 Session
#     失败 → 保持 "In progress"
#   审查阶段（review）:
#     成功 → "Done"        — dispatcher 检测到后自动合并到主分支
#     失败 → 保持 "In review"
#
# 前置条件:
#   - .vk/status_map.json — 状态名→status_id 映射（编排者初始化项目时创建）
#   - .vk/issue_id — 当前 workspace 关联的 Issue UUID
#
# 用法:
#   source scripts/vk-hooks.sh
#   vk_on_cleanup_success    # 质量门禁通过后调用
#   vk_on_cleanup_failure    # 质量门禁失败后调用
# ============================================================================

# VK API 基础地址
VK_API_URL="${VK_API_URL:-http://127.0.0.1:${PORT:-9527}}"

# 项目根目录（相对于本脚本位置）
_VK_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---- 内部函数 ----

# 获取当前 Workspace 关联的 Issue ID
_vk_get_issue_id() {
    # 优先级 1: .vk/issue_id 文件
    local issue_file="${_VK_PROJECT_ROOT}/.vk/issue_id"

    if [ -f "$issue_file" ]; then
        cat "$issue_file" | tr -d '[:space:]'
        return 0
    fi

    # 优先级 2: 环境变量
    if [ -n "${VK_ISSUE_ID:-}" ]; then
        echo "$VK_ISSUE_ID"
        return 0
    fi

    return 1
}

# 检测当前阶段: coding（编码）或 review（审查）
_vk_detect_phase() {
    # 优先级 1: 显式 .vk/phase 文件（dispatcher 创建 Session 时写入）
    local phase_file="${_VK_PROJECT_ROOT}/.vk/phase"
    if [ -f "$phase_file" ]; then
        cat "$phase_file" | tr -d '[:space:]'
        return 0
    fi

    # 优先级 2: 分支名约定（VK 审查分支包含 "review"）
    local branch
    branch=$(git -C "${_VK_PROJECT_ROOT}" rev-parse --abbrev-ref HEAD 2>/dev/null)
    if [[ "$branch" == *review* ]]; then
        echo "review"
    else
        echo "coding"
    fi
}

# 从 .vk/status_map.json 解析状态名到 status_id
# REST API PATCH /api/remote/issues/{id} 只接受 status_id，不接受状态名称
_vk_resolve_status_id() {
    local status_name="$1"
    local map_file="${_VK_PROJECT_ROOT}/.vk/status_map.json"

    if [ ! -f "$map_file" ]; then
        echo ""
        return 1
    fi

    # 用 python3 解析 JSON（避免依赖 jq）
    local status_id
    status_id=$(python3 -c "
import json, sys
with open('${map_file}') as f:
    m = json.load(f)
print(m.get('${status_name}', ''))
" 2>/dev/null)

    if [ -n "$status_id" ]; then
        echo "$status_id"
        return 0
    fi

    return 1
}

# 调用 VK REST API 更新 Issue 状态
# 注意: REST API 需要 status_id，通过 _vk_resolve_status_id 从 status_map.json 解析
_vk_update_issue_status() {
    local issue_id="$1"
    local new_status="$2"

    # 解析 status_id
    local status_id
    if ! status_id=$(_vk_resolve_status_id "$new_status"); then
        echo -e "  \033[1;33m⚠\033[0m 无法解析状态 '${new_status}' 的 status_id"
        echo -e "    请确认 .vk/status_map.json 存在且包含该状态"
        return 1
    fi

    local response
    response=$(curl -s -w "\n%{http_code}" -X PATCH \
        "${VK_API_URL}/api/remote/issues/${issue_id}" \
        -H "Content-Type: application/json" \
        -d "{\"status_id\": \"${status_id}\"}" 2>/dev/null)

    local http_code
    http_code=$(echo "$response" | tail -1)
    local body
    body=$(echo "$response" | sed '$d')

    if [ "$http_code" = "200" ]; then
        echo -e "  \033[0;32m✓\033[0m VK Issue 状态已更新: → ${new_status}"
        return 0
    else
        echo -e "  \033[1;33m⚠\033[0m VK Issue 状态更新失败 (HTTP ${http_code}), 需手动更新"
        return 1
    fi
}

# ---- 公开钩子函数 ----

# 质量门禁通过后调用
# 根据阶段自动设置目标状态:
#   coding → push 分支到远端 → "In review"（dispatcher 会自动创建 PR + 审查 Session）
#   review → "Done"（dispatcher 会自动通过 GitHub API 合并 PR）
vk_on_cleanup_success() {
    local issue_id
    if ! issue_id=$(_vk_get_issue_id); then
        echo -e "  \033[1;33m⚠\033[0m 未找到 VK Issue ID (.vk/issue_id 或 VK_ISSUE_ID)，跳过状态流转"
        return 0  # 非致命错误，不影响 cleanup 退出码
    fi

    local phase
    phase=$(_vk_detect_phase)

    echo -e "\n  \033[0;34m▸ VK 工作流钩子: cleanup 成功 (阶段: ${phase})\033[0m"

    case "$phase" in
        review)
            _vk_update_issue_status "$issue_id" "Done" || true
            ;;
        coding|*)
            # 编码完成: 先推送分支到 GitHub，再更新状态
            # Dispatcher 检测到 In review 后会自动创建 PR + 启动审查 Session
            _vk_push_branch || true

            # 写入 QG 通过标记（供 Dispatcher 兜底机制识别）
            local sha
            sha=$(git -C "${_VK_PROJECT_ROOT}" rev-parse HEAD 2>/dev/null)
            if [ -n "$sha" ]; then
                mkdir -p "${_VK_PROJECT_ROOT}/.vk/qg_passed"
                touch "${_VK_PROJECT_ROOT}/.vk/qg_passed/${sha}"
                echo -e "  \033[0;32m✓\033[0m QG 标记已写入: ${sha:0:8}"
            fi

            # 统一交由 Dispatcher 通过直读 SQLite 状态来完成流转，避免数据撕裂
            if [ "${VK_SKIP_STATUS_UPDATE:-1}" = "1" ]; then
                echo -e "  ℹ 默认跳过 REST 状态更新，由 Dispatcher 在 SQLite 检测通过后统一推进流程。"
            else
                _vk_update_issue_status "$issue_id" "In review" || true
            fi
            ;;
    esac
}

# 质量门禁失败后调用
# 保持当前状态（不回退），等待人工或 Agent 修复后重跑
vk_on_cleanup_failure() {
    local issue_id
    if ! issue_id=$(_vk_get_issue_id); then
        return 0
    fi

    local phase
    phase=$(_vk_detect_phase)

    echo -e "\n  \033[0;34m▸ VK 工作流钩子: cleanup 失败 (阶段: ${phase})\033[0m"
    # 失败时保持当前状态，不做额外操作
    echo -e "  Issue 保持当前状态"
}

# 推送当前分支到 GitHub remote
# 编码完成后推送，为后续 Dispatcher 创建 PR 做准备
_vk_push_branch() {
    local branch
    branch=$(git -C "${_VK_PROJECT_ROOT}" rev-parse --abbrev-ref HEAD 2>/dev/null)
    if [ -z "$branch" ]; then
        echo -e "  \033[1;33m⚠\033[0m 无法获取当前分支，跳过 push"
        return 1
    fi

    # 检查是否有 remote
    if ! git -C "${_VK_PROJECT_ROOT}" remote get-url origin &>/dev/null; then
        echo -e "  \033[1;33m⚠\033[0m 无 origin remote，跳过 push（本地模式）"
        return 0
    fi

    echo -e "  \033[0;34m▸ 推送分支 ${branch} → origin\033[0m"
    if git -C "${_VK_PROJECT_ROOT}" push origin "${branch}" 2>/dev/null; then
        echo -e "  \033[0;32m✓\033[0m 分支已推送"
        return 0
    else
        echo -e "  \033[1;33m⚠\033[0m push 失败（可能无网络或认证问题），Dispatcher 会重试"
        return 1
    fi
}
