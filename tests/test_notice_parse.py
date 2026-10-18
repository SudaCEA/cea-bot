# -*- coding: utf-8 -*-
"""通知解析层单测：任务 3.2 的 `decode_html`、任务 3.3 的 `parse_list`、任务 3.4 的 `parse_detail`。

**任务 3.3（`parse_list`）**：真实列表页样本 → 20 条、首条 `edit_id == "40773772"`、
首条日期 `2026-10-13`、首条标题含 `学生标兵`、发布单位非空；把页面里的**数据行删掉**
（导航行与搜索表单留着，页面仍然正常返回）后必须抛 `NoticeParseError` ——
0 条被判成"没有新通知"就是静默失效，这条用例专门把该分支钉住。

**任务 3.4（`parse_detail`）**：真实详情页样本 → 文号 `学字﹝2026﹞63号`、附件 3 项且扩展名
依次 `.xls`/`.doc`/`.doc`、正文含 `特此通知` 且**不含** `<span`/`font-family`/`&nbsp;`/`<p`
这类标签与富文本残留；另造"无文号无附件"的最小样本 → 不抛错且两个字段为空；
以及"标题/正文区域/附件清单被改坏"时必须响亮失败。

**任务 3.2（`decode_html`）**覆盖任务书要求的五个用例：

① 真实样本 `tests/fixtures/notice_list_type1.bin`：HTTP 声明 `charset=gb2312`、
   **实际字节是 UTF-8**（现场记录见 `tests/fixtures/README.md`）→ 仍要解出正确中文；
② 反向样本：声明 UTF-8、字节实为 GB18030 → 回退到 GB18030 后内容正确；
③ 二进制垃圾 `bytes(range(256)) * 2` → 抛 `NoticeParseError`（断言具体异常类，不放宽）；
④ `declared_charset=None` → 仍能解出列表页中文；
⑤ 把判据写成可核对的数字：`学生标兵` 出现次数 > 0 且 `\\ufffd` 计数 == 0。

另有 `test_list_fixture_is_really_the_encoding_trap`：反向确认样本**仍然是**"声明与实际不符"
的现场（万一将来有人把 fixture 换成转码后的文本，本文件其余用例会静默失去意义）。
该用例名被 `tests/fixtures/README.md` 逐字引用，改名要同步改文档。

约定：本文件不含协程场景，故无需 `asyncio.run` 包裹；**不引入** `pytest-asyncio`。
"""
import logging
import re
from pathlib import Path

import pytest

