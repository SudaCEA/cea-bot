# CEA Bot

平台无关的 QQ 机器人框架（`cea`）+ 业务应用（`cea_bot`），支持两个可互换的平台前端：
QQ 官方开放平台（`qq-botpy`）与 NapCat（OneBot 11 正向 WS）。

仓库采用 **src-layout**：`src/cea/` 是框架核心，`src/cea_bot/` 是业务应用。
`src/` 不在解释器的模块搜索路径里，**必须先安装（editable）才能 import 任何东西**；
"换 cwd 也能跑"这个可移植性来自这次安装，而不是"恰好站在仓库根"。

## 1. 首次安装

需要 Python 3.13+（与 `pyproject.toml` 的 `requires-python` 对齐）。
**Ubuntu 24.04 自带的是 3.12**，Linux 上要先装 3.13 —— 完整步骤见 [8. 在 Ubuntu 上部署](#8-在-ubuntu-上部署)。

Windows（cmd / PowerShell）：

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]" --index-url https://pypi.org/simple
```

Ubuntu / POSIX：

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]" --index-url https://pypi.org/simple
```

两条说明：

- **`--index-url https://pypi.org/simple` 不能省**：清华镜像暂缺 `qq-botpy`，只走镜像会装不上。
- **`-e`（editable）不能省**：src-layout 下未安装时 `import cea` / `import cea_bot` 全部失败，
  连 `pytest` 都起不来（`tests/conftest.py` 会打印上面这条安装命令并以非 0 退出，而不是丢一段裸 traceback）。

安装后自检（上面哪条对你，就用哪条的解释器）：

```bat
.venv\Scripts\python -c "import cea, cea_bot, cea_bot.handlers.basic, aiohttp, yaml; print('ok')"
```

```bash
.venv/bin/python -c "import cea, cea_bot, cea_bot.handlers.basic, aiohttp, yaml; print('ok')"
```

依赖与工具配置的**唯一真相源**是根目录 `pyproject.toml`（`requirements.txt` 已删除）。

## 2. 配置

```bat
copy config.example.yaml config.yaml
```

```bash
cp config.example.yaml config.yaml
```

`config.yaml` 含真实凭据、已被 `.gitignore` 排除，**不要提交**；键名与位置也别改
（框架固定读仓库根的 `config.yaml`）。

| 键 | 取值 / 语义 |
|---|---|
| `adapter` | `qqofficial`（缺省，QQ 官方开放平台）或 `napcat`（OneBot 11 / NapCat） |
| `group_whitelist` | 群号字符串列表。**留空 = 不过滤**；只约束群聊，**私聊不受限**；**改后重启生效** |
| `appid` / `secret` | `adapter: qqofficial` 用。AppID / AppSecret 取自 <https://q.qq.com/qqbot/#/developer/developer-setting> →「开发设置」页 |
| `ws_url` / `token` | `adapter: napcat` 用。NapCat 正向 WS 地址（如 `ws://127.0.0.1:3001`）与鉴权 token |

NapCat 侧前置配置（WebUI → 网络配置 → 新建 → **WebSocket 服务端**），三条缺一不可：

1. 监听 host / port（如 `0.0.0.0:3001`），与 `config.yaml` 的 `ws_url` 对应；
2. 启用 token（公网部署必开），与 `config.yaml` 的 `token` 保持一致；
3. 消息格式 `messagePostFormat` **必须选 `array`**（本前端按消息段数组解析）。

配置不合法时启动会打印明确文案并以非 0 退出，例如缺 `appid` / `secret`（qqofficial）、
缺 `ws_url`（napcat）、`adapter` 取值未知。

## 3. 启动

```bat
.venv\Scripts\python -m cea_bot
```

```bash
.venv/bin/python -m cea_bot
```

