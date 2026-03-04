# Agent 交接手册

> 本文档由 GitHub Copilot 在 dogfooding 接入阶段整理，供后续接管本项目的 Agent 参考。
> 最后更新：2026-03-04

---

## 一、项目概况

**项目**：`multi-agent-workflow`  
**仓库**：`tomturing/multi-agent-workflow`（GitHub）  
**本地路径**：`/mnt/d/Workflow/multi-agent-workflow`  
**用途**：本项目自托管自身的 multi-agent 工作流（dogfooding）  

VK 项目 ID：`65f08fde-be22-47d2-bae7-405dcf96685a`  
VK 仓库 ID：`24e156dd-c300-4573-a234-c7d0ed619ec8`  
GitHub owner/repo：`tomturing/multi-agent-workflow`  
主分支：`main`（不是 master）

---

## 二、环境说明

### 运行环境

| 组件 | 位置 / 说明 |
|------|------------|
| Python 虚拟环境 | `.venv/`（`uv sync --dev` 创建） |
| ruff | `.venv/bin/ruff`（uv dev 依赖） |
| pytest | `.venv/bin/pytest`（uv dev 依赖） |
| GitHub Token | `.vk/github_token`（已在 .gitignore，不入库） |
| Dispatcher 配置 | `.vk/dispatcher.json` |
| 状态映射 | `.vk/status_map.json`（6 个状态 UUID） |

### 依赖安装

```bash
cd /mnt/d/Workflow/multi-agent-workflow
uv sync --dev      # 安装 ruff + pytest
```

### 质量门禁验证

```bash
bash scripts/agent-quality-gate.sh
# 预期输出：通过: 3  失败: 0  跳过: 0（ruff check + ruff format + pytest skip-无tests）
```

---

## 三、当前 Dispatcher 状态

**hci-troubleshoot-platform 的 Dispatcher**（已运行）：
```bash
cd /mnt/d/Workflow/multi-agent-workflow
python3 -m dispatcher --project-dir /mnt/d/Workflow/hci-troubleshoot-platform run
# 日志: /tmp/dispatcher.log
```

**multi-agent-workflow 自身的 Dispatcher**（待启动）：
```bash
cd /mnt/d/Workflow/multi-agent-workflow
python3 -m dispatcher --project-dir /mnt/d/Workflow/multi-agent-workflow run
# 日志: /tmp/dispatcher-maw.log
```

启动前检查：
```bash
# 确认 VK 可达
curl -s http://localhost:9527/api/health

# 确认配置正确
python3 -c "import json; d=json.load(open('.vk/dispatcher.json')); print(d['project_id'])"
# 预期: 65f08fde-be22-47d2-bae7-405dcf96685a
```

---

## 四、状态映射（status_map.json）

`.vk/status_map.json` 已写入，内容：

```json
{
  "Backlog": "c7d272b2-5364-4361-a59a-0d3577957d1e",
  "To do": "3e0bb954-b8a1-48c5-befb-d8d44208c727",
  "In progress": "cdf6854a-fb32-4229-8b76-ec7f5cf3319a",
  "In review": "2d54a487-e9e8-4ff0-8215-8ceac6529b28",
  "Done": "4992cfb3-787e-4399-bfec-72c4c257dba4",
  "Cancelled": "34309404-a601-4771-ac98-eb0de63bc7ec"
}
```

**自动发现机制**（无需手动维护）：若 `status_map.json` 丢失或为空，Dispatcher 启动时自动运行两条路径：
1. 快速路径：从现有 Issues 提取 `status_id`（纯 REST，秒级）
2. 探针路径：无 Issue 时用 MCP 创建临时 Issue 循环所有状态读回 UUID，完成后删除

---

## 五、待处理的 VK Issues

当前 Backlog 中有两个 Issue，请按优先级处理：

