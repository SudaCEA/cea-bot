# Design: add-notice-digest

## Context

动机与站点实测事实见 `proposal.md`。此处只写影响技术路线的现状约束：

- **框架只提供每日定点调度**：`@router.schedule("HH:MM")` 走单一共享时钟，`due_jobs` 同一分钟
  只触发一次、离线错过不补跑、**允许执行重叠**（`runtime` 规格明文）。没有间隔调度原语。
- **投递能力两前端不对等**：QQ 官方前端明确丢弃文件消息（`src/cea/adapters/qqbotpy.py:110-115`），
  NapCat 前端支持文件段；当前 `config.yaml` 的 `adapter` 为 `napcat`。
- **仓库既无持久化也无数据库**：全仓无 `sqlite`/ORM 痕迹，唯一状态是 `handlers/basic.py` 的进程内字典。
- **硬约束**（`AGENTS.md`）：依赖方向 `handlers → cea_bot → cea` 禁止反向；标准库优先、新增依赖须列明理由；
  纯函数优先、IO 隔离；不内置限流/调度原语；中文文档与规格。
- **目标站点结构脆弱**：靠 `onclick="open_tz('<id>')"`、`<td class="listTableLine222">`、`id="content"`
  这类字符串定位；一旦改版，静默解析出 0 条会把功能变成"永远没新通知"。

## Goals / Non-Goals

**Goals:**

- 定时发现有新发文 → 生成摘要与 PDF → 推送到配置的会话，**不重复、不遗漏、失败响亮**
- 抓取/解析/摘要/状态四层**全部可离线单测**（固定 HTML 字节样本 + 假 session + 假模型 + 内存库）
- 增量判定在**进程重启、检查重叠、单条推送失败**三种情况下都正确
- 新增依赖只允许一个（PDF 渲染），其余全部用标准库与已有依赖

**Non-Goals:**

- 不转附件（`.doc`/`.xls` → PDF 需 LibreOffice，属重依赖且非核心诉求；一期只列清单）
- 不翻页、不做历史全量回溯（40469 条无意义，只抓首页 + 增量）
- 不在 `cea` 框架里加持久化/调度原语（框架保持平台无关的消息管道）
- 不做通知的语义分类/订阅过滤（按关键词推给不同群）——二期
- 不做 PDF 归档的自动清理策略（一期保留，量小）
- 不引入 ORM / 迁移框架 / `openai` SDK

## Decisions

**D1. 抓取用普通 HTTP，不用 Playwright。** 实测列表页与详情页都是服务端渲染、无需登录，
一次 GET 即得完整 HTML。用已有的 `aiohttp` + 标准库 `html.parser`/正则解析即可，
且能拿真实 HTML 字节做离线 fixture。备选：Playwright 抓页面（多一层浏览器依赖、
慢一个数量级、还得处理浏览器安装，且没必要）。**Playwright 只用在 PDF 渲染这一件事上。**

**D2. PDF 用 Playwright（Chromium）`page.pdf()` 渲染详情页正文。**
用户已选定。理由：正文是 Word 粘贴的富文本（仿宋 16pt、缩进、表格），只有真浏览器引擎能保真；
`page.pdf()` 支持 A4、页边距、只渲染 `#content` 区域、可注入打印样式。备选：
① 系统 Edge/Chrome headless `--print-to-pdf`（零 pip 依赖，但探测浏览器路径、绑定本机环境，易碎）；
② 纯 Python（fpdf2 + 中文字体，样式丢失、中文字体需自带）。
代价：约 300MB 浏览器二进制（`playwright install chromium`，落在用户目录，不入库）+
仓库首个重依赖 → 必须写进 `README` 安装步骤，并在 `start.bat`/启动自检里对"浏览器未安装"给出可执行提示。

**D3. DeepSeek 只做摘要，不做"文本提取"。** 正文是 HTML，本地提取纯文本零成本、零延迟、
可离线测试；把提取交给云端 API 既慢又要为每次解析付费。摘要的价值是真的：正文常三四千字，
QQ 里直接刷屏不可读。用**已有的 `aiohttp`** 直连 OpenAI 兼容接口
（`POST https://api.deepseek.com/chat/completions`，`model: deepseek-flash`），
**不引 `openai` SDK**（一个 HTTP POST，引入 SDK 属净增依赖）。
prompt 与响应解析做成纯函数；模型名可配（`deepseek_model`）。
一期只用文本；`deepseek-flash` 支持图像理解，二期可用于扫描件来源。

