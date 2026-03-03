"""
VK 中央调度器 — 多 Agent 工作流自动化引擎

通过轮询 VK REST API 检测 Issue 状态变化，自动触发：
- To do     → 创建编码 Session（可选）
- In review → 创建交叉审查 Session
- Done      → 合并分支到主分支

用法:
    python -m dispatcher --project-dir /path/to/project
    python -m dispatcher --once   # 单次轮询（测试用）
"""

__version__ = "0.1.0"
