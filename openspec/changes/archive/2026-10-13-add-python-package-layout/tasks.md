# Tasks

## 1. 打包元数据与类型检查基线

- [x] 1.1 新建 `pyproject.toml`：`[project]`（`name = "cea-bot"`、`requires-python = ">=3.13"`、`dependencies = ["qq-botpy==1.2.1", "aiohttp>=3.9", "PyYAML>=6.0"]`）、`[project.optional-dependencies] dev = ["pytest>=8.0", "mypy>=2.0", "types-PyYAML"]`、`[tool.setuptools.packages.find] where = ["src"]`；在 qq-botpy 条目旁加注释保留"清华镜像缺此包、须用官方源安装"的提示。验证：`.venv\Scripts\python -c "import tomllib;t=tomllib.load(open('pyproject.toml','rb'));print(t['project']['name']);print(t['project']['dependencies'])"` 输出 `cea-bot` 与三条依赖
- [x] 1.2 写 `[tool.mypy]` 与 `[[tool.mypy.overrides]]`（`module = ["botpy", "botpy.*"]`、`ignore_missing_imports = true`），并安装 `types-PyYAML`。验证：`.venv\Scripts\python -m mypy cea main.py` 的 `import-untyped` 由 4 条降为 0 条（对照基线：`botpy` 3 条 + `yaml` 1 条）

## 2. 结构迁移：src/ 双包

- [x] 2.1 `git mv cea src/cea`，纯移动、不改任何文件内容。验证：`git status` 显示 rename 而非 modify；`src/cea/__init__.py` 的 `__all__` 十一项（App / Router / Bot / Chat / FileData / FileRef / Message / OutMessage / User / FileTooLargeError / MAX_FILE_BYTES）逐字未变
- [x] 2.2 新建 `src/cea_bot/__init__.py`、`src/cea_bot/config.py`（`_QQ_SETTING_URL` / `check_config` / `load_config` 从 `main.py` 迁入）、`src/cea_bot/assembly.py`（`build_adapter`，保留对两个前端的延迟 import）、`src/cea_bot/__main__.py`（`main()` 与 `__main__` 守卫）、`src/cea_bot/handlers/__init__.py`；`git mv handlers/basic.py src/cea_bot/handlers/basic.py`。
  **"迁入"允许且仅允许三处改动**（其余逐字保留）：
  ① `CONFIG_PATH` 的根目录解析：`Path(__file__).resolve().parent` → `parents[2]`，仍指向仓库根 `config.yaml`（照抄会让 `load_config` 去找 `src/cea_bot/config.yaml`，属静默失败）；
  ② 提示文案中的过时路径：`main.py` → `-m cea_bot`、`cea/adapters/napcat.py` → `src/cea/adapters/napcat.py`（`main.py` 本变更已删除，留旧文案即成死链）；
  ③ `main()` 内的 `from handlers import basic` → `from cea_bot.handlers import basic`；`handlers/__init__.py` docstring 里的"由 main.py 挂载" → "由 cea_bot.__main__ 挂载"。
  验证：`src/cea_bot/` 下 6 个 `.py` 文件齐备（`__init__` / `config` / `assembly` / `__main__` / `handlers/__init__` / `handlers/basic`）；`grep -rn "from main import" tests/` 无命中；**新增 `tests/test_layout.py`**，断言 `cea_bot.config.CONFIG_PATH` 指向仓库根（含 `pyproject.toml` 的那一级）下的 `config.yaml` —— 用测试逼出①这类路径静默失败（否则错误路径只在"配置缺失"文案里出现，会被 3.4 当成合法报错放过）；因此测试基数 53 → **54**，2.4 / 2.7 / 4.1 的期望值同步为 54
