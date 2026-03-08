# multi-agent-workflow 需求与设计深度诊断报告

## 一、系统架构与运行机制概述

项目 `multi-agent-workflow` 提供了一套将多个不同 AI Agent（Claude, Codex, Gemini）串联并在隔离 Worktree 中进行并行开发、互相 Review、最后合并代码的工作流引擎。

核心机制依赖于 **双态同步**：
1. **云端工作流状态：** Issue 在 VK （Vibe Kanban）看板上的位置（`To do` / `In progress` / `In review` / `Done`）。
2. **本地执行状态：** 调度器 (Dispatcher) 不直接负责 Agent 的启停，而是通过直读 VK 运行时记录在本地的 Sqlite 数据库 (`~/.local/share/vibe-kanban/db.v2.sqlite`) 中的 `execution_processes` 和 `coding_agent_turns` 表，以此判断 Agent 是成功 (`exit_code=0`)、失败以及 Review 的具体结论（总结中正则匹配 `APPROVED` 或 `CHANGES_REQUESTED`）。

---

## 二、全流程跑不通的缺陷分析与 Bug 诊断

通过对现有代码仓库的全面研读、对 `scripts/` 的排查以及与产生的工作空间数据库 (`SQLite`) 的联动分析，诊断出以下足以导致“全流程跑了几次都没有跑通”的核心问题：

### 1. VK MCP 核心工具的返回解析 Bug 与死循环创建资源 (PIT-VK-002)
- **缺陷表现：** 
  当 Issue 从 `To do` 变为 `In progress` 或进入 `In review` 阶段时，调度器会调用 VK MCP `start_workspace_session`。但在当前版本 (v0.1.22-v0.1.23) 下，由于后端返回格式与 MCP 期望格式不一致，MCP 会错误抛出 `{"success": false, "error": "Failed to parse VK API response"}`。
- **致命影响：** 
  因为 `ws_id` 解析失败为 `None`，Dispatcher 会认为此创建失败。如果缺少对 `Container_ref` / `Session` 的有效幂等性兜底防范，或由于配置缺陷，Dispatcher 会在每次轮询 (默认30秒一次) 重复创建大量“僵尸” Workspace，导致全流程立刻瘫痪（在报错日志和 sqlite 记录中可观察到同一 Issue 被开了几十个 Session）。
- **缓解存在逻辑漏洞：** 
  仓库在后续中加入了 REST API 轮询兜底 (`existing = self.rest.find_workspace_by_title(title)`) 但此逻辑在复杂组合状态下存在漏洞（依赖 precise `round` string 匹配可能由于打回轮次计算错乱失效）。

### 2. 状态扭转桥接点极为脆弱 (Review 打回与依赖 Agent Prompt)
- **缺陷表现：**
  当流水线流转到 `In review` 阶段，审查 Agent 被下发了 PR diff，此时 Dispatcher 判断它是否通过是*完全*依赖直读 `SQLite` 里的 `coding_agent_turns.summary`，利用代码进行硬编码检测：`if "APPROVED" in summary_upper` / `"CHANGES_REQUESTED" in summary_upper`。
- **致命影响：**
  如果 Agent 输出时因为 Token 截断、模型本身话痨、或者格式略带偏离（例如 `Approve` 未加 D，或者大写没有完美命中），`vk_db.is_review_done` 会直接返回 `None`。
  而且在没有超时或硬干预的机制下，Dispatcher 会无限期判定该 Issue 处于 "仍在运行 或 无法判断" 状态，导致流水线永久性卡死在 `In review`。

### 3. "清理钩子 (cleanup_script) 与 SQLite QG 判断" 逻辑双轨制冲突
- **缺陷表现：**
  根据文档与代码，流水线期望在 `codingagent` 运行 exit_code=0 后，触发仓库级别的 `cleanup_script` (即 `agent-quality-gate.sh`)，在此通过 lint+test，然后推送分支。
  但在实际 `init.sh` 脚本和 Repo 默认配置下，`setup_script` 和 `cleanup_script` 极大可能是 NULL。
