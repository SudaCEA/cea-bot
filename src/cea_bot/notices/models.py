# -*- coding: utf-8 -*-
"""通知领域的数据模型：纯数据、不可变（任务 3.2）。

三个 dataclass 都是 `frozen=True` 的纯数据载体，不含 IO、解析或校验逻辑 ——
解析在 `parse.py`、抓取在 `fetch.py`、状态在 `schema.py`。冻结的理由：这些对象要跨
"解析 → 入库 → 摘要 → 推送"多段流转，可变对象在中间被谁改过一眼看不出来，
不可变则"谁构造、谁负责"。

字段口径（3.3 / 3.4 的解析器共用；改这里必须同步改那两处）：

- `kind`：栏目类型，与配置键 `notice_type` 同口径（站点导航口径：1 = 校内通知，2 = 学校发文，
  3 = 学术报告）。
  它与 `edit_id` 一起构成增量状态的稳定唯一标识（"栏目 + 详情标识"，见 notice-digest 规格）；
  用 int 而不是 str，是为了与 `check_config` 校验后的 `notice_type` 类型一致。
- `edit_id`：详情页标识，取自列表页 `onclick="open_tz('<editId>')"` 里的**原始字符串**，
  此处刻意不做 int 转换：页面给的就是字符串，来回转换只会引入"前导零/超长数字"这类无谓风险。
- `published_on`：发布日期，**归一化后的 ASCII `YYYY-MM-DD`**（页面给出非 ASCII 十进制数字或
  全角连字符时，由解析层在入口归一化一次，见 `parse.py` 的 `_normalize_date` 与 design.md D2）；
  此处不解析成 `date`，以免把"页面格式变了"变成"解析直接抛错"（那是 3.3 的判断，不归数据模型管）。
- `url`：详情页 URL。列表页锚点没有 `href`（见 tests/fixtures/README.md），
  故该字段只能由业务层按 `edit_id` 拼出，这里只负责携带。
- `attachments`：附件清单用 tuple 而不是 list —— 与 `frozen` 配套，
  否则"外壳冻结、内容仍可改"，冻结就只剩形式。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Attachment:
    """一条附件：`name` 是页面上展示给用户看的名字，`save_name` 是站点上的保存名。

    两个名字都保留：推送时给用户看 `name`（人类可读），日后再想下载附件则要用 `save_name`
    （站点真正认的那个）；只在解析期丢掉其中一个，后面就补不回来了。
    """

    name: str
    save_name: str


@dataclass(frozen=True)
class NoticeBrief:
    """列表页的一条通知（只有"摘要"信息，正文需再抓详情页）。"""

    kind: int
    edit_id: str
    published_on: str
    title: str
    dept: str


@dataclass(frozen=True)
class NoticeDetail:
    """详情页的一条通知：标题、文号、正文纯文本与附件清单。

    `doc_no`（文号）与 `attachments` 允许为空：部分通知确实没有文号或没有附件，
    这**不是**错误（规格"无附件或文号的通知"：对应字段为空、解析继续成功）。
    """

    kind: int
    edit_id: str
    url: str
    title: str
    doc_no: str
    body_text: str
    attachments: tuple[Attachment, ...]
