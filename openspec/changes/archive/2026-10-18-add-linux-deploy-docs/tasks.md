# Tasks

> 测试一律用 `asyncio.run(...)` 包裹协程场景，**不引入 pytest-asyncio**；单测不依赖真实网络/浏览器/模型。

## 1. 提示文案按平台生成

- [x] 1.1 `src/cea_bot/`：新增一个**纯函数**给出 venv 解释器的相对路径，形如
  `_venv_python(is_windows: bool) -> str`，返回 `.venv\Scripts\python` 或 `.venv/bin/python`。
  **平台必须做成可注入参数**（不要在函数里直接读 `sys.platform`），否则只有一个分支能被单测覆盖。
  放在何处由实施方定（放两个模块其中之一、或一个极小的新模块都行），但**不得**让
  `cea_bot/notices/**` 反向依赖上层（依赖方向硬约束）。
- [x] 1.2 把 `src/` 里 4 处写死 Windows 路径的提示改为按平台生成（**只改字符串，不改任何逻辑**）：
  - `src/cea_bot/config.py:74`（凭据缺失提示里的 `.venv\Scripts\python -m cea_bot`）
  - `src/cea_bot/notices/pdf.py:157`（浏览器启动失败提示）
  - `src/cea_bot/notices/pdf.py:182`（`install_hint` 常量）
  - `src/cea_bot/notices/pdf.py:188`（未安装 playwright 的提示）
  其中 playwright 相关一律改用 **`<venv-python> -m playwright install chromium`** 形态
  （已实测 `python -m playwright` 可用，可同时适配 Windows 的 `Scripts\` 与 Linux 的 `bin/`）。
  验证：单测**两个分支都断言**（`is_windows=True/False` 各一条），断言文案里含正确的解释器路径、
  且**不再出现** `.venv\Scripts` 与 `.venv/bin` 之外的第三种写法；并 grep 全仓确认
  `src/` 内已无写死的 `.venv\Scripts`。
- [x] 1.3 `pyproject.toml` 与 `README.md` 里 Windows 专属的注释/示例改为平台中立或并列两种形态
  （`pyproject.toml` 第 2、19、28-29 行附近；README 第 16、64、163、177、261 行附近）。
  **不得**改 `pyproject.toml` 的任何功能字段（依赖、`requires-python`、mypy 配置一律不动）。
- [x] 1.4 `tests/conftest.py:30` 的"项目未安装"提示同样写死了 `.venv\Scripts\python` ——
  它会在**新克隆的 Linux 上忘记 `pip install -e` 时立即打印**，比 `src/` 那几处更容易撞上，
  是本次漏在初始清单之外、由设计智能体复查补入的一处。改为按平台生成。
  **关键实现约束**：conftest 的职责正是"`cea` 尚未安装时的提示"，故它**不得** import
  `cea_bot` 里的任何 helper（那会在最需要它的场景下先崩掉）；只能用 `sys.platform` 就地判断一行，
  并加注释说明为何不能与 1.1 的纯函数共用。同时把 `tests/test_framework.py:5` 与
  `tests/test_napcat.py:4` docstring 里的 Windows 运行示例改为平台中立。
  验证：**`src/` 内已无写死的 Windows 口径提示** —— 该串在 `src/` 下只应剩 `platform_hints.py`
  的真相源常量（它在 `pyproject.toml`、`tests/conftest.py`、测试的**期望值字面量**里出现均属合理，
  `start.bat` 与 README 的 Windows 小节本应如此）；
  【更正：本句初稿写成"grep 全仓后只剩 start.bat 与 README 的 Windows 小节"，与实况不符 ——
  独立审查指出该串还合法地出现在 `pyproject.toml`、`conftest.py`、`platform_hints.py` 常量与
  测试用例里，故把判据收窄为"`src/` 内无写死提示"这一真正要守的边界。】conftest 的"仅 `cea` 缺失才算未安装"判据**未被动过**；
  `tests/test_notice_pdf.py:188` 那条 `"playwright install chromium" in message` 的断言
  在新写法下仍成立（新形态 `<venv-python> -m playwright install chromium` 包含该子串），
  **不得**为迁就改动而放宽它。

## 2. start.sh

- [x] 2.1 新增 `start.sh`，与 `start.bat` 的三件事**逐一对齐**：
  ① `cd "$(dirname "$0")"`（不依赖调用方 cwd）；② 自检 `.venv/bin/python` 存在、
  且 `import cea, cea_bot, cea_bot.handlers.basic, aiohttp, yaml` 通过，失败时打印可执行的安装提示
  （含**官方源**：清华镜像缺 `qq-botpy`）并以非 0 退出；③ 执行 `.venv/bin/python -m cea_bot`。
  用 `#!/usr/bin/env bash` 与 `set -u`，中文提示。**不做 systemd**。
  验证：`bash -n start.sh` 语法通过；且**在 Windows 上无法实跑** —— 必须在报告里如实声明未在 Ubuntu 上执行过。

