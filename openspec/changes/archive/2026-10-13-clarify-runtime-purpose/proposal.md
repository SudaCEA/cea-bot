# Proposal: clarify-runtime-purpose

## Why

`openspec validate --specs --strict` 报 `spec/runtime` 一项失败：

```
✗ spec/runtime
Totals: 4 passed, 1 failed (5 items)
⚠ [WARNING] overview: Purpose section is too brief (less than 50 characters)
```

实测各主规格 Purpose 正文长度（2026-10-13）：`messaging` 66、`napcat-adapter` 95、
`qqbot-adapter` 65、`dispatching` 51、**`runtime` 41** —— 阈值为 50 字，只有 `runtime` 不合格。
`openspec/specs/runtime/spec.md` 自基线 `2766a3e` 起逐字节未变，故这是**既有缺陷**，
不是 `add-python-package-layout` 引入的。

两重损失：

- `--strict` 比 `validate --all` 更严，任何想用严格模式把关的改动都会被这条既有警告挡下；
  长期下来会训练人忽略 `--strict` 输出（与"把 mypy 噪音清零"同类的动机）。
- `runtime` 的 Purpose 只覆盖 4 件事，**漏了该规格里确实存在的另外两条需求**
  （前端装配选择、文件传输句柄），概述与自身范围不符。

## What Changes

- 仅改写 `openspec/specs/runtime/spec.md` 的 `## Purpose` 正文：由 41 字扩到 **183 字**
  （阈值 50；字数不是目标，覆盖 6 条需求范围才是——见 `design.md` D2），
  并把该规格实际包含的 6 条需求范围补齐（主动发送句柄 / 常驻任务注入 / 共享时钟定时调度 /
  生命周期与优雅退出 / 前端装配选择 / 文件传输句柄）
- **零 SHALL 改动**：`## Requirements` 之下所有 Requirement 与 Scenario 文本一字不动

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

（无）

需求层零 delta。`## Purpose` 是规格的概述文字、不是 requirement，而 OpenSpec 的 delta 制品
只能表达 requirement 级的 ADDED / MODIFIED / REMOVED，无法承载概述改写。因此按 schema 模板
的规定（"A change with no capabilities at all (pure refactor, tooling, docs) must set
`skip_specs: true`"）在 `.openspec.yaml` 置 `skip_specs: true`，直接改主规格概述。
**不为通过校验而凭空造需求。**

## Impact

- **规格**：`openspec/specs/runtime/spec.md` 的 `## Purpose` 段（唯一改动点）
- **代码 / 依赖 / 配置 / 测试**：零改动
- **风险**：① 概述与需求范围不符 → 以逐条比对 6 条 requirement 标题核对；
  ② 误碰 requirement 文本 → 以 `git diff` 证明改动只落在 Purpose 段
