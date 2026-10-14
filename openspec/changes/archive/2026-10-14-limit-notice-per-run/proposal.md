# Proposal: limit-notice-per-run

## Why

`notice-digest` 现在每次检查会把**全部**待推送条目一次投完（`specs/notice-digest/spec.md` 的
`定时检查`：SHALL 推送全部尚未推送的条目）。上线/停用一段时间后重新启用、或站点某天集中发布时，
一次检查可能连发十几条「文本 + PDF」，把目标群刷屏；而当前唯一的缓冲手段是把 `notice_times`
调稀，代价是及时性下降。

需要给"单次投递条数"一个独立的配置上限：**超过上限的条目不是丢弃，而是留到下次定点继续投**。

## What Changes

- 新增配置键 **`notice_max_per_run`**（缺省 `0` = 不限制；取值为非负整数）：
  单次检查**成功投递**的条数上限
- 编排层在达到上限后停止本轮投递，未投递的条目保持"未推送"状态（不记账），下次定点继续
- 达到上限时记明确日志：本次投递 N 条（上限 M）、剩余 K 条待下次
- `config.example.yaml`（注释形式）与 `README.md` 第 7 节配置表同步登记该键

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `notice-digest`：`定时检查` 需求由"每次推送全部待推送条目"改为"受可配置上限约束，
  超出部分留待后续检查继续投递（不得丢弃）"；新增两个场景（超上限、失败条目不占名额）

本变更只改这一条需求的行为，其余 8 条 `SHALL` 一字不动。

## Impact

- **代码**：`src/cea_bot/config.py`（新增校验分支 + 落进返回值）、
  `src/cea_bot/handlers/notice.py`（`_run_locked` 应用上限、`RunResult` 增加剩余条数）
- **配置**：`config.example.yaml` 增加注释形式的可选键；`config.yaml` 无需改动（缺省 = 不限制，
  既有部署行为不变）
- **规格**：`notice-digest` 的 `定时检查` 需求被 MODIFIED（delta 制品），1 条需求 + 2 个新场景
- **风险**：① "0 = 不限制"与 `notice_max_chars` 的"必须为正整数"语义相反，容易误读 →
  在两处文档与错误提示里写清；② 上限过小（如 1）时，积压条目要多个定点才能推完 →
  日志必须报剩余条数，README 给出取值建议；③ 若把上限用于"处理条数"而不是"成功投递条数"，
  持续失败的最新条目会反复占掉名额、把更旧的条目饿死 → 见 `design.md` D2
