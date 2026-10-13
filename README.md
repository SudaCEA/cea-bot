# CEA Bot

平台无关的 QQ 机器人框架（`cea`）+ 业务应用（`cea_bot`），支持两个可互换的平台前端：
QQ 官方开放平台（`qq-botpy`）与 NapCat（OneBot 11 正向 WS）。

仓库采用 **src-layout**：`src/cea/` 是框架核心，`src/cea_bot/` 是业务应用。
`src/` 不在解释器的模块搜索路径里，**必须先安装（editable）才能 import 任何东西**；
"换 cwd 也能跑"这个可移植性来自这次安装，而不是"恰好站在仓库根"。

## 1. 首次安装

需要 Python 3.13+（与 `pyproject.toml` 的 `requires-python` 对齐）。

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]" --index-url https://pypi.org/simple
```

两条说明：

- **`--index-url https://pypi.org/simple` 不能省**：清华镜像暂缺 `qq-botpy`，只走镜像会装不上。
- **`-e`（editable）不能省**：src-layout 下未安装时 `import cea` / `import cea_bot` 全部失败，
  连 `pytest` 都起不来（`tests/conftest.py` 会打印上面这条安装命令并以非 0 退出，而不是丢一段裸 traceback）。

安装后自检：

```bat
.venv\Scripts\python -c "import cea, cea_bot, cea_bot.handlers.basic, aiohttp, yaml; print('ok')"
```

依赖与工具配置的**唯一真相源**是根目录 `pyproject.toml`（`requirements.txt` 已删除）。

## 2. 配置

```bat
copy config.example.yaml config.yaml
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

也可以直接双击 `start.bat`：它会 `cd /d "%~dp0"` 到仓库根、自检 `.venv` 与包是否已安装，
未安装时打印安装提示并以非 0 退出（不会继续跑到裸 traceback）。

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

- 测试用 `asyncio.run(...)` 包裹协程场景，**不引入 `pytest-asyncio`**；
- `pytest` 直跑与 `python -m pytest` 均为 225 passed（数量随用例增长；两种姿势都必须绿）；
- 未安装项目时 `tests/conftest.py` 会让 `pytest` 以非 0 退出并打印安装提示。

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

三点注意：

- 该工具在 `.local/` 下（已被 `.gitignore` 排除，不随版本库分发）；
- 它的依赖**不在 `pyproject.toml` 里**（工具自带依赖，避免为一个离线脚本给运行时背包袱），
  需要另行安装：
  `.venv\Scripts\python -m pip install pillow svglib reportlab --index-url https://pypi.org/simple`；
- 该脚本只写 `assets/cea-avatar.png`；`assets/cea-2026.png` 是同一脚本把底部
  标识改成「2026」后的产物，重跑上面的命令**不会**覆盖它。

## 7. 校内通知推送（`notice-digest`）

定时检查校内通知栏目，把**新发布**的条目整理成「要点摘要 + PDF」推送到指定会话。
该功能**默认关闭**：`notice_targets` 留空时完全不注册定时任务、不联网。

### 启用三步

1. 安装 PDF 渲染内核（Playwright 的浏览器二进制不入版本库，只装一次；约 300MB）：

```bat
.venv\Scripts\playwright install chromium
```

2. 在 `config.yaml` 里按需填写（既有键不动）：

```yaml
notice_targets: ["12345"]                    # 群号；也支持 "c2c:用户号" / "channel:子频道号"
notice_type: 1                               # 1 校内通知 / 2 学校发文 / 3 学术报告
notice_times: ["09:00", "15:00", "21:00"]    # 每日检查定点（本地时区）
deepseek_api_key: "sk-..."                   # 摘要用；只写在 config.yaml（已被 .gitignore 排除）
```

3. 重启 bot：`.venv\Scripts\python -m cea_bot`。

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
| `notice_max_chars` | `1500` | 正文/摘要长度上限，超出即截断并在消息中注明"已截断" |
| `notice_dry_run` | `false` | 试运行：照常抓取/摘要/渲染 PDF，但**不发送任何消息**，只把将发送的文本与 PDF 路径写日志 |

### 行为要点

- **只抓列表首页**（20 条），且只对**未推送过**的条目抓详情；不翻页、不回溯历史。
- **首次运行只建立基线**：把首页条目登记为已推送并记日志，**不推送历史**；从下一次定点起推增量。
- **不重复推送**：增量状态存在 SQLite 里，进程重启、连续定点重叠都仍然有效（重叠的那次跳过并记日志）。
- **解析到 0 条 = 页面改版**：报错中止本次并记日志，**不会**被当成"没有新通知"。
- **摘要失败降级**：模型超时/报错时发送截断后的正文原文，并在消息里注明"摘要不可用"（绝不发空消息）。
- **PDF 失败降级**：只发文本并在消息里注明；该条仍按已推送记账，不会无限重试。
- **只把正文文本发给模型**：附件仅发送**名称**，不发送内容。
- **QQ 官方前端不支持文件消息**：此时只发文本并在消息中说明（NapCat 前端支持文件）。
- **重置状态**：删除 `data/state/cea_bot.db` 后重启，会按"首次运行"重新建立基线（不补推历史）。

### 互斥与频率

同一进程内两次检查不会并发执行（拿不到锁的那次直接跳过并记日志）。框架不内置限流原语，
抓取频率完全由 `notice_times` 决定：每次运行只发出 1 个列表请求 + 每个新增条目 1 个详情请求。

### 成本与合规

- 摘要走 `deepseek-flash`：一条四五千字的通知约 4k 输入 + 0.5k 输出 token，按官方价格约
  **0.006–0.012 元/条**（空闲/高峰时段有差价），每天几条时每月不到一元。
- **合规提示**：校内通知**正文会发送到第三方云端 API**。若不可接受，把 `deepseek_base_url`
  指向自建的 OpenAI 兼容端点即可（`deepseek_api_key` 填自建服务的密钥，不能留空或以 `YOUR_` 开头）。

## 目录结构

```
src/cea/               框架核心（平台无关：消息模型、inbound/outbound 双总线、Adapter 契约、
                       Router 装饰器、共享时钟、优雅退出）
src/cea/adapters/      两个可互换的平台前端（qqbotpy / napcat）
src/cea_bot/           业务应用（config.py / assembly.py / __main__.py）
src/cea_bot/store.py   业务层持久化底座（SQLite 开库 / PRAGMA / 建表登记）
src/cea_bot/handlers/  业务 handler（basic.py 为用法范本，notice.py 为校内通知推送）
src/cea_bot/notices/   校内通知推送的领域实现（fetch / parse / schema / summarize / pdf）
tests/                 单测（framework / napcat / layout / store / notice*）
tests/fixtures/        通知页面的真实原始字节样本（离线解析测试用）
data/                  运行数据（SQLite 状态库与 PDF 归档；已 gitignore，不入库）
openspec/              规格与变更
pyproject.toml         依赖与工具配置的唯一真相源
start.bat              Windows 启动脚本（等价于 python -m cea_bot）
```