from cea_bot.notices import parse as notice_parse
from cea_bot.notices import pdf as notice_pdf
from cea_bot.notices.parse import (
    NoticeParseError,
    _normalize_date,
    decode_html,
    parse_detail,
    parse_list,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
LIST_FIXTURE = FIXTURE_DIR / "notice_list_type1.bin"
DETAIL_FIXTURE = FIXTURE_DIR / "notice_detail_40773772.bin"

# "GBK 误读 UTF-8 的特征码点"，与实现同口径但**独立列一份**：
# 直接 import 实现的常量会让测试变成"实现抄实现"，判据写错了也测不出来。
_MOJIBAKE_CHARS = "\u2032\u2033\u2039\u203a\u02c6\u02dc\u201a\u201e\u2020\u2021"

# 样本里一定出现的中文（站点实测存在的栏目词），作为"解对了"的锚点。
_ANCHOR = "学生标兵"


def _read_list_fixture() -> bytes:
    """读取列表页 fixture 的原始字节（不做任何转码预处理）。"""
    return LIST_FIXTURE.read_bytes()


# ---- ① 真实样本：声明 gb2312、字节实为 UTF-8 ----


def test_list_fixture_declared_gb2312_still_decodes_to_chinese():
    """① 按声明解不出来时回退 UTF-8：解出的中文正确、无替换字符、无乱码特征码点。"""
    text = decode_html(_read_list_fixture(), "gb2312")
    assert _ANCHOR in text, "回退解码后应含列表页里的中文栏目词"
    assert "\ufffd" not in text, "回退结果不得含替换字符（那就是乱码）"
    assert not any(ch in text for ch in _MOJIBAKE_CHARS), "回退结果不得含 GBK 误读特征码点"


def test_list_fixture_is_really_the_encoding_trap():
    """反向确认 fixture 仍是"声明与实际不符"的现场（README 逐字引用本用例名）。

    若样本被换成"声明与实际一致"的页面，上面那条用例就退化成"顺手成功"，失去意义；
    故这里直接断言：按声明的 `gb2312` 严格解码**必须失败**（实测在第 106 字节抛错）。
    """
    raw = _read_list_fixture()
    assert any(byte > 0x7F for byte in raw), "样本必须含非 ASCII 字节（否则它是纯 ASCII 页面）"
    with pytest.raises(UnicodeDecodeError):
        raw.decode("gb2312")


# ---- ② 反向样本：声明 UTF-8、字节实为 GB18030 ----


def test_gb18030_bytes_declared_utf8_falls_back_to_gb18030():
    """② 声明 UTF-8 但字节是 GB18030 → 依次回退，最终由 GB18030 解出正确内容。"""
    source = "校内通知 学生标兵"
    raw = source.encode("gb18030")
    with pytest.raises(UnicodeDecodeError):  # 先证明确实是"声明与实际不符"的反向样本
        raw.decode("utf-8")
    text = decode_html(raw, "utf-8")
    assert text == source
    assert _ANCHOR in text
    assert "\ufffd" not in text


# ---- ③ 二进制垃圾：响亮失败 ----


@pytest.mark.parametrize("declared", [None, "utf-8", "gb2312"], ids=["none", "utf-8", "gb2312"])
def test_binary_garbage_raises_notice_parse_error(declared):
    """③ 任意二进制垃圾都要抛 `NoticeParseError`（断言具体异常类，不放宽成任意异常）。"""
    raw = bytes(range(256)) * 2
    with pytest.raises(NoticeParseError):
        decode_html(raw, declared)


def test_error_message_mentions_candidates_and_no_garbage_is_returned():
    """③b 失败信息要能诊断（含候选判定），且绝不返回乱码。"""
    raw = bytes(range(256)) * 2
    with pytest.raises(NoticeParseError) as exc:
        decode_html(raw, "utf-8")
    message = str(exc.value)
    assert "UTF-8" in message and "GB18030" in message


# ---- ④ 未声明字符集 ----


def test_declared_none_still_decodes_list_fixture():
    """④ `declared_charset=None`（响应没给声明）→ 直接走回退链，仍解出同样的中文。"""
    raw = _read_list_fixture()
    text = decode_html(raw, None)
    assert _ANCHOR in text
    assert "\ufffd" not in text
    # 与"声明 gb2312"那条路径的结果应逐字一致：两条路最终都落在同一个候选上
    assert text == decode_html(raw, "gb2312")


# ---- ⑤ 判据写成可核对的数字 ----


def test_list_fixture_anchor_and_replacement_char_counts():
    """⑤ 真实样本解出的文本里：`学生标兵` 次数 > 0，替换字符计数 == 0。"""
    text = decode_html(_read_list_fixture(), "gb2312")
    anchor_hits = text.count(_ANCHOR)
    replacement_hits = text.count("\ufffd")
    assert anchor_hits > 0, f"锚点中文出现次数应 > 0，实测 {anchor_hits}"
    assert replacement_hits == 0, f"替换字符 U+FFFD 计数应为 0，实测 {replacement_hits}"
    # 顺带把"确实解出了一整页中文"钉成数字：解错编码时 CJK 字符不可能有上千个
    cjk_hits = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    assert cjk_hits > 1000, f"解出的 CJK 字符数应 > 1000（实测 {cjk_hits}），否则多半解错了编码"


# ===========================================================================
# 任务 3.3：`parse_list` —— 列表页 HTML → list[NoticeBrief]
# ===========================================================================

# 测试自己写一份"数据行"正则（不 import 实现的私有常量）：实现改了正则，测试也不会
# 跟着一起错，否则就变成"实现抄实现"。
_ROW = re.compile(r"<tr\b[^>]*>.*?</tr\s*>", re.S | re.I)


def _list_html() -> str:
    """真实列表页 fixture → 可信文本（**先过 3.2 的解码层**，与运行时走同一条路径）。"""
    return decode_html(LIST_FIXTURE.read_bytes(), "gb2312")


def _detail_html() -> str:
    """真实详情页 fixture → 可信文本（该页声明 utf-8，字节也是 utf-8，无编码陷阱）。"""
    return decode_html(DETAIL_FIXTURE.read_bytes(), "utf-8")


def _drop_data_rows(html: str) -> tuple[str, int]:
    """删掉列表页里所有含 `open_tz` 的数据行，返回 (改造后的 HTML, 删掉的行数)。

    模拟"站点改版"：页面照常返回（表格标签与搜索表单都还在），但数据行结构变了。
    """
    dropped = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal dropped
        if "open_tz" in match.group(0):
            dropped += 1
            return ""
        return match.group(0)

    return _ROW.sub(replace, html), dropped


def test_parse_list_real_fixture_20_briefs():
    """① 真实列表页：20 条；首条 edit_id / 发布日期 / 标题 / 发布单位逐项核对。"""
    briefs = parse_list(_list_html(), 1)
    assert len(briefs) == 20, f"列表首页应为 20 条，实测 {len(briefs)}"

    first = briefs[0]
    assert first.edit_id == "40773772"
    assert first.published_on == "2026-10-13"
    assert "学生标兵" in first.title
    # 标题属性与锚文本在本页一致；按"优先属性、缺失退回锚文本"的口径取到的是同一个字符串
    assert first.title == "关于开展苏州大学第十九届学生标兵选拔工作的通知"
    assert first.dept.strip() != ""
    assert first.dept == "党委学生工作部（学生工作处）"

    # 每一条都要有稳定标识与标题，否则增量判定就没有依据；同一页内标识不得重复
    assert all(b.edit_id and b.title for b in briefs), "每条都必须解析出稳定标识与标题"
    assert len({b.edit_id for b in briefs}) == 20

    # kind 来自"请求的是哪个栏目"，列表页里没有这个信息，必须原样透传
    assert {b.kind for b in briefs} == {1}
    assert {b.kind for b in parse_list(_list_html(), 2)} == {2}


def test_parse_list_zero_rows_raises_notice_parse_error():
    """② 数据行被删掉后必须抛 `NoticeParseError`（断言具体异常类，不放宽成任意异常）。

    这条把"0 条"这个分支钉死：一旦实现退化成 `return []`，上层会把它当成"没有新通知"，
    功能从此静默失效（design.md 的 Risks 第一条）。
    """
    mutated, dropped = _drop_data_rows(_list_html())
    assert dropped == 20, f"样本里应删掉 20 个数据行，实测 {dropped} 个"
    # 判"数据行锚点"是否清干净，判据是**调用形态** `open_tz('<id>')`：页面 `<script>` 里另有
    # 一句 `function open_tz(id){…}` 的函数定义（不在任何 `<tr>` 内、也不带引号实参），
    # 它是页面骨架而非数据行，改造后本就该留着 —— 故不能拿裸子串 `open_tz` 当判据。
    assert re.search(r"open_tz\(\s*'", mutated) is None, "改造样本里不应再残留任何数据行锚点"
    # 证明这是"页面照常返回、只是行结构没了"，而不是把样本换成空串
    assert "<!DOCTYPE" in mutated
    assert "<title>校内通知</title>" in mutated
    assert "查询" in mutated, "搜索表单行应保留（站点改版时它通常还在）"

    with pytest.raises(NoticeParseError):
        parse_list(mutated, 1)


# ===========================================================================
# 任务 3.4：`parse_detail` —— 详情页 HTML → NoticeDetail
# ===========================================================================

# "无文号无附件"的最小样本：结构与真实页面同形（标题 `<strong>` + `<td id="content">` 正文），
# 但首段不是文号、页面里没有任何 hidden input。
_MINIMAL_DETAIL = """<html><head><title>校内通知</title></head><body>
<table>
<tr><td><strong id="title" style="font-family:宋体;font-size:36px;">关于某项工作的通知</strong></td></tr>
<tr valign="top"><td style="font-family:仿宋_GB2312;font-size:16px;" id="content" class="_c">
<p style="text-align: center;"><span style="font-family: 仿宋; font-size: 16pt;">各学院（部）：</span></p>
<p style="text-indent: 32pt;"><span style="font-family: 仿宋; font-size: 16pt;">请遵照执行。</span></p>
</td></tr>
</table>
</body></html>"""


def test_parse_detail_real_fixture_fields():
    """③ 真实详情页：文号 / 附件 3 项与扩展名 / 正文含关键句且无标签与富文本残留。"""
    detail = parse_detail(
        _detail_html(), "40773772", 1, "http://example.invalid/gov/info_view_my.jsp?editId=40773772"
    )
    assert detail.edit_id == "40773772"
    assert detail.kind == 1
    assert detail.url.endswith("editId=40773772")
    assert detail.title == "关于开展苏州大学第十九届学生标兵选拔工作的通知"

    # 文号在正文首段：`学字﹝<span>2026</span>﹞<span>63</span>号` —— 中间夹着 `<span>`，
    # 去标签后必须拼回一个完整文号（这也是"去标签"是否真的做对了的判据）
    assert "学字" in detail.doc_no and "63" in detail.doc_no
    assert detail.doc_no == "学字﹝2026﹞63号"

    # 附件：展示名与保存名同序各 3 项，扩展名依次 .xls/.doc/.doc
    assert len(detail.attachments) == 3
    assert [f".{a.name.rsplit('.', 1)[-1]}" for a in detail.attachments] == [".xls", ".doc", ".doc"]
    assert detail.attachments[0].name.startswith("附件1：")
    assert "名单汇总表" in detail.attachments[0].name
    assert all(a.save_name for a in detail.attachments), "保存名必须与展示名同序配对"
    assert [a.save_name.rsplit(".", 1)[-1] for a in detail.attachments] == ["xls", "doc", "doc"]

    # 正文：关键句在，且**不含**任何标签或 Word 粘贴带来的富文本残留
    assert "特此通知" in detail.body_text
    assert len(detail.body_text.split("\n")) > 5, "正文应恢复出多个段落"
    for residue in ("<span", "font-family", "&nbsp;", "<p", "style=", "16pt", "</"):
        assert residue not in detail.body_text, f"正文不得残留 {residue!r}"


def test_parse_detail_without_doc_no_or_attachments_is_not_an_error():
    """④ 无文号无附件：不抛错，`doc_no` 为空串、`attachments` 为空 tuple（规格明文）。"""
    detail = parse_detail(
        _MINIMAL_DETAIL, "1", 3, "http://example.invalid/gov/info_view_my.jsp?editId=1"
    )
    assert detail.doc_no == ""
    assert detail.attachments == ()
    assert detail.title == "关于某项工作的通知"
    assert "各学院" in detail.body_text and "请遵照执行" in detail.body_text
    assert "<span" not in detail.body_text and "font-family" not in detail.body_text


@pytest.mark.parametrize(
    "sample",
    [
        # 标题的 `<strong>` 整块消失了（页面改版把标题换成了别的标签）
        _MINIMAL_DETAIL.replace(
            '<strong id="title" style="font-family:宋体;font-size:36px;">关于某项工作的通知</strong>',
            "关于某项工作的通知",
        ),
        # 正文容器的 id 变了 → 定位不到正文区域
        _MINIMAL_DETAIL.replace('id="content"', 'id="cont"'),
        # 附件只剩展示名、保存名整块消失 → 两个清单数量不一致（静默按位配对会指向错文件）
        _MINIMAL_DETAIL.replace(
            "</table>",
            '<input type="hidden" name="infoPicName" value="附件1：A.xls|附件2：B.doc|"/></table>',
        ),
    ],
    ids=["title-missing", "content-missing", "attachment-count-mismatch"],
)
def test_parse_detail_structural_damage_raises_notice_parse_error(sample: str) -> None:
    """⑤ 结构性损坏必须响亮失败，不返回"没有标题/没有正文/附件错位"的残缺详情。

    与 ④ 的分界：**字段缺失**（文号、附件）不是错误；**结构定位失败**是错误 ——
    残缺详情一旦推出去，该条目已被记成"已推送"，等于永久漏发（design.md 的 D7）。
    """
    with pytest.raises(NoticeParseError):
        parse_detail(sample, "1", 1, "http://example.invalid/gov/info_view_my.jsp?editId=1")


# ===========================================================================
# 日期归一化（任务 1.2–1.6）：`published_on` 一律是 ASCII `YYYY-MM-DD`
# ===========================================================================
#
# 规格 delta（`openspec/changes/normalize-notice-date-digits/specs/notice-digest/spec.md`）：
# 解析出的发布日期 SHALL 为 ASCII `YYYY-MM-DD`；页面给出非 ASCII 十进制数字或非 ASCII 连字符时
# SHALL 归一化并**记录日志**（SHALL NOT 静默改写）；归一化后仍不构成日期形式的按"发布日期缺失"
# 处理（字段为空），SHALL NOT 因此中断本次运行。
#
# 输入一律是**真实列表页**（`_list_html()`）加上"定向替换某一个日期单元格"：
# 不另造一套假 HTML，其余 19 行、导航行、脚本与编码陷阱都还是现场样本。

# 全角连字符 U+FF0D 是**日期分隔符**（本次新增进接受集），破折号 U+2014（正文排版常见）**不是**。
# 两者字形相近，写清楚才不会出现"测试自己把接受集放宽了"。
_HYPHEN_FULLWIDTH = "\uff0d"
_EM_DASH = "\u2014"

# 归一化告警的文案关键词：与既有的"缺少发布日期"告警刻意用词不同（design.md D4），
# 断言时只挑这一条，不会把"缺少发布日期"误当成"被归一化过"。
_NORMALIZED_KEYWORD = "归一化为 ASCII"

# 聚合告警里的 `N/M 条` 计数：`"1/20" in warnings[0]` 这类子串断言在 N=1 时区分不出
# "数对了"与"计数恒为 1"（审查 F2），故把两个数都抓出来逐个核对。
_NORMALIZED_COUNT = re.compile(r"列表页有 (\d+)/(\d+) 条日期被归一化为 ASCII")

# 站点日期单元格写法（实测）：`<td  class="listTableLine222" align="center" nowrap>2026-10-13</td>`。
# 实测 20 个数据行各一个，且**第 k 个单元格就是第 k 条 brief**（与 `open_tz` 锚点同序，
# 已用"只改第 k 个单元格 → 只有第 k 条 brief 的日期变化"核对过）。
# 日期串单独成组（group 2）：前置条件类断言要拿**页面原文**说话，不能拿被测代码的产物自证（审查 F9）。
# 但数字侧是 `\d`（与 `_DATE` 同源，会连全角数字一起命中），所以"样本是全 ASCII 日期"不能靠
# "匹配上了"来证明，调用方还得用 `_ASCII_DATE` 显式判一次（见 `_page_raw_dates`）。
_DATE_CELL = re.compile(
    r"(<td\b[^>]*\bnowrap\b[^>]*>)\s*(\d{4}-\d{2}-\d{2})\s*(</td\s*>)", re.I
)

# "页面原文就是 ASCII 规范形式"的判据：显式写 `[0-9]`，与 `pdf._ISO_DATE` 同口径 ——
# 用 `\d` 判 ASCII 等于什么都没判（它本来就会命中全角数字）。
_ASCII_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

# 全角数字：页面原文的两种非 ASCII 形态都由它构造（连字符另行处理）。
_FULLWIDTH_DIGIT = re.compile("[０-９]")
_FULLWIDTH_DIGITS = str.maketrans("0123456789", "０１２３４５６７８９")


def _fullwidth_digits(text: str) -> str:
    """`2026-10-01` → `２０２６-10-01`：**数字全角、连字符半角**（proposal 表格里的第一种形态）。"""
    return text.translate(_FULLWIDTH_DIGITS)


def _fullwidth_date(ascii_date: str) -> str:
    """`2026-10-13` → `２０２６－１０－１３`：数字与连字符**都**全角（中文输入法的实际产物）。"""
    return _fullwidth_digits(ascii_date).replace("-", _HYPHEN_FULLWIDTH)


def _page_raw_dates(html: str) -> list[str]:
    """按页面顺序取出列表页的**原始**日期串（第 k 个即第 k 条 brief 的日期）。

    读的是页面本身、不经过 `parse_list` 的归一化，故可以做"样本长什么样"的前置断言 ——
    拿被测代码的产物证明"样本全 ASCII"是循环论证（审查 F9：把样本换成全角页，旧写法照样通过，
    失败信息还会指向错误的方向）。本函数只负责取原文，"是不是全 ASCII"由调用方用
    `_ASCII_DATE` 显式判：这里的正则数字侧是 `\\d`，全角数字也会被命中。
    """
    return [match.group(2) for match in _DATE_CELL.finditer(html)]


def _replace_date_cells(html: str, replacements: dict[int, str]) -> str:
    """把若干数据行的日期单元格换成给定的**页面原文**（下标 → 原文，0 基）。

    一次换多个必须走这一条路径：`_DATE_CELL` 只认 ASCII 连字符，某个单元格一旦被换成全角写法
    就不再被命中，"换一个、重算一次下标"会让后续下标整体错位。故先在**原始页面**上把所有下标
    解析成位置，再从后往前替换（靠后的替换不影响靠前的偏移）。
    """
    matches = list(_DATE_CELL.finditer(html))
    for index in replacements:
        # 下界也要判：负下标会被 `finditer` 列表悄悄接受（`matches[-1]` 是最后一个单元格），
        # 那就是"改错了单元格却全绿"的静默失败。
        assert 0 <= index < len(matches), f"样本里应有第 {index} 个日期单元格，实测只有 {len(matches)} 个"
    out = html
    for index in sorted(replacements, reverse=True):
        match = matches[index]
        out = (
            f"{out[: match.start()]}{match.group(1)}"
            f"{replacements[index]}{match.group(3)}{out[match.end() :]}"
        )
    return out


def _replace_date_cell(html: str, index: int, raw_date: str) -> str:
    """单单元格版本（既有用例都走它）：把第 `index` 个（0 基）数据行的日期换成 `raw_date` 原文。"""
    return _replace_date_cells(html, {index: raw_date})


def _warning_count(message: str) -> tuple[int, int]:
    """从聚合告警文案里取出 `(被归一化条数, 本页条数)`；取不到就让断言以原文失败。"""
    match = _NORMALIZED_COUNT.search(message)
    assert match is not None, f"聚合告警文案里应含 `N/M 条` 计数，实测：{message}"
    return int(match.group(1)), int(match.group(2))


def _normalization_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """只挑**本次调用**产生的归一化告警：`caplog` 捕获的是整个会话的日志。

    调用方负责在触发解析前 `caplog.clear()`；这里再按 logger 名（全仓库共用的 `cea` 通道）
    与文案关键词过滤一道，避免把别的告警算进来。
    """
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "cea" and _NORMALIZED_KEYWORD in record.getMessage()
    ]