- [x] 2.3 删除 `main.py` 与 `handlers/`（内容已全部迁出）。验证：`git diff --stat` 中 `main.py`、`handlers/__init__.py`、`handlers/basic.py` 均为 deleted，且 `grep -rn "from handlers import\|import handlers" --include=*.py .` 无命中
- [x] 2.4 改写 `tests/test_napcat.py` 的导入：`from main import build_adapter, check_config` → `from cea_bot.assembly import build_adapter` 与 `from cea_bot.config import check_config`。验证：`.venv\Scripts\python -m pytest tests --collect-only -q` 收集到 **54** 项（53 条既有 + `tests/test_layout.py` 的 1 条）
- [x] 2.5 新增 `tests/conftest.py`：仅在 `import cea` 失败时打印"请先 `pip install -e \".[dev]\"`"并失败退出，**不得**出现 `sys.path`。捕获须精确：`except ModuleNotFoundError as exc` 后 `if exc.name != "cea": raise`（否则会把 `cea` 内部缺 `aiohttp` 之类的真实故障伪装成"未安装"），用 `pytest.exit(..., returncode=非 0)` 退出。验证：`grep -n "sys.path" tests/conftest.py` 无输出；已安装状态下 `pytest tests -q` 正常通过（收集数与 2.4 一致）。**反向验证失败分支**（不可只看代码）：把 2.6 安装产生的 editable 装载文件（`.venv\Lib\site-packages` 下 `*editable*`，用 `Get-ChildItem` 列出真实文件名）临时改名挪开 → `pytest tests -q` 必须打印"请先 pip install -e"提示且退出码非 0 → **立刻改名还原**，并用 `.venv\Scripts\python -c "import cea, cea_bot"` 证明还原成功；全过程与原始输出记入报告
- [x] 2.6 执行 `.venv\Scripts\python -m pip install -e ".[dev]"`（qq-botpy 须用官方源）。验证：`.venv\Scripts\python -c "import cea, cea_bot, cea_bot.handlers.basic, aiohttp, yaml; print('ok')"` 输出 ok
- [x] 2.7 测试转绿。验证：**`pytest` 直跑**（非 `python -m`）`54 passed`；`python -m pytest` 亦 `54 passed`
- [x] 2.8 在 `[tool.mypy]` 补 `files = ["src"]`（设计裁定 D6 的收口）：mypy 2.x 在无目标时报 `Missing target module, package, files, or command`（迁移前后均如此，非本变更引入），故 AGENTS.md 所列的裸 `mypy` 验收命令此前一直是形同空跑；补 `files` 后它才真实生效。验证：仓库根裸跑 `.venv\Scripts\python -m mypy` → `Success: no issues found`（与 `.venv\Scripts\python -m mypy src` 同口径）；再跑一次 4.2 的命令确认未被 `files` 架空

## 3. 依赖清理与文档

- [x] 3.1 删除 `requirements.txt`。验证：`Test-Path requirements.txt` 为 False；"清华镜像缺 `qq-botpy`、须用官方源"的提示已在 `pyproject.toml` 注释（1.1）与 `README.md`（3.2）各存一份
- [x] 3.2 新增 `README.md`，含六节：① 首次安装（`pip install -e ".[dev]"` + qq-botpy 官方源提示）② 配置（`config.example.yaml` → `config.yaml`、`adapter` 选择与各前端参数、群白名单）③ 启动（`python -m cea_bot`）④ 如何新增 handler（`@router.command` / `@router.on_message` / `@router.schedule` + `include_router`）⑤ 测试与类型检查命令 ⑥ `assets/` 的来源与再生成方式。验证：README 中安装与启动两条命令逐字复制可执行
- [x] 3.3 `git mv tools/make_avatar.py .local/tools/make_avatar.py`，并把其依赖 `pillow` / `svglib` / `reportlab` 写进该文件 docstring。**设计裁定：移动后必须同步修该文件自身的路径解析** —— 它用 `ROOT = Path(__file__).resolve().parents[1]` 定位仓库根下的 `assets/`，从 `tools/` 移到 `.local/tools/` 后 `parents[1]` 变成 `.local`，会去找 `.local/assets/cea-logo.svg`（不存在，属静默失败）；须改为 `parents[2]`，并把 docstring 的用法行 `tools\make_avatar.py` → `.local\tools\make_avatar.py`。验证：`git status` 中 `tools/` 下文件为 deleted；`git check-ignore .local/tools/make_avatar.py` 命中；`grep -n "pillow\|svglib\|reportlab" .local/tools/make_avatar.py` 有命中；**以 importlib 按路径加载该模块**（不执行 `main()`），断言 `SVG_PATH.is_file()` 且 `SVG_PATH == 仓库根/assets/cea-logo.svg`、`OUT_PATH.parent.is_dir()`；`git status --short assets/` 无输出（证明没有误覆盖已入库的头像产物）
- [x] 3.4 改写 `start.bat`：改为 `.venv\Scripts\python -m cea_bot`，启动前自检 `.venv` 与包是否已安装，缺失时打印安装提示而非裸 traceback（自检报错须 `exit /b` 非 0，不得继续执行）；可加 `cd /d "%~dp0"` 使相对路径不依赖调用方 cwd。验证：`grep -n "main.py" start.bat` 无输出；已安装环境运行 `start.bat`（须限时，勿让它常驻）能打出装配日志 `应用启动，共 N 个命令 / N 个消息处理器 / N 个定时任务` 或配置校验报错；该装配日志与迁移前 `python main.py` 逐字一致，配置校验报错文案按 2.2 的口径（仅命令/路径 token 改动）

