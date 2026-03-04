
# ============================================================================
# 多 Agent 工作流命令（由 multi-agent-workflow init.sh 自动添加）
# ============================================================================

.PHONY: vk vk-stop vk-restart quality-gate conflict-check post-merge dispatcher dispatcher-status

# VK 固定端口（避免每次重启端口变化导致 MCP 需要 Reload Window）
VK_PORT ?= 9527

vk:
	@echo "启动 Vibe Kanban（端口 $(VK_PORT)）..."
	PORT=$(VK_PORT) npx vibe-kanban

vk-stop:
	@echo "停止 Vibe Kanban..."
	@pkill -f "vibe-kanban" 2>/dev/null && echo "✓ VK 已停止" || echo "VK 未在运行"

vk-restart:
	@echo "重启 Vibe Kanban..."
	@pkill -f "vibe-kanban" 2>/dev/null || true
	@sleep 1
	PORT=$(VK_PORT) npx vibe-kanban

quality-gate:
	@echo "运行质量门禁..."
	bash scripts/agent-quality-gate.sh

conflict-check:
	@echo "运行 Worktree 冲突预检..."
	bash scripts/check-worktree-conflicts.sh

post-merge:
	@echo "运行合并后集成验证..."
	bash scripts/post-merge-verify.sh

dispatcher:
	@echo "启动中央调度器（轮询间隔 30s, Ctrl+C 停止）..."
	VK_PORT=$(VK_PORT) python -m dispatcher --project-dir .

dispatcher-once:
	@echo "单次轮询..."
	VK_PORT=$(VK_PORT) python -m dispatcher --project-dir . run --once

dispatcher-status:
	@echo "调度器状态..."
	VK_PORT=$(VK_PORT) python -m dispatcher --project-dir . status --cached
