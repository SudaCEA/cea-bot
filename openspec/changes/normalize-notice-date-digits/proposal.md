# Proposal

## Why

解析层用 `\d` 匹配发布日期（`src/cea_bot/notices/parse.py:197`）。Python 在 `str` 模式下 `\d`
匹配 Unicode `Nd` 类，**全角数字也认**，于是页面上的 `２０２６-１０-０１` 会被原样认下、原样落库、
原样显示在推送文本里。而下游两处都按 ASCII 口径处理，于是同一个日期在三层里表现不一致：

- **排序**：`schema.pending` 用 `ORDER BY published_on DESC`（TEXT 二进制比较），全角数字的
  UTF-8 首字节 `0xEF` 大于 ASCII `2` 的 `0x32` —— 这条**最旧**的通知会排到队首，
  抢走 `notice_max_per_run` 的名额，与规格"仅投递**最近的**若干条"相反；
- **产物命名**：`pdf.py` 的 `_ISO_DATE` 只认 `[0-9]`，同一个日期在文件名里回落成 `000000`；
- **告警**：`handlers/notice.py:259` 的 `missing_dates` 只统计**空串**，所以这类条目
  **一条日志都不会有** —— 属静默失效，不是"看起来正常"。

**为什么是现在**：这条是 `2026-10-14-rename-notice-pdf-with-title` 明确登记下来的遗留项
（该变更的 Impact 只覆盖命名，动解析层属另一处行为）。2026-10-18 的只读实测（`.local/probe_date_forms.py`）
表明触发条件**至今没有在真实数据里出现过**：三个栏目首页共 60 条，`published_on` 全部是 ASCII，
整页非 ASCII 的 `Nd` 字符数为 0。因此这是一次**预防性修复**：把三层口径收敛成一层，
让"站点哪天用全角输入日期"不再变成静默的排序错乱。

## What Changes

- **解析层只产出规范形式**：`published_on` 一律为 ASCII `YYYY-MM-DD`。页面给出 Unicode 十进制数字
  （含全角 `０-９`、阿拉伯-印度数字等 `Nd` 类）或全角连字符 `－`（U+FF0D）时，先归一化成 ASCII 等价形式。
- **归一化不静默**：一页里发生过几次归一化，就记一条聚合告警（口径与既有的"缺少发布日期"告警一致：
  `N/M 条 …`），而不是逐条刷屏、也不是什么都不说。
- **明确非目标**（继续按"发布日期缺失"处理，走既有告警，不在本次放宽）：
  - 未补零写法（`2026-1-3`）—— 放宽它会同时改动 `pdf.py` 已钉住的行为与 PDF 命名规格；
  - 中文数字日期（`二〇二六年十月一日`）—— 不是 `Nd` 类，属另一类识别问题。
- **不做历史数据迁移**：已落库的行不会被改写。当前状态库已重建、真实数据里没有这类行；
  若某个部署确有，处置方式与既有的状态版本处置一致（备份后删库重建基线），不为此写迁移代码。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `notice-digest`：在"通知列表抓取"要求里补一条**日期规范化**约束 —— 解析出的发布日期 SHALL 为
  ASCII `YYYY-MM-DD`；页面给出非 ASCII 数字或全角连字符形式时 SHALL 归一化，且 SHALL 记录日志
  （SHALL NOT 静默改写）；无法归一化时按"发布日期缺失"处理。这条把原先只存在于实现里的
  "日期长什么样"提升为规格约束，使排序与产物命名的 ASCII 前提不再依赖页面习惯。

## Impact

- `src/cea_bot/notices/parse.py`：`_DATE` 的匹配口径（数字类与连字符）、新增归一化步骤、
  每页一条聚合告警（该模块目前**没有** logger，需新增）；
- `src/cea_bot/notices/models.py`：`NoticeBrief.published_on` 的语义注释要改 ——
  现在写的是"页面原文"，归一化后它是"归一化后的 ASCII 形式"（docstring 与规格必须同口径）；
- `tests/test_notice_parse.py`：新增用例（全角数字、全角连字符、其它 `Nd` 数字族、
  归一化告警、以及"解析输出必然符合 `_ISO_DATE`"的一致性断言）；
- **不改**：`pdf.py`（`_ISO_DATE` 本就是 ASCII 口径，归一化后两边自动一致，
  其既有"非 ASCII → `000000`"的用例保持有效）、`schema.py`、`handlers/notice.py`
  （既有 `missing_dates` 告警不动）、任何配置键与缺省值；
- **不新增依赖**：`unicodedata` 属标准库。