## 3. README 的 Ubuntu 部署一节

- [x] 3.1 新增「在 Ubuntu 上部署」一节，**必须**包含以下小节（顺序自定）：
  1. **前置**：Ubuntu + Python **≥3.13**（`requires-python` 硬门槛；24.04 只带 3.12 →
     给 deadsnakes / uv / 25.04+ 三条路）；NapCat **必须与 bot 同机**（附理由：PDF 是把本机绝对路径
     交给 NapCat 读的，跨机会让 PDF 静默丢失且不再重试）
  2. **系统依赖**：`python3.13-venv`；Playwright 的共享库
     （`python -m playwright install --with-deps chromium`，需 sudo；无 sudo 时用官方
     `mcr.microsoft.com/playwright/python` 基底镜像）
  3. **中文字体（硬前置）**：`fonts-noto-cjk` 最低要求；不装则 PDF 中文成豆腐块；
     可选 `/etc/fonts/local.conf` 别名把 `宋体/仿宋/黑体/等线/仿宋_GB2312` 映射到已装字体
  4. **安装**：`python3.13 -m venv .venv` + `.venv/bin/python -m pip install -e ".[dev]" --index-url https://pypi.org/simple`
     （**必须官方源**：镜像缺 `qq-botpy`）+ `.venv/bin/python -m playwright install chromium`
  5. **配置**：从 `config.example.yaml` 复制为 `config.yaml`（含密钥、已被 gitignore）；
     **必须改**：`ws_url: ws://127.0.0.1:<端口>`、`notice_pdf_dir` 改成 **Linux 绝对路径**、
     `deepseek_api_key`；并**显式警告** Windows 绝对路径（`D:\...`）在 Linux 上是合法文件名，
     会创建怪目录且 NapCat 读不到 —— 用一个"错误示例 vs 正确示例"的对照写明
  6. **时区**：`notice_times` 是本地时间 → `sudo timedatectl set-timezone Asia/Shanghai`
     （容器则 `TZ=Asia/Shanghai`），否则 6 个定点整体平移
  7. **启动**：`chmod +x start.sh`（Git 在 Windows 上不保留可执行位）→ `./start.sh` 或 `bash start.sh`
  8. **分层验收清单**：字体（渲染一页中文非豆腐块）→ 依赖（`pytest` 全绿）→ 连通（WS + `get_login_info`
     retcode=0）→ 入站（群里 `ping` 有 `pong`）→ 出站通知（**先干跑**看日志，再关干跑，
     并**去群里肉眼确认 PDF 真的出现**，`retcode=0` 不等于送达）
- [x] 3.2 同节补一句"状态库可选迁移"：带 `data/state/cea_bot.db` 过去 = 沿用"已推过"的记忆，
  不带 = 首跑重建基线；两者都不推历史。

## 4. 验收

- [x] 4.1 四条全绿并贴原始输出：`.venv\Scripts\pytest`（直跑）/ `.venv\Scripts\python -m pytest` /
  `.venv\Scripts\python -m mypy` / `openspec validate --all --strict`
- [x] 4.2 **不启动 bot、不连 NapCat、不访问真实站点、不发消息、不调用模型**（本变更是文档与提示改动）
- [x] 4.3 报告里如实声明：`start.sh` **未在真实 Ubuntu 上执行过**、字体方案**未在 Ubuntu 上验证过**、
  `README` 里的命令**未逐条在 Ubuntu 上复现**（本机是 Windows）

## 5. 换行符保护（用户裁定后追加）