也可以直接双击 `start.bat`（Windows）：它会 `cd /d "%~dp0"` 到仓库根、自检 `.venv` 与包是否已安装，
未安装时打印安装提示并以非 0 退出（不会继续跑到裸 traceback）。
Ubuntu 上对应的是 `./start.sh`（或 `bash start.sh`），三件事完全对齐 —— 见
[8. 在 Ubuntu 上部署](#8-在-ubuntu-上部署) 的第 8.7 小节。

启动成功后装配日志形如（数字随 handler 数量变化）：

```
应用启动，共 1 个命令 / 1 个消息处理器 / 1 个定时任务
```

Ctrl+C 优雅退出（先置停止信号、丢弃未发消息，再取消任务）。

## 4. 如何新增 handler

在 `src/cea_bot/handlers/` 下新建模块（仿 `basic.py`）：模块级建一个 `Router()`，
用装饰器挂载**纯异步函数**，然后在 `src/cea_bot/__main__.py` 里 `include_router`。

```python
# src/cea_bot/handlers/my.py
from cea import Bot, Chat, Message, Router

router = Router()

# 业务侧自管状态：主动消息（定时任务）没有入站 msg，会话要自己记着
_last_chat: dict[str, Chat] = {}


@router.command("echo")                          # 命令 handler：签名 (bot, msg, args)
async def echo(bot: Bot, msg: Message, args: list[str]) -> None:
    await bot.send(msg.chat, text=" ".join(args), reply_to=msg.id)
    _last_chat["chat"] = msg.chat


@router.on_message()                             # 全量消息 handler：签名 (bot, msg)
async def spy(bot: Bot, msg: Message) -> None:
    ...


@router.schedule("12:00")                        # 定时任务：签名 (bot)，每日 HH:MM（本地时区）
async def noon(bot: Bot) -> None:
    chat = _last_chat.get("chat")
    if chat is not None:
        await bot.send(chat, text="午间播报：CEA Bot 运行中")
```

注册（`src/cea_bot/__main__.py`）：

```python
from cea_bot.handlers import basic, my

app = App(adapter=build_adapter(cfg), group_whitelist=cfg["group_whitelist"])
app.include_router(basic.router)
app.include_router(my.router)
```

签名与语义要点：

- `@router.command("a", "b")` 可绑多个触发词别名；匹配是**剥离 @ 后的首词精确匹配**（无子串模糊），
  按路由挂载顺序取第一个命中，其余词作为 `args` 传给函数；
- `@router.schedule("HH:MM")` 规则格式错或越界会立刻 `ValueError`；
  进程离线错过的触发点不补跑，同一分钟只触发一次；
- 每个命中的 handler 都在独立协程里跑，异常由框架记日志（`log_done`），不影响其它 handler；
- 业务层只依赖 `cea`，**不要**在 handler 里 import 平台 SDK 或框架内部实现；
- 出站消息 `text` / `image` / `file` 三选一互斥（同时给出即 `ValueError`）；
  被动回复传 `reply_to`，主动消息不传。

## 5. 测试与类型检查

```bat
.venv\Scripts\pytest -q             # 直跑
.venv\Scripts\python -m pytest -q   # 回归：两种姿势都要绿
.venv\Scripts\python -m mypy        # 类型检查（[tool.mypy] 的 files = ["src"]，裸跑即全量）
openspec validate --all             # 规格与变更校验
```

```bash
.venv/bin/python -m pytest -q       # Ubuntu / POSIX：命令同上，只换解释器路径
.venv/bin/python -m mypy
```

- 测试用 `asyncio.run(...)` 包裹协程场景，**不引入 `pytest-asyncio`**；
- `pytest` 直跑与 `python -m pytest` **两种姿势都必须绿**（刻意不在此处登记用例数：
  它会随每次变更漂移，写死只会把人误导成"回归"）；
- 未安装项目时 `tests/conftest.py` 会让 `pytest` 以非 0 退出并打印安装提示
  （提示里的解释器路径按平台生成：Windows `.venv\Scripts\python`、POSIX `.venv/bin/python`）。

## 6. `assets/` 的来源与再生成

`assets/` 是机器人的身份资产，随版本库分发：

| 文件 | 来源 |
|---|---|
| `assets/cea-logo.svg` | 字标源文件（唯一的手工源） |
| `assets/cea-avatar.png` | 由该 SVG 渲染的 1024×1024 方形头像（底部标识「QQ BOT」） |
| `assets/cea-2026.png` | 同一渲染流程的 1024×1024 产物（底部标识「2026」） |

再生成是离线操作，不参与运行时与测试：

```bat
.venv\Scripts\python .local\tools\make_avatar.py
```

```bash
.venv/bin/python .local/tools/make_avatar.py
```

三点注意：

- 该工具在 `.local/` 下（已被 `.gitignore` 排除，不随版本库分发）；
- 它的依赖**不在 `pyproject.toml` 里**（工具自带依赖，避免为一个离线脚本给运行时背包袱），
  需要另行安装（Windows：`.venv\Scripts\python -m pip install ...`；Ubuntu：`.venv/bin/python -m pip install ...`）：
  `pip install pillow svglib reportlab --index-url https://pypi.org/simple`；
- 该脚本只写 `assets/cea-avatar.png`；`assets/cea-2026.png` 是同一脚本把底部
  标识改成「2026」后的产物，重跑上面的命令**不会**覆盖它。

## 7. 校内通知推送（`notice-digest`）

定时检查校内通知栏目，把**新发布**的条目整理成「要点摘要 + PDF」推送到指定会话。
该功能**默认关闭**：`notice_targets` 留空时完全不注册定时任务、不联网。

### 启用三步

1. 安装 PDF 渲染内核（Playwright 的浏览器二进制不入版本库，只装一次；约 300MB）：

```bat
.venv\Scripts\python -m playwright install chromium
```

```bash
.venv/bin/python -m playwright install chromium
```

统一用 `python -m playwright`（不是 `Scripts\playwright.exe` / `bin/playwright`）：
包内自带 `__main__.py`，故两个平台是同一条命令，只有解释器路径不同。
Ubuntu 上还要先装 Chromium 的系统共享库 —— 见 [8. 在 Ubuntu 上部署](#8-在-ubuntu-上部署) 的
**8.4**（`.venv/bin/python -m playwright install --with-deps chromium`，**需要 sudo**）。

2. 在 `config.yaml` 里按需填写（既有键不动）：

```yaml
notice_targets: ["12345"]                    # 群号；也支持 "c2c:用户号" / "channel:子频道号"
notice_type: 1                               # 1 校内通知 / 2 学校发文 / 3 学术报告
notice_times: ["09:00", "15:00", "21:00"]    # 每日检查定点（本地时区）
deepseek_api_key: "sk-..."                   # 摘要用；只写在 config.yaml（已被 .gitignore 排除）
```

3. 重启 bot：`.venv\Scripts\python -m cea_bot`（Ubuntu：`.venv/bin/python -m cea_bot`）。

### 配置项一览

| 键 | 缺省 | 说明 |
|---|---|---|
| `notice_targets` | `[]` | 推送目标；**留空 = 关闭该功能**。支持 `"<kind>:<id>"` 或裸 `"<id>"` |
| `notice_target_kind` | `group` | 裸会话号按哪种会话推送（`group` / `c2c` / `channel`） |
| `notice_type` | `1` | 栏目：1 校内通知、2 学校发文、3 学术报告 |
| `notice_times` | `["09:00","15:00","21:00"]`（模板值） | 每日定点；**代码侧没有缺省**：启用后缺键或留空一律报错（留空 = 永不触发） |
| `notice_db_path` | `data/state/cea_bot.db` | 增量状态库（SQLite，首次运行自动建库） |
| `notice_pdf_dir` | `data/notices` | PDF 归档目录 |
| `deepseek_api_key` | 无（必填） | 摘要服务密钥；启用该功能时缺失会在启动阶段快速失败 |
| `deepseek_model` | `deepseek-flash` | 摘要模型名 |
| `deepseek_base_url` | `https://api.deepseek.com` | OpenAI 兼容端点；可指向自建服务 |
| `notice_max_chars` | `1500` | 正文/摘要长度上限，超出即截断并在消息中注明"已截断"；**必须为正整数**（0 等于把正文截空） |
| `notice_max_per_run` | `0`（不限制） | 单次检查**最多成功投递**多少条；达到上限本轮停止，剩余条目由下个定点继续投（不丢失）。建议 3～5。与上一行相反：这里的 `0` 是合法的"不限制"；**留空 / 空串 / 纯空白同样等于不限制**（静默按 `0` 处理，不报错） |
| `notice_dry_run` | `false` | 试运行：照常抓取/摘要/渲染 PDF，但**不发送任何消息**，只把将发送的文本与 PDF 路径写日志。**注意：干跑同样会记账** —— `claim_and_mark` 在试运行分支**之外**无条件执行，那批条目在状态库里已算"已推送"，切回 `false` 后**不会再发**。想"预演又不吃掉真条目"，把 `notice_db_path` 指向一个**临时库**（首跑同样只建基线、不推历史）。详见 [8.8](#88-分层验收清单按顺序做前一层不过就别往后走) 第 5 步 |

### 行为要点

- **只抓列表首页**（20 条），且只对**未推送过**的条目抓详情；不翻页、不回溯历史。
- **单次投递可控**：`notice_max_per_run` 限制每次检查**成功投递**的条数（缺省不限制）；达到上限后
  本轮停止，剩余条目保持"未推送"、由下个定点继续投（日志会报"剩余 N 条待下次定点"）。
  处理失败的条目**不占**上限名额——否则一个持续失败的最新条目会把更旧的条目永远挤在后面。
  **注意：上限只约束成功投递条数，不约束单轮处理量**——待推送集合取自整张状态表
  （`pushed_at IS NULL`，不限于列表首页 20 条），故站点持续故障（条目不断累积）时，
  单轮发出的详情请求次数会随积压线性增长；上限只能约束"成功投递"这一步。
- **首次运行只建立基线**：把首页条目登记为已推送并记日志，**不推送历史**；从下一次定点起推增量。
- **不重复推送**：增量状态存在 SQLite 里，进程重启、连续定点重叠都仍然有效（重叠的那次跳过并记日志）。
- **解析到 0 条 = 页面改版**：报错中止本次并记日志，**不会**被当成"没有新通知"。
- **摘要失败降级**：模型超时/报错时发送截断后的正文原文，并在消息里注明"摘要不可用"（绝不发空消息）。
- **PDF 失败降级**：只发文本并在消息里注明；该条仍按已推送记账，不会无限重试。
- **PDF 归档命名**：产物名为 `YYMMDD-<通知标题>.pdf`（如 `261001-关于XX的通知.pdf`），标题取自
  列表页。标题里的文件系统非法字符（`< > : " / \ | ? *`、控制字符，以及结尾的点与空格）会被
  剔除，**中文完整保留**；标题过长截断到 80 字符；日期缺失用 `000000-` 开头，标题净化后为空
  写成 `未命名`。名字里不再有 `editId`，故同日同标题会撞名——**覆盖已有文件前记 WARNING**
  （不静默覆盖）。**代价要说清**：同日同标题 → 同名 → **后者覆盖前者**，先到那份的 PDF 内容
  就此丢失，归档里只剩一个文件，而状态库里有两条记录的 `pdf_path` 指向它。
  **历史产物不迁移**：改动前生成的 `2026-10-13-40775683.pdf` 之类旧名文件保持
  原样，状态库里旧的 `pdf_path` 也不回填。
- **只把正文文本发给模型**：附件仅发送**名称**，不发送内容。
- **QQ 官方前端不支持文件消息**：此时只发文本并在消息中说明（NapCat 前端支持文件）。
- **重置状态**：删除 `data/state/cea_bot.db` 后重启，会按"首次运行"重新建立基线（不补推历史）。
  老版本可能把非 ASCII 日期（如全角 `２０２６-１０-０１`）原样写进状态库，这类行仍会排在队首；
  处置与重置状态一致 —— 先备份状态库，再删除重建基线（不做自动迁移）。

### 互斥与频率

同一进程内两次检查不会并发执行（拿不到锁的那次直接跳过并记日志）。框架不内置限流原语，
抓取频率完全由 `notice_times` 决定：每次运行只发出 1 个列表请求 + 每个新增条目 1 个详情请求。

### 成本与合规

- 摘要走 `deepseek-flash`：一条四五千字的通知约 4k 输入 + 0.5k 输出 token，按官方价格约
  **0.006–0.012 元/条**（空闲/高峰时段有差价），每天几条时每月不到一元。
- **合规提示**：校内通知**正文会发送到第三方云端 API**。若不可接受，把 `deepseek_base_url`
  指向自建的 OpenAI 兼容端点即可（`deepseek_api_key` 填自建服务的密钥，不能留空或以 `YOUR_` 开头）。

## 8. 在 Ubuntu 上部署

目标：照着一路装到"群里真的收到通知与 PDF"。每一步都写了**判据** —— 命令跑完该看到什么，
而不是"跑过就算"。

**诚实声明（逐项列明，不写笼统的"没验证过"）**。以下已在**真实 Ubuntu 24.04.3（WSL2 用户态）**
上实测：

- `python3 -V` = **3.12.3**，`python3.13` **不存在** → 8.1 的硬门槛成立；
- `python3.13-venv` **不在**默认源（未加 deadsnakes 时）；`fonts-noto-cjk` 在 `noble/main`；
- `/bin/sh` → **`/usr/bin/dash`**（故 `sh start.sh` 与 `bash start.sh` 行为不同，见 8.7）；
- `file start.sh` → `Bourne-Again shell script, Unicode text, UTF-8 text executable`（无 CRLF）；
- **CRLF 的后果确实如此**：报 `/usr/bin/env: 'bash\r': No such file or directory`；
- `fc-match 宋体/仿宋/黑体` 落到 **DejaVu Sans**（非 CJK）；而 `fc-match ':charset=4e2d'` →
  **Droid Sans Fallback**（中文仍会被画出来，只是字形不对，见 8.3）；
- 8.3 那套 fontconfig 片段：**能加载、别名确实生效**（`fc-pattern -c` 判据）；而缺
  `<fontconfig>` 根元素的旧片段报 `junk after document element` + `Cannot load config file`
  （对照组原始输出见 8.3）。

**仍未验证**：deadsnakes PPA 的实际可用性；`playwright install --with-deps` 的实际安装；
Chromium 的真实渲染与 `pdffonts` 判据；NapCat 真机行为（按约束未连接）；容器镜像内的 Python 版本。
以及最关键的一条：**真实 Ubuntu 服务器** —— 上面的实测全部来自 WSL2 **用户态**，
它**不是一台部署机**（网络、systemd、QQ 登录、对校内的真实抓取都没有走过）。

### 8.1 前置条件（两条硬门槛）

**① Python ≥ 3.13。** `pyproject.toml` 写死 `requires-python = ">=3.13"`，而
**Ubuntu 24.04 只自带 Python 3.12** —— 用 3.12 建 venv，`pip install -e` 阶段就会被直接拒掉。
三条可选路径：

- **deadsnakes PPA**（最贴近 apt 口径）：

  ```bash
  sudo add-apt-repository ppa:deadsnakes/ppa
  sudo apt update && sudo apt install -y python3.13 python3.13-venv
  ```

- **uv**（自带解释器下载，最省事）：

  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # 这里只装 uv；建 venv 放到 8.4 的②（仓库就位后）：uv venv --seed --python 3.13 .venv
  ```

  **装完当前 shell 可能找不到 `uv`**：安装器把它放进 `~/.local/bin`，而该目录不一定已在
  `PATH` 里（会得到 `uv: command not found`）。**重开一个 shell**，或先执行
  `export PATH="$HOME/.local/bin:$PATH"`（写进 `~/.bashrc` 可持久化）。

  上面那句"仓库就位后"指的是 **8.4 的第 ① 步（取仓库）** ——
  顺序是：装 uv → **取仓库（8.4 ①）** → 在仓库里建 venv（8.4 ②）。

- **Ubuntu 25.04+，或直接用 3.13 为基底的容器**：系统源里就是 3.13，无需 PPA。

**判据**：系统解释器路径下 `python3.13 -V` 输出 `Python 3.13.x`；uv 路径则在 8.4 建好 venv 后
用 `.venv/bin/python -V` 确认。**不要**用 3.12 硬跑：本仓库从未在 3.12 上验证过。

**② NapCat 必须与 bot 同机**（架构约束，不是建议）。PDF 的投递形态是"把**本机绝对路径**
原样交给 NapCat 去读"（见 `openspec/specs/napcat-adapter/spec.md` 的「文件消息收发」：
文件源引用转成 `file` 消息段，由 NapCat 读取发送）。若 NapCat 留在原机器、只有 bot 搬到 Linux，
现象是**文本到达、PDF 只在日志里报错、群里看不出，且该条已记账不会重试** —— 最难查的一类失败。
故 `config.yaml` 必须是 `ws_url: ws://127.0.0.1:<端口>`，NapCat 也装在本机。

### 8.2 系统依赖

```bash
sudo apt update
# venv 模块（Debian 系单独打包；包名随 3.13 的来源而异：
# deadsnakes 与 25.04+ 都是 python3.13-venv，不确定时先试它）
# 走 8.1 的 uv 路径可跳过本行：uv 自带解释器，不需要系统的 venv 包。
sudo apt install -y python3.13-venv
```

**若 8.1 走的是 uv 路径，本步可整段跳过**：uv 会用它自己下载的 3.13 解释器建 venv，
而在未加 deadsnakes 的 Ubuntu 24.04 上系统源里并没有 `python3.13-venv`。
容器路径也跳过本步：直接按下面的 `python3 -V` 判据确认版本即可。

Playwright 的无头 Chromium 还依赖一批系统共享库（`libnss3`、`libatk`、字体等）。
**`playwright install` 不放在 8.2**：它要用 `.venv/bin/python`，而 `.venv` 到 8.4 才创建、
`playwright` 包也要先经 pip 安装 —— 在这里跑只会得到 `No such file or directory`。
8.4 的 `playwright install --with-deps chromium` 会顺带用 apt 装好这些库（**需要 sudo**）。

拿不到 sudo 时，可改用官方基底镜像 `mcr.microsoft.com/playwright/python` 兜底
（Chromium 与共享库都已就位），在容器里装本项目即可。
**但 Python 版本必须自己确认**：官方镜像的基底 Ubuntu 版本随 tag 而变，未必自带 3.13；
而本仓库 `requires-python = ">=3.13"`，低于它 `pip install -e` 会被直接拒掉。
本文**不指定具体 tag**（无法确证哪个 tag 满足门槛）。进容器先跑 `python3 -V`，**必须 ≥ 3.13**；
不满足就在容器内用 uv 或 deadsnakes 再装 3.13。不要假定 `latest` 或任何未经你实测的 tag 就是 3.13。

**容器里的两条差异**（照抄宿主机的 `sudo …` 会直接失败）：

- **容器里通常没有 `sudo`**：容器一般直接以 root 跑，把本节、8.4 与 8.8 里的 `sudo` 去掉即可
  （root 环境里往往根本没装 `sudo`，会报 `sudo: command not found`）。
- **容器里走 deadsnakes 同样要 `python3.13-venv`**：`add-apt-repository ppa:deadsnakes/ppa`
  只带来解释器本体，`venv` 模块在 Debian 系是**单独打包**的 —— 容器里也必须
  `apt install -y python3.13 python3.13-venv`（与 8.2 宿主机的做法一致），
  否则 `python3.13 -m venv .venv` 会以 `ensurepip is not available` 之类报错收场。

### 8.3 中文字体（硬前置 —— 不装就会字形不对或直接豆腐块）

通知正文带 Word 内联字族（`仿宋` / `宋体` / `黑体` / `等线` / `仿宋_GB2312`）。
Windows 上这些字族能落到系统自带的 SimSun / FangSong / MicrosoftYaHei / SimHei；
**Linux 上没有这些字体**。

**先把"不装会怎样"说准**（把后果说过头与说轻一样有害）。Ubuntu 上通常**已经**有一个碰巧存在的
CJK 回退字体：`fonts-droid-fallback`（由 `libgs10-common` 拉入，实测 `fc-match ':charset=4e2d'`
→ **Droid Sans Fallback**），或者 `fonts-wqy-zenhei`（`playwright` 在 ubuntu24.04 的
nativeDeps 清单里含它，按 8.4 走就会装上）。所以真实后果是两种，
**两种都不是你要的效果**：

- 系统里有这类回退字体时：中文**会**被画出来，但**字形与公文体例不符** —— `仿宋`/`宋体`
  这类衬线公文字被换成黑体风格的无衬线回退字形；
- 在**没有任何 CJK 字体**的最小安装上：直接是 `□□□` 豆腐块，而消息照发、`retcode=0`、
  日志无异常 —— 这是本仓库最忌讳的**静默失败**。

**让 `仿宋`/`宋体`/`黑体` 落到正确字体上，靠的是下面那套 fontconfig 别名**；
`fonts-noto-cjk` 提供的是**别名要指向的目标字族**。两者缺一不可 ——
别名不是"可选装饰"，它是"字形正确"的关键（而没有 `fonts-noto-cjk`，别名只是指了个空目标，
匹配会回退到上面那种碰巧存在的字体）。

**① 装目标字族：**

```bash
sudo apt install -y fonts-noto-cjk
```

**若前面没跑过 `apt update`（例如 8.1 走了 uv、8.2 整段跳过），先跑一次** ——
精简镜像上包列表可能是空的，`apt install` 会报"无法定位软件包"：

```bash
sudo apt update
```

**② 建别名。`/etc/fonts/local.conf` 通常不存在，需要新建**（实测 Ubuntu 24.04.3：
`[ -e /etc/fonts/local.conf ]` 为假、`dpkg -S /etc/fonts/local.conf` 找不到任何路径；
`fontconfig-config` 只装 `/etc/fonts/conf.d/51-local.conf`，其内容是
`<include ignore_missing="yes">local.conf</include>` —— 即该文件是被**可选包含**的）。

所以下面给的是**整文件内容**，**必须含 `<fontconfig>` 根元素**：把 `<match>` 片段直接粘进一个
新建的空文件，fontconfig 会报 `junk after document element` + `Cannot load config file`，
**别名静默不生效**（正是这份文档最不该出现的失败形态）。实测原始输出：

```text
Fontconfig error: "/tmp/fc2/old-local.conf", line 5: junk after document element
Fontconfig error: Cannot load config file from /tmp/fc2/old-local.conf
```

```bash
sudo nano /etc/fonts/local.conf      # 新建，粘贴下面的整文件内容
```

```xml
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <!-- 宋体/仿宋系 → Serif；黑体/等线系 → Sans -->
  <match target="pattern">
    <test name="family"><string>宋体</string></test>
    <edit name="family" mode="assign" binding="strong"><string>Noto Serif CJK SC</string></edit>
  </match>
  <match target="pattern">
    <test name="family"><string>仿宋</string></test>
    <edit name="family" mode="assign" binding="strong"><string>Noto Serif CJK SC</string></edit>
  </match>
  <match target="pattern">
    <test name="family"><string>仿宋_GB2312</string></test>
    <edit name="family" mode="assign" binding="strong"><string>Noto Serif CJK SC</string></edit>
  </match>
  <match target="pattern">
    <test name="family"><string>黑体</string></test>
    <edit name="family" mode="assign" binding="strong"><string>Noto Sans CJK SC</string></edit>
  </match>
  <match target="pattern">
    <test name="family"><string>等线</string></test>
    <edit name="family" mode="assign" binding="strong"><string>Noto Sans CJK SC</string></edit>
  </match>
</fontconfig>
```

**③ 若你的系统上该文件已存在**（有人先前加过配置）：**不要整文件覆盖**，否则会丢掉原有条目 ——
在既有的 `<fontconfig>…</fontconfig>` 内**追加**上面那些 `<match>` 条目（用 `sudoedit` 打开）。

**④ 让字体缓存与配置生效，并按判据确认：**

```bash
sudo fc-cache -fv
fc-pattern -c -f '%{family}\n' 宋体   # 判据：只输出 Noto Serif CJK SC
fc-match 宋体                          # 判据：输出 Noto Serif CJK SC（需已装 fonts-noto-cjk）
```

两条判据的分工要说清：`fc-pattern -c` 做的是**配置替换**（即"别名有没有生效"），
**与是否装了目标字体无关**，所以它是别名这一步的硬判据；`fc-match` 做的是**真实匹配**，
没装 `fonts-noto-cjk` 时它会回退到别的字体（实测落到 `DejaVu Sans`）——
那不是别名没生效，而是目标字族不存在。

**说明与边界**：别名是"尽量贴近"，不宣称与 Windows 排版逐像素一致。
本次已在真实 Ubuntu 24.04.3（WSL2）上实测：上面这份**整文件片段**用
`FONTCONFIG_FILE=<临时文件>` **能加载、五个字族的别名全部生效**
（`fc-pattern -c -f '%{family}'` 对 `宋体`/`仿宋`/`仿宋_GB2312` 输出 `Noto Serif CJK SC`、
对 `黑体`/`等线` 输出 `Noto Sans CJK SC`）；而缺根元素的旧片段如上所示报错、别名不生效。
**最终字形是否可读仍要你用眼睛看** —— 判据见 8.8 第 1 步（需要装了 `fonts-noto-cjk`，
本机未装，故此处的"目标字体真的被选中"未验证）。

### 8.4 安装

**① 取仓库（本节其余命令都要求"在仓库根执行"，所以这一步必须先做）。**
机器上还没有 `git` 就先装：

```bash
sudo apt update && sudo apt install -y git      # 容器里去掉 sudo，见 8.2
```

然后 clone 到 `/srv/cea-bot`（下文各节都按这个目录写；换目录也行，但要前后一致）：

```bash
cd /srv && sudo git clone https://github.com/SudaCEA/cea-bot   # /srv 属 root，不加 sudo 会 Permission denied
sudo chown -R "$USER" /srv/cea-bot        # clone 出来的文件属 root，后面的 venv 与运行数据都要写
cd /srv/cea-bot
```

**不用 git 时也可以把整个目录 `scp` 上去**（两种方式各有代价，见 8.7）：

```bash
scp -r ./cea-bot user@server:/srv/cea-bot
```

**② 建 venv 并装依赖**：

```bash
python3.13 -m venv .venv              # 8.1 用了 uv 的话，这一行换成 uv venv --seed --python 3.13 .venv
.venv/bin/python -m pip install -e ".[dev]" --index-url https://pypi.org/simple
.venv/bin/python -m playwright install --with-deps chromium   # --with-deps 会走 apt 装共享库，需要 sudo
```

**用 uv 建 venv 必须带 `--seed`**：`uv venv` 的既有行为是**不安装 pip**，不带 `--seed` 时
下一行的 `.venv/bin/python -m pip` 会直接报 `No module named pip`。
（`--python 3.13` 会让 uv 自动准备 3.13 解释器，不必再走 8.1 的 deadsnakes。）

**`--index-url https://pypi.org/simple` 必须显式指定**：清华镜像暂缺 `qq-botpy`，只走镜像会装不上。

**判据**：

```bash
.venv/bin/python -c "import cea, cea_bot, cea_bot.handlers.basic, aiohttp, yaml; print('ok')"
```

输出 `ok`（这正是 `start.sh` 第二条自检做的事；失败时它会打印可执行的安装命令并以非 0 退出）。

### 8.5 配置

```bash
cp config.example.yaml config.yaml
```

`config.yaml` 含密钥、已被 `.gitignore` 排除，**不要提交**。Linux 上**必须改**的四项：

| 键 | 改成 | 为什么 |
|---|---|---|
| `adapter` / `ws_url` / `token` | `adapter: "napcat"`；`ws_url: "ws://127.0.0.1:<端口>"`；`token` 与 NapCat 一致 | NapCat 必须与 bot 同机（8.1 ②） |
| `notice_targets` | `["<群号>"]`（留空 = 关闭该功能） | 通知推送的总开关；模板默认 `[]`，空值**不报错**，只是整个功能静默关闭 |
| `notice_pdf_dir` | Linux **绝对路径**，如 `"/srv/cea-notices/pdf"` | 见下方警告：Windows 绝对路径在 Linux 上不会报错，只会静默失效 |
| `deepseek_api_key` | 真实 key | 启用通知推送时缺失会启动即报错 |

**没填 `notice_targets`，§8.8 第 5 步不会有任何 `[干跑]` 日志，且不会有任何报错。**
`check_config` 在它为空时**不返回任何 notice 键**（`config.example.yaml` 的默认值正是
`notice_targets: []`），于是定时任务根本不会注册 —— 功能是关的，不是"没到点"。
先把它填上再往下走。

**警告：`D:\...` 在 Linux 上是一个合法的相对文件名，不是错误。**

```yaml
# ✗ 错误：从 Windows 的 config.yaml 直接抄过来（注意是**单引号** —— YAML 双引号里 \D 是非法转义，
#   会当场解析报错；单引号/裸标量才能解析成这个字符串，于是静默失效）。
#   反斜杠在 Linux 上只是普通字符，`D:\Ds_...\notices` 是**单层目录名**：
#   程序不会报错，只会在仓库里建出一个名字带反斜杠的怪目录；
#   再把这个名字交给 NapCat —— NapCat 读不到 → 文本到达、PDF 只在**日志**里报错、群里看不出、该条已记账不重试。
notice_pdf_dir: 'D:\Ds_Projects\Python\cea-bot\data\notices'

# ✓ 正确：Linux 绝对路径（目录要存在或可创建，且运行用户可写）
notice_pdf_dir: "/srv/cea-notices/pdf"
```

同一个坑也适用于 `notice_db_path`（默认 `data/state/cea_bot.db` 是**相对路径**，以 bot 的
工作目录为基准 —— `start.sh` 会先切到仓库根，故相对默认值是安全的；但若你把它写成 `D:\...`，
SQLite 会静默建出一个怪名字的库文件）。

**注意**：本变更**不动**这两个键的默认值（改默认缓存路径属另一个 change），
所以"平台正确的绝对路径"必须由你在 `config.yaml` 里显式写。

目录与权限：

```bash
sudo mkdir -p /srv/cea-notices/pdf
sudo chown "$USER" /srv/cea-notices/pdf
```

启动时 `ensure_notice_dirs` 会**真实写入探测**这两个目录；不可创建或不可写会立刻报错并以非 0
退出（含对应的配置键名），而不是等到半夜推送时才发现。
**限定：这只在"启用通知推送"（`notice_targets` 非空）时发生** —— `notice_targets` 留空时
它直接返回、**完全不碰文件系统**（见 `src/cea_bot/config.py`），所以别把它的沉默当成"目录没问题"。

### 8.6 时区（不设就整体平移）

`notice_times` 是**本地时间**：调度判定用的是 `datetime.now()`（朴素本地时间，见
`src/cea/app.py` 的 `due_jobs(self._jobs, datetime.now())`）。Ubuntu 服务器常是 UTC，
不设时区会让所有 `notice_times` 定点**整体平移 8 小时**（例如 `09:00` 变成北京时间 17:00）。

```bash
sudo timedatectl set-timezone Asia/Shanghai
timedatectl          # 判据：Time zone: Asia/Shanghai (CST, +0800)
```

容器里没有 systemd 时用环境变量：`TZ=Asia/Shanghai`（启动 bot 时传入，或写进 compose / unit）。

### 8.7 启动

```bash
./start.sh            # 或：bash start.sh
```

**可执行位不用你操心**：本仓库交付时 `start.sh` 已带可执行位（提交时用
`git add --chmod=+x start.sh` 把 `100755` 记进了索引），所以 `git clone` 到 Ubuntu 后
**直接 `./start.sh` 就行**（clone 会带上可执行位）。想自查：`git ls-files -s start.sh`
应以 `100755` 开头。**只有当你不是克隆、而是用 `scp`/U 盘"拷贝文件"过去时**，才需要补一次：

```bash
chmod +x start.sh
```

**不要用 `sh start.sh`** —— Ubuntu 的 `/bin/sh` 是 **dash**，而本脚本需要 bash。
脚本开头会识别出这一点、给出可读提示并以非 0 退出（实测 `sh start.sh` → 提示 + 退出码 1，
**不会**误启动）。用 `./start.sh` 或 `bash start.sh`。

`start.sh` 与 `start.bat` 做**同样三件事**：① 切到脚本自身所在目录（不依赖调用方 cwd）；
② 自检 `.venv/bin/python` 存在、且 `import cea, cea_bot, cea_bot.handlers.basic, aiohttp, yaml`
通过，失败时打印含官方源的安装命令并以非 0 退出；③ `exec .venv/bin/python -m cea_bot`。

**不做 systemd**：本仓库只提供这一个 sh，开机自启 / 守护 / 日志轮转由部署者按需自行决定。
但**前台运行会被 SSH 断开带走**：`./start.sh` 是前台进程，SSH 会话一断它就收到 SIGHUP 一起结束，
而 8.8 第 5 步要求"到点"时进程还活着。所以请用 `tmux`（推荐）或 `nohup` 让它活过会话：

```bash
tmux new -s cea-bot './start.sh'          # 之后 `tmux attach -t cea-bot` 回来看日志
# 或：nohup ./start.sh > /var/log/cea-bot.log 2>&1 &
```

`nohup` 方式直接看日志文件；`tmux` 方式下**别按 Ctrl+C**（那会真的停掉 bot），
要脱离会话按 `Ctrl+B` 再按 `D`。
（仍然**不引入 systemd** —— 这里只给"会话保活"的手段，守护与开机自启仍由部署者决定。）

**换行必须是 LF**：`start.sh` 在版本库里是 LF（`.gitattributes` 已锁 `*.sh text eol=lf`）。
若你是从 Windows 工作区把它**拷贝**过去（而不是在 Ubuntu 上直接 `git clone`），
注意别让它变成 CRLF —— 那会让 `./start.sh` **根本起不来**（实测退出码 127）：

```text
/usr/bin/env: 'bash\r': No such file or directory
/usr/bin/env: use -[v]S to pass options in shebang lines
```

核对：`file start.sh` 应显示 `UTF-8 text`，且**不带** `with CRLF line terminators`。

**判据**：日志出现「应用启动，共 N 个命令 / N 个消息处理器 / N 个定时任务」，
且没有 WS 连接报错。Ctrl+C 优雅退出。

**注意：本仓库刻意不做启动期渲染环境探测** —— 探测要把 Node driver 拉起来（约 0.7 秒），
且会给启动引入一族与"早告警"这点收益不成比例的失败模式（详见该变更的 design D3）。
所以渲染依赖（`playwright` 包或浏览器内核）没装好时，**启动阶段不会有任何提示**：
要到第一次定点推送，日志里才会出现"PDF 生成失败，降级为只发文本"与可执行的安装命令，
而群里**只有文本、没有 PDF**。因此**部署时务必按 8.8 第 1 步先手动渲染一页中文确认**，
不要等到定点推送时才发现。

### 8.8 分层验收清单（按顺序做，前一层不过就别往后走）

1. **字体**（先做，否则后面"推送成功"可能只是一堆豆腐块）：
   用仓库自己的渲染器渲染一页中文，再检查 PDF 里到底有没有 CJK 字体：

   ```bash
   .venv/bin/python - <<'PY'
   import asyncio
   from pathlib import Path
   from cea_bot.notices.pdf import render_pdf

   html = '<p style="font-family: 仿宋; font-size: 16pt">校内通知：中文字体渲染自检</p>'
   asyncio.run(render_pdf(html, Path("/tmp/cjk-probe.pdf")))
   print("渲染完成：/tmp/cjk-probe.pdf")
   PY
   sudo apt install -y poppler-utils     # 容器里去掉 sudo（容器通常以 root 跑，往往没装 sudo）
   pdffonts /tmp/cjk-probe.pdf     # 判据：列出 NotoSerifCJK / NotoSansCJK 等中文字体
   ```

   最后**打开这个 PDF 用眼睛看**：中文可读、不是 `□`。两个判据都要过。

2. **依赖**：`.venv/bin/python -m pytest -q` 全绿（这一步只碰单测，不联网、不发消息）。
3. **连通**：把 bot 起在后台（**先做 8.7 的 `tmux`/`nohup` 那一步**，否则 SSH 一断进程就没了），
   然后看 NapCat WebUI 的「网络配置」里该 WS 服务端显示已连接；
   要更硬的证据，在 NapCat 的接口调试里发 `get_login_info`，**判据**是返回 `retcode: 0`。

   **这一步假定 NapCat 已经就绪**（已装好、QQ 已登录、WS 服务端已创建）。
   **NapCat 自身的安装、QQ 登录与 WS 服务端创建不在本节范围**，见 NapCat 官方文档；
   若 NapCat 已在同机跑着，直接进这一步即可。
4. **入站**：在群里发 `ping`，**判据**是收到 `pong`。
   注意 `group_whitelist` 留空 = 不过滤，非空时只有名单内的群会被响应。
5. **出站通知 —— 先读这段再动手：试运行会"吃掉"待推送条目。**

   **`notice_dry_run: true` 同样会记账。** 干跑分支（`[干跑] …将推送…` 那段日志）**之后**是
   无条件的 `claim_and_mark`（见 `src/cea_bot/handlers/notice.py`）—— 也就是说干跑把这些条目在
   状态库里标成了"已推送"，**切回 `false` 后那批条目永远不会真正发出**。若那时站点又没发布新
   通知，群里就什么都没有，而日志一切正常 —— 极易被误判成"出站坏了"。

   所以分两种做法，按你的目的选：

   - **只想预演、不想吃掉真条目** → 把 `notice_db_path` 指到一个**临时库**（例：
     `notice_db_path: "data/state/dryrun.db"`），配合 `notice_dry_run: true` 重启，到点看日志里
     "将发送的文本 + PDF 路径"，确认路径是 Linux 绝对路径、文件真的落在盘上。
     预演完把这两个键改回真实值。**注意临时库首跑同样只建立基线**（把首页条目登记为已推送、
     **不推历史**），所以它预演的是"下一个增量的样子"，不是"首页长什么样"。
   - **要真验收投递** → `notice_dry_run` 保持 `false`，到点后**去群里肉眼确认 PDF 真的出现**
     （`retcode=0` 只说明请求发出去了，不等于消息送达）。**若此前已用真实库干跑过一次，
     那批条目不会再发 —— 必须等站点发布新通知**，别把"这批没来"当成出站故障。

   另外两条时序上的坑：**首次运行只建立基线**（把首页条目登记为已推送，**不推历史**），
   所以第一个定点可能什么都不发、下一个定点才有增量；而**前台 `./start.sh` 到不了点**
   （SSH 断开即终止），请按 8.7 用 `tmux`/`nohup`。
   （前提：`notice_targets` 已在 8.5 填好；留空时这一步**不会有任何 `[干跑]` 日志，也不会有任何报错**。）

出问题时按同一顺序回退定位：字体 → 依赖 → 连通 → 入站 → 出站。

### 8.9 状态库可选迁移

`data/state/cea_bot.db` 是增量状态库（记录"哪些条目已推过"），**带不带过去都行**：

- **带过去**（`scp` 到同一相对路径）= 沿用"这些已经推过了"的记忆，不会因为换机器而重推；
- **不带** = 首跑按"首次运行"重建基线，同样**不推历史**。

两种做法都不会**全量**补推历史；带库迁移时，首页中旧库没登记过的条目仍会按增量推一次。

**"最多一页条数"只对"首页里新出现的条目"成立。** 待推送集合取自**整张状态表**
（`pushed_at IS NULL`，不限于列表首页 20 条，见 `src/cea_bot/notices/schema.py` 的 `pending()`），
所以旧库里**历史上处理失败**、`pushed_at` 仍为 `NULL` 的条目也会被一并补推；
积压多时，单轮发出的详情请求次数会随积压线性增长。
这与第 7 节「行为要点」里 `notice_max_per_run` 的口径一致：上限只约束**成功投递**条数，
不约束单轮处理量。库里的 `pdf_path` 列存的是 Windows 路径，但该列**只写不读**，
无害（详见 [7. 校内通知推送](#7-校内通知推送notice-digest) 的「行为要点」）。

## 目录结构

```
src/cea/               框架核心（平台无关：消息模型、inbound/outbound 双总线、Adapter 契约、
                       Router 装饰器、共享时钟、优雅退出）
src/cea/adapters/      两个可互换的平台前端（qqbotpy / napcat）
src/cea_bot/           业务应用（config.py / assembly.py / __main__.py）
src/cea_bot/platform_hints.py  平台相关的可执行提示（venv 解释器路径的唯一真相源）
src/cea_bot/store.py   业务层持久化底座（SQLite 开库 / PRAGMA / 建表登记）
src/cea_bot/handlers/  业务 handler（basic.py 为用法范本，notice.py 为校内通知推送）
src/cea_bot/notices/   校内通知推送的领域实现（fetch / parse / schema / summarize / pdf）
tests/                 单测（framework / napcat / layout / store / notice* / platform_hints）
tests/fixtures/        通知页面的真实原始字节样本（离线解析测试用）
data/                  运行数据（SQLite 状态库与 PDF 归档；已 gitignore，不入库）
openspec/              规格与变更
pyproject.toml         依赖与工具配置的唯一真相源
start.bat              Windows 启动脚本（等价于 python -m cea_bot）
start.sh               Ubuntu / POSIX 启动脚本（与 start.bat 三件事对齐，见第 8 节）
```