def test_fullwidth_date_is_normalized_to_ascii() -> None:
    """任务 1.2：全角数字 + 全角连字符 `２０２６－１０－０１` → **精确等于** `2026-10-01`。

    只归一化数字是不够的：中文输入法全角状态下连字符也是全角 `－`(U+FF0D)，
    若接受集不含它，这个串根本不会被日期正则命中，照旧落成"缺少日期"。
    """
    raw_date = f"２０２６{_HYPHEN_FULLWIDTH}１０{_HYPHEN_FULLWIDTH}０１"
    briefs = parse_list(_replace_date_cell(_list_html(), 0, raw_date), 1)
    assert len(briefs) == 20, "归一化不该改变条目数（更不该让整条通知消失）"
    assert briefs[0].edit_id == "40773772"
    assert briefs[0].published_on == "2026-10-01"


@pytest.mark.parametrize(
    "raw_date",
    [
        "٢٠٢٦-١٠-٠١",  # 阿拉伯-印度数字 U+0660–U+0669
        "२०२६-१०-०१",  # 天城文数字 U+0966–U+096F
    ],
    ids=["arabic-indic", "devanagari"],
)
def test_other_unicode_decimal_digit_families_are_normalized(raw_date: str) -> None:
    """任务 1.3：其它 `Nd` 数字族同样被归一化（`int()` 认全部 Unicode 十进制数字，见 D2）。

    断言的是**精确串** `2026-10-01`，不是 `isascii()` 这类宽断言 —— 后者只证明"变成 ASCII 了"，
    "变成哪个 ASCII 串"（例如数字被读错位）照样能混过去。
    """
    briefs = parse_list(_replace_date_cell(_list_html(), 0, raw_date), 1)
    assert briefs[0].edit_id == "40773772"
    assert briefs[0].published_on == "2026-10-01"