- [x] 5.1 新增 `.gitattributes`，内容**只针对 shell 脚本**：`*.sh text eol=lf`。
  **不得**写 `* text=auto eol=lf` 或任何会波及 `.bat` 的规则 —— `cmd.exe` 对 LF-only 的 `.bat`
  有已知问题（标签与 `goto` 的解析），`start.bat` 必须保持 CRLF 兼容。
  理由：本机 `core.autocrlf=true`；没有这条规则时，在 Windows 上 checkout 会把 `start.sh` 变成 CRLF，
  而它被拷到 Ubuntu 后会直接 `bad interpreter` / `$'\r'` 报错 —— 那是本变更交付物**自身**的部署风险。
  加一行即可从根上消除，比只在 README 里口头提醒可靠（README 8.7 的人工提醒保留，作为二次兜底）。
  验证：① `git check-attr text eol -- start.sh` 显示 `text: set`、`eol: lf`；
  ② `git check-attr text eol -- start.bat` **不被该 `*.sh` 规则命中**（保持原状）；
  ③ 工作区 `start.sh` 仍为 LF（`CR` 计数 0）；④ 暂存后 `git diff --cached --stat` 只多出
  `.gitattributes` 这一个文件；⑤ 重跑四条验收，确认无副作用。

---

## 实施记录（实施智能体，2026-10-18，Windows 本机）

**四条验收（原始输出，均已贴进报告）**：`.venv\Scripts\pytest` → `274 passed in 7.13s`；
`.venv\Scripts\python -m pytest` → `274 passed in 5.84s`；`.venv\Scripts\python -m mypy` →
`Success: no issues found in 24 source files`；`openspec validate --all --strict` →
`Totals: 7 passed, 0 failed (7 items)`。四条退出码均为 0。

**`start.sh` 的验证深度（说清边界）**：`bash -n start.sh` 退出码 0（Git Bash 5.2.26 / MSYS2）；
文件 2216 字节、CR 计数 0（LF）。控制流用**假解释器**离线验过：在临时目录造 `.venv/bin/python`
后从 `/` 调用，实测 ① `cd "$(dirname "$0")"` 生效、② 自检失败时打印含官方源的安装命令并以 1 退出
且**未执行启动**、③ 自检通过时 `exec` 到 `-m cea_bot` 并**原样透传退出码**。真实仓库内实跑
`bash start.sh`（本机没有 `.venv/bin/python`）→ 停在自检一、退出码 1、**未启动 bot**。

**诚实声明（第二轮审查 + 设计方 WSL 实证后已更新）**：原先只写"未在真实 Ubuntu 上验证过"，
现改为**逐项列明**——这比一句笼统的"未验证"更诚实也更有用。

**已实证**（在**真实 Ubuntu 24.04.3**（WSL2 用户态）上只读探查，未安装任何包、未改发行版）：

- `python3 -V` = **3.12.3**、`python3.13` **不存在** → §8.1 的硬门槛成立；
- `apt-cache policy`：`python3.13-venv` 不在默认源；`fonts-noto-cjk` 在 `noble/main`（`1:20230817+repack1-3`）；
- `/bin/sh` → **`/usr/bin/dash`**（故 `sh start.sh` 与 `bash start.sh` 行为不同，见 §7 R3）；
- `file start.sh` → `Bourne-Again shell script, Unicode text, UTF-8 text executable`（无 CRLF）；
- **CRLF 的后果已复现**：`/usr/bin/env: 'bash\r': No such file or directory`
  （首轮审查曾声明此项在其本机无法复现）；
- `fc-match 宋体/仿宋/黑体` 一律落到 **DejaVu Sans**（非 CJK）→ §8.3 的"字族缺失"前提成立；
  但 `fc-match ':charset=4e2d'` → **Droid Sans Fallback**（`fonts-droid-fallback` 已装，由
  `libgs10-common` 拉入）→ 中文**仍会被画出来**，只是字形不对，故"豆腐块"的说法必须收窄（见 §7 R5）。

**仍未验证**：deadsnakes PPA 下 `python3.13-venv` 的可用性；`playwright install --with-deps` 的实际安装；
Chromium 的真实渲染与 `pdffonts` 判据；NapCat 真机行为（按约束未连接）；容器镜像内的 Python 版本；
**真实 Ubuntu 服务器**（WSL 是用户态，不是部署机）；无 systemd 容器里 `timedatectl` 的替代路径。