- [x] 3.5 同步 `AGENTS.md` 的「代码在哪」一节至重构后布局（该节现存文字明确标注了会随本变更过期），并清掉同一文件里其它过时路径（开头的角色说明里也有 `handlers/`）。验证：该节列出 `src/cea/`、`src/cea_bot/`、`pyproject.toml`、`tests/`，且不再出现 `main.py`、`handlers/`、`tools/` 三项；`grep -n "实施完成后须改为" AGENTS.md` 无输出；文中"验收命令"一节的 `pytest` / `python -m pytest` / `mypy`（裸跑，2.8 后已真实生效）/ `openspec validate --all` 与当前实况一致

- [x] 3.6 收口任务组 2 实测暴露的两处遗留（设计裁定）：
  ① `src/cea_bot/__main__.py` 的模块 docstring 仍在写 `cea/adapters/qqbotpy.py`、`cea/adapters/napcat.py`、"业务代码写在 handlers/ 下" → 同步为 `src/cea/adapters/…` 与 `cea_bot/handlers/`（2.2 的"逐字保留"针对代码语义；docstring 里的路径自指属文档，必须与实现一致。`config.py` 的提示文案已在 2.2 改妥，此处是同一件事的漏网处）；
  ② `.gitignore` 增加 `*.egg-info/`：2.6 的 `pip install -e .` 会在源码树生成 `src/cea_bot.egg-info/`，不忽略则往后每次安装都弄脏 `git status`（任务组 2 已把该目录清掉）。
  验证：`Select-String -Path src\*.py,src\*\*.py -Pattern "cea/adapters/|写在 handlers/"` 只剩 `src/cea/adapters/…` 这一种正确写法；`git check-ignore -v src/cea_bot.egg-info/PKG-INFO` 命中 `*.egg-info/`（若 git 要求路径存在，先建同名探针目录、验证后删除）；`.venv\Scripts\python -m pytest -q` 仍 `54 passed`

## 4. 集成验收

- [x] 4.1 从任意 cwd 验证可移植性。验证：`cd C:\` 后执行 `D:\Ds_Projects\Python\cea-bot\.venv\Scripts\pytest D:\Ds_Projects\Python\cea-bot\tests -q` → `54 passed`（迁移前同一命令为 `ModuleNotFoundError: No module named 'cea'`）
- [x] 4.2 类型检查零噪音。验证：`.venv\Scripts\python -m mypy src` → `Success: no issues found`
- [x] 4.3 确认规格未被触碰（本变更 `skip_specs: true`）。验证：`openspec validate --all` 通过；`git diff --stat 2766a3e -- openspec/specs` 无输出
- [x] 4.4 确认密钥与本地资产未入库。验证：`git ls-files config.yaml .local botpy.log` 无输出；`git status --short` 不含 `config.yaml` / `.local/` / `*.egg-info` / `*.log`
- [x] 4.5 收口 `pyproject.toml` 第 21 行的过期注释（设计裁定，任务组 3 上报）：`# 以下两个是直接 import（cea/adapters/napcat.py、main.py）` —— `main.py` 已删除、框架已移入 `src/`，且 `PyYAML` 的真实直接 import 点是 `src/cea_bot/config.py`；改为 `# 以下两个是直接 import（src/cea/adapters/napcat.py、src/cea_bot/config.py）`。验证：`Select-String -Path pyproject.toml -Pattern "main\.py"` 无输出；`.venv\Scripts\python -c "import tomllib;tomllib.load(open('pyproject.toml','rb'))"` 仍解析通过（改的是注释，不得碰到任何键值）；`.venv\Scripts\python -m mypy` 仍 `Success`

## 5. 审查后修复（独立审查智能体 D1 / D2 / D3，设计智能体定性为"文档滞后"与"自检强度不足"，由实施修复）