**D4. 触发用 `@router.schedule` 每日多个定点，不自建间隔循环。**
用户选定每日多个定点（缺省 `09:00`/`15:00`/`21:00`）。用现有装饰器逐个注册即可，
零新机制、零新状态，且天然复用共享时钟。备选：`bot.create_task` 自建 `while + sleep` 间隔循环
（更及时，但要多写协作退出、失败退避，且框架明确不内置调度原语）。
代价：定点之间的新增要等下一次定点才推（最坏延迟 = 间隔）。缓解：每次运行推送**全部**未推送条目，
不限于最新一条。

**D5. 重叠运行靠业务侧 `asyncio.Lock` 串行化。** `runtime` 规格允许定时任务重叠执行
（"前一次未结束时新触发 SHALL 正常启动"），而本功能的"取增量 → 发送 → 记账"流程若并发进入，
会出现两条消息推同一条通知。框架**不提供**限流/互斥原语（规格明文，由业务自实现），
故在 handler 内用模块级 `asyncio.Lock`：拿不到锁就**跳过本次并记日志**，不排队堆积。

**D6. 增量状态用标准库 SQLite；底座归业务层，领域归 handler（用户确认的 B 方案）。**
选 SQLite 而不是 JSON 文件，理由不是性能（数据量极小），而是**原子性 + 幂等 + 可审计**：
JSON 全量重写在崩溃时会状态全丢；SQLite 用唯一键天然防重复入账、用条件更新保证并发下只有一个赢家、
用事务保证"登记/记账"不半途而废。`sqlite3` 是标准库 → **零新增依赖**（实测 3.50.4：WAL、UPSERT、
STRICT 表、部分索引均可用）。

归属边界（**这条边界要长期遵守**）：

| 归属 | 内容 | 理由 |
|---|---|---|
| `src/cea_bot/store.py`（业务层底座，约 30–40 行） | 开库（`connect`/`autocommit`）、`PRAGMA journal_mode=WAL` / `busy_timeout` / `foreign_keys`、`:memory:` 支持、建表/迁移的登记与执行次序 | 知识密集但面积极小；任何未来的持久化消费者都需要，写错代价高（漏 `busy_timeout` → 偶发 `database is locked`） |
| `src/cea_bot/notices/schema.py`（领域） | `notice` 表 DDL、`ix_notice_pending` 部分索引、幂等登记 SQL、待推送查询、抢占式记账 SQL | 领域特定且会持续增长 |
| `cea` 框架 | **不参与** | 框架是平台无关的消息管道；`runtime` 规格的取向是调度/限流等原语不内置，持久化同理 |

**硬规则：`cea_bot/store.py` 不得 import 任何 handler**（否则依赖方向倒挂）。
物理上**单库** `data/state/cea_bot.db`（不按 handler 分文件）：将来跨 handler 关联查询要同库同事务，
WAL 与清理只需一份，负载极低时一个 handler 阻塞另一个的风险可忽略；schema 隔离靠各自的表名前缀。

**D7. 崩溃语义取 at-least-once（宁重发、不漏发）。** 顺序是"先发送成功 → 再写 `pushed_at`"；
若在两者之间进程被杀，下次会重发一次。反过来（先记账后发）就是**漏发**。
学校通知漏发的代价明显高于重复发一次，故如此选；重复窗口仅存在于"发送成功后进程立刻死亡"，
并靠 D5 的锁与条件更新（`UPDATE … WHERE pushed_at IS NULL` + `rowcount` 检查）把并发重复降到零。

**D8. 首次运行只建立基线，不推送历史。** 库里空且列表首页有 20 条时，若直接"推送全部未推送条目"，
首次上线会连发 20 条（含当天已发的全部）。故：**首次运行登记首页条目为"已见"并标记为已推送、
只记日志不发送**，从下一次运行起推增量。代价：上线当天的最新发文不会自动补推（用户可手动触发一次）。
备选：首次推最新一条（可用，但"最新"是相对抓取时刻的，容易在部署时误发旧文）。

