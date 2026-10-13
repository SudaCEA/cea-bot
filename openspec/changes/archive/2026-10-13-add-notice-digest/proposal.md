# Proposal: add-notice-digest

## Why

校内通知（`file.suda.edu.cn` 的通知/发文栏目）目前只能人工去网站翻。希望 Bot 能定时检查，
把**新发布的发文**整理成「文本摘要 + PDF」推到 QQ 会话里，形成可留存的推送。

调研已确认的站点事实（2026-10-13 实测，均为公网可访问、无需登录）：

| 事实 | 证据 |
|---|---|
| **服务端渲染的静态 HTML，无需 JS、无需登录** | 一次普通 HTTP GET 即返回完整列表：`SInformAction.do?action=init&type=1`，20 条/页，共 **2024 页 / 40469 条** |
| 列表首页首条即最新 | 实测首条为当日发文；四个栏目 `type=1` 校内通知 / `2` 学校发文 / `3` 学术报告 |
| 详情页 URL 规则 | 行内 `onclick="open_tz('40773772')"`，`open_tz` 定义为 `window.open('/defaultroot/gov/info_view_my.jsp?whir_new_verifyCode=1&editId='+id)` |
| **正文是内联 HTML**（非附件） | 详情页正文在 `<td id="content" class="_c">`，含 Word 粘贴残留样式；标题在 `<strong class="…title">`；文号是正文首段 |
| 附件清单可直接解析 | `<input type="hidden" name="infoPicName" value="附件1：….xls|附件2：….doc|">` 与 `infoPicSaveName` 成对；附件为 `.doc`/`.xls` |
| ⚠️ 编码谎报 | 列表页声明 `charset=gb2312`，**实际字节是 UTF-8**；详情页声明 UTF-8 正常 → 解析必须做编码回退 |
| 无官方 PDF | 无 iframe、无 `window.print`，PDF 必须自己生成 |

因此原设想中的两处需要修正：**抓取不需要 Playwright**（普通 HTTP 即可，且可离线测试）；
**DeepSeek 不应拿来做"文本提取"**（正文是 HTML，本地提取零成本、零延迟），
它的价值在**结构化成摘要**（正文常三四千字，QQ 里直接刷屏不合适）。

## What Changes

- **新增 capability `notice-digest`**：定时检查可配置的校内通知栏目，把**尚未推送过**的条目
  整理为「要点摘要 + 正文 + PDF」推送到配置的会话
- **新增 handler** `src/cea_bot/handlers/notice.py`：只做编排；解析/抓取/摘要/PDF/状态各自成模块
- **新增业务层持久化底座** `src/cea_bot/store.py`（SQLite 开库/PRAGMA/建表登记，约 30–40 行，纯标准库），
  领域 schema 与增量语义留在 `src/cea_bot/notices/schema.py`（归属边界见 `design.md` D6）
- **新增运行时依赖 `playwright`**（含 `playwright install chromium` 的浏览器二进制）——**唯一新增依赖**，
  仅用于把详情页正文渲染为 PDF；抓取、解析、摘要、状态全部零新增依赖
- **配置新增键**（既有键名与位置不动，同步 `config.example.yaml` + `check_config` 校验）：
  `notice_targets` / `notice_type` / `notice_times` / `notice_db_path` / `notice_pdf_dir` /
  `deepseek_api_key` / `deepseek_model` / `notice_dry_run`
- **新增** `data/`（运行数据：SQLite 库与 PDF 归档）并加入 `.gitignore`
- **文档**：`README.md` 增加"校内通知推送"一节（含 `playwright install chromium` 一步）；
  `AGENTS.md`「代码在哪」增加 `store.py` 与 `data/` 两行

## Capabilities

### New Capabilities

- `notice-digest`: 定时检查校内通知栏目、做增量判定、生成摘要与 PDF 并推送到指定会话

### Modified Capabilities

（无）

本变更**不修改任何既有 capability 的行为**：`runtime` 的调度/装配、`messaging` 的双总线、
`dispatching` 的装饰器语义全部沿用，没有 `SHALL` 需要改。新增能力以 ADDED 需求写进
`specs/notice-digest/spec.md`。

## Impact

- **代码**：新增 `src/cea_bot/store.py`、`src/cea_bot/notices/`（`models` / `parse` / `fetch` /
  `schema` / `summarize` / `pdf`）、`src/cea_bot/handlers/notice.py`；`src/cea_bot/config.py`
  只**新增**校验分支（既有分支不动）；`cea` 框架**零改动**
- **依赖**：`playwright`（运行时，唯一新增）；不引 `openai` SDK（用已有 `aiohttp` 直连兼容接口）；
  不引 ORM/迁移框架（用标准库 `sqlite3`）
- **配置**：`config.example.yaml` 新增 8 个键；`config.yaml`（本地、已忽略）需同步填写
- **数据**：`data/state/cea_bot.db`（SQLite，WAL）、`data/notices/*.pdf`；`data/` 加入 `.gitignore`
- **规格**：新增 9 条 `SHALL` 需求（`notice-digest`），既有 26 条需求零 delta
- **风险**：① 目标站点改版 → 解析 0 条必须报错而非当"无新通知"（否则静默失效）；
  ② Playwright 浏览器未安装 → 启动自检与 README 双路径提示；
  ③ 重复推送 vs 漏发 → 取 at-least-once（宁重不漏），窗口与理由见 `design.md` D7；
  ④ 校内通知正文外发给第三方 API → 已确认允许，**只发正文文本、不发附件**，密钥不入库不落日志；
  ⑤ 磁盘增长（PDF 归档）→ 一期保留，清理策略另议
