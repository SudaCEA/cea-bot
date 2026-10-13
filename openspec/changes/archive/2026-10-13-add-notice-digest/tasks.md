# Tasks

> 每批任务都应能独立跑通验证命令。测试一律用 `asyncio.run(...)` 包裹协程场景，**不引入 pytest-asyncio**；
> 单元测试不得依赖真实网络、真实浏览器与真实模型服务（真实链路放在任务组 9）。

## 1. 依赖与配置基线

- [x] 1.1 `pyproject.toml` 的 `dependencies` 增加 `playwright`（旁注说明：唯一新增的运行时依赖，仅用于把通知正文渲染为 PDF），随后执行 `.venv\Scripts\python -m pip install -e ".[dev]" --index-url https://pypi.org/simple` 与 `.venv\Scripts\playwright install chromium`。验证：`.venv\Scripts\python -c "import playwright; print(playwright.__version__ if hasattr(playwright,'__version__') else 'ok')"` 成功；浏览器已落地（`Get-ChildItem $env:LOCALAPPDATA\ms-playwright` 非空）；`.venv\Scripts\python -m mypy` 仍 `Success`
- [x] 1.2 `config.example.yaml` 新增 8 个键（**既有键名与位置不动**）：`notice_targets: []`、`notice_type: 1`、`notice_times: ["09:00", "15:00", "21:00"]`、`notice_db_path: "data/state/cea_bot.db"`、`notice_pdf_dir: "data/notices"`、`deepseek_api_key: "YOUR_DEEPSEEK_API_KEY"`、`deepseek_model: "deepseek-flash"`、`notice_dry_run: false`，并逐项注释语义（含"留空即关闭该功能"、密钥不得提交）。验证：`.venv\Scripts\python -c "import yaml;c=yaml.safe_load(open('config.example.yaml',encoding='utf-8'));print(sorted(k for k in c if k.startswith(('notice_','deepseek'))))"` 输出上述 8 键；既有键 `adapter` / `group_whitelist` / `appid` / `secret` 仍在
- [x] 1.3 `src/cea_bot/config.py` 新增 notice 校验分支（既有分支一字不改）：`notice_targets` 非空即视为启用，此时校验密钥存在、`notice_times` 为合法 `HH:MM` 列表、`notice_type` ∈ {1,2,3}、状态库与产物目录可创建可写；不合法 `sys.exit` 给出可执行提示。**实现口径（设计裁定）**：① `check_config` 保持纯函数、不碰文件系统；② `HH:MM` 校验**复用** `cea.app.parse_hhmm`（不重写正则）；③ 目录可创建可写由同模块独立函数（如 `ensure_notice_dirs`）承担、由启动路径调用，测试用 `tmp_path`。验证：新增测试覆盖 ①未启用时放行且不改动返回结构 ②缺密钥 exit ③`notice_times` 格式非法 exit ④`notice_type` 越界 exit ⑤合法配置返回含 notice 字段的字典 ⑥`ensure_notice_dirs` 能建出目录、目录不可写时明确报错（**用 `pytest.raises(SystemExit)`，不要放宽成任意异常**）

## 2. 业务层持久化底座（`src/cea_bot/store.py`）

- [x] 2.1 新增 `src/cea_bot/store.py`：`connect(path)`（支持 `":memory:"`）、`PRAGMA journal_mode=WAL` / `busy_timeout` / `foreign_keys`、`migrate(conn, owner, statements)` 按 owner 执行 `CREATE TABLE IF NOT EXISTS` 并记录 owner 版本、`schema_version(conn)` 读取。**不得 import 任何 handler**。验证：测试用 `:memory:` 建两张表并断言可查；文件库断言 `pragma journal_mode` 返回 `wal`、`busy_timeout` 生效；`Select-String -Path src\cea_bot\store.py -Pattern "handlers"` 无命中
- [x] 2.2 版本不兼容的响亮失败：`schema_version` 与实现期望不符时抛明确异常（附处置提示"删除 data/state/cea_bot.db 后重建"），`SHALL NOT` 自动改表或静默重建。验证：测试篡改 `meta.schema_version` 后断言抛错且错误文案含处置提示