**D9. 投递两条消息（文本 + PDF 文件），并做能力探测。**
文本 = 标题 + 文号 + 发布日期/发布单位 + 要点摘要 + 附件清单 + 详情链接；
PDF 单独一条文件消息。当前端不支持文件（QQ 官方前端）时只发文本并在文本里说明"当前前端不支持文件"。
`bot.send(file=…)` 走 outbound 总线，失败只记日志不外抛（总线语义），故**必须在日志里留下可诊断信息**
（通知 id + 目标会话）。

**D10. 编码自适配策略。** 先按 HTTP 头/`<meta>` 声明解码，再用"是否出现替换字符/常见乱码序列"判据校验；
不确定则依次尝试 UTF-8、GB18030，取第一个能正确解码者；全部失败 → 报错中止本次（不拿乱码去解析）。
这个站点正是"声明 gb2312、实际 UTF-8"，故该分支**必须有测试**（用真实字节样本）。

**D11. 试运行开关 `notice_dry_run`。** 开启时完成抓取/解析/摘要/PDF 渲染并落盘，但**不向任何会话发送**，
把将发送的文本与 PDF 路径写入日志。理由：本功能的端到端验收会触碰真实 QQ 群，
而仓库要求"验收必须真跑"；干跑让"真实抓取 + 真实渲染 + 真实摘要"可在**无副作用**下验收。
它也是排查线上问题的第一手段。

**D12. 配置校验快速失败，密钥不落日志。** `notice_targets` 非空即视为启用该功能，
此时缺 `deepseek_api_key`、`notice_times` 格式非法、`notice_type` 不在 1/2/3、库/产物目录不可写
→ `check_config` 直接 `sys.exit` 给可执行提示（沿用既有 `check_config` 的风格与错误文案习惯）。
`deepseek_api_key` 只从 `config.yaml` 读（已 gitignore），**任何日志、异常、干跑输出都不得包含它**。
运行时 DeepSeek 调用失败属**运行期降级**（D3/需求 5），与配置期快速失败是两件事。

## Risks / Trade-offs

- [目标站点改版 → 解析 0 条被当成"无新通知"（静默失效）]
  → 需求层规定"0 条 = 页面结构异常，必须报错且不更新任何推送状态"；fixtures 断言标题/ID 抽取；
  干跑时把解析条数写进日志
- [Playwright 浏览器未安装 → 每次定点都失败]
  → README 安装步骤 + 启动自检提示 + 渲染失败降级为只发文本并告警（不静默）
- [重复推送 vs 漏发] → at-least-once（D7），并用锁 + 条件更新压缩重复窗口
- [校内通知外发第三方 API（合规）] → 用户已确认允许；只发正文文本、**不发附件**、不发发布单位之外的人员信息；
  密钥不入库不落日志；如需完全本地化，改 `deepseek_model` 指向自建兼容端点即可（配置层已留口子）
- [摘要/正文超长把 QQ 消息撑爆] → 正文与摘要都按可配置上限截断，并在文本里注明"已截断"
- [磁盘增长（PDF 归档）] → 一期保留 `data/notices/`，量级为每天几 MB；清理策略另立 change
- [重依赖把"零新增依赖"的传统打破] → 只此一个，且用途单一（PDF）；其余环节刻意保持零新增
- [单库被一个 handler 的 bug 拖住] → WAL 下读写不互斥；负载极低；若将来真出现跨 handler 干扰，
  再按"每 handler 一库"拆分（底座已隔离了连接与 PRAGMA，拆分成本低）

## Migration Plan

1. **依赖**：`pyproject.toml` 增加 `playwright`；`.venv\Scripts\python -m pip install -e ".[dev]" --index-url https://pypi.org/simple`
   后执行 `.venv\Scripts\playwright install chromium`
2. **数据**：首次运行自动 `CREATE TABLE IF NOT EXISTS` 建库（`data/state/cea_bot.db`）；
   `data/` 加入 `.gitignore`；`meta.schema_version` 不符时**明确报错**并提示删库重建（源头可重抓，代价可接受），不做自动迁移
3. **配置**：`config.example.yaml` 新增键 → 本地 `config.yaml` 按需填写；`notice_targets` 留空即不启用（默认关闭）
4. **验证**：先以 `notice_dry_run: true` 跑通全链路（真实抓取 + 真实 PDF + 真实摘要，零发送），
   再关掉干跑观察一次真实推送
5. **回滚**：`notice_targets: []` 即停用；删 `data/` 即清空状态；`git revert` 撤销代码
