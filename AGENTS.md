# AGENTS.md

本文件是智能体在本仓库工作的**约定入口**。它做两件事：给出必须遵守的硬约束，
以及把详细规则索引到各自的权威文件。**与被指向的文件冲突时，以被指向的文件为准。**

## 这是什么

CEA Bot —— 平台无关的 QQ 机器人框架 + 业务应用。

- `src/cea/` 框架核心：统一消息模型、`inbound` / `outbound` 双总线、Adapter 契约、
  Router 装饰器、共享时钟、优雅退出。**不 import 任何平台 SDK**。
- `src/cea/adapters/` 两个可互换的平台前端：QQ 官方（`qq-botpy`）与 NapCat（OneBot 11 正向 WS）。
- `src/cea_bot/` 业务应用：配置校验、前端装配、入口，以及 `src/cea_bot/handlers/` 业务层
  （只用装饰器挂载纯异步函数，不碰平台细节）。

布局是 **src-layout**：`src/` 不在解释器的模块搜索路径里，动手前先
`.venv\Scripts\python -m pip install -e ".[dev]" --index-url https://pypi.org/simple`
（首次安装见 `README.md`），否则连 `import cea` 都失败。

## 协作规范

**`dsh_cooperation.md` 是本仓库的智能体分工契约，开始任何工作前先读它。** 它规定：

- 三类角色 **设计 / 实施 / 审查** 由互相独立的智能体承担，**不得由同一个智能体在同一轮里兼任**；
  实施与审查必须是互不共享上下文的两个智能体，否则"自己审自己"会系统性放过实现缺陷。
- **设计智能体不写实现代码**，但全程把握实施进度，且始终是质量的最终责任人。
- 实施阶段**分批派发**（按任务组切片，不一次性交出全部 tasks）；每批回收后由设计智能体
  **核对勾选的真实性** —— 抽查勾选项对应的测试是否真的存在且通过，而不是只读 `[x]`。
- **判断是否该停**：子 Agent 出现连续的写入未生效、参数丢传、静默降级时，应主动切断并留下
  干净检查点，而不是继续硬推。
- 审查结论**不被照单全收**：设计智能体必须二次检查、给缺陷定性、核对修复质量、并留痕。
- **实施红线**见该文件；其中"静默失败"一类问题必须用测试逼出来，不能靠"看起来对"。
- 该文件还记录了 `subagent` / `subagent_fork` 的实际参数限制（无 `provider` / `model`；
  `id` 是返回值不是入参）与交接物要求（prompt 必须自包含：change 路径、当前勾选状态、
  上一轮遗留问题、验收命令）。

## 规格驱动：OpenSpec

| 位置 | 内容 |
|---|---|
| `openspec/specs/<capability>/spec.md` | 主规格，已交付能力的行为真相源 |
| `openspec/changes/<name>/` | 进行中的变更：proposal / design / specs / tasks |
| `openspec/changes/archive/YYYY-MM-DD-<name>/` | 已归档变更 |

- **先读规格再动代码**。需求以 `SHALL` 措辞定义行为；实现与规格不一致时优先怀疑实现。
- 改行为 → 走一个 change；纯重构 / 工具 / 文档 → 在 change 的 `.openspec.yaml` 置
  `skip_specs: true`，**不得为通过校验而凭空造需求**。
- 归档由设计智能体执行，步骤见 `dsh_cooperation.md` 的归档节。

## 硬约束

以下为规格与既有实现反复确认过的，不要绕过：

- **依赖方向单向**：`src/cea_bot/handlers/` → `cea`，反向禁止。框架核心既不得 import
  平台 SDK，也不得 import 业务层。
- **不内置限流原语**（Semaphore 等）：并发控制由业务任务自行实现，`runtime` 规格明文规定。
- **总线语义与请求-响应语义分开**：`bot.send()` 只投递 outbound 总线、失败记日志**不外抛**；
  `bot.fetch_file()` 是请求-响应，异常抛给调用方。两者不要混。
- **出站消息 text / image / file 三选一互斥**：同时给出即 `ValueError`，模型层硬校验。
- **不改 `config.yaml` 的键名与位置**：含真实凭据且被 `.gitignore` 排除；模板是 `config.example.yaml`。
- **纯函数优先、IO 隔离**：事件归一化、段构造、命令匹配、调度判定均为纯函数，
  单测不依赖真实 WS 或平台。
