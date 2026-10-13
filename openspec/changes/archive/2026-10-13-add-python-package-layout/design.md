# Design: add-python-package-layout

## Context

动机与改动清单见 `proposal.md`。此处只写影响技术路线的现状约束：

- **包边界已经存在于规格，但不在目录上。** `messaging` 的"平台前端契约"要求"更换或新增平台前端实现 SHALL NOT 要求修改框架核心与业务层代码"；`dispatching` 把 handler 定义为纯异步函数。但 `cea/` 与 `handlers/` 是平级顶层目录，依赖方向（`handlers` → `cea`，反向禁止）全靠自觉，没有任何机制强制。
- **导入完全依赖 cwd。** `python -m` 会把 cwd 注入 `sys.path[0]`，这正是当前 53 个测试能跑起来的唯一原因；`pytest` 直跑即失败（见 proposal 的实测表）。
- **`main.py` 承担三种职责**：配置校验（纯函数，已被 `tests/test_napcat.py` 测试）、adapter 工厂（可测）、CLI 入口（薄壳）。测试以 `from main import build_adapter, check_config` 直接 import 顶层脚本。
- **直接依赖未声明**：`cea/adapters/napcat.py` import `aiohttp`、`main.py` import `yaml`。
- **mypy 有 4 条噪音**：`botpy` / `botpy.api` / `botpy.message`（无 stub）与 `yaml`（未装 `types-PyYAML`）。
- `config.yaml` 含真实凭据且已被 `.gitignore` 排除；重构**不得**触碰它。

## Goals / Non-Goals

**Goals:**

- 依赖方向从"自觉"变成"结构"：`cea_bot` → `cea` 单向，反向 import 在目录上一眼可见
- 测试口径收窄为一种：`pytest` 与 `python -m pytest` 都必须通过，且从任意 cwd 通过
- `mypy` 输出零噪音，可作为验收命令长期使用

**Non-Goals:**

- 不改任何 `SHALL` 行为语义（26 条需求零 delta）
- 不改 `config.yaml` 的键名、位置或校验规则
- 不重命名公开 API（`App` / `Router` / `Bot` / `Message` 等导出名保持不变）
- 不引入新运行时依赖；不改两个平台前端的任何逻辑
- 不做 CI、不做 pre-commit、不发布到 PyPI

## Decisions

**D1. src-layout，而非 flat + 打包元数据。** `src/` 不在 `sys.path` 里，只有安装后才能 import —— "在我机器上能跑"的假阳性被结构性排除，且测的正是"安装后用户拿到的东西"。备选 flat + `pyproject.toml`（改动最小，但拿不到隔离收益，且 `main` 会变成一个叫 `main` 的顶层模块，是个埋雷的名字）；`lib/` 布局（非主流，工具链支持差）。代价：开发前必须 `pip install -e .`，否则什么都 import 不到，由 README"首次安装"一步覆盖。

**D2. 双包 `cea`（框架）+ `cea_bot`（应用），而非单包。** 拆开后 `messaging` 那条"换平台不改业务"的承诺在目录上可见。备选：单包内含 `handlers/` 子包（框架与业务混装，边界糊掉）；三包 `cea` / `cea_bot` / `cea_tools`（`tools/` 已退出版本库，过度设计）。命名备选 `bot_app` / `ceabot` / `app`，选 `cea_bot`：PEP 8 模块命名（小写 + 下划线），与仓库名 `cea-bot` 一一对应。

**D3. 入口用 `python -m cea_bot`，不设 console-script。** 这是跑在自己机器上的 bot，不是发布到 PyPI 的库；`python -m` 不依赖 entry point 生成，`start.bat` 一行搞定。备选 `[project.scripts] cea-bot = "cea_bot.__main__:main"`（更标准，但多一层安装假设，本项目无收益）。影响：`start.bat` 由 `.venv\Scripts\python main.py` 改为 `.venv\Scripts\python -m cea_bot`。

**D4. `main.py` 按可测性分界拆为三块，而非按文件长度。**

| 去处 | 内容 | 为什么单独存在 |
|---|---|---|
| `cea_bot/config.py` | `check_config` / `load_config` | 纯函数 + IO 边界，已被测试直接依赖 |
| `cea_bot/assembly.py` | `build_adapter` | 工厂；保留对两个前端的**延迟 import**，避免选 napcat 时仍拉起 qq-botpy |
| `cea_bot/__main__.py` | `main()` + `__main__` 守卫 | 薄壳，不写逻辑、不测 |

病灶是 `tests/test_napcat.py:15` 的 `from main import ...`。拆开后测试导入的是正经模块。备选：全部塞进 `cea_bot/__init__.py`（污染包命名空间）；`cli.py`（本项目无子命令，名不副实）。

