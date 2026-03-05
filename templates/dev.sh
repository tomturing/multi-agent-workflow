#!/usr/bin/env bash
# .vk/dev.sh — 一键启动 VK + Dispatcher，守护异常并发送通知
#
# 用法:
#   bash .vk/dev.sh         （直接执行）
#   make dev-up              （通过 Makefile 别名）
#
# 注入来源: multi-agent-workflow init.sh
# 本文件位于 .vk/ 隐藏目录，不影响目标项目的业务代码。

set -euo pipefail

VK_PORT="${VK_PORT:-9527}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

LOG_DIR="${PROJECT_DIR}/.vk/logs"
VK_LOG="${LOG_DIR}/vk.log"
DISPATCHER_LOG="${LOG_DIR}/dispatcher.log"

HEALTH_URL="http://127.0.0.1:${VK_PORT}/api/health"
HEALTH_TIMEOUT=30  # 等待 VK 就绪的最大秒数

mkdir -p "${LOG_DIR}"

# ── 平台检测 ──────────────────────────────────────────────────────────────────
is_wsl2() {
    grep -qi "microsoft" /proc/version 2>/dev/null
}

# ── Toast 通知（跨平台，与 VK 通知.rs 保持一致）──────────────────────────────
notify() {
    local title="$1" msg="$2"
    if is_wsl2 || [[ "${OS:-}" == "Windows_NT" ]]; then
        # WSL2 / Windows：通过 PowerShell Toast
        powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "
\$ErrorActionPreference='SilentlyContinue'
[void][Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]
\$t = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
\$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(\$t)
\$x.GetElementsByTagName('text')[0].AppendChild(\$x.CreateTextNode('${title}')) | Out-Null
\$x.GetElementsByTagName('text')[1].AppendChild(\$x.CreateTextNode('${msg}')) | Out-Null
\$toast = [Windows.UI.Notifications.ToastNotification]::new(\$x)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Vibe Kanban').Show(\$toast)
" 2>/dev/null || true
    elif command -v notify-send >/dev/null 2>&1; then
        # Linux 桌面
        notify-send -t 10000 "${title}" "${msg}" 2>/dev/null || true
    elif command -v osascript >/dev/null 2>&1; then
        # macOS
        osascript -e "display notification \"${msg}\" with title \"${title}\"" 2>/dev/null || true
    fi
    # 无论平台，同时打印到终端
    echo "🔔  ${title}: ${msg}"
}

# ── Ctrl+C / TERM 信号：干净退出两个进程 ────────────────────────────────────
VK_PID=""
DISPATCHER_PID=""
STOPPING=false

cleanup() {
    STOPPING=true
    echo ""
    echo "⏹  正在停止..."
    [[ -n "${DISPATCHER_PID}" ]] && kill "${DISPATCHER_PID}" 2>/dev/null || true
    [[ -n "${VK_PID}" ]] && kill "${VK_PID}" 2>/dev/null || true
    # 等待两个子进程真正退出
    wait "${DISPATCHER_PID}" 2>/dev/null || true
    wait "${VK_PID}" 2>/dev/null || true
    echo "✓  VK + Dispatcher 已停止"
    exit 0
}
trap cleanup INT TERM

# ── 1. 检测端口占用（幂等保护）────────────────────────────────────────────────
if curl -sf "${HEALTH_URL}" > /dev/null 2>&1; then
    echo "ℹ️  VK 已在端口 ${VK_PORT} 运行，跳过启动"
    VK_PID=""  # 不由本脚本管理
    VK_ALREADY_RUNNING=true
else
    VK_ALREADY_RUNNING=false
    echo "▶  启动 VK（端口 ${VK_PORT}）..."
    PORT="${VK_PORT}" npx vibe-kanban >> "${VK_LOG}" 2>&1 &
    VK_PID=$!

    # 健康检查等待（真正就绪，非 sleep hack）
    echo "⏳  等待 VK 就绪（最多 ${HEALTH_TIMEOUT}s）..."
    WAITED=0
    until curl -sf "${HEALTH_URL}" > /dev/null 2>&1; do
        sleep 1
        WAITED=$((WAITED + 1))
        if [[ ${WAITED} -ge ${HEALTH_TIMEOUT} ]]; then
            echo "❌  VK 启动超时（${HEALTH_TIMEOUT}s）"
            notify "⚠️ MAW: VK 启动失败" "超时 ${HEALTH_TIMEOUT}s，请查看 .vk/logs/vk.log"
            kill "${VK_PID}" 2>/dev/null || true
            exit 1
        fi
    done
    echo "✓  VK 已就绪（等待 ${WAITED}s）"
fi

# ── 2. 启动 Dispatcher ────────────────────────────────────────────────────────
echo "▶  启动 Dispatcher..."
VK_PORT="${VK_PORT}" python3 -m dispatcher --project-dir "${PROJECT_DIR}" \
    >> "${DISPATCHER_LOG}" 2>&1 &
DISPATCHER_PID=$!

echo ""
echo "✅  全部就绪"
echo "   VK 日志:         tail -f ${VK_LOG}"
echo "   Dispatcher 日志: tail -f ${DISPATCHER_LOG}"
echo "   按 Ctrl+C 或 make dev-down 停止"
echo ""

# ── 3. 守护：等待 Dispatcher 退出，区分崩溃 vs 用户主动停止 ──────────────────
wait "${DISPATCHER_PID}" 2>/dev/null || true
DISPATCHER_EXIT=$?

# 若是用户 Ctrl+C 触发的 cleanup，STOPPING=true，不报异常
if [[ "${STOPPING}" == false ]]; then
    if [[ ${DISPATCHER_EXIT} -ne 0 ]]; then
        MSG="Dispatcher 异常退出 (exit=${DISPATCHER_EXIT})，请查看 .vk/logs/dispatcher.log"
        echo "❌  ${MSG}"
        notify "⚠️ MAW: Dispatcher 崩溃" "${MSG}"
    else
        # 正常退出（如 --once 模式）
        echo "ℹ️  Dispatcher 已正常退出 (exit=0)"
    fi
    # Dispatcher 已不在，顺带停止 VK（保持两者生命周期一致）
    [[ -n "${VK_PID}" ]] && [[ "${VK_ALREADY_RUNNING}" == false ]] && kill "${VK_PID}" 2>/dev/null || true
fi
