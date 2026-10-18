# Design: fix-pdf-degrade-when-playwright-unavailable

## Context

- 违规点：`src/cea_bot/notices/pdf.py` 的 `playwright_renderer` 里，
  `from playwright.async_api import async_playwright` 位于 `try` **之外**；
  而调用方 `handlers/notice.py` 的 `_push_one` 只捕 `(PdfRenderError, OSError)`。
  于是"包不可用"这一类失败会以 `ModuleNotFoundError` **冒穿整轮**，连文本都不发。
- 规格要求（`openspec/specs/notice-digest/spec.md`「PDF 生成与投递」的 Scenario"PDF 生成失败"）：
  渲染环境不可用 SHALL **只发送文本**并记录告警，该条仍按已推送记账 —— 即**必须降级，不得中止**。
- 同文件的 `render_env_error` / `playwright_install_hint` / `playwright_missing_hint`
  经独立审查确认**零生产调用**（只有定义 + 测试）。其中 `playwright_missing_hint`
  正是这条漏网路径**本该**给出的提示。
- `render_env_error` 的实现只读 `playwright.chromium.executable_path`（**不启动浏览器**），故可廉价地用于启动期探测。

## Goals / Non-Goals

**Goals:**

- "渲染依赖不可用"一律走**规格定义的降级路径**（只发文本 + 告警 + 照常记账），不再中止整轮
- 那三个零调用函数**要么被真正消费，要么被删** —— 不留"看起来启动期探测过浏览器"的假象
- 失败提示**可执行**（含正确平台的安装命令，复用 `platform_hints.venv_python`）

**Non-Goals:**

- 不改 PDF 渲染参数、文件名规则、任何 `notice_*` 配置项
- 不把渲染环境探测改成**启动即拒绝**（见 D3）
- 不引入新依赖、不改 `src/cea/**`、不改任何既有 SHALL 的文本

## Decisions

**D1. 导入与初始化纳入同一条降级路径；导入那一行的捕获面取 `except Exception`（不取 `BaseException`）。**
把 `from playwright.async_api import async_playwright` 移进 `try`，与既有的 `chromium.launch()` 失败
**收敛到同一个异常类型** `PdfRenderError`。
**收敛范围仍然很窄**：只包住**导入这一行**，不动既有那两处为 launch 与渲染而写的 `except Exception`。

**（审查后修订）** 本条初稿写的是"导入那行只捕 `ImportError`"，理由是"别把磁盘满之类真实缺陷
吞成依赖不可用"。**该理由对导入语句不成立**，独立审查用实测推翻了它：
导入期抛 `RuntimeError`（如"部分安装"）时，`except ImportError` 不接，
于是 ①渲染路径上异常冒穿 → `handlers/notice.py` 的 `(PdfRenderError, OSError)` 也接不住
→ **整轮中止、连文本都不发（原规格违反在这个更窄的触发类上复现）**；
②启动路径上的探测无 try → **直接把启动搞崩**（见 D3）。
导入语句不会产生"磁盘满 vs 依赖不可用"的歧义 —— 导入期任何失败**就是**"渲染依赖不可用"，
按规格就该降级。故改为 `except Exception`：`asyncio.CancelledError`/`KeyboardInterrupt` 等
`BaseException` 仍然冒穿，**D2 不受影响**。

**D2. `CancelledError` 必须继续冒穿。**
`asyncio.CancelledError` 在 Python 3.8+ 继承 `BaseException`，故 `except Exception` 捕不到它 ——
**这是框架优雅退出所依赖的**（`base.py` 的契约：run() 被取消即停止重连并收尾）。
实现时必须复核收敛后的代码**没有**把捕获面扩大到 `BaseException`。

**D3.（经审查与实测后**撤回**）原计划把 `render_env_error` 接进启动路径做"未就绪就告警"；
现改为**删除该函数**、不做任何启动期探测。**
本条的演变值得完整留着，它是一个"非必需的可观测性增强如何长出一族风险"的样本：

- **初版（设计方）**：接进 `main()` 的 `notice_enabled` 分支；未就绪只 `log.warning`、
  **不** `sys.exit`。理由：规格已定义"渲染环境不可用 → 只发文本"的降级路径，
  拒绝启动会剥夺这条降级能力、并连带停掉 echo / ping 等与该依赖无关的功能；
  这与 `ensure_notice_dirs` 的硬失败性质不同（目录不可写时功能**没有任何可降级形态**）。
