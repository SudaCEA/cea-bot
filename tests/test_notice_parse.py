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
import re
from pathlib import Path

import pytest

from cea_bot.notices.parse import (
    NoticeParseError,
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