### MAW-2: feat: 质量门禁自动探测优化（medium 优先级）
- Issue ID：`3d72e8ae-ea2e-4335-b9bf-0262755d6742`
- 核心工作：多语言支持（Node/Go/Rust）、`init.sh` 自动生成 `pyproject.toml`、依赖未装时给出明确提示而非静默 SKIP
- 验收：在 hci 项目和 multi-agent-workflow 项目上运行质量门禁全通过

### MAW-1: feat: 添加 Secret Scanning（low 优先级）
- Issue ID：`ff5d6dc6-8542-475b-b683-db207d1702b4`
- 核心工作：新增 `scripts/scan-secrets.sh`，集成到质量门禁，安装 pre-commit hook
- 推荐方案：轻量 grep 脚本，无外部依赖

---

## 六、调通过程中的所有问题记录

> 这是本次接入 dogfooding 过程中遇到的全部问题，均已修复，留存供优化参考。

### P-001：status_map 无法自动化收集

**现象**：VK status_map（状态名→UUID）之前通过手动 MCP 探针循环获取，无法在 Dispatcher 启动时按需发现。

**根因**：VK REST API 没有 `GET /statuses` 端点；`GET /api/remote/projects/{id}` 响应也不含 statuses 字段。唯一能获得 `status_id` 的地方是 issue 对象的字段。

**解决方案**（已实现，提交 `e40d1c7`）：
- `VKRestClient.get_status_map_from_issues()`：快速路径，从现有 Issue 提取（纯 REST）
- `VKMCPClient.discover_status_map()`：探针路径，创建临时 Issue + 循环 update_issue + REST 读回 + 删除
- `Dispatcher._auto_discover_status_map()`：启动时若 status_map 为空自动执行上述两路径，并写入 `status_map.json`

**相关文件**：`dispatcher/vk.py`（+112 行），`dispatcher/core.py`（+54 行）

---

### P-002：项目无 pyproject.toml，uv run 无法找到 ruff/pytest

**现象**：质量门禁运行后 ruff/pytest 全部 SKIP；日志显示 `ruff 未安装`。

**根因**：`uv run ruff` 在项目 `.venv` 中查找 ruff，但项目没有 `pyproject.toml`，因此没有 `.venv`，ruff 未安装。

**解决方案**（已实现）：
1. 创建 `pyproject.toml`，`[dependency-groups] dev = ["ruff>=0.4", "pytest>=8.0"]`
2. 运行 `uv sync --dev` 安装
3. `_ruff_cmd()` 新增 `uvx ruff` 回退（即使项目无 dev 依赖也可运行）

**防止复现**：`init.sh` 注入时需检测目标项目是否有 `pyproject.toml`，无则自动生成（已记录为 MAW-2 的子任务）。

---

### P-003：bash readarray 空数组产生含空元素的数组

**现象**：质量门禁的 `check_python_test()` 在 `PYTHON_TEST_DIRS` 为空时仍然运行 pytest，输出 "no tests ran"，然后 FAIL。

**根因**：
```bash
readarray -t ARR < <(printf '%s\n' "${ARR[@]}" | sort -u)
```
当 `ARR` 为空数组时，`printf '%s\n'` 实际上打印一个空行，`sort -u` 输出这个空行，`readarray` 将其读入数组，产生 `ARR=("")`（含一个空元素）。此时 `[ ${#ARR[@]} -eq 0 ]` 为 false，循环继续执行 `pytest ""`（空路径），pytest 扫描当前目录。

**解决方案**（已实现）：
```bash
for tdir in "${PYTHON_TEST_DIRS[@]}"; do
    [[ -z "$tdir" ]] && continue   # 过滤空元素
    ...
done
```

---

### P-004：pytest exit code 5 被误判为失败

**现象**：测试目录不存在时（或测试目录存在但无测试文件），pytest 退出码为 5（"no tests collected"），被质量门禁判定为 FAIL。