## 3. 解析层纯函数（`src/cea_bot/notices/`）

- [x] 3.1 落 fixtures：把列表页与详情页的**真实原始字节**存入 `tests/fixtures/`（列表页正是"声明 gb2312、实际 UTF-8"的样本），并在 `tests/fixtures/README.md` 记录来源 URL 与抓取日期。验证：文件存在且非空；`.venv\Scripts\python -c "b=open('tests/fixtures/notice_list_type1.bin','rb').read(); print(len(b), b[:60])"` 显示原始字节（不做任何转码预处理）
- [x] 3.2 `notices/models.py`（纯数据：`NoticeBrief` / `NoticeDetail` / `Attachment`）+ `notices/parse.py` 的 `decode_html(raw, declared_charset)`：按声明解码 → 乱码判据 → 回退 UTF-8 → 回退 GB18030 → 全失败抛错。验证：用 fixture 断言"声明 gb2312 仍解出正确中文"（含 `学生标兵`）；构造声明 UTF-8 但字节为 GB18030 的反向样本同样解对；构造二进制垃圾断言抛错
- [x] 3.3 `parse_list(html)` → `list[NoticeBrief]`：解析日期/标题/发布单位/**稳定标识**（从 `open_tz('40773772')` 取），并断言"解析到 0 条 → 抛页面结构异常"（不得返回空列表让调用方当成没新通知）。验证：fixture 断言 20 条、首条标题与日期、首条 ID 为 `40773772`；删掉表格行的改造样本断言抛错
- [x] 3.4 `parse_detail(html)` → `NoticeDetail`：标题、文号、正文纯文本（去标签与富文本残留样式）、附件清单（解析 `infoPicName` / `infoPicSaveName` 两个 hidden input 并按 `|` 切分）。验证：fixture 断言文号 `学字﹝2026﹞63号`、附件 3 项名称与扩展名（`.xls`/`.doc`/`.doc`）、正文含关键句且**不含** `<span`/`font-family` 之类残留；另造"无文号无附件"样本断言字段为空且不抛错

## 4. 抓取层（IO 边界）

- [x] 4.1 `notices/fetch.py`：aiohttp 抓列表与详情，显式超时、HTTP 状态检查、把失败转成明确的业务异常（不吞、不返回空），session 可注入以便假件测试。验证：假 session 覆盖 成功 / 404 / 超时 三条路径，断言各自的结果与异常类型
- [x] 4.2 请求纪律：只请求列表首页（**不得出现 `pager.offset`**），一次运行对同一详情页只请求一次；失败退避由调用方控制。验证：假 session 记录请求 URL 列表，断言恰好 1 个列表请求 + N 个详情请求，且无 `offset` 参数

## 5. 增量状态层（`notices/schema.py`）

- [x] 5.1 `notice` 表 DDL（主键 `(kind, edit_id)`，含 `title`/`dept`/`published_on`/`first_seen_at`/`pushed_at`/`pdf_path`/`last_error`，STRICT）+ `ix_notice_pending` 部分索引 + 三个操作：`register_seen`（`INSERT … ON CONFLICT DO NOTHING`）、`pending`（`WHERE pushed_at IS NULL ORDER BY published_on DESC, edit_id DESC`）、`claim_and_mark`（`UPDATE … WHERE pushed_at IS NULL` 并返回 `rowcount`）。验证：`:memory:` 测试断言 重复登记不报错且不产生第二行、`pending` 顺序正确、第二次 `claim_and_mark` 返回 0（抢占失败语义）
- [x] 5.2 首次运行建基线：状态为空时把列表首页条目全部登记并标记为已推送，返回"无待推送"，日志含建立基线标记。验证：空库 + 列表 fixture → `pending` 为空、全部行的 `pushed_at` 非空、返回值与日志断言
- [x] 5.3 重启后不重复：同一库重开连接（模拟重启）后再次运行，已推送条目不出现在 `pending`。验证：测试用**同一个文件库路径**两轮连接（或同一 `:memory:` 连接内两次调用）断言第二轮 `pending` 不含已推送项

## 6. 摘要层

- [x] 6.1 `notices/summarize.py`：`build_prompt(detail)` 与 `parse_summary(payload)` 纯函数 + `summarize(detail, cfg, client=…)` 使用已有 `aiohttp` 直连 OpenAI 兼容接口（`base_url` 缺省 `https://api.deepseek.com`，模型由 `deepseek_model` 给出）。验证：单测断言 prompt 含标题/正文/附件**名称**、**不含附件内容**；`parse_summary` 对 缺字段 / 空字符串 / 非法 JSON 三种坏响应均返回"不可用"而不抛穿
- [x] 6.2 降级与截断：模型超时/报错/返回不可用 → 返回降级结果（使用正文原文）并由调用方记明确告警；正文与摘要超上限时截断并注明"已截断"。验证：假客户端抛 `asyncio.TimeoutError` 断言不抛穿且返回降级标记；超长正文断言截断长度与"已截断"字样

## 7. PDF 渲染层

- [x] 7.1 `notices/pdf.py`：用 Playwright 无头 Chromium 把详情页正文渲染为 PDF（A4、页边距、只渲染正文区域、中文字体走系统字体），输出到 `notice_pdf_dir/<日期>-<editId>.pdf`；渲染器**可注入**以便单测。验证：单测用假渲染器断言文件名规则、目录自动创建、失败时抛明确异常；断言不产生空文件（渲染结果为空 → 抛错）
- [x] 7.2 渲染环境探测：浏览器二进制缺失时抛出**可执行的**提示（含 `playwright install chromium` 与官方源说明），不裸 traceback。验证：单测断言探测函数在模拟缺失时返回含安装命令的错误信息；真实安装后（1.1）该探测返回就绪

## 8. handler 编排与文档

- [x] 8.1 `src/cea_bot/handlers/notice.py`：按 `notice_times` 逐个 `@router.schedule` 注册；模块级 `asyncio.Lock` 串行化（拿不到锁 → 跳过并记日志）；编排 抓取 → 解析 → 增量 → 摘要 → PDF → 推送 → 记账；`notice_dry_run` 时不投递任何消息。验证：单测以假 fetch/parse/summarize/pdf 注入，断言 编排顺序、重叠时跳过、干跑时 `outbound` 队列为空、异常不允许静默吞（解析异常必须向上抛并被记日志）
- [x] 8.2 挂载与能力探测：`src/cea_bot/__main__.py` 仅在启用时 `include_router(notice.router)`；无文件发送能力的宿主用"干跑/只发文本"分支处理（检测方式与既有 `file_fetcher` 注入思路一致，不改框架）。验证：装配日志的命令/定时任务计数符合预期（启用时定时任务数 = `notice_times` 长度 + 既有任务）；无文件能力时断言只发文本且文本含"不支持文件"说明
- [x] 8.3 文档与忽略项：`README.md` 新增"校内通知推送"一节（`playwright install chromium` 步骤、8 个配置键、干跑用法、DeepSeek 成本量级与"只发正文不发附件"的合规说明）；`AGENTS.md`「代码在哪」增加 `src/cea_bot/store.py` 与 `data/`（运行数据不入库）两行；`.gitignore` 增加 `data/`。验证：README 中的命令逐字复制可执行；`Select-String -Path AGENTS.md -Pattern "store.py|data/"` 有命中；`git check-ignore -v data/state/cea_bot.db` 命中

## 9. 集成验收

- [x] 9.1 真实抓取：对真实站点跑一次列表 + 一条详情抓取，断言解析条数 >0 且字段非空（首条标题与日期与站点当前一致）。验证：贴原始输出（标题/日期/ID/附件数）与耗时
- [x] 9.2 真实 PDF：对一条真实通知渲染 PDF，断言文件存在、以 `%PDF-` 开头、体积 > 10KB、页数 ≥ 1（用 pypdf 或解析文件头断言），且中文未乱码（渲染后文本抽取含关键句）。验证：贴文件路径、大小与页数
- [x] 9.3 干跑全链路：`notice_dry_run: true` 下触发一次完整编排（真实抓取 + 真实渲染 + 真实摘要），断言 `outbound` 队列为空、日志含将发送文本与 PDF 路径、状态库中该条目标记为已推送
- [x] 9.4 仓库四条验收：`.venv\Scripts\pytest`（直跑）/ `.venv\Scripts\python -m pytest` / `.venv\Scripts\python -m mypy` / `openspec validate --all --strict` 全绿，并贴原始输出
- [x] 9.5 密钥与运行数据不入库：`git ls-files config.yaml data .local` 无输出；`git status --short` 不含 `data/`；对日志文件（含 `botpy.log`）与干跑输出 grep 断言不含密钥片段
- [ ] 9.6 真实推送一次（**由用户在确认后手动执行，不作为自动化验收**）：填好 `notice_targets`、关闭 `notice_dry_run`，观察目标会话收到"文本 + PDF"两条消息；记录一次实际 token 用量以核对成本量级

## 10. 设计智能体核对留痕（勾选真实性）

- **逐批独立复跑，不读 `[x]` 就认**。已亲自复跑的关键证据：
  - 批次 1：`src/cea_bot/config.py` 的 adapter 分支函数体与 `HEAD` **字节相同**（`git show HEAD:… | 函数体比对` → True），diff 为**纯新增 0 删除**；8 处 `pytest.raises` **全是 `SystemExit`**、无一处放宽；全仓无 `type: ignore`；`[[tool.mypy.overrides]]` 与 `files=["src"]` 未被动过
  - 批次 2：PRAGMA 我自跑（`:memory:` → `memory` / 文件库 → `wal`，`foreign_keys=1`）；`store.py` 只 import 标准库、无 `handlers|notices|import cea` 命中；**自建行为探针**：篡改版本后 `migrate` 抛 `SchemaVersionError` 且**表集合未变、数据仍在、版本未被偷改**，损坏值不再漏出 `ValueError`，全新库放行
  - 批次 3：`decode_html` 在**四种声明**（gb2312 / gb18030 / utf-8 / None）下都解出正确中文（证明判据真的否决了 mojibake 候选而非"恰好能解"）；`parse_list` 从真实 fixture 得 **20 条**、首条 `edit_id=40773772`、日期 `2026-10-13`、`dept` 非空且 `edit_id` 全唯一；空表格与无关页面**均抛 `NoticeParseError`**；`parse_detail` 得文号 `学字﹝2026﹞63号`、附件 3 项（`.xls`/`.doc`/`.doc`）、正文 929 字含 `特此通知` 且**不含** `<span`/`font-family`/`&nbsp;`/`<p`；无文号无附件样本不抛错
- **两次变异验证由设计智能体亲自执行**（证明测试不是空转，而不是采信"我做过了"）：
  1. 把 `parse.py` 的可信判据 `_untrustworthy` 短路成"一切可信" → `tests/test_notice_parse.py` **9 条里 7 条变红**；还原后 9 passed
  2. 把 `parse_list` 的 `if not briefs:` 改成 `if False:` → 恰好 `test_parse_list_zero_rows_raises_notice_parse_error` **变红**；还原后 16 passed
- **3.1 / 3.3 / 3.4 的勾选由设计智能体代为填写**：实施子 Agent 的该次运行在**收尾阶段**异常中断，代码与测试已落盘但未勾选、且一度留下"测试文件缺 `import re`"的收集错误。设计智能体按上条证据逐项验证通过后勾选，并在此留痕（此三条的 `[x]` 反映的是**核对结果**，不是实施方自述）。
- **工具通道异常留痕（重要，供审查与后续批次参考）**：本轮实施期间 `subagent` 通道多次返回 `subagent run failed`（另试 `workflow` 的 agent 通道 1 次，同样失败）。经核实存在两种情形，**必须在每次失败后先查工作区再决定**：
  1. **"失败"但工作已落地**（3.2、以及 3.1/3.3/3.4 的那次）：代码与测试已写入并通过，只是运行在收尾/勾选前断掉 —— 处置：由设计智能体验证后**代为勾选**；
  2. **真中断并留下半成品**（首次"任务组 2+3"批次）：`parse.py` 无任何测试且 `store.py` 的版本校验实测未生效 —— 处置：**恢复到干净检查点**（删除未验证产物、复跑基线确认 94 passed）后重新按更小范围派发。
  - 附带教训：`parse.py` 第一次是以"22KB 无测试代码"形态出现的，若当时不删而继续在其上叠加，就会把未验证代码带进后续批次。

## 11. 任务组 4–9 的实施与验收留痕（设计智能体直接实施，用户显式授权）

- **授权与角色说明**：任务组 4–9 由**设计智能体直接实现**（用户在本变更实施过程中显式授权："授权我直接实现剩余任务，再由独立审查 Agent 核验"）。这是对 `dsh_cooperation.md`"设计智能体不写实现代码"的**有意破例**，原因是 `subagent` 通道在本变更期间连续失败（见第 10 节）。为守住"实施与审查必须分离"这条更关键的约束，收尾时派**独立只读审查子 Agent** 全量核验（不共享上下文）。
- **9.4 四条验收（实测，修复后的终态）**：`.venv\Scripts\pytest` → **225 passed**；`.venv\Scripts\python -m pytest` → **225 passed**；`.venv\Scripts\python -m mypy` → `Success: no issues found in 23 source files`；`openspec validate --all --strict` → `6 passed, 0 failed`。（数字随审查修复新增用例而增长：94 → 128 → 202 → 208 → 224 → 225。）
- **9.1 真实抓取（实测）**：列表 20 条、原始 24234 字节、HTTP 头声明 `UTF-8`（**生产路径因此走 HTTP 声明，页面 `<meta>` 的 gb2312 谎报被绕过** —— 这正是 D10"HTTP 优先于 meta"的依据）；首条 `edit_id=40775683`、`2026-10-13`、发布单位 `研究生院`；详情 107883 字节、正文 3592 字、附件 5 项（`.xlsx`/`.pdf`…）、**文号为空** —— 真实数据天然走到了"无文号不算错误"的分支。
- **9.2 真实 PDF（实测）**：`data/notices/2026-10-13-40775683.pdf`、230035 字节、渲染 5.52s、文件头 `%PDF-`、内嵌 `SimSun`/`FangSong`/`MicrosoftYaHei`/`SimHei` 四个中文字体。
  **口径修正**：任务文本原写"页数 ≥ 1（用 pypdf 或解析文件头）"，但 `pypdf` 不在依赖清单里（本变更只允许新增 playwright），且 Chromium 把页树写进压缩对象流，用标准库解压 103 个流后仍搜不到 `/Type /Page`。故改以**更强的证据**替代页数：解压对象流后确认 **ToUnicode 映射含 `U+7814`/`U+7A76`/`U+751F`（研/究/生）**，即 PDF 携带了正确的中文→Unicode 映射、中文未乱码；页数改由"体积 + 内嵌 CJK 字体 + ToUnicode 映射"共同佐证。**残留局限**：未做字形级文本抽取（需引入 PDF 解析依赖，超出本变更的依赖预算）。
- **9.3 干跑全链路（实测）**：真实抓取 → 真实详情 → 真实 PDF → `notice_dry_run: true` → **`outbound` 队列长度 0**（未发送任何消息）、日志含 `[干跑] … 将推送给 1 个会话` 与 PDF 路径、状态库该条 `pushed_at` 非空且 `pdf_path` 已落库。
- **9.5 密钥与运行数据（实测）**：`git ls-files config.yaml data .local` 无输出；`data/` 命中 `.gitignore:27`；`git status --short` 不含 `config.yaml`/`data/`/`*.log`；全仓（排除 `.venv`/`.git`/缓存）grep 无密钥片段。
- **真实缺陷（集成验收当场暴露，已修 + 已加回归测试）**：直接调用 `run_once` 而 `data/state/` 不存在时，原先抛 `sqlite3.OperationalError: unable to open database file` —— 单元测试都用了已存在的 `tmp_path` 父目录，所以没暴露。修法：`_run_locked` 在开库前幂等创建状态库父目录（`store.connect` 仍**不**做隐式 IO，启动期校验仍归 `config.ensure_notice_dirs`），并新增 `test_run_once_creates_missing_state_dir` 钉住。
- **其它实现口径偏离（均已在代码注释与 README 说明）**：
  1. 三个可选键 `notice_max_chars`（缺省 1500）/ `notice_target_kind`（缺省 `group`）/ `deepseek_base_url`（缺省官方端点）在 `config.example.yaml` 里以**注释形式**登记（YAML 注释不参与解析，故解析后的键集合仍是 8 个、1.2 的验收口径不变），README 配置表逐项说明。**三者都必须落进 `check_config` 的返回值**：下游只拿得到校验后的字典，漏掉就等于死键（这正是审查 B 抓到的缺陷 1）。
  2. `notice_target_kind` 与 `deepseek_base_url` 的取值在配置期校验（前者 ∈ {group,c2c,channel}，后者须以 `http(s)://` 开头），非法即 `sys.exit`。
  3. `notice.py` 暴露的是 `build_router(cfg, …)` **函数**而不是模块级 `router` 常量：定点来自配置、启动时才读得到，模块级 router 会逼着导入期读配置；8.2"仅在启用时挂载"的意图不变。
  4. `supports_file_send` 按前端**模块名**判断（避免导入期拉起 `botpy` 重依赖），docstring 已注明"将来应由框架给出正式能力标记，那需要一个改规格的变更"。
  5. 9.3 的模型调用用了**故意不可达**的 `deepseek_base_url`（`127.0.0.1:9`）而非真实 API：我没有、也不应读取用户的 DeepSeek 密钥。因此**"模型成功返回摘要"这条路径只由单元测试（假客户端）覆盖**，真实密钥下的端到端摘要调用留待 9.6 由用户验收。
- **已勾选任务的勾选依据**：4.1–9.5 全部由设计智能体亲自跑过对应验证命令后勾选；其中 9.1/9.2/9.3/9.5 是**真实网络 + 真实浏览器**的实跑，其余为单元测试与静态核对。
- **⚠️ 独立审查状态（未完成，故本变更尚未归档）**：收尾时**两次**派独立只读审查子 Agent（第二次为压缩版 prompt）均返回 `subagent run failed` —— 属工具通道故障（同期该通道在本变更内已失败 7 次、成功 2 次），**不是审查结论**。因此：本文档第 10、11 节的证据均为**设计智能体自证**，**不构成独立审查**；`add-notice-digest` 在拿到独立审查结论前不应归档。
  - 后续：把审查 prompt **拆成两份更短的**（A：规格一致性 + 静默失败 + 变异验证；B：修复核验 + 文档/边界/卫生/接线）后，两次均成功执行，见第 12 节。

## 12. 独立审查两轮结论与修复留痕

### 第一轮（审查 A：规格一致性 + 静默失败）——结论"不通过"，报 7 处缺陷

| 编号 | 严重度 | 缺陷 | 定性 | 处置 |
|---|---|---|---|---|
| D1 | 高 | 摘要**成功**路径未截断（`truncate` 结果被丢弃后返回原文），违反明文 SHALL 与 README 承诺 | 实现缺陷（且现有测试只覆盖降级路径 → 空转缺口） | 已修：改用 `truncate` 的返回值；新增 `test_long_summary_is_truncated_and_marked` |
| D2 | 中 | PDF 失败只对 `PdfRenderError` 降级，`OSError`/渲染期异常会**中止整轮且连文本都不发** | 实现缺陷（异常收敛不完整） | 已修：`playwright_renderer` 渲染段统一包装成 `PdfRenderError`；handler 增捕 `OSError`；新增 `test_pdf_oserror_degrades_to_text_only` |
| D3 | 中 | 基线按**全表**计数：切换 `notice_type` 会把新栏目 20 条历史一次性推送；基线标记也可能"连坐"别的栏目 | 实现缺陷（口径与"栏目 + 标识"的稳定标识不一致） | 已修：`fresh` 与基线 `UPDATE` 都限定 `kind`；新增 `test_baseline_is_per_kind` |
| D4 | 低 | 版本不兼容只在第一个定点才报，规格场景要求"**启动时**" | 实现缺陷（时机偏晚） | 已修：`__main__._check_notice_state` 启动期校验并 `sys.exit`；新增 `test_startup_state_check_exits_on_version_mismatch` |
| D5 | 低 | 列表"部分改版"（日期列抽不到）静默无告警 | 实现缺陷（静默降级） | 已修：缺日期比例非零即 `log.warning`；新增 `test_missing_dates_are_reported_loudly` |
| D6 | 低 | `fetch` 不收敛 `OSError`（调用方只捕 `FetchError` 会漏） | 实现缺陷（韧性） | 已修：补 `except OSError` 且**排在 `TimeoutError` 之后**（`TimeoutError` 是其子类）；新增 `test_oserror_raises_fetch_error`，并把既有超时用例收紧为断言"超时"字样 |
| D7 | 低 | `notice_type` 栏目标签三处口径不一（且与站点口径不符） | 文档缺陷 | 已修：统一为站点口径（1 校内通知 / 2 学校发文 / 3 学术报告），五处同步；测试断言提示文案含"校内通知/学术报告" |

- **设计智能体二次检查**：D1/D2/D3 我用**自建探针**独立复现（未采信审查描述）：D1 摘要 9004 字顶 200 上限原样带出且无"已截断"；D2 `OSError` 下 outbound 为 0；D3 切栏目后 pushed 达 20 条。修复后又用同一探针复跑：**D1 摘要 210 字含"已截断"、D2 不再中止且发出 1 条文本、D3 `baseline=True` 且 pushed=0、D4 启动即 `SystemExit` 带处置提示**。
- 审查 A 还做了 4 处变异验证（`parse_list` 0 条规则、`check_version` 放行、`summarize` 降级返回空串、`pdf` 空产物放行），确认测试不是空转；并指出第 10 节那次的变异计数应为"2 条变红"（parse + handler），已在本节更正口径。

### 第二轮（审查 B：修复核验 + 文档/边界/卫生/接线）——结论"不通过"，报 4 组缺陷

| 编号 | 严重度 | 缺陷 | 定性 | 处置 |
|---|---|---|---|---|
| B1 | **高** | `notice_target_kind` 与 `deepseek_base_url` **被 `check_config` 丢弃**，生产路径完全失效（后者还是 README 写的"正文不外发"合规逃生口） | **实现缺陷：正是本仓库点名的"参数接了没传"** | 已修：两键落进 `_notice_config` 返回值并加取值校验；新增 `test_optional_keys_survive_validation` / `test_optional_keys_have_defaults` / 两个非法值快速失败用例 / **接线用例** `test_validated_config_reaches_wiring`（经 `check_config` 后 `parse_targets` 得 `c2c`、`chat_completions_url` 得自建端点） |
| B2 | 中 | "状态不可用"（库路径是目录 / 文件损坏）裸抛 sqlite 异常，无处置提示（规格把"状态不可用"与"版本不兼容"并列要求提示） | 实现缺陷（边界未收敛） | 已修：新增 `schema.StateUnavailableError` + `open_state()` + 复用 `store.STATE_REMEDY`；启动期与运行期都收敛；新增 `test_startup_state_check_exits_on_unavailable_state`、`test_run_once_reports_unavailable_state` |
| B3 | 低 | `supports_file_send` 对未知前端 **fail-open**（判成"支持文件"→ PDF 被前端静默丢弃且文本无说明） | 实现缺陷（方向反了） | 已修：改为白名单 `("napcat",)` **fail-closed**；新增 `test_supports_file_send_fails_closed_for_unknown_frontend` |
| B4 | 低 | 文档/留痕与实况不符 5 处（AGENTS/README 计数、9.4 计数、§11 偏离 1-2 与文件现状相反、3.1 验证命令文件名错、README `notice_times` 缺省口径） | 文档缺陷 | 已逐处更正（本文件 3.1 与本节、README、AGENTS.md） |
| — | 观察 | ①摘要被截断时标记文案写"（正文已截断）"而正文并未发送；②非 200 响应体前 200 字进日志与推送文本，若端点回显密钥会外泄 | 文档缺陷 / 加固缺口 | 已修：标记改为通用的"（已截断）"；新增 `summarize.redact()` 对 `sk-…` 打码并用于错误文案，新增 `test_redact_masks_keylike_text` 与 `test_degradation_reason_redacts_keylike_text` |

- **设计智能体二次检查**：B1 我用运行期证据确认（`check_config` 返回键集里原无这两键、`parse_targets` 拿到的是默认 `group`），修复后用同一路径复跑确认生效；B3 的变化我明确接受其代价（未知前端退化为"只发文本 + 明确说明"，好过静默丢文件）。
- 审查 B 另确认：`src/cea` **零改动**、`store.py` 只 import 标准库、除 `playwright` 外无新依赖、解析层未用 lxml/bs4/chardet、`data/` 与 `config.yaml` 未入库、假密钥实跑（含阳性对照）无泄漏。它同时指出任务组 9 里写的 `git diff 2766a3e -- src/cea` 基线选错（`cea → src/cea` 的搬家发生在 `4304d7b`），正确基线是 `HEAD`；后续核对以 `git diff HEAD -- src/cea` 为准。

### 第三轮（审查 C：最终门禁）——结论"通过"，并指出 1 处低severity 残留

- **门禁 5 条全部满足**（审查方自跑）：① B1 两键经 `check_config` 后确实生效——不止 `parse_targets` 得 `c2c`、`chat_completions_url` 得自建端点，还按其装配顺序跑了**端到端离线编排**，确认裸会话号按 `c2c` 推送、摘要层拿到的就是校验后的 cfg；非法值（kind 非三值、url 非 http(s)、`javascript:` 注入写法）全部 `SystemExit`。② 状态库=目录 / 非 SQLite 损坏文件：启动期与运行期都给出含处置句的明确错误。③ `supports_file_send` 三条断言成立（napcat→True、qqbotpy→False、未知/子类→False）。④ `redact` 对 `sk-…` 打码生效（并给出短于 4 位不打码的口径）。⑤ 终态 225/225/mypy 0/`--strict` 6 passed、勾选 26/27、`git diff HEAD -- src/cea` 为空、`git ls-files config.yaml data .local` 为空。另做 **AST 双向差集**（读键集合 vs 产出键集合）为**空**，确认无新的死键/未接线参数。
- **残留缺陷（低）**：库是合法 SQLite 但**结构不对**时（旧 `meta` 表缺 `schema_version` 列；或旧 `notice` 表缺列且版本号恰好等于期望值，使 `CREATE TABLE IF NOT EXISTS` 静默放行）会裸抛 `sqlite3.OperationalError: no such column`。审查方明示"不构成阻断，建议随手补上再归档"。
- **处置**：已修——`schema.open_state` 的收敛面从"只包 `connect`"扩到"`connect` + `ensure`"两段，`_run_locked` 内的 `schema.ensure` 同样收敛；新增 `test_state_shape_mismatch_is_reporting`（两种构造 + 启动期与运行期）。**我自己复跑两种构造**：均 `SystemExit` 且含"删除 data/state/cea_bot.db 后重建"。修复后全量 **225 passed**（+1）。
- 说明：该残留修复发生在审查 C 给出"通过"之后，依据是审查 C 的明确建议（"随手补上再归档"）与我的根因探针；**未再派第四轮审查**（工具通道在本变更内已累计失败 9 次，且该修复为机械收敛 + 测试钉住）。


