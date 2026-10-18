# Proposal: fix-pdf-degrade-when-playwright-unavailable

## Why

**这是一条真实的规格违反，由独立审查在验证"死代码"时顺带挖出。**

主规格 `openspec/specs/notice-digest/spec.md` 的「PDF 生成与投递」写明：

> **Scenario: PDF 生成失败**
> - **WHEN** PDF 生成失败（例如渲染环境不可用）
> - **THEN** 只发送文本消息并记录告警，该条仍按推送成功的语义记账，不无限重试

但实现里有一条漏网路径：`src/cea_bot/notices/pdf.py` 的

```python
from playwright.async_api import async_playwright   # ← 在 try 之外
```

若 playwright **包**不可用（部分安装、`--no-deps`、venv 被破坏），抛出的是
`ModuleNotFoundError` 而**不是** `PdfRenderError`；而 `handlers/notice.py` 只捕
`(PdfRenderError, OSError)` → 异常冒穿 → **整轮中止、连文本都不发**。
即：规格要求"降级"，实现却"中止"。

讽刺的是紧邻的注释正在警告这件事（"若放任它们以原类型冒出…结果是 PDF 失败 → 整轮中止 →
连文本都没发"），而它上方几行的 import 就是那条漏网路径。

同时，同一个文件里有一簇**零生产调用**的函数，本次一并处置（见 What Changes）。

## What Changes

- `notices/pdf.py`：把 `playwright` 的导入纳入**同一条降级路径** —— 渲染依赖不可用
  （导入期**任何**异常、浏览器内核缺失、启动失败）统一收敛成 `PdfRenderError` 并带上**可执行的**提示，
  从而走规格要求的"只发文本 + 记告警 + 照常记账"。
  导入那一行的捕获面取 `except Exception`（**不是** `BaseException`）—— 理由见 design D1 的审查后修订。
- 文案修正：`playwright_missing_hint` 的"未安装 playwright"改为"无法导入 playwright"
  （传递依赖缺失时原标签与真实异常自相矛盾，实测会输出"未安装 playwright（No module named 'pyee'）"）。
- **删除 `render_env_error`**（及其实例化的测试）：它是上一变更遗留的零生产调用函数。
  本变更一度把它接进启动路径做"渲染环境未就绪就告警"，**经审查与实测后撤回**（见 design D3 的审查后修订）：
  启动期探测让进程启动多约 0.7s，并引入一族"探针反过来搞崩或挂起启动"的风险，
  而它带来的只是"比第一个定点早几小时知道"——边际价值不足以承担这些风险。
  `playwright_install_hint` / `playwright_missing_hint` 仍**由渲染器消费**，保留。
- 补测试：把"渲染依赖不可用 → 降级"这条路径钉住（既有用例注入的是**假**渲染器，
  覆盖不到真实的导入期失败，这正是缺陷长期潜伏的原因）。

## Capabilities

（`skip_specs: true`：**没有任何需求文本需要改动**。本变更让实现回到既有 SHALL 上。）
**但这不等于"无行为变化"** —— 可观察行为会从"整轮中止、连文本都不发"变成"只发文本 + 告警"。
若审查认为有必要为这条路径补一个 Scenario，可另议；本提案的判断是既有 SHALL 已经覆盖它。

## Non-Goals

- **不**新增任何启动期渲染环境探测（撤回理由见 design D3）
- 不改 PDF 渲染参数、不改文件名规则、不改任何 `notice_*` 配置项
- 不引入新依赖；不改 `src/cea/**`

## Impact

- **代码**：`src/cea_bot/notices/pdf.py`（导入纳入降级路径、文案修正、删除 `render_env_error`）。
  **不涉及** `src/cea_bot/__main__.py`（启动期探测已撤回），也**不需要**改 `handlers/notice.py`
  的捕获元组（`PdfRenderError` 本就命中）
- **测试**：`tests/test_notice_pdf.py`（新增真实导入期失败的用例；删除 `render_env_error` 的用例）
- **风险**：把捕获面放宽到导入语句上的 `except Exception`，会不会掩盖真实缺陷？
  → 不会：**范围**仍只包住导入那一行，且导入期不存在"磁盘满 vs 依赖不可用"的歧义
  （见 design D1 的审查后修订）。`asyncio.CancelledError` 等 `BaseException` 继续冒穿（D2）。