@pytest.mark.parametrize(
    "raw_date",
    [
        "2026-1-3",  # 未补零：接受集刻意不放宽
        "二〇二六年十月一日",  # 中文数字：不是 `Nd` 类，属另一类识别问题
        f"2026{_EM_DASH}10{_EM_DASH}01",  # 破折号 U+2014：排版符号，不是日期分隔符
    ],
    ids=["not-zero-padded", "chinese-numerals", "em-dash"],
)
def test_near_miss_forms_stay_missing_without_normalization_warning(
    raw_date: str, caplog: pytest.LogCaptureFixture
) -> None:
    """任务 1.4（负向，防"放宽过头"）：三种近似写法都**不算日期**，且不产生归一化告警。

    "不算日期"的观测点是 `published_on == ""` —— 这正是编排层统计"缺少发布日期"的口径
    （`handlers/notice.py:259` 按空串计数，口径由 `tests/test_notice_handler.py` 的
    `test_missing_dates_are_reported_loudly` 钉住）；解析层自己的新告警则必须**一条都没有**，
    否则"没归一化"和"归一化了"在日志里就分不清了。

    这三条只是"缺日期"，不是结构异常：解析继续、条目数不变、不抛异常。
    """
    html = _replace_date_cell(_list_html(), 0, raw_date)
    with caplog.at_level(logging.WARNING, logger="cea"):
        caplog.clear()
        briefs = parse_list(html, 1)
    assert len(briefs) == 20
    assert briefs[0].edit_id == "40773772"
    assert briefs[0].published_on == ""
    assert _normalization_warnings(caplog) == [], [
        record.getMessage() for record in caplog.records
    ]