- [x] 5.1 【D1·低·文档滞后】`src/cea/adapters/napcat.py:23` 的 `见 cea/adapters/base.py 契约` → `见 src/cea/adapters/base.py 契约`。**只改这一行 docstring，不得碰任何逻辑**。定性理由：`src/cea_bot/config.py:47` 的用户可见报错把用户指向该模块 docstring，故它是"运行时报错路径可达的文档"；3.6 已确立"docstring 路径自指须与实现一致"的口径，此处是同一口径下的漏网。注：2.1 的"blob 与基线相同"证据在 5.1 之后**不再成立**（本轮改动会使该文件与 `HEAD:cea/adapters/napcat.py` 差一行文档），这是设计智能体明确接受的代价。验证：`Select-String -Path src\cea\*.py,src\cea\adapters\*.py -Pattern "(?<!src/)cea/(adapters|app|bot|types)"` 无命中；`git diff 2766a3e -- src/cea/adapters/napcat.py` **只有这一行**（贴全文）；`.venv\Scripts\python -m pytest -q` 仍 54 passed；`.venv\Scripts\python -m mypy` 仍 `Success`
- [x] 5.2 【D2·低·自检形同虚设】`start.bat:19` 的自检 `"%PY%" -c "import cea_bot"` 换成与 README §1 一致的强探针：`"%PY%" -c "import cea, cea_bot, cea_bot.handlers.basic, aiohttp, yaml"`。定性理由：`src/cea_bot/__init__.py` 只有 docstring，原探针**一个依赖都不验证**，与 tasks 3.4"缺失时给安装提示而非裸 traceback"的意图不符（依赖被卸或 `--no-deps` 安装时会漏到裸 traceback）。验证：① 根因证据——`.venv\Scripts\python -c "import sys, cea_bot; print('cea:', 'cea' in sys.modules, 'yaml:', 'yaml' in sys.modules)"` 与强探针版对比；② 新探针在已安装环境退出码为 0；③ 把 editable 装载 `.pth`（`.venv\Lib\site-packages\__editable__*.pth`，用 `Get-ChildItem` 取真实文件名）临时改名挪开 → 运行 `cmd /c "start.bat < NUL"` 必须打印"请先 … pip install -e"提示、退出码非 0、**不得出现"应用启动"** → 立刻还原并用 `import cea, cea_bot` 证明还原；④ `Select-String -Path start.bat -Pattern "main\.py"` 无输出
- [x] 5.3 【D3·低·文档自相矛盾】`README.md:164` 的"**随仓库分发**的该脚本只写 `assets/cea-avatar.png`"与同节 `:160`"**不随版本库分发**"直接冲突；删掉"随仓库分发的"五字，保持原意（该脚本只写 `assets/cea-avatar.png`；`cea-2026.png` 不会被重跑覆盖）。验证：贴改后该段全文；`Select-String -Path README.md -Pattern "随仓库分发"` 无命中；`.venv\Scripts\python -m pytest -q` 仍 54 passed（文档改动不得牵连代码）
- [x] 5.4 【5.2 的同一根因·提示未同步，由实施智能体在 5.2 报告中主动上报】`start.bat` 的 `:not_installed` 分支里"复现自检"那行仍打印弱探针 `.venv\Scripts\python -c "import cea_bot"`，与已修好的第 19 行强探针不一致 —— 用户照提示复现会得到"自检通过"的假象，正是 D2 的同一问题。改为与第 19 行逐字一致的强探针：`.venv\Scripts\python -c "import cea, cea_bot, cea_bot.handlers.basic, aiohttp, yaml"`。**只改这一行 echo，其余结构一行不动**。验证：`Select-String -Path start.bat -Pattern 'import cea_bot"'` 无命中；失败分支实跑（挪开 `.venv\Lib\site-packages\__editable__*.pth` → `cmd /c "start.bat < NUL"`）贴出的提示里"复现自检"那行为强探针，且仍退出码非 0、仍无"应用启动"；`try/finally` 还原并证明还原成功
- 备注（设计口径修正，5.1 报告上报）：5.1 的验证命令写成了 `git diff 2766a3e -- src/cea/adapters/napcat.py`，该 pathspec 不含旧路径、无法配对 rename，会整文件显示为 `new file`；**正确口径**是 `git diff 2766a3e:cea/adapters/napcat.py src/cea/adapters/napcat.py`（旧 blob vs 新文件，`--numstat` 为 `1 1`），实施已按此取证，设计裁定采纳。