- **标准库优先、零新增依赖**：新依赖必须在 change 的 proposal 里列明，并说明为什么绕不开。
- **中文**：docstring、注释、规格、commit message 一律中文；标识符用英文。

## 验收命令

改完代码必须全过，不要只跑单测（下面都是仓库根 + 仓库内 venv；激活 `.venv` 后去掉前缀即可）：

```
.venv\Scripts\pytest                # 直跑
.venv\Scripts\python -m pytest      # 回归，两种姿势都要绿
.venv\Scripts\python -m mypy        # 零报错；[tool.mypy] 里 files = ["src"]，仓库根裸跑即全量
openspec validate --all             # 规格与变更校验
```

历史坑（均已在 `add-python-package-layout` 修掉）：src-layout 之前 `pytest` 直跑 0 collected、
换 cwd 就 `ModuleNotFoundError`；mypy 2.x 在 `[tool.mypy]` 缺 `files` 时报
`Missing target module, package, files, or command`，所以裸 `mypy` 曾长期是空跑。现在两条都真实生效。

测试写法：用 `asyncio.run(...)` 包裹协程场景，**不引入 `pytest-asyncio`**。

## 代码在哪

依赖方向在目录上可见：`src/cea_bot/` → `src/cea/` 单向。

| 位置 | 内容 |
|---|---|
| `src/cea/types.py` | 统一消息模型：`Message` / `OutMessage` / `Chat` / `User` / `FileRef` / `FileData` |
| `src/cea/bot.py` | `Bot` 句柄：`send` / `fetch_file` / `create_task` / `stopping`；`MAX_FILE_BYTES` |
| `src/cea/app.py` | `Router` 装饰器、`match_commands`、`due_jobs`、`App`（分发 / 时钟 / 生命周期） |
| `src/cea/adapters/base.py` | `Adapter` Protocol + `pump_outbound` 通用发送消费者 |
| `src/cea/adapters/qqbotpy.py` | QQ 官方前端（含 `SeqCounter`） |
| `src/cea/adapters/napcat.py` | NapCat 前端（含 `normalize_event` / `build_segments` 纯函数） |
| `src/cea_bot/config.py` | 配置读取与校验（`check_config` / `load_config`，定位仓库根 `config.yaml`） |
| `src/cea_bot/assembly.py` | `build_adapter`：按已校验配置装配前端（对两个前端延迟 import） |
| `src/cea_bot/__main__.py` | 入口薄壳：读配置 → 装配 → 挂路由 → `app.run()`，即 `python -m cea_bot` |
| `src/cea_bot/handlers/basic.py` | 业务 handler 示例（`echo` / `ping` / `noon_report`） |
| `src/cea_bot/store.py` | 业务层持久化底座：SQLite 开库 / PRAGMA / 按 owner 建表登记与版本校验（**不得** import 上层） |
| `src/cea_bot/notices/` | 校内通知推送的领域实现：`fetch`（抓取）/ `parse`（解析与编码回退）/ `schema`（增量状态）/ `summarize`（摘要）/ `pdf`（渲染） |
| `src/cea_bot/handlers/notice.py` | 通知推送编排：定时注册、重叠跳过、干跑、推送与记账（`notice_targets` 为空时不挂载） |
| `tests/test_framework.py` | 框架单测 |
| `tests/test_napcat.py` | NapCat 前端单测 + 装配选择 |
| `tests/test_layout.py` | 包布局断言（`CONFIG_PATH` 必须指向仓库根） |
| `tests/test_store.py` | 持久化底座单测（PRAGMA / 建表 / 版本不兼容响亮失败） |
| `tests/test_notice*.py` | 通知推送单测（配置 / 解析 / 抓取 / 状态 / 摘要 / PDF / 编排） |
| `tests/fixtures/` | 通知页面真实原始字节样本（含"声明 gb2312 实为 UTF-8"的现场） |
| `data/` | 运行数据：SQLite 增量状态库与 PDF 归档（已 gitignore，不入库） |
| `start.bat` | Windows 启动脚本：自检 venv 与安装，再 `python -m cea_bot` |
| `pyproject.toml` | 依赖、打包（src-layout）与 mypy 配置的唯一真相源 |
| `README.md` | 安装 / 配置 / 启动 / 新增 handler / 测试 / `assets/` 来源 |
| `.local\tools\make_avatar.py` | 离线工具，生成 `assets/`，不参与运行时（`.local/` 已忽略，不入库） |
| `openspec/` | 规格与变更（见上节） |