**D5. `pyproject.toml` 是唯一依赖真相源，`requirements.txt` 删除。** 直接依赖 `qq-botpy==1.2.1` / `aiohttp>=3.9` / `PyYAML>=6.0`；`dev` 组 `pytest>=8.0` / `mypy>=2.0` / `types-PyYAML`。理由：`aiohttp` 与 `PyYAML` 是直接 import，现在只靠 `qq-botpy` 传递，qq-botpy 一改依赖树就崩。备选：`requirements.txt` 留 `-e .[dev]` shim 或与 pyproject 双写 —— 两处真相迟早分叉，都不取。`pillow` / `svglib` / `reportlab` **不进** `pyproject`：它们只服务已退出版本库的 `tools/make_avatar.py`，改由该文件 docstring 自述依赖（工具携带自己的依赖）。

**D6. mypy 配置写进 `pyproject.toml` 的 `[tool.mypy]`，按模块精确放行。** 对 `botpy.*` 设 `ignore_missing_imports = true`（无 stub 且非本项目可控）；`yaml` 靠 `types-PyYAML` 解决。让 `mypy` 成为零噪音验收命令 —— 否则 4 条噪音会训练人忽略输出。备选：逐行 `# type: ignore`（散落易漏）；全局 `ignore_missing_imports = true`（把真问题一起藏掉）。

**D7. 可移植性来自 editable install，`tests/conftest.py` 不做 `sys.path` 手脚。** "任意 cwd 可跑"是 `pip install -e .` 的结果，不是 conftest 的功劳 —— 这一点必须写准，否则实施会误以为要靠 `sys.path` 补路径。`src/` 布局下任何 `sys.path.append` 都是在对抗打包，且会掩盖"忘记安装"这个真问题。因此 `conftest.py` 只承担一件事：`import cea` 失败时给出"请先 `pip install -e \".[dev]\"`"的快速失败提示，把裸 `ModuleNotFoundError` 变成可执行的下一步。备选：`conftest.py` 里 `sys.path.insert(0, .../src)`（治标）；干脆不要 conftest（"忘记安装"时只有裸 `ModuleNotFoundError`，无行动指引）。

**D8. `tools/make_avatar.py` 移入 `.local/tools/`，不新建 `tools/` 包。** 离线资产生成器，与运行时无关；`.local/` 已被 `.gitignore` 覆盖，天然不入库。备选：留在 `tools/` 并标注为非打包目录（仍要为其维护依赖）；直接删除（产出物 `assets/` 还在，留着可复现）。

**D9. `assets/` 留在版本库。** 0.11MB，是 bot 的身份资产（logo / 头像）；产出物与产出工具可以分离，可复现性由 `.local/tools/make_avatar.py` 承担。备选：一并移入 `.local/`（资产随机器丢失）；移入包内作 package-data（无代码引用，是死重）。

## Risks / Trade-offs

- [移动后 import 断链，尤其是两个前端的延迟 import 与 `cea/__init__.py` 的重导出] → 验收口径包含 `python -m cea_bot` 真实启动 + `mypy` 全量检查，不只跑单测
- [`pip install -e .` 成为开发前置，未安装则什么都跑不了] → README 首步写明；`start.bat` 启动前自检并给出安装提示
- [`tools/` 移出后，头像生成的知识只剩该文件 docstring] → README 标注 `assets/` 的来源与再生成方式
- [删除 `requirements.txt` 使既有 `pip install -r` 习惯失效] → README 安装节写新命令，并保留"清华镜像缺 `qq-botpy`、须用官方源"提示
- [mypy overrides 若写宽会掩盖真类型问题] → 只对 `botpy.*` 精确放行，`yaml` 走 `types-PyYAML`
- [53 个测试通过 ≠ 结构对；测试本身 import 路径也要跟着改] → 明确改写的导入点见 tasks，并以"直跑 `pytest`"作为验收口径（它现在是红的，改完必须变绿）

## Migration Plan

1. **基线**：在旧结构下确认 `.venv\Scripts\python -m pytest -q` 为 53 passed，记录为对照
2. **移动**：`cea/` → `src/cea/`；`main.py` + `handlers/` → `src/cea_bot/`（按 D4 拆三块）；`tools/` → `.local/tools/`
3. **新增**：`pyproject.toml`、`tests/conftest.py`、`README.md`
4. **删除**：`requirements.txt`
5. **安装**：`.venv\Scripts\python -m pip install -e ".[dev]"`（qq-botpy 须用官方源）
6. **验收**（四条全过才算完）：
   - `pytest` **直跑** → 53 passed（当前为 0 collected，这条是核心指标）
   - `python -m pytest` → 53 passed（回归，确保没改坏原有姿势）
   - `mypy src` → 零报错
   - `python -m cea_bot` → 装配日志与 `python main.py` 一致（adapter 校验报错文案**除命令/路径 token 外**逐字一致；token 改动口径见 `tasks.md` 2.2②，即 `.venv\Scripts\python main.py` → `.venv\Scripts\python -m cea_bot`、`cea/adapters/napcat.py` → `src/cea/adapters/napcat.py`）
7. **回滚**：`git revert` 本变更提交。无数据迁移，`config.yaml` 不动，两个平台前端逻辑不动

## Open Questions

（无——`assets/` 去留、`tools/` 归属、入口形态、依赖声明方式均已在探索期定案。）
