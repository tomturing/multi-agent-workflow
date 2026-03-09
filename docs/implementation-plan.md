# Dispatcher 悲观等待型重构 — 实现计划

> 记录日期：2026-03-09  
> 状态：**已完成**

## TL;DR

将 Dispatcher 从"乐观推进"改为"悲观等待"：信号不明确时停止等待而非猜测推进。彻底移除对 Agent 自然语言输出的解析，只信任 VK/GitHub 基础设施写入的字段。Review 结论改由 Reviewer Agent 通过 MCP 工具直接修改 issue 状态来表达。

---

## Phase 1：移除 NLP 解析，建立 STUCK 状态机 ✅

**目标**：废弃所有对 `coding_agent_turns.summary` 的解析，建立超时/重试/阻塞机制。

### Steps

1. **IssueTracker 增加字段**（`dispatcher/core.py`）
   - `stuck_reason: str | None` — 卡住的原因描述
   - `retry_count: int` — 当前阶段已重试次数
   - `stuck_since: str | None` — 进入 STUCK 的时间戳
   - `last_exit_code: int | None` — 上次失败的 exit_code

2. **删除 `vk_db.py` 的 NLP 解析**
   - 删除 `is_review_done()` —— 解析 summary 关键词，不可信
   - 删除 `get_review_summary()` —— 废弃方法
   - `is_qg_passed()` 只保留基于 `exit_code` 的判断（本身就是基础设施信号，保留）

3. **在 `_check_pending()` 加入 STUCK 检测**
   - Coding session 超过 `max_coding_wait_minutes`（默认 60）`exit_code` 仍为 null → STUCK
   - Review session `exit_code=0` 后超过 `max_review_wait_minutes`（默认 15）`issue.status` 未变化 → STUCK

4. **STUCK 处理逻辑**
   ```
   retry_count < max_retries → 重建对应类型 session，retry_count += 1
   retry_count >= max_retries → 调用 update_issue_status("Blocked")，停止处理此 issue，告警日志
   ```

5. **`dispatcher.json` 新增配置项**
   ```json
   "max_coding_retries": 2,
   "max_review_retries": 2,
   "max_coding_wait_minutes": 60,
   "max_review_wait_minutes": 15
   ```

---

## Phase 2：Review 阶段重设计（核心）✅

**目标**：Dispatcher 不再解析 review 结论，改为等待 issue.status 变化。

### Steps

1. **重写 `_build_review_prompt()` 末尾**（`dispatcher/core.py`）

   在每次生成的 review prompt 末尾固定追加 MCP 工具调用指令：
   ```
   ## ⚠️ 审查完成方式（系统要求，必须执行）

   审查通过时：
     调用 get_context 获取 issue_id
     调用 update_issue(issue_id=..., status="Done")

   审查不通过时：
     调用 get_issue 获取当前 description
     调用 update_issue(issue_id=..., description=description+"## Review Feedback\n...", status="In progress")
   ```

2. **重写 `_action_finish_review()` 逻辑**（`dispatcher/core.py`）

   现在：解析 SQLite summary 关键词  
   改后：读取 `issue.description` 中最后一个 `## Review Feedback` 段落

3. **新增 `_extract_review_feedback()`**（`dispatcher/core.py`）
   ```python
   def _extract_review_feedback(self, description: str) -> str:
       idx = description.rfind("## Review Feedback")
       return description[idx:].strip() if idx != -1 else ""
   ```

4. **状态流转信号**：`issue.status` 由 In review 变为 Done / In progress，由 Reviewer 通过 MCP 写入

---

## Phase 3：Coding 阶段健壮性 ✅

**目标**：exit_code != 0 时能优雅重试，超时能检出。

### Steps

1. **exit_code != 0 的处理**（`_check_pending()`）
   ```python
   if exit_code != 0:
       error_context = f"上一次执行失败（exit_code={exit_code}）。请检查日志了解原因，修复后重新完成任务。"
       self._action_start_coding(tracker, extra_context=error_context)
       tracker.retry_count += 1
   ```

2. **`_action_start_coding(extra_context=None)`**  
   extra_context 注入到 coder prompt 开头（最高优先级）

3. **`is_coding_timed_out(branch, max_minutes)`**（`vk_db.py`）  
   检查 `started_at`，若 `exit_code IS NULL` 且距今超过阈值 → True

---

## Phase 4：清理与观测 ✅

### Steps

1. `get_status_report()` 增加 STUCK / retry 信息展示  
2. 日志使用 `⚠️ STUCK` 和 `🚫 BLOCKED` 前缀  
3. 删除 `vk_db.py` 废弃的 NLP 相关方法

---

## 验证计划

1. 创建一个 issue，走完 To do → In progress → In review → Done，全程不人工干预
2. 模拟 cleanup 失败（exit_code=1）→ 观察自动重试带入错误上下文
3. 模拟 reviewer 不调用工具 → 观察超时后 STUCK → retry → Blocked 的完整降级链路
4. 模拟两轮 review 不通过再通过 → 观察 feedback 是否准确传递给每轮 coder