**换行符保护（5.1，用户裁定后追加）**：新增 `.gitattributes`，规则只有 `*.sh text eol=lf` 一条
（外加三行注释说明为何只锁 `*.sh`：`cmd.exe` 对 LF-only 的 `.bat` 有已知的标签/goto 解析问题，
故**不**写 `* text=auto eol=lf`）。实测：`git check-attr text eol -- start.sh` → `text: set` / `eol: lf`；
`git check-attr text eol -- start.bat` → 两条都是 `unspecified`（未被 `*.sh` 规则命中）；
`start.sh` 工作区仍 2216 字节、CR 计数 0；`git add .gitattributes` 后 `git diff --cached --stat`
只有 `.gitattributes | 6 ++++++`；四条验收复跑全绿、无副作用。

**残留（不在本变更任务范围，供后续变更决定）**：`AGENTS.md` 的验收命令示例与
`tests/fixtures/README.md` 的抓取命令仍是 Windows 口径。

---

## 6. 独立审查与处置留痕

**审查结论：有条件通过**（独立只读子 Agent，与实施方不共享上下文）。报出 **15 条**缺陷（D1–D15）。
设计智能体**逐条独立复现**，全部成立，处置如下。

### 6.1 已确认属实、必须修的（交实施子 Agent 修复批次）

| ID | 严重度 | 定性 | 处置 |
|---|---|---|---|
| D1 ⚠️ | 中 | **文档顺序不可执行** | §8.2 让跑 `.venv/bin/python -m playwright install --with-deps chromium`，而 `.venv` 与 playwright 包要到 §8.4 才存在 → 必得 `No such file or directory`；8.4 里重复的那条又不带 `--with-deps`，共享库被漏 → 首次推送浏览器启动失败。**已把 `--with-deps` 整条移到 8.4 的 pip install 之后**，并要求通读 §8.2→§8.9 确认可顺序执行 |
| D2 ⚠️ | 中 | **文档自相矛盾** | 三条安装路径里的 `uv venv` 建的 venv **不含 pip**，而 8.4 用 `python -m pip` → `No module named pip`。改为 `uv venv --seed …` 并点明"不带 `--seed` 的 uv venv 没有 pip" |
| D3 ⚠️ | 中 | **漏掉唯一"打开功能"的键** | 必改表列了 `deepseek_api_key` 却漏了 `notice_targets`。**实测**：模板 `notice_targets: []` 时 `check_config` 返回的 dict **连一个 notice 键都没有**（静默关闭、零报错、不注册定时任务）→ 运维照 §8 走完全程功能根本没开，而 §8.8 第 5 步的"首次只建基线"会进一步掩盖。**已补进必改表并写明"没填它第 5 步不会有任何 `[干跑]` 日志、也不会有任何报错"** |
| D4 ⚠️ | 中 | **兜底方案与硬门槛冲突** | 容器兜底镜像基于的 Ubuntu 自带 Python < 3.13，与本仓库 `requires-python = ">=3.13"` 冲突，且文档未写 tag。改为**不依赖具体 tag 的判据**："进容器先 `python3 -V`，必须 ≥3.13；否则容器内用 uv / deadsnakes 装 3.13"（**不许**写未核实的 tag） |
| D6 ⚠️ | 低 | **文档与事实不符** | README 原写 `file start.sh` 应显示 `ASCII text`；**实测**为 `Bourne-Again shell script, Unicode text, UTF-8 text executable`（脚本含中文）。改为"应显示 `UTF-8 text` 且**不带** `with CRLF line terminators`"（后者才是真判据 —— 对照 `start.bat` 实测带 `with CRLF`） |
| D7 | 低 | 措辞 | "6 个定点"是作者本机 `config.yaml` 的值，模板 `config.example.yaml:36` 只有 **3** 个。去掉硬编码数字 |
| D8 | 低 | 措辞 | 把后果说成"PDF 静默丢失"**不准确**：`napcat.py:322-333` 对异常与 `retcode != 0` 都 `log.error`（含 retcode/wording）。真正静默的是"**群里看不出** + 该条照记账、**不重试**"。已改为精确表述 |
| D9 | 低 | 措辞 | "两种做法都不会补推历史"过强：baseline 判据是"该**栏目**在表里没有任何行"，带库迁移时库非空 → 首页中旧库未登记的条目会按增量推一次。改为"不会**全量**补推" |
| D10 | 低 | 注释与实现不符 | 测试 docstring 把 `Scripts\python` 列为会被拒的反例，但正则要求 `.venv` 前缀 → 该例根本不会被拒。已要求"换真会被拒的例子，或把断言做强"，且**实跑正则验证每个反例** |
| D12 | 低 | 措辞 | `/etc/fonts/local.conf` 在 Ubuntu 上已存在（空壳），文档给的是整文件内容 → 补"在既有 `<fontconfig>` 内追加，别丢原有条目" |
| D14 | 低 | 健壮性（未文档化的用法） | 实测 `source ./start.sh`：`$0` 是调用方 shell → `cd` 变 no-op、`set -u` **泄漏进调用方 shell**、`exec` 直接替换调用方 shell。**已在 `set -u` 之前加 `BASH_SOURCE[0] != $0` 守卫**（放前面才能同时避免泄漏） |

