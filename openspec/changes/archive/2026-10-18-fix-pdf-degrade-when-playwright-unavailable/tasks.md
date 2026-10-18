# Tasks

> 测试一律用 `asyncio.run(...)` 包裹协程场景，**不引入 pytest-asyncio**；单测不依赖真实网络/浏览器/模型。

## 1. 让"渲染依赖不可用"走规格定义的降级路径

- [x] 1.1 `src/cea_bot/notices/pdf.py` 的 `playwright_renderer`：把
  `from playwright.async_api import async_playwright` 纳入 `try`，使 `ImportError` 与既有的
  `chromium.launch()` 失败**收敛到同一个 `PdfRenderError`**；前者用 `playwright_missing_hint`
  （自带安装命令），后者沿用 `browser_launch_hint`。
  **硬约束（design D1/D2）**：只补导入这一处，**不得**新增更宽的捕获面；
  收敛后必须复核 `asyncio.CancelledError` 仍能冒穿（它在 3.8+ 继承 `BaseException`，
  优雅退出依赖它）—— 用一个显式用例或探针证明，不要只在心里推理。
  验证：单元级用例让 `playwright.async_api` 不可导入，断言 `render_pdf` 抛 `PdfRenderError`
  且文案含 `<本平台 venv 解释器> -m playwright install chromium`；断言**不是** `ModuleNotFoundError`。
- [x] 1.2 `src/cea_bot/__main__.py`：在 `notice_enabled` 分支里，与 `ensure_notice_dirs` /
  `_check_notice_state` 并列调用 `render_env_error()`；返回非 `None` 时用**原样携带**该提示
  `log.warning(...)`。
  **硬约束（design D3）**：**只告警，不 `sys.exit`、不改退出码、不影响启动**。
  理由写在代码注释里：拒绝启动会剥夺规格自己定义的"PDF 失败 → 只发文本"降级路径，
  并连带停掉与依赖无关的 echo / ping；这与 `ensure_notice_dirs` 的硬失败性质不同
  （目录不可写时功能没有任何可降级形态）。
  验证：单测断言"渲染环境未就绪时 `main()` 的路径只产生 WARNING、不抛 SystemExit"；
  另实测启动耗时无显著变化（探测只读 `executable_path`，不启动浏览器）。
- [x] 1.3 处置零调用簇（design D4）：确认 `playwright_missing_hint` / `playwright_install_hint`
  已被 `playwright_renderer` 消费、`render_env_error` 已被启动路径消费；
  **若仍有函数无人消费，就地删除并同步删其测试**，不要留着。
  验证：grep 全仓给出每个函数的**全部**引用点（定义处 + 调用处 + 测试），
  逐个说明"谁在用它"；并在报告里贴出 grep 原始输出。
- [x] 1.4 测试：按 design D5 补单元级用例（真实导入失败 → `PdfRenderError`）；
  确认既有编排级"PDF 失败 → 只发文本 + 记告警 + 照常记账"用例**仍然有效**（不要为过测试放宽它）。
  验证：贴新增用例的函数名与断言；`tests/test_notice_pdf.py` 既有的
  `"playwright install chromium" in message` 那条断言**不得**被放宽（新形态仍含该子串）。

## 2. 验收

- [x] 2.1 四条全绿并贴原始输出：`.venv\Scripts\pytest`（直跑）/ `.venv\Scripts\python -m pytest` /
  `.venv\Scripts\python -m mypy` / `openspec validate --all --strict`
- [x] 2.2 **不启动 bot、不连 NapCat、不访问真实站点、不发消息、不调用模型**；
  **不要**真的卸载 playwright 来制造失败（那会破坏 venv），一律用 `monkeypatch` 模拟。
- [x] 2.3 报告里如实声明：未在真实缺失 playwright 的环境上验证过；
  "启动期只告警不退出"只由单测与代码路径证明，未做端到端实跑。

## 3. 实施、三轮审查与最终裁决（含一次**撤回**）

### 3.1 实施与设计方的核对

实施方交付 D1（导入纳入降级路径）等，+7 用例（274 → 281），并用**红→绿负对照**自证：
还原 HEAD 版 `pdf.py` → 新用例在规格违反点失败；把捕获面宽成 `except BaseException` → D2 用例失败；
把告警换成 `sys.exit` → 启动用例失败。设计方独立核对：捕获面无 `BaseException`，验收复跑全绿。

### 3.2 三轮审查各自推翻了什么