- **一轮审查**：实测出三条"非致命探针其实是致命路径"——①无超时 → driver 不回应时启动**无限期挂起**；
  ②导入期非 `ImportError` 时探测无 try → **启动中止**；③`asyncio.run` 在运行中的事件循环里
  `RuntimeError`（潜伏）。→ 补了 `wait_for(timeout=15s)` + 整体 `except Exception` 兜底，
  并立下原则"探测必须在**结构上**不可能中断启动"。
- **二轮实测（实施方）**：补超时后又暴露出**新的**窗口 —— 若取消恰好落在
  `asyncio.create_subprocess_exec` 的 await 里（即"拉起 driver 进程"本身比超时还慢），
  `asyncio.run` 收尾的 `_cancel_all_tasks` 会**永久等待**一个 pending 任务 → **启动挂起**，
  且残留一个 node 进程。**与老行为对照**：无超时时该窗口根本不会取消，只是"启动被拖慢" ——
  即**超时修复本身把"慢启动"变成了"永久挂起"**。
  彻底修它要么靠更微妙的取消编排（有界 grace，且仍残留进程），要么改线程隔离（机制更重）。
- **最终裁定（用户确认）：撤掉启动期探测，删除 `render_env_error`。** 理由：
  ① **规格从未要求启动期探测** —— 规格要的"降级 + 告警"已由**渲染路径**的修复满足；
  ② 探测是设计方的主意，边际价值只是"比第一个定点早几小时知道"，
     而 README §8.8 第 1 步本来就要求运维在部署时**手动渲染一页中文**验证；
  ③ 它的成本却持续长出新风险（多 0.7s 启动 + 一族"探针搞崩/挂起启动"的窗口），
     每轮修补都在扩大接触面，而这恰是最容易藏 bug 的一类代码；
  ④ 仓库自己的模式是：**只有"没有可降级形态"的前提才在启动期硬失败**（状态库、目录写权限）——
     渲染环境**有**可降级形态，本就不属于那一类。

**教训**：一个"听起来无害"的可观测性增强，如果它调用的是别人的**重初始化**路径
（这里是拉起 Node driver），就会把对方的失败模式引进自己的启动路径。
评估这类增强时，代价要按"它可能引入的**新失败模式**"算，而不是按"它正常情况下多花多少时间"算。

**D4. 零调用簇的归宿：两个被消费，一个删除 —— 不留死代码。**
`playwright_missing_hint`（导入失败）与 `playwright_install_hint`（安装命令）由 `playwright_renderer`
消费；`render_env_error` 在 D3 撤回后**零消费者**，故**删除并同步删其测试**
（D4 初版即写明："若实施中发现某个函数仍无法被消费，就地删除并同步删其测试"）。

**D5. 测试必须覆盖**真实**导入失败，而不是只测假渲染器。**
现有"PDF 失败降级"的编排级用例注入的是**假** `render_pdf`，因此覆盖不到"真实导入失败"这条路径
（这正是缺陷能长期潜伏的原因）。本变更要求两个层次各有用例：
① **单元级**：让 `playwright.async_api` 不可导入（如 `monkeypatch.setitem(sys.modules, "playwright.async_api", None)`），
   断言 `render_pdf` 抛的是 `PdfRenderError`（**不是** `ModuleNotFoundError`），且文案含可执行安装命令；
② **编排级**：`_push_one` 收到 `PdfRenderError` 时确实"只发文本 + 记告警 + 照常记账"（既有用例已覆盖，
   本次只需确认它仍然有效，不重复造）。

## Risks / Trade-offs

- [收敛异常会掩盖真实缺陷] → D1 的**范围**仍只包住导入那一行（不新增宽捕获的区域）；
  且告警文案要能区分"依赖缺失"与"渲染失败"两类（前者含安装命令，后者是 `browser_launch_hint`）
- [启动期探测拖慢启动] → **已随 D3 撤回而消失**（不再有启动期探测）。
  但过程值得留档：实测该探测约 **0.7s**（`async_playwright()` 拉起 Node driver 占 0.666s，
  读 `executable_path` 本身 0.000s、确实**不启动浏览器**）。
  **更正**：设计方在本文与 task 1.2 里原写"探测很廉价"是**错的** ——
  把"不启动浏览器"误当成"不要钱"，漏掉了驱动进程的启动开销。
- [删掉 `render_env_error` 的测试会减少覆盖] → 该函数在 D3 撤回后零消费者，按 D4 删除并同步删测试；
  **不**为了"保住覆盖"而留一个没人调用的函数
- [README 与本变更不同步] → **撤回 D3 后 README 不需要新增"启动时会告警"那段**
  （设计方一度加过，须一并撤掉）；README §8.3 / §8.8 原有的"渲染环境需自备、部署时手动渲染验证"仍然有效，
  它就是本变更撤回探测后的可观测性兜底