### 6.2 设计智能体裁定为"不在本变更处理"

| ID | 定性 | 裁定的理由 |
|---|---|---|
| **D5** | **未使用代码（零生产调用）** | 审查独立确认：`render_env_error` / `playwright_install_hint` / `playwright_missing_hint` 构成**闭环死簇**，装配链 `__main__.py` 与 `handlers/notice.py` 里没有任何环境探测。**不构成规格违反** —— 主规格「配置校验」枚举的运行前提不含"浏览器可用"，浏览器不可用走的是「PDF 生成失败 → 降级只发文本」那条 SHALL；README 也从未宣称启动期探测浏览器。**但接线或删除都属行为变更**，与本变更 `skip_specs: true`（只改文档/提示/工具）矛盾，故**留待后续变更**，不在本次顺手改 |
| D11 | 断言偏宽（已被补偿） | `tests/test_notice_pdf.py:189` 在本机走 `message is None` 分支（浏览器已装）而**空过**；但同批次新增的参数化用例用 monkeypatch 强行走 ImportError 分支、逐字断言两平台文案，已覆盖该分支。故**不动**（该断言也是设计智能体明确要求实施方**不得放宽**的那条） |
| D13 | 建议（可执行位） | 审查建议 `git add --chmod=+x start.sh` 把 100755 记进索引，克隆到 Linux 即有可执行位。**由设计智能体在提交阶段执行**（属暂存动作，不是文件改动）；README 的 `chmod +x` 保留为兜底 |
| D15 | 留痕不精确 | 已在上方 1.4 的验证句就地更正并留痕 |

### 6.3 审查附带发现的一条**既有缺陷**（本变更范围外，但必须留痕）

审查在验证死代码时发现：`src/cea_bot/notices/pdf.py:167` 的
`from playwright.async_api import async_playwright` **在 `try` 之外**（`try` 从 `:170` 才开始）。
若 playwright **包**缺失（如部分安装/`--no-deps`），`render_pdf` 抛的是 `ModuleNotFoundError`
而**不是** `PdfRenderError`，而 `handlers/notice.py:369` 只捕 `(PdfRenderError, OSError)`
→ **整轮中止、连文本都不发**，恰好违背主规格「PDF 生成失败 SHALL 降级为只发文本」那条 SHALL。

- **这是既有问题，不是本变更引入**（本变更只搬运了提示字符串）；
- 讽刺的是 `pdf.py:185-187` 的注释正在警告这件事（"若放任它们以原类型冒出…结果是 PDF 失败 →
  整轮中止 → 连文本都没发"），而它上方几行的 import 正是那个漏网路径；
- 触发条件罕见（playwright 是声明依赖，正常安装不会缺），但**一旦命中就是规格违反**；
- 修法很简单（把 import 收进 `try`，或让 `render_env_error` 真正接进启动路径），
  **但那是行为变更，必须走自己的变更** —— 已连同 D5 一并提请用户决定是否立刻立新变更。