def test_one_normalized_row_out_of_twenty_logs_exactly_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """任务 1.5（正向）：20 条里恰好 1 条被归一化 → **恰好一条**告警，且计数为 `1/20`。

    "恰好一条"就是"不刷屏"：逐条告警在一页 20 条全角时会刷 20 行，而聚合告警只说一次。
    """
    raw_date = f"２０２６{_HYPHEN_FULLWIDTH}１０{_HYPHEN_FULLWIDTH}０１"
    html = _replace_date_cell(_list_html(), 0, raw_date)
    baseline = parse_list(_list_html(), 1)
    with caplog.at_level(logging.WARNING, logger="cea"):
        caplog.clear()
        briefs = parse_list(html, 1)
    warnings = _normalization_warnings(caplog)
    assert len(warnings) == 1, [record.getMessage() for record in caplog.records]
    assert "1/20" in warnings[0], warnings[0]
    assert briefs[0].published_on == "2026-10-01"
    # 其余 19 条的日期逐字未变：归一化只动"写法不规范"的那一条，不碰已规范的
    assert [b.published_on for b in briefs[1:]] == [b.published_on for b in baseline[1:]]


def test_ascii_page_logs_no_normalization_warning(caplog: pytest.LogCaptureFixture) -> None:
    """任务 1.5（反向）：整页都是 ASCII 日期时**零条**归一化告警（不是"总是告警"）。

    前置条件（任务 5.4②，审查 F9）直接检查**页面原始日期串**，而不是检查 `parse_list` 的产物：
    产物已经被归一化过，拿它证明"样本里没有非 ASCII 日期"是循环论证 —— 把样本换成一页全角日期，
    旧写法照样通过，失败信息还会把人引到错误的方向。
    """
    page = _list_html()
    raw_dates = _page_raw_dates(page)
    # 两个条件缺一不可：① 每个数据行的日期单元格都**被页面形状正则命中**（含全角连字符的写法
    # 根本不会被命中，于是单元格数量会少 —— 这不是"样本干净"，恰恰是样本不干净）；
    # ② 命中的原文是 ASCII `YYYY-MM-DD`（`_DATE_CELL` 的数字侧是 `\d`，全角数字也会被命中，
    # 所以必须用 `[0-9]` 显式再判一次，否则一条全角数字的日期能伪装成 ASCII 混过前置）。
    assert len(raw_dates) == 20, f"样本应有 20 个日期单元格，实测 {len(raw_dates)} 个"
    not_ascii = [raw for raw in raw_dates if _ASCII_DATE.fullmatch(raw) is None]
    assert not not_ascii, (
        "现场样本本身应全是 ASCII 日期（2026-10-18 实测 60 条，见 proposal.md），"
        f"实测不符的有 {not_ascii}"
    )
    with caplog.at_level(logging.WARNING, logger="cea"):
        caplog.clear()
        briefs = parse_list(page, 1)
    assert len(briefs) == 20
    # 全 ASCII 的页面不该被改写：产物与页面原文**逐字相同**（归一化只动写法不规范的）
    assert [b.published_on for b in briefs] == raw_dates
    assert _normalization_warnings(caplog) == [], [
        record.getMessage() for record in caplog.records
    ]