**根因**：脚本原先用 `if pytest ...; then` 判断，exit code 非 0 全判 FAIL。pytest 约定：
- 0 = 测试全部通过
- 1 = 有测试失败
- 5 = 无测试收集（不算失败）

**解决方案**（已实现）：
```bash
local ec=0
$pytest_cmd "$tdir" ... || ec=$?
if [ $ec -eq 0 ] || [ $ec -eq 5 ]; then
    log_pass "${tdir} — 通过"
else
    log_fail "${tdir} — 部分失败 (exit=$ec)"
fi
```

---

### P-005：ruff 两处 lint 错误（F541 + F841）

**现象**：质量门禁首次运行发现 2 个 ruff 错误：

1. `dispatcher/core.py:811` — `F541: f-string without any placeholders`
   ```python
   issue_section = f"## 当前任务\n\n"  # ← 无占位符，多余 f 前缀
   ```
2. `dispatcher/github.py:222` — `F841: Local variable 'result' is assigned to but never used`
   ```python
   result = subprocess.run(cmd, ...)  # ← result 从未使用
   ```

**解决方案**（已修复）：
1. 移除 f 前缀：`"## 当前任务\n\n"`
2. 直接调用不赋值：`subprocess.run(cmd, ...)`

**注**：这类问题本应在开发时通过 pre-commit hook 拦截，这也是 MAW-1（Secret Scanning + pre-commit hook）的动机之一。

---

### P-006：dispatcher.json 主分支默认值错误

**现象**：模板文件 `templates/vk/dispatcher.json` 中 `"main_branch": "master"`，但 `multi-agent-workflow` 仓库使用 `main`。

**根因**：模板值未参数化，`init.sh --non-interactive` 直接复制不修改。

**解决方案**（当次手动修复）：`.vk/dispatcher.json` 已修改为 `"main_branch": "main"`。

**待优化**：`init.sh` 应在注入时 `git symbolic-ref --short HEAD` 自动探测主分支名（已记录为 MAW-2 关联优化）。

---

## 七、代码提交记录

| 提交 SHA | 说明 |
|----------|------|
| `0a9bcd1` | feat: 启动 GC 清理漏删分支 + merged_at 二次确认 |
| `e40d1c7` | feat: status_map 自动发现 + 初始化 dogfooding 配置 |
| `(本次)` | feat: 质量门禁自动探测 + pyproject.toml + 问题修复 |

---

## 八、接管清单

接管本项目后，请按顺序执行：

- [ ] 确认 VK 服务运行：`curl -s http://localhost:9527/api/health`
- [ ] 确认 GitHub token 有效：`cat .vk/github_token`
- [ ] 运行质量门禁确认绿灯：`bash scripts/agent-quality-gate.sh`（预期 3 PASS 0 FAIL）
- [ ] 启动 Dispatcher：`python3 -m dispatcher --project-dir . run > /tmp/dispatcher-maw.log 2>&1 &`
- [ ] 在 VK 看板中将 MAW-2 移至 "To do" 状态开始编码
- [ ] 处理完 MAW-2 后，将 MAW-1 移至 "To do" 开始编码

---

## 九、关键命令速查

```bash
# 项目根目录
cd /mnt/d/Workflow/multi-agent-workflow

# 质量门禁
bash scripts/agent-quality-gate.sh

# dispatcher 调试（单次轮询）
python3 -m dispatcher --project-dir . run --once

# dispatcher 守护进程
python3 -m dispatcher --project-dir . run > /tmp/dispatcher-maw.log 2>&1 &
tail -f /tmp/dispatcher-maw.log

# 安装 dev 依赖
uv sync --dev

# lint + format
uv run ruff check dispatcher/
uv run ruff format dispatcher/

# 查看 VK issues
curl -s "http://localhost:9527/api/remote/issues?project_id=65f08fde-be22-47d2-bae7-405dcf96685a" | python3 -m json.tool
```