**审查明确判为无问题的项**（备后续参考）：`start.sh` 六个控制流场景（含从 `/` 调用仍切目录、
退出码原样透传、路径含空格、相对子目录调用）；`.gitattributes` 只 `*.sh` 且 `.bat` 不可能被命中；
`conftest.py` 确实只 import `sys`/`pytest`；平台事实只在一处读（`src/` 内 `sys.platform` 零命中）；
**行为中立经旧实现对比验证**（10 组配置在默认参数下 `SystemExit` 文案与返回 dict **逐字全等**，
`is_windows=True` == 缺省）；mypy 确实覆盖新模块（24 = `src/` 下全部 .py）。

**审查自述的盲区**：无 Ubuntu 环境（§8 命令与字体方案均未实机验证）；CRLF → `bad interpreter`
未在本机复现（MSYS 对 `bash\r` 宽容）；`--with-deps` 的 sudo 交互未实跑；容器镜像 Python 版本
证据不足（故 D4 标"存疑"而非"有误"）；NapCat 真机语义未验证（任务禁止连接）。

---

## 7. 第二轮独立审查与处置留痕

**结论：有条件通过**。第一轮 11 条修复中 **9 条改到根上**，但发现 **D12 修过了头**、
**D14 的守卫引入回归**，另有两处会让运维卡住/误判的整段缺失。报出 **R1–R14**。

### 7.1 设计智能体的失职留痕（必须写明）

**R4（`/etc/fonts/local.conf`）是我造成的，而且我违反了自己正在执行的那条契约。**

首轮审查称"Ubuntu 自带 `/etc/fonts/local.conf`（内容为空壳）"。**我没有核实就把它写进了修复指令**，
要求实施方改成"该文件已存在，在既有 `<fontconfig>` 内追加"。实测（真实 Ubuntu 24.04.3）：

```
$ [ -e /etc/fonts/local.conf ]   → ★ 不存在
$ dpkg -S /etc/fonts/local.conf  → no path found matching pattern
$ dpkg -L fontconfig-config | grep local
    /etc/fonts/conf.d/51-local.conf   → 内容为 <include ignore_missing="yes">local.conf</include>
```

即：该文件**默认不存在**（被 `51-local.conf` 以 `ignore_missing="yes"` 可选包含）；
而实施方照我的指令给出的片段**缺 `<fontconfig>` 根元素**，粘进新建空文件会
`junk after document element` + `Cannot load config file` —— **别名静默不生效**，
恰是这份文档最不该出现的失败形态。

**教训**：`dsh_cooperation.md` 明写"审查结论**不被照单全收**，设计智能体必须二次检查"。
我要求别人这么做，自己却对首轮审查里一条**用 `dpkg -S` 就能证伪**的断言照单全收，
还把错误前提传给了实施方。第二轮审查（另一个独立上下文）一条命令就证伪了它 ——
这恰好说明为什么审查必须换人做、以及"二次检查"为什么不能省。

### 7.2 处置