def test_normalize_date_returns_none_for_non_decimal_digits() -> None:
    """任务 1.6（前半）：归一化认不出来的串返回 `None`，且 ASCII 规范串经同一条路径原样返回。

    为什么这条分支必须显式钉住：它**不是**"理论上不会发生"的兜底 —— 接受集一旦被谁放宽，
    非 `Nd` 的命中就会走到这里；那时的正确表现是"落成缺少日期"，而不是抛异常。
    """
    assert _normalize_date("abcd-10-01") is None
    assert _normalize_date("2026-10-01") == "2026-10-01"  # ASCII 输入原样返回（宽度 4/2/2）


def test_non_decimal_date_hit_degrades_to_missing_without_raising(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """任务 1.6（后半）：认不出来的日期命中必须落成"缺少日期"，**不抛异常、不记归一化告警**。

    规格："归一化后仍不构成 `YYYY-MM-DD` 形式的，按'发布日期缺失'处理，SHALL NOT 因此中断本次运行。"

    怎么让这条分支变得可达：真实 `_DATE` 只命中 `Nd` 数字（`int()` 全认），所以"命中了却认不出来"
    在今天的接受集下确实走不到。这里**临时把接受集放宽**成"小写字母也算数字"（模拟将来有人改宽
    `_DATE`），页面里那一条 `abcd-10-01` 于是真的被命中，`_normalize_date` 返回 None ——
    断言它在**真实页面路径**（`parse_list`，其余 19 条照常解析）上退化成"缺日期"。

    另外用一个探针记录归一化**真的被喂到了**这个串：否则"`published_on` 为空"可能只是因为 `_DATE`
    压根没命中，那样这条用例就是空过 —— 可达性要可观测，不能靠"应该会走到"。
    """
    html = _replace_date_cell(_list_html(), 0, "abcd-10-01")
    monkeypatch.setattr(notice_parse, "_DATE", re.compile(r"[a-z]{4}-\d{2}-\d{2}"))
    fed: list[str] = []
    original = notice_parse._normalize_date

    def spy(raw: str) -> str | None:
        fed.append(raw)
        return original(raw)

    monkeypatch.setattr(notice_parse, "_normalize_date", spy)
    with caplog.at_level(logging.WARNING, logger="cea"):
        caplog.clear()
        briefs = parse_list(html, 1)  # 不抛异常：这是字段缺失，不是结构异常
    assert "abcd-10-01" in fed, f"放宽后的 `_DATE` 该把该串交给归一化，实测喂进去的是 {fed}"
    assert len(briefs) == 20, "其余 19 条必须照常解析出来（一次运行不因一条日期异常而中止）"
    assert briefs[0].edit_id == "40773772"
    assert briefs[0].published_on == ""
    assert briefs[0].title, "日期退化不该连带把标题丢掉"
    assert _normalization_warnings(caplog) == [], [
        record.getMessage() for record in caplog.records
    ]


# ===========================================================================
# 任务 5.1–5.4（独立审查后的补强批次）：补上审查用运行期变异证明的三条覆盖缺口
# ===========================================================================
#
# 证伪手法是**运行期变异**（`.local/review_mut/mut.py`，不改任何被跟踪文件）：
#   $env:PYTHONPATH='.local/review_mut'; $env:MUT='fwlog'; .venv\Scripts\python -m pytest -q -p mut
# 三条缺口依次对应 `fwlog`（5.1）/ `sat1`（5.2）/ `dash2013`（5.3）：
# 每个变异都必须让**本节对应用例**变红，否则这几条断言就是摆设。

# D1 明令**不加**进连字符接受集的排版符号（`‐` `‑` `‒` `–` `―` `−` `﹣`，共 7 个）。
# 放宽它们＝放宽"什么算日期"：这些符号在正文与标题里常见（实测 fixtures 的 `——` 就在标题里），
# 一旦接受，正文片段就有机会被当成日期。故逐个钉住"不算日期、也不记归一化告警"。
_NOT_ACCEPTED_DASHES = ["\u2010", "\u2011", "\u2012", "\u2013", "\u2015", "\u2212", "\ufe63"]


@pytest.mark.parametrize(
    "raw_date",
    [
        "２０２６-10-01",  # proposal.md 表格里的原文（只有年份是全角数字）
        _fullwidth_digits("2026-10-01"),  # 数字全角、连字符仍半角（同一种形态，更彻底）
    ],
    ids=["year-fullwidth", "all-digits-fullwidth"],
)
def test_fullwidth_digits_with_ascii_hyphen_is_normalized_and_logged(
    raw_date: str, caplog: pytest.LogCaptureFixture
) -> None:
    """任务 5.1（审查 F1）：**数字全角、连字符半角**同样要归一化**并留痕**。

    这是几种写法里唯一会**静默抢走投递名额**的一种：旧 `_DATE` 的连字符侧只认 ASCII `-`，
    所以它**命中了**并原样落库 —— 排序按 TEXT 二进制比较（全角数字的 UTF-8 首字节 `0xEF`
    大于 ASCII `2` 的 `0x32`）会把它排到队首，却不进任何告警；"数字与连字符都全角"虽然也错，
    但旧实现压根不命中，至少有"缺少发布日期"告警，不是静默的。规格与 proposal 举例的正是这一种。

    只断言"值归一化成 `2026-10-01`"**不够**：把判据改成"只有整行含全角连字符才算被归一化"
    （变异 `fwlog`）之后，这条日期照样被归一化，只是不再记日志 —— 静默失效原样回来，
    而值断言看不出来。故这里同时钉住"恰一条告警"与"计数 `1/20`"。
    """
    # 输入形态自检：必须是"全角数字 + 半角连字符"，否则本用例与 1.2（两者都全角）重复
    assert _FULLWIDTH_DIGIT.search(raw_date) is not None, (
        f"输入必须是全角数字写法，否则这条用例什么都没测：{raw_date!r}"
    )
    assert _HYPHEN_FULLWIDTH not in raw_date, f"本用例针对的是'连字符半角'：{raw_date!r}"
    with caplog.at_level(logging.WARNING, logger="cea"):
        caplog.clear()
        briefs = parse_list(_replace_date_cell(_list_html(), 0, raw_date), 1)
    assert len(briefs) == 20, "归一化不该改变条目数（更不该让整条通知消失）"
    assert briefs[0].edit_id == "40773772"
    assert briefs[0].published_on == "2026-10-01"
    warnings = _normalization_warnings(caplog)
    assert len(warnings) == 1, [record.getMessage() for record in caplog.records]
    assert _warning_count(warnings[0]) == (1, 20), warnings[0]


def test_three_of_twenty_normalized_rows_log_exact_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """任务 5.2（审查 F2）：一页 20 条里改 **3** 条 → **恰一条**告警且计数为 `3/20`。

    计数必须能取到 1 以外的值：只钉 `1/20` 的话，"计数恒为 1"或"少算"都能混过去 ——
    审查的 `sat1` 变异（把日志参数饱和成 `min(1, N)`、返回值不动）在此前 290 条用例下全绿。
    另外 17 条的日期要**逐字不变**：归一化只动写法不规范的那些，不重排、不批量重写。
    """
    page = _list_html()
    baseline = parse_list(page, 1)
    raw_dates = _page_raw_dates(page)
    changed = (0, 5, 19)
    # 三条各自改写成"自己页面原文的全角写法"：归一化后应当回到该行原来的 ASCII 日期
    html = _replace_date_cells(page, {index: _fullwidth_date(raw_dates[index]) for index in changed})
    with caplog.at_level(logging.WARNING, logger="cea"):
        caplog.clear()
        briefs = parse_list(html, 1)
    assert len(briefs) == 20
    warnings = _normalization_warnings(caplog)
    assert len(warnings) == 1, [record.getMessage() for record in caplog.records]
    assert _warning_count(warnings[0]) == (3, 20), warnings[0]
    for index in changed:
        assert briefs[index].published_on == raw_dates[index], (
            f"第 {index} 条应归一化回页面原文 {raw_dates[index]!r}，"
            f"实测 {briefs[index].published_on!r}"
        )
    unchanged = [index for index in range(len(briefs)) if index not in changed]
    assert [briefs[index].published_on for index in unchanged] == [
        baseline[index].published_on for index in unchanged
    ], "未被改写的 17 条日期必须逐字不变"


@pytest.mark.parametrize(
    "dash", _NOT_ACCEPTED_DASHES, ids=[f"U+{ord(ch):04X}" for ch in _NOT_ACCEPTED_DASHES]
)
def test_dashes_d1_refuses_are_not_dates(dash: str, caplog: pytest.LogCaptureFixture) -> None:
    """任务 5.3（审查 F4）：D1 明令不加的 7 个连字符一律**不算日期**，且不产生归一化告警。

    此前只有 em dash（U+2014，正文排版里真的出现过的那一个）被钉住，其余 7 个只在 `_DATE` 的
    注释里写着"不加" —— 把 U+2013（en dash）悄悄加进接受集（变异 `dash2013`），此前全绿。

    口径与 1.4 一致：`published_on == ""` 就是"落成发布日期缺失"，而那正是编排层统计"缺少
    发布日期"的判据；同时解析层**一条**归一化告警都不该有。
    """
    raw_date = f"2026{dash}10{dash}01"
    html = _replace_date_cell(_list_html(), 0, raw_date)
    assert raw_date in html, "定向替换必须真的落到页面上，否则这条用例什么都没测"
    with caplog.at_level(logging.WARNING, logger="cea"):
        caplog.clear()
        briefs = parse_list(html, 1)
    assert len(briefs) == 20
    assert briefs[0].edit_id == "40773772"
    assert briefs[0].published_on == ""
    assert _normalization_warnings(caplog) == [], [
        record.getMessage() for record in caplog.records
    ]


def test_parsed_dates_always_satisfy_pdf_iso_date_contract() -> None:
    """任务 5.4①（审查 F7）：解析层的输出必须落在命名层 `_ISO_DATE` 的口径里。

    `proposal.md` 的 Impact 承诺了这条跨层一致性断言，此前没有落地：解析层说"我只产出 ASCII
    `YYYY-MM-DD`"，`pdf._date_stamp` 按 `[0-9]{4}-[0-9]{2}-[0-9]{2}` 取日期戳 —— 两句话对不对
    得上，只有把两层接起来断言才算数（两边各自的单测都绿、接起来错位，正是本仓库踩过的坑）。

    页面里混三种形态："数字全角 + 半角连字符"（会**静默**排到队首的那一种）、"数字与连字符都
    全角"（旧实现完全不命中）、"纯 ASCII"（不该被改写）；另加一条 em dash 排版符 → 落成空日期，
    用来钉住"**只有**空日期才允许回落 `000000-`"：非空日期若还出现这个前缀，说明解析层产出了
    命名层不认的形式，两层口径又分家了。
    """
    page = _list_html()
    baseline = parse_list(page, 1)
    raw_dates = _page_raw_dates(page)
    html = _replace_date_cells(
        page,
        {
            0: _fullwidth_digits("2026-10-01"),  # 数字全角、连字符半角
            1: _fullwidth_date("2026-10-01"),  # 数字与连字符都全角
            3: f"2026{_EM_DASH}10{_EM_DASH}01",  # 排版破折号：不是日期分隔符 → 落成缺失
        },
    )
    briefs = parse_list(html, 1)
    assert len(briefs) == 20
    assert briefs[0].published_on == "2026-10-01"
    assert briefs[1].published_on == "2026-10-01"
    assert briefs[2].published_on == raw_dates[2], "未改写的纯 ASCII 行必须逐字保持页面原文"

    missing = [brief for brief in briefs if brief.published_on == ""]
    assert len(missing) == 1, (
        f"只有被改成 em dash 的那一条该缺日期，实测空日期有 {len(missing)} 条："
        f"{[brief.published_on for brief in briefs]}"
    )
    assert missing[0].edit_id == baseline[3].edit_id

    for brief in briefs:
        name = notice_pdf.pdf_file_name(brief.published_on, brief.title)
        if brief.published_on:
            assert notice_pdf._ISO_DATE.match(brief.published_on) is not None, (
                f"解析层的输出必须满足命名层的 ASCII 口径，实测 {brief.published_on!r}"
            )
            assert not name.startswith("000000-"), (
                f"非空日期 {brief.published_on!r} 不该回落成占位日期戳：{name}"
            )
        else:
            assert name.startswith("000000-"), f"空日期才该回落占位日期戳：{name}"
