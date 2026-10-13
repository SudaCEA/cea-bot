# Proposal: add-python-package-layout

## Why

仓库不是可安装的 Python 包：没有 `pyproject.toml`，`cea/`、`handlers/`、`main.py` 三者平铺在根目录，能否导入完全取决于"cwd 恰好是仓库根"。

实测证据（2026-09-24）：

| 命令 | 结果 |
|---|---|
| `python -m pytest`（仓库根） | 28 collected ✓ |
| `pytest`（**同在仓库根**） | ERROR，0 collected ✗ |
| `pytest`（其它 cwd，传绝对路径） | `ModuleNotFoundError: No module named 'cea'` ✗ |
| `python -c "import main"`（其它 cwd） | `ModuleNotFoundError: No module named 'main'` ✗ |

即：换人、换机器、换 IDE 一旦不是"仓库根 + `python -m`"这一种姿势，测试立刻全红。同时 `cea/adapters/napcat.py` 直接 `import aiohttp`、`main.py` 直接 `import yaml`，两者都未在 `requirements.txt` 声明，仅靠 `qq-botpy` 的传递依赖碰巧带进来。

首次提交 `2766a3e` 已建立版本库起点（72 文件 / 7286 行，GPG 已签），现在做结构重构的回溯成本最低。

## What Changes

- 新增 `pyproject.toml`：唯一的依赖真相源，声明**直接**依赖 `qq-botpy` / `aiohttp` / `PyYAML`，另设 `dev` 可选组（pytest / mypy / types-PyYAML）与 `[tool.mypy]` 配置（消除 4 条 `import-untyped` 报错）
- **BREAKING** 布局改为 src-layout：`cea/` → `src/cea/`
- **BREAKING** 拆出应用包 `src/cea_bot/`，`main.py` 与 `handlers/` 合并消除：
  - `src/cea_bot/config.py` ← `main.py` 的 `load_config` / `check_config`
  - `src/cea_bot/assembly.py` ← `main.py` 的 `build_adapter`
  - `src/cea_bot/__main__.py` ← `main.py` 的 `main()`；启动命令由 `python main.py` 改为 `python -m cea_bot`
  - `src/cea_bot/handlers/basic.py` ← `handlers/basic.py`
- 新增 `tests/conftest.py`：只做「包未安装」的快速失败提示，**不**做 `sys.path` 操作。可移植性来自 editable install（测试从任意 cwd 可跑）
- 新增 `README.md`：安装、配置、启动、如何新增 handler；`requirements.txt` 里"清华镜像缺 `qq-botpy`、须用官方源"的提示迁入此处
- 移除 `requirements.txt`（被 `pyproject.toml` 取代）
- 移除 `tools/`：`make_avatar.py` 移入 `.local/tools/`，随已忽略的 `.local/` 退出版本库；其依赖 `pillow` / `svglib` / `reportlab` 同步退出依赖清单，改为写进该工具自身的 docstring（工具自己携带自己的依赖）
- 保留 `assets/`、`config.example.yaml`、`config.yaml`（仍被忽略）、`start.bat`（改为 `python -m cea_bot`）

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

（无）

本变更为**纯结构重构**：26 条需求的 `SHALL` 语义一条未改，仅移动文件、拆分模块、补齐打包元数据。配置文件仍留在根目录，故 `qqbot-adapter` 中"凭据 SHALL 从 `config.yaml` 读取"的表述无需改动。因此在 `.openspec.yaml` 置 `skip_specs: true`，不新增任何需求。

## Impact

- **代码**：`cea/` → `src/cea/`；`main.py` + `handlers/` → `src/cea_bot/`（4 个模块）；`tools/` 退出版本库
- **依赖**：`aiohttp`、`PyYAML` 由隐式传递改为显式声明；`pillow` / `svglib` / `reportlab` 随 `tools/` 退出
- **启动**：`python main.py` → `python -m cea_bot`（`start.bat` 同步改写）
- **测试**：`tests/test_napcat.py` 的 `from main import build_adapter, check_config` → 改从 `cea_bot.config` / `cea_bot.assembly` 导入
- **文档**：新增 `README.md`；同步 `AGENTS.md`（仓库既有的智能体约定入口，按 `tasks.md` 3.5 更新「代码在哪」等章节）；改写 `start.bat`；`.gitignore` 增加 `*.egg-info/`（`pip install -e .` 会在源码树生成该目录）
- **未触碰**：`dsh_cooperation.md` 的改动**不属于**本变更——它在变更开始前就已在工作区存在，仅因未提交而出现在 `git status` 里，勿与其一并提交
- **规格**：零 delta（`skip_specs: true`）
- **风险**：① 移动后 import 断链 → 以 53 个测试 + `pytest` **直跑**（非 `python -m`）为验收口径；② 入口变更影响既有运维习惯 → README 与 `start.bat` 双路径兜底；③ `assets/` 与 `tools/` 分离后，头像产出物与产出工具不在同一处 → 在 README 标注来源关系
