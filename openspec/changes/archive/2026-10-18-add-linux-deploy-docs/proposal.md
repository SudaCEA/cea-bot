# Proposal: add-linux-deploy-docs

## Why

仓库目前是**纯 Windows 口径**：README 的安装/启动全部写 `.venv\Scripts\...` 与 `start.bat`，
`src/` 里还有 4 处错误提示把 Windows 路径写死。要把 bot 部署到 Ubuntu（与 NapCat 同机）时，
没有任何可依据的步骤，而排障时那些写死的提示会**把人带偏**（Linux 上 `.venv\Scripts\python` 根本不存在）。

代码本身是可移植的（`src/` 无任何 Windows 专有 API，路径全走 `pathlib`），缺的是**部署知识**与**平台正确的提示**。

## What Changes

- `README.md` 新增「Ubuntu 部署」一节：Python 3.13 门槛、系统依赖、**CJK 字体**、
  官方源安装、`config.yaml` 的 Linux 适配、`start.sh` 用法、NapCat 同机约束、分层验收清单
- 新增 `start.sh`（与 `start.bat` 行为对齐：切到脚本目录 → 自检 venv 与安装 → 启动），
  **不做 systemd**（用户明确要求进程管理只给一个 sh）
- `src/` 的 4 处 Windows 口径提示改为**按平台生成**：
  `config.py:74`、`notices/pdf.py:157` / `:182` / `:188`
- `pyproject.toml` 与 `README.md` 里其它 Windows 专属的**注释/示例**改为平台中立（或并列两种形态）

## Non-Goals

- **不改默认缓存路径**（用户明确要求"路径的更改之后再 propose change"）——
  本变更只在 README 里**显式警告**该风险，不动 `config.py` 的 `_DEFAULT_NOTICE_*` 常量
- 不提供 systemd unit / Dockerfile / CI（只给 `start.sh`）
- 不改 `config.yaml` 的键名与位置（仓库硬约束）
- 不引入任何新依赖；不改任何 `SHALL` 行为

## Capabilities

（本变更 `skip_specs: true`：不改行为、不新增/修改需求。现有规格里"给出**可执行的**修复提示"
的要求本就涵盖提示文案，本变更是**让它真正可执行**，属兑现既有要求而非新增要求。）

## Impact

- **文档**：`README.md`（新增一节 + 修正既有 Windows 口径示例）
- **工具**：新增 `start.sh`
- **代码**：`src/cea_bot/config.py`、`src/cea_bot/notices/pdf.py`（**仅提示字符串**，无行为变化）
- **配置**：`pyproject.toml` 注释措辞（无功能变化）
- **风险**：① 提示文案改动会被既有测试断言到 → 需检查是否有测试钉住了这些字符串；
  ② `start.sh` 需可执行位，但 Git 在 Windows 上不保留可执行位 → 需在 README 里写明
  `chmod +x start.sh`（或由部署者用 `bash start.sh` 调用）
