# Design: add-linux-deploy-docs

## Context

- 仓库是**纯 Windows 口径**：README 的安装/启动全写 `.venv\Scripts\...` 与 `start.bat`；
  `src/` 里 4 处错误提示把 Windows 解释器路径写死（`config.py:74`、`notices/pdf.py:157/182/188`）。
- **代码可移植性已核实**：`src/` 中无 `winreg` / `msvcrt` / `win32` / `sys.platform` 分支，
  路径全走 `pathlib`；`config.py:285` 的权限提示本就是双平台（Windows 只读/安全设置 与 POSIX `chmod u+w`）。
  故本次**不需要**为可移植性改任何逻辑。
- 真实部署约束来自既有实现与实测：`requires-python = ">=3.13"`；详情页正文带 Word 内联字族
  （`仿宋` / `宋体` / `黑体` / `等线` / `仿宋_GB2312`），Windows 上实测内嵌了
  SimSun/FangSong/MicrosoftYaHei/SimHei 四个中文字体；PDF 是把**本机绝对路径**原样交给 NapCat 去读。

## Goals / Non-Goals

**Goals:**

- 一个 Ubuntu 运维照着 README 能一路装到"群里收到通知"，不必猜
- 错误提示在 Linux 上**真的可执行**（不再指向不存在的 `.venv\Scripts\...`）
- 进程管理只给一个 `start.sh`（用户明确要求；不做 systemd）

**Non-Goals:**

- 不改默认缓存路径（`_DEFAULT_NOTICE_PDF_DIR` / `_DEFAULT_NOTICE_DB_PATH`）—— 用户要求**之后单独 propose**
- 不提供 systemd / Dockerfile / CI
- 不改 `config.yaml` 键名与位置；不新增依赖；不改任何 `SHALL` 行为

## Decisions

**D1. 目标发行版 Ubuntu，apt 口径；Python 3.13 是硬门槛。**
`pyproject.toml` 写死 `requires-python = ">=3.13"`，而 Ubuntu 24.04 只带 3.12。
文档给三条可选路径：① deadsnakes PPA 装 `python3.13`；② `uv`（自带解释器下载，最省事）；
③ 直接用 25.04+ / 3.13 基底容器。**不**在文档里假装 3.12 能跑。

**D2. 进程管理只给 `start.sh`，行为与 `start.bat` 三件事对齐。**
① `cd` 到脚本自身所在目录（不依赖调用方 cwd）；② 自检 `.venv/bin/python` 存在、
且 `import cea, cea_bot, cea_bot.handlers.basic, aiohttp, yaml` 成功，失败给可执行提示并非 0 退出；
③ `.venv/bin/python -m cea_bot`。
**可执行位陷阱**：Git 在本仓库当前环境（Windows）不保留可执行位，克隆到 Ubuntu 后 `./start.sh`
会 Permission denied —— README 必须写明 `chmod +x start.sh`，或直接用 `bash start.sh` 调用。

**D3. 提示文案按平台生成，playwright 统一用 `python -m playwright`。**
实测 `python -m playwright --version` → `Version 1.63.0`（包内带 `__main__.py`），
故统一写成 `<venv-python> -m playwright install chromium`，**同时适配** Windows 的
`.venv\Scripts\playwright.exe` 与 Linux 的 `.venv/bin/playwright`，不必分平台分叉。
解释器路径由一个小**纯函数**给出（如 `_venv_python(is_windows: bool)`），
把平台作为**可注入参数**而非直接读 `sys.platform` —— 这样**两个分支都能在任一操作系统上被单测钉住**，
符合本仓库"纯函数优先、单测不依赖平台"的取向。

**D4. 默认缓存路径本变更不改，但 README 必须给出显式警告。**
现状 `notice_pdf_dir` 默认 `data/notices`（相对），而生产 `config.yaml` 里是 Windows 绝对路径。
**这个坑很隐蔽**：`D:\Ds_Projects\...` 在 Linux 上是一个**合法的相对文件名**（含反斜杠），
程序不会报错，而是创建出一个名字带反斜杠的怪目录，再把该名字交给 NapCat —— NapCat 读不到，
于是**文本到达、PDF 静默丢失、该条已记账不重试**。
故 README 必须写明"Linux 上必须改成 Linux 绝对路径（如 `/srv/cea-notices/pdf`）"并给示例。

**D5. CJK 字体是硬前置，最低 `fonts-noto-cjk`；可选 fontconfig 别名还原公文字体。**
不装 CJK 字体时，Chromium 找不到 `仿宋`/`宋体`/`黑体` 等字族 → PDF 里中文成豆腐块（□□□）。
可选增强：用 `/etc/fonts/local.conf` 把 `宋体`/`仿宋`/`黑体`/`等线`/`仿宋_GB2312`
别名到已装 CJK 字体，尽量贴近公文排版。文档给出**判据**（渲染一页中文后中文可读、非豆腐块），
而不是只说"装字体"。

**D6. NapCat 必须与 bot 同机 —— 这是架构约束，不是建议。**
PDF 的投递形态是"把本机绝对路径交给 NapCat 读"（见 napcat-adapter 规格「文件消息收发」）。
若 NapCat 留在原机器、bot 搬到 Linux，会原样重现"文本到了、PDF 静默丢失且不再重试"。
故 README 把"NapCat 同机 + `ws_url: ws://127.0.0.1:<端口>`"写成**前置条件**。

**D7. 时区必须显式设置。**
`notice_times` 是**本地时间**（`app.py` 里 `due_jobs(..., datetime.now())`，朴素本地时间）。
Ubuntu 服务器常是 UTC → 6 个定点整体平移 8 小时。README 要求 `timedatectl set-timezone Asia/Shanghai`
（容器则设 `TZ=Asia/Shanghai`）。

**D8. 状态库可选择性迁移。**
`data/state/cea_bot.db` 带过去 = 沿用"这些已推过"的记忆；不带 = 首跑重建基线。
两种都不推历史。库里的 `pdf_path` 是 Windows 路径，但该列**全仓只写不读**，无害。

## Risks / Trade-offs

- [提示文案改动可能被既有测试断言到] → 实施时先 grep 测试里是否钉住了 `.venv\Scripts`；
  若有则同步更新，**不得**为了过测试而放宽断言
- [`start.sh` 在 Windows 上无法本地验证] → 只做语法检查（`bash -n`，需 Git Bash/WSL）
  并在 README 写明部署时 `chmod +x`；**诚实留痕"未在真实 Ubuntu 上跑过"**
- [字体还原度无法在 Windows 上验证] → 文档给的是"最低要求 + 可选增强 + 判据"，
  不宣称排版与 Windows 逐像素一致
- [文档写了却与实现漂移] → 文档里的凡涉及行为的描述，一律**引用规格/代码事实**（本 design 的 Context 段已核对）