- **致命影响：**
  Dispatcher 新增了 `_ensure_repo_vk_config` 去尝试周期补全 `NULL` 的 Repo 脚本设定，但此动作通过 `MCP` 实现，而且频率很低 (每10轮一次)。如果在刚启动时 Repo 未被配置，`is_qg_passed` 的回退逻辑会认为“既然没配置 cleanup，就依赖 shell hook (`vk-hooks.sh` `REST PATCH` 发起更新)”。但这两个机制会在并发下产生数据撕裂：SQLite 认为它在 running，而 webhook 已经把云端 Issue 推到了 `In review`。

### 4. 质量门禁自动脚本 (agent-quality-gate.sh) 对运行环境强行绑架
- **缺陷表现：**
  `agent-quality-gate.sh` 的项目结构探索太过“简陋聪明”。对于纯 Python/前端工程，如果在同一个 Git Repo 里结构略复杂（例如有一个微前端与 Python 混排，或 `.venv` 未被正确屏蔽），它会自动识别错 `PYTHON_RUNNER`（例如系统环境存在全局 `uv` 但实际上需要依靠容器环境执行）。
- **致命影响：**
  脚本一旦检测错 lint 或 pytest 执行域，直接触发 `exit 1`。触发后，`cleanup_script` 本地 SQLite 状态就锁定在 Failed (`exit_code != 0`)。Dispatcher 看到错误之后就会保持阻断状态不流转，永久等待人工介入。

### 5. `init.sh` 破坏性与非交互式注入不严谨
- **缺陷表现：** 
  脚本 5-6 步骤通过 `sed` 和 `realpath` 修改 `CLAUDE.md.tmpl` 和 `mcp.json` 并强行写入根目录。
- **致命影响：** 
  如果目标工程路径名中包含空格或特殊字符，脚本将会失败崩溃。如果通过非交互模式 `--non-interactive` 启动，可能会越权向不必要的地方写默认值，导致 `VK` 在启动时根本无法解析正确的 Workspace 路径，从头到尾都启动不了容器，直接报错退出。

### 6. VK 服务依赖脆弱，未处理断联后的重新握手
- **缺陷表现：** 
  Dispatcher 是依靠后台 `make dispatcher` 拉起一个 Python daemon。但在真实测试下，它所依赖的 `Vibe-Kanban` node 服务经常会为了更新或解决端口死锁而被操作者重启。
- **致命影响：**
  服务一旦重启，之前 MCP stdio 子进程管道 `self._proc` 会静默中断，Dispatcher 在调用 API 时产生死锁，并且不具备“关闭僵尸子进程，并在下一轮 poll 中重新启动 MCP `npx -y vibe-kanban`”的自动愈合机制。结果就是后台看似活着，但没有任何状态在实际流转。

---

## 三、改进与修复建议规划

1. **(P0) 修复死循环创建 Session 的漏洞：** 在 `start_workspace_session` 中，抛弃依赖 MCP Tool 的 success 标志，强制在调用前和调用后 100% 依赖 REST `/api/task-attempts` 验证标题和 Branch 创建。若已存在，应当硬抛弃后续重新创建。
2. **(P0) 强化 SQLite 探查与审查摘要检测：** 在 `is_review_done()` 中引入 LLM 再判读或提升正则表达式宽容度。当 Agent 的输出无法定性时，添加一个 Fallback Timeout（例如超过 15 分钟无确定性结论自动流转到“人工介入手工审核” 并向终端 push Warning）。
3. **(P1) 收束清理钩子 (Hooks) 单一真相来源：** 彻底禁用 webhook 向上通过 REST 更新 Issue 的做法，统一收归 SQLite 检测（`ep.exit_code`）。保证代码的唯一 Truth Source 在 `vk_db.py` 中。
4. **(P1) 重构 `agent-quality-gate.sh` 以允许项目逃生窗：**  不要让 bash 进行复杂的推测，而是采用约定的 `.vk/quality-gate.conf` 告知具体的运行命令，简化并解耦对宿主架构的不当推断。

