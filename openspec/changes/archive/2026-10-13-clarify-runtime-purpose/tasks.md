# Tasks

## 1. 改写 runtime 主规格的 Purpose 段

- [x] 1.1 把 `openspec/specs/runtime/spec.md` 的 `## Purpose` 正文改写为覆盖该规格 6 条需求范围、字数 ≥50（目标 ~100）的一段话；**`## Requirements` 以下一字不动**。验证：Purpose 正文字符数 ≥50；`git diff -- openspec/specs/runtime/spec.md` 只有 Purpose 段；`### Requirement:` 仍 6 条、`#### Scenario:` 计数与基线一致
- [x] 1.2 严格校验转绿。验证：`openspec validate --specs --strict` → `Totals: 5 passed, 0 failed`；`openspec validate --all` → 0 failed
- [x] 1.3 确认零行为漂移。验证：`git diff 2766a3e -- openspec/specs/runtime/spec.md` 中除 Purpose 段外无差异；`SHALL` 计数与基线一致

## 2. 回归

- [x] 2.1 代码侧不受影响。验证：`.venv\Scripts\pytest -q` 与 `.venv\Scripts\python -m pytest -q` 各 `54 passed`；`.venv\Scripts\python -m mypy` → `Success`

## 3. 实施记录（设计智能体执行，供审查复核）

- 1.1 实测：Purpose 正文 **41 → 183** 字；`### Requirement:` = 6、`#### Scenario:` = 14、`SHALL` 匹配数 = 25（基线亦 25）；本次改动的基线是上一提交 `4304d7b`，`git diff 4304d7b --stat -- openspec/specs/runtime/spec.md` = `4 insertions(+), 1 deletion(-)`，改动全部落在 Purpose 段内（注：裸 `git diff 2766a3e --stat` 会把已提交的 src-layout 重构一并列出，不是本次口径）
- 1.2 实测：`openspec validate --specs --strict` 由 `4 passed, 1 failed` 转为 `5 passed, 0 failed`（exit 0）；`openspec validate --all` → `6 passed, 0 failed`（exit 0）
- 2.1 实测：`pytest` 直跑 54 passed、`python -m pytest` 54 passed、`mypy` → `Success: no issues found in 14 source files`
- 改动方式说明：本 change 的唯一交付物是规格概述文字，按 `design.md` D4，由设计智能体执行（"修改规格"属设计职责），
  并由**独立审查子 Agent**（不共享上下文）做只读复核：严格校验是否真绿、requirement 文本是否零漂移、
  概述的每项提法是否在该规格 requirement 里有出处

## 4. 审查与二次检查留痕

- **独立审查结论**（独立上下文、全程只读）：**通过**。无阻断 / 高 / 中缺陷；报 2 项低（均为 change 制品文字口径不符，已修）
  与 1 项非本次引入（`dsh_cooperation.md` 未提交改动，提交时排除）。其主动证伪手段可复核：把 `openspec/` 复制到
  `%TEMP%` 并把 `runtime/spec.md` 换回 `2766a3e` blob，**受控复现**基线 `4 passed, 1 failed`（证明修的是真实既有缺陷）；
  按 `### Requirement:` 切块**逐字节**比对（6 块全等、`SHALL` 计数 25 = 25）；核对校验器常量
  `MIN_PURPOSE_LENGTH = 50` 与包 mtime，排除"阈值被改松"；逐项核对新 Purpose 的提法在 requirement 里的出处；
  另按 6 条需求抽查实现（`send` / `create_task` / `due_jobs` / `_shutdown` / `build_adapter` / `MAX_FILE_BYTES`）均相符。
- **设计智能体二次检查**（抽查审查清单之外）：
  1. 复核审查引用的 schema 原文**属实**（`@fission-ai/openspec` 的 `schemas/spec-driven/schema.yaml`：
     "To change an existing capability's Purpose … edit `openspec/specs/<capability-path>/spec.md` directly"），
     已并入 `design.md` D1 —— 本 change 的路径是 schema 规定，不是变通；
  2. 复核审查提出的既有观察"`Bot.create_task` 的任务集合只增不减"：`src/cea/app.py:148`
     `self._tasks.add(task)`，而 `src/cea/bot.py` 的 `log_done` 只记日志、**不 discard**，
     且两文件与基线 `2766a3e:cea/*` **blob 完全相同** → 定性为**既有实现问题、非本变更引入**；
     修它属行为改动，须另立 change（本 change 零行为改动，故不修），已上报用户；
  3. 缺陷定性：审查所报 2 项均为"change 制品文字口径"（非实现缺陷、非规格语义），
     已由设计智能体直接修正（`proposal.md` / `design.md` / 本文件 1.1 记录）；
     修复只落在 change 制品内，不动规格与代码，故无需重跑代码验证。
- **最终处置**：3 项低缺陷中 2 项已修、1 项确认为既有且不在本 change 范围；无遗留未处置项。