| 轮次 | 发现 | 后果 |
|---|---|---|
| **一轮** | ①`ImportError` 误报（传递依赖缺失被说成"未安装 playwright"）②导入期**非** `ImportError` 时**渲染路径仍中止、启动也被中止** ③探测**无超时** → 最坏情况启动**永久挂起** ④`asyncio.run` 在运行中的事件循环里 `RuntimeError` ⑤`render_env_error` 两个分支零测试 | 修 F1–F5；**修订 D1**（导入那行改 `except Exception` —— 原"不宽化"的论证对**导入语句**不成立）；**修订 D3**（立下"探测必须在**结构上**不可能中断启动"的原则） |
| **二轮**（实施方实测） | 补超时后暴露**新窗口**：取消落在 `create_subprocess_exec` 的 await 里时，`asyncio.run` 的 `_cancel_all_tasks` **永久等待** → 启动挂起 + node 进程残留。**且与老行为对照：无超时时该窗口根本不会取消、只是"启动被拖慢" —— 即超时修复把"慢启动"变成了"永久挂起"** | 提请裁决 |
| **设计方裁决（用户确认）** | **撤掉启动期探测、删除 `render_env_error`** | 见 3.3 |

### 3.3 最终裁决：撤回 D3，删除 `render_env_error`

理由（完整版见 `design.md` D3 的"经审查与实测后撤回"段）：

1. **规格从未要求启动期探测** —— 规格要的"降级 + 告警"已由**渲染路径**的修复满足；
2. 探测是设计方的主意，边际价值只是"比第一个定点早几小时知道"，
   而 README §8.8 第 1 步本来就要求运维在部署时**手动渲染一页中文**验证；
3. 它的成本持续长出新风险（多 0.7s 启动 + 一族"探针搞崩/挂起启动"的窗口），每轮修补都在扩大接触面，
   而这恰是最容易藏 bug 的一类代码；
4. 仓库自己的模式是：**只有"没有可降级形态"的前提才在启动期硬失败**（状态库、目录写权限）——
   渲染环境**有**可降级形态，本就不属于那一类。

### 3.4 撤回后的最终状态（设计方复核）

- `src/cea_bot/__main__.py` **相对 HEAD 零差异**（`git diff HEAD --` 无输出）—— 启动期探测被彻底撤掉；
- `render_env_error` **已删除**；`browser_launch_hint` / `playwright_missing_hint` /
  `playwright_install_hint` 保留且仍由 `playwright_renderer` 消费
  （导入失败 → `playwright_missing_hint`；launch 失败 → `browser_launch_hint`；
  `playwright_install_hint` ← `playwright_missing_hint`）；
- **`src/` 与 `tests/` 内 `render_env_error` / `_warn_if_render_env_unready` /
  `RENDER_ENV_PROBE_TIMEOUT_SECONDS` 零引用**；`src/` 内 `except BaseException` 仍为零；
- 核心修复**未被误伤**（抽查确认仍在）：`test_render_pdf_converges_missing_playwright_to_pdf_render_error`、
  `test_import_time_non_import_error_converges_to_pdf_render_error`（F2 渲染侧）、
  `test_missing_hint_says_cannot_import_not_not_installed`（F1）、
  `test_playwright_renderer_converges_launch_failure_to_pdf_render_error`、
  `test_cancelled_error_propagates_through_render_path`（D2）、
  `test_pdf_failure_degrades_to_text_only` / `test_pdf_oserror_degrades_to_text_only`（编排级降级）；
- 删除的 19 行测试**逐条归属于被撤功能**（`render_env_error` 的两个专用用例），没有删掉任何有效断言；
- 四条验收：`.venv\Scripts\pytest` **278 passed** / `python -m pytest` **278 passed** /
  `mypy` 零报错（24 文件）/ `openspec validate --all --strict` 全绿。

**教训（同时写进 design）**：一个"听起来无害"的可观测性增强，如果它调用的是别人的**重初始化**路径
（这里是拉起 Node driver），就会把对方的失败模式引进自己的启动路径。
**评估这类增强时，代价要按"它可能引入的**新**失败模式"算，而不是按"它正常情况下多花多少时间"算。**

**注（过程与分工）**：F1–F5 与撤回批次**分开两次派发**；实施方**没有**自行改动设计或规格 ——
撤回的依据由用户裁定、由设计方写进 `design.md` / `proposal.md`，实施方按裁决执行删除。
**另**：README §8.7 那段"不做启动探测、启动阶段无提示、部署时按 8.8 手动渲染验证"由设计方在撤回后补写
（实施方把 README 整file 恢复 HEAD 时，连带删掉了设计方此前**替换**上去的版本，已重新加回并保留）。
