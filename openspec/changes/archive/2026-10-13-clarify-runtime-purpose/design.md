# Design: clarify-runtime-purpose

## Context

- 触发点：`openspec validate --specs --strict` 的 `overview: Purpose section is too brief
  (less than 50 characters)`；`runtime` 正文 41 字，阈值 50。
- `openspec/specs/runtime/spec.md` 与基线 `2766a3e` 逐字节相同（`git diff` 为空），
  且 `add-python-package-layout` 全程未触碰 `openspec/specs/**`，故这是既有缺陷。
- 该文件有 6 条 requirement：主动发送句柄 / 常驻任务注入 / 共享时钟定时调度 /
  生命周期与优雅退出 / 前端装配选择 / 文件传输句柄；而现有 Purpose 只覆盖其中 4 条涉及的 4 件事。
- 主规格是"已交付能力的行为真相源"，其 requirement 文本受 change + delta 流程保护；
  概述段没有对应的 delta 载体。

## Goals / Non-Goals

**Goals:**

- `openspec validate --specs --strict` 全绿（5 passed / 0 failed）
- Purpose 段**如实**覆盖该规格的 6 条需求范围，而不是凑字数过关

**Non-Goals:**

- 不改任何 `SHALL` 语义，不改 requirement / scenario 文本
- 不顺手"补齐"或统一其它规格的 Purpose（`dispatching` 51 字虽贴线但合格，无证据表明它有问题）
- 不动已归档的 `add-python-package-layout` 制品

## Decisions

**D1. 改主规格概述 + `skip_specs: true`，而不是造一份 delta spec。**
delta 制品只能表达 requirement 级操作；硬造一条 ADDED requirement 来承载概述文字，
既是"为通过校验凭空造需求"，也会污染行为真相源。schema 模板明文允许 docs 类零 delta 变更
走 `skip_specs: true`；更进一步，schema 原文直接规定了本情形该怎么做
（`@fission-ai/openspec` 的 `schemas/spec-driven/schema.yaml`）：

> Do NOT add `## Purpose` to a delta for an existing capability - that spec already has one
> and the delta's is ignored. To change an existing capability's Purpose … edit
> `<planningHome.root>/openspec/specs/<capability-path>/spec.md` directly.

即"直接改主规格的 Purpose"是 schema 明文路径，不是变通做法。代价：本次主规格改动不经由
delta 同步路径，故必须在 tasks 里用"逐条比对 6 条 requirement 标题 + `git diff` 只动 Purpose 段"
来替代 delta 的机械校验。

**D2. 字数以"覆盖 6 条需求"为准，不设上限。**
阈值只是 50 字的下限，压线写法（参见 `dispatching` 的 51 字）在任何措辞微调后都会再次跌破；
且概述本就该说清这个 capability 覆盖什么。最终实交 **183 字**（去空白 170 字），是逐条映射
6 条需求范围的结果，不是刷字数。备选：只在原句尾追加"以及前端装配与文件传输"（改动最小，
但概述依旧残缺、且仍贴着阈值）。

**D3. 措辞只陈述规格里已有的事实。**
新增提及的"前端装配选择""文件传输句柄"，以及保留的"不内置限流原语""停止信号""16MB 上限"
等提法，都能在该文件对应 requirement 里找到出处；不得引入规格未定义的行为。

**D4. 由设计智能体执行这次规格改写（不派实施子 Agent）。**
本 change 的唯一交付物是规格概述文字，而"修改规格"本就是设计角色的职责；
实施/审查分离要防的是"自己写实现、自己审实现"，此处由独立审查子 Agent 验证
（严格校验转绿、requirement 文本零漂移、概述与 6 条需求逐条对得上）。

## Risks / Trade-offs

- [概述与实际范围不符] → 逐条对照 6 条 requirement 标题核对；独立审查重做一遍
- [误碰 requirement 文本] → `git diff -- openspec/specs/runtime/spec.md` 必须只含 Purpose 段；
  另以 `### Requirement:` / `#### Scenario:` 计数与 `SHALL` 计数对比基线
- [`--strict` 过了但 `validate --all` 回归] → 两条都跑
- [阈值是字数而非语义，容易被"刷字数"过关] → 用 D2 + D3 约束：字数是结果，覆盖 6 条需求才是目的