| ID | 严重度 | 定性 | 处置 |
|---|---|---|---|
| **R1** | **高** | **缺失步骤** | §8 全文没有"仓库如何上机"（无 `git clone`/`scp`），而 §8.4 第一条就是 `cd /srv/cea-bot`；§8.1 还写着"仓库就位后"却从未说明如何就位 → **全新机器在此卡住**。**补「取仓库」步骤**（含 `git` 未装时的 apt、clone、`chown`） |
| **R2** | **中** | **文档与实现不符 / 缺步骤** | §8.8 第 5 步让"先干跑、再关掉干跑"，但**干跑也会记账**（`claim_and_mark` 在试运行分支之外）→ **那批通知永远不会真正发出**；切回 `false` 后若无新通知，群里什么都没有 → 运维**误判成出站故障**。设计方实机操作时**自己踩过这个坑**，而文档没写 → 在 §8.8 与 §7 都写明，并给出"用临时 `notice_db_path` 预演"的正确做法 |
| **R3** | **中** | **实现缺陷（回归）** | `start.sh` 守卫在 dash 下从"友好提示 rc=1"退化为 **`Bad substitution` rc=2**；且 `bash < start.sh` **fail-open**（守卫提示后 `return` 失效、继续执行，仓库有 venv 时会真的 exec）。**设计方也在 WSL 上独立复现了两者** → 守卫前加 POSIX 安全的 bash 判定；中止改用两种场景都正确的写法；README 补"不要用 `sh start.sh`"；**要求九种调用姿势逐一实跑**（不许只 `bash -n`） |
| **R4** | 低 | **文档与事实不符（我的指令所致）** | 见 7.1 → 改为"通常不存在、需新建且**必须含 `<fontconfig>` 根**；已存在则在其中追加"，并**在 WSL 里实做加载验证**（旧片段作对照必须报错） |
| **R5** | 低 | 措辞说过头 | "不装就是豆腐块"与两条独立证据冲突：① 设计方实测 `fonts-droid-fallback` 已装且覆盖中文（`fc-match ':charset=4e2d'`）；② 审查证 `--with-deps` 会带 `fonts-wqy-zenhei` → 改为"要么由碰巧存在的 CJK 回退字体画出（**字形不对**），要么在无任何 CJK 字体的最小安装上才是豆腐块"，并点明**别名才是字形正确的关键** |
| R6 | 低 | 缺失（外部前置） | §8.8 第 3 步依赖 NapCat 就绪，但 §8 无其安装/登录说明 → 补范围声明 |
| R7 | 低 | 缺运维提示 | 前台 `./start.sh` 随 SSH 断开会终止，而第 5 步要求"到点"仍活着 → 提 `tmux`/`nohup`（**不引入 systemd**，用户明确不做） |
| R8 | 低 | 引用漂移 | `README.md:221` 与 `pyproject.toml` 仍写"见 8.2"，而命令已移到 8.4 → 改引用 |
| R9 | 低 | 前置未说明 | uv 装到 `~/.local/bin`，当前 shell 可能找不到 → 提重开 shell / source |
| R10 | 低 | 边缘不自洽 | uv 路径跳过 8.2 会连带跳过唯一的 `apt update` → 在 8.3 前补一句 |
| R11 | 低 | 容器兜底不自洽 | 容器里通常没有 sudo，而 §8.8 第 1 步用 `sudo apt` → 补"容器内用 root 或去掉 sudo" |
| R12 | 低 | 口径残余 | "最多一页条数"只对**首页新增**成立；旧库中 `pushed_at IS NULL` 的历史失败条目也会补推 |
| R13 | 低 | 协调项 | 设计方将在提交时 `git add --chmod=+x start.sh` → "克隆后 Permission denied"的叙述随之过时，改为"只有 `scp` 拷贝时才需 `chmod +x`" |
| R14 | 低 | 措辞 | `ensure_notice_dirs` 仅在**启用通知推送时**探测 → 补限定 |

### 7.3 设计方另行补入的两条（非审查所提）

- **W2（与 R5 相互印证）**：设计方用 `fc-match ':charset=4e2d'` 验证"会不会豆腐块"，
  认识到应按**字符覆盖**而非**字族存在**判断，得出与 R5 相同的结论 ——
  两条**互相独立**的证据链指向同一处措辞问题。
- **W3**：README §8 抬头与本节原写的"未在真实 Ubuntu 上验证过"**已部分不准确**，
  改为逐项列明"哪些已实证、哪些仍未验证"（见上方「诚实声明」段）。

### 7.4 审查明确复核通过的

第一轮 11 条修复中的 **D1/D2/D3/D4/D6/D7/D8/D9/D10 九条**经第二轮独立复现确认改到根上
（含用探针复验 `check_config` 空 `notice_targets` 的行为、`file start.sh` 形态、baseline 判据等）。
另有一批文档断言被逐条核对为**无误**：`due_jobs` 用 `datetime.now()`、NapCat 读本机路径、
YAML 双引号 `\D` 会报错、POSIX 反斜杠路径是单层目录名、`pdf_path` 只写不读、`config.yaml` 已 gitignore。

**第二轮审查自述的盲区**：无真实 Ubuntu **服务器**（结论均来自 WSL2 用户态）；Ubuntu 上未跑 pytest
（Windows venv）；字体/Chromium 未端到端渲染；NapCat 未连接；变更未提交故只能审当前工作区
（两个未提交变更同树，验收覆盖两者）；uv 安装器的 PATH 与最小镜像的 `add-apt-repository` 未实测。

