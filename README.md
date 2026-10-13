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
- `pytest` 直跑与 `python -m pytest` 均为 54 passed（两种姿势都必须绿）；
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

## 目录结构

```
src/cea/               框架核心（平台无关：消息模型、inbound/outbound 双总线、Adapter 契约、
                       Router 装饰器、共享时钟、优雅退出）
src/cea/adapters/      两个可互换的平台前端（qqbotpy / napcat）
src/cea_bot/           业务应用（config.py / assembly.py / __main__.py）
src/cea_bot/handlers/  业务 handler（basic.py 为用法范本）
tests/                 单测（framework / napcat / layout）
openspec/              规格与变更
pyproject.toml         依赖与工具配置的唯一真相源
start.bat              Windows 启动脚本（等价于 python -m cea_bot）
```
