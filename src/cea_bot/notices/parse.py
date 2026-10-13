# -*- coding: utf-8 -*-
"""通知页面的解码层：把响应体的**原始字节**变成**可信文本**（design.md 的 D10）。

为什么单独成层：目标站点正是"HTTP 声明 gb2312、实际字节 UTF-8"（现场记录见
`tests/fixtures/README.md`）。按声明解码要么直接报错、要么得到乱码；而乱码继续往下解析会产出
"看起来正常"的错误标题，比直接报错更难发现。故本模块的契约是：**要么返回可信文本，
要么抛 `NoticeParseError`**，绝不把乱码交出去（规格："SHALL NOT 用乱码继续解析"）。

候选顺序（D10）：**声明 → UTF-8 → GB18030**，返回第一个通过可信判据的候选。
两类候选的试法不同，这是有意的：

- **声明**来自 HTTP 头或 `<meta>`，本样本已证明它**不可信**，故做"探测式解码"
  （`errors="replace"`，先容忍不可解字节把候选文本拿到手），再交给可信判据否决。
  这正是 D10 的字面口径："先按声明解码，再用**是否出现替换字符**/乱码序列判据校验"。
- **回退**候选是 D10 的"依次尝试 UTF-8、GB18030，取第一个**能正确解码**者"，故严格解码；
  严格解码抛 `UnicodeDecodeError` 即该候选不可信（判据①）。

本模块同时承载任务 3.3 / 3.4 的**结构解析**：

- `parse_list`：列表页 HTML → `list[NoticeBrief]`（日期 / 标题 / 发布单位 / 稳定标识）。
  解析到 0 条**必须抛** `NoticeParseError`：这是列表页改版的唯一信号，返回空列表会被上层
  当成"没有新通知"，功能静默失效（design.md 的 Risks 第一条）。
- `parse_detail`：详情页 HTML → `NoticeDetail`（标题 / 文号 / 正文纯文本 / 附件清单）。
  文号与附件缺失**不是**错误（规格明文），标题与正文区域定位不到才是。

只依赖标准库（`codecs` / `re` / `html.unescape`）：本变更唯一允许新增的依赖是 PDF 渲染（D2），
`lxml` / `beautifulsoup4` / `chardet` 都不在依赖清单里，**禁止**引入 —— 本模块也不需要它们。
"""
import codecs
import re
from html import unescape

from .models import Attachment, NoticeBrief, NoticeDetail

# 回退候选（元组顺序即优先级）：先 UTF-8，再 GB18030。GB18030 是 GBK/GB2312 的超集且更宽松，
# 故排在最后：候选越宽松，"解出来但是错的"概率越高，严格的先试。
_FALLBACK_ENCODINGS: tuple[str, ...] = ("utf-8", "gb18030")

# 判据②的字符集：任务书给定的"GBK 误读 UTF-8 的特征码点"。
# 记录实测依据（本仓库实际跑过的映射，不是猜的）：
#   ① GBK 系符号区：0xA1E4 / 0xA1E5 → ′ ″（`gb2312`/`gb18030` 均如此）；
#   ② 单字节代码页 cp1252 的对应位：0x82 → ‚、0x84 → „、0x86 → †、0x87 → ‡、
#      0x88 → ˆ、0x98 → ˜、0x8B → ‹、0x9B → ›。
# 两类都是"多字节字符被逐字节/错位切开"时的典型产物，中文通知正文里几乎不会自然出现，
# 故只要出现就判"解错了"——宁可响亮报错，也不把乱码送去解析。
_MOJIBAKE_CHARS = "\u2032\u2033\u2039\u203a\u02c6\u02dc\u201a\u201e\u2020\u2021"
_MOJIBAKE = re.compile(f"[{_MOJIBAKE_CHARS}]")

# 判据③：私用区 U+E000–U+F8FF。实测 `gb18030` 把 GBK 的用户自定义区（0xAAA1 起）映射到这里
# （0xAAA1 → U+E000），正常页面正文不含私用区码点；出现即说明字节被按错误的编码切开了。
_PUA = re.compile("[\ue000-\uf8ff]")

# 判据④：CJK 判据。范围按任务书给定：U+4E00–U+9FFF（基本区）、U+3400–U+4DBF（扩展 A）、
# U+3000–U+303F（CJK 符号与标点）。用 bytes 模式匹配原始字节，避免先解码再判断。
_NON_ASCII = re.compile(b"[\x80-\xff]")
_CJK = re.compile("[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f]")


class NoticeParseError(ValueError):
    """通知页面解析失败（本模块负责其中的"解码"环节）—— 响亮失败，不返回乱码。

    为什么必须响亮：规格要求"全部候选均失败时 SHALL 报错中止本次运行"。若这里退化成
    "返回能解出来的东西"，上层会拿乱码去抽标题，抽不到就变成"解析到 0 条"，
    最终表现成"永远没有新通知"这类静默失效（design.md 的 Risks 第一条）。

    为什么继承 `ValueError`：它的语义是"拿到的数据不对"，与"IO 失败"（抓取层的异常）区分开，
    调用方可以按需分别处理；同时这也是任务书给定的基类。
    """


def decode_html(raw: bytes, declared_charset: str | None) -> str:
    """把响应体原始字节解码为可信文本；全部候选都不可信时抛 `NoticeParseError`。

    `declared_charset` 是响应声明的字符集（HTTP `Content-Type` 或页面 `<meta charset>`），
    允许为 `None`（未声明），也允许是无法识别的名字或带参数的写法（如 `"gb2312; charset=x"`）——
    这两种情况按"没有声明"处理并继续回退，因为声明本身来自外部数据、不可信：
    为它抛错会把"站点又写错头"变成"功能整个挂掉"，而回退链完全能救回来。
    """
    rejection: list[str] = []
    for encoding, tolerate_errors in _candidates(declared_charset):
        text = _decode(raw, encoding, tolerate_errors=tolerate_errors)
        if text is None:
            # 判据①：解码报 UnicodeDecodeError → 该候选不可信（不在这里抛，继续下一个候选）
            rejection.append(f"{encoding}: 严格解码失败（UnicodeDecodeError）")
            continue
        reason = _untrustworthy(raw, text)
        if reason is None:
            return text
        rejection.append(f"{encoding}: {reason}")
    raise NoticeParseError(
        "通知页面解码失败：声明的字符集与回退候选（UTF-8 / GB18030）都解不出可信文本，"
        "拒绝用乱码继续解析。候选判定：" + "；".join(rejection)
    )


def _candidates(declared_charset: str | None) -> list[tuple[str, bool]]:
    """给出 `(编码名, 是否容忍解码错误)` 的候选序列：声明（探测式）→ UTF-8 / GB18030（严格）。

    声明与回退重名时只留前者：探测式解码在严格解码成功时给出**完全相同**的文本，
    重复试一次只会让失败信息里出现两条一样的记录，不会带来新信息。
    """
    candidates: list[tuple[str, bool]] = []
    declared = _normalize(declared_charset)
    if declared is not None:
        candidates.append((declared, True))
    candidates.extend(
        (encoding, False) for encoding in _FALLBACK_ENCODINGS if encoding != declared
    )
    return candidates


def _normalize(declared_charset: str | None) -> str | None:
    """把声明归一成标准编码名；空值、未知名字一律视为"没有声明"（返回 None）。

    用标准库 `codecs.lookup` 而不是自建别名表：别名体系（`gb2312` / `GB_2312-80` / `euc-cn` …）
    由标准库维护，自己抄一份必然漂移。取 `;` 前的一段是防御 HTTP 头把参数一起塞进来的写法。
    """
    if not declared_charset:
        return None
    name = declared_charset.split(";")[0].strip().strip("\"'")
    if not name:
        return None
    try:
        return codecs.lookup(name).name
    except LookupError:
        return None  # 不认识的声明 = 没有声明；这里抛错会因"站点又写错头"而整体失败


def _decode(raw: bytes, encoding: str, *, tolerate_errors: bool) -> str | None:
    """按给定编码解码；严格模式下解不出返回 None（判据①在此落实）。

    探测模式（`tolerate_errors=True`）只用于**声明**候选：不可解的字节会变成 U+FFFD，
    随后由判据②否决，从而得到"按声明解码得到乱码"这一可读诊断。回退候选一律严格解码。
    """
    if tolerate_errors:
        return raw.decode(encoding, errors="replace")
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError:
        return None


def _untrustworthy(raw: bytes, text: str) -> str | None:
    """可信判据：不可信时返回原因文案，可信时返回 `None`。

    判据①（解码报 `UnicodeDecodeError`）已在 `_decode` 里落实——严格解码失败直接淘汰候选，
    根本进不到本函数；本函数负责判据②③④，顺序即"最确定的先判"（替换字符最确定，
    CJK 判据最宽松）。
    """
    if "\ufffd" in text:
        return f"文本含 {text.count(chr(0xFFFD))} 个替换字符 U+FFFD（该编码下存在不可解字节）"
    hit = _MOJIBAKE.search(text)
    if hit is not None:
        return f"文本含 GBK 误读 UTF-8 的特征码点 U+{ord(hit.group()):04X}"
    hit = _PUA.search(text)
    if hit is not None:
        return f"文本含私用区码点 U+{ord(hit.group()):04X}（正常正文不含）"
    if _NON_ASCII.search(raw) is not None and _CJK.search(text) is None:
        return "原始字节含非 ASCII，但解出文本一个 CJK 字符都没有"
    return None


# ===========================================================================
# 任务 3.3：列表页解析（HTML → list[NoticeBrief]）
# ===========================================================================
#
# 站点实测结构（原始样本见 tests/fixtures/notice_list_type1.bin）：
#
#   <TR style="FONT-WEIGHT: normal;">
#     <td  class="listTableLine222" align="center" nowrap>2026-10-13</td>
#     <td  class="listTableLine222"><a target="_blank" style="cursor:hand;"
#          onclick="javascript:this.style.color='#666666';open_tz('40773772');document.cookie='TZGG40773772=40773772';"
#          title="关于开展苏州大学第十九届学生标兵选拔工作的通知">关于开展…通知</a></td>
#     <td  class="listTableLine222" align="center">党委学生工作部（学生工作处）</td>
#   </TR>
#
# 为什么用正则而不是 HTML 解析器：站点用大写 `<TR>`、属性之间有多空格与 `\r\n`、
# 正文里还有不合法的标签嵌套（Word 粘贴产物），标准库 `html.parser` 在这种输入上
# 要靠自建状态机才能定位 `<tr>`/`<td>`，而这里只需要"锚点 → 所在行 → 三个单元格"。
# 正则全部 IGNORECASE + DOTALL，容忍任意空白与换行。

# `<tr>` / `<td>` 的配对（同名标签配对，不做全局树解析）。
_TR_OPEN = re.compile(r"<tr\b[^>]*>", re.I)
_TR_CLOSE = re.compile(r"</tr\s*>", re.I)
_TD = re.compile(r"<td\b[^>]*>(.*?)</td\s*>", re.S | re.I)
# 锚点：属性串 + 锚文本。行内锚点**没有** href（README 记录），详情页 URL 由上层按 edit_id 拼。
_ANCHOR = re.compile(r"<a\b([^>]*)>(.*?)</a\s*>", re.S | re.I)
# 稳定标识的来源：`onclick="…open_tz('40773772')…"`。这里**不**把模式写死成 `\d+`：
# `edit_id` 在 models.py 里就是字符串，站点哪天换成非纯数字标识，不该表现成"解析到 0 条"。
_OPEN_TZ = re.compile(r"open_tz\(\s*'([^']+)'\s*\)")
# HTML 注释：先剔掉，避免注释里的 `<tr>`/`</td>` 把区域判断带偏。
_COMMENT = re.compile(r"<!--.*?-->", re.S)
# 属性取值：双引号（站点写法）与单引号（防御）都认。
_ATTR_TITLE = re.compile(r"\btitle\s*=\s*\"([^\"]*)\"|\btitle\s*=\s*'([^']*)'", re.I)
_ATTR_NAME = re.compile(r"\bname\s*=\s*\"([^\"]*)\"|\bname\s*=\s*'([^']*)'", re.I)
_ATTR_VALUE = re.compile(r"\bvalue\s*=\s*\"([^\"]*)\"|\bvalue\s*=\s*'([^']*)'", re.I)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TAG = re.compile(r"<[^>]*>")
_WS = re.compile(r"\s+")


def parse_list(html: str, kind: int) -> list[NoticeBrief]:
    """解析通知列表页，返回首页的全部条目（任务 3.3）；**一条都解析不出来就抛错**。

    `kind` 是栏目类型（与配置键 `notice_type` 同口径），只做透传：列表页里没有这个信息，
    它来自"请求的是哪个栏目"，故由调用方给出并写进每条 `NoticeBrief`。

    以**稳定标识**（`open_tz('<edit_id>')`）为遍历锚点，而不是以 `<tr>` 为锚点：
    页面里另有导航行、搜索表单行也是 `<tr>`（实测 32 个 `<tr>` 里只有 20 个数据行），
    按 `<tr>` 遍历就得写"这行看起来像不像数据行"的启发式；按 `open_tz` 遍历则是精确的。

    0 条即 `NoticeParseError`：规格要求"解析结果为 0 条 SHALL 视为页面结构异常并中止本次运行"。
    这条判断比任何字段校验都重要 —— 缺了它，页面改版会被上层读成"没有新通知"，永远静默。
    """
    briefs: list[NoticeBrief] = []
    seen: set[str] = set()
    for match in _OPEN_TZ.finditer(html):
        edit_id = match.group(1).strip()
        # 同一标识只取一次：嵌套表格会让同一个锚点落在"内层行"与"外层行"两个片段里，
        # 去重后不会把同一条通知算成两条（同一条通知的重复推送比漏一条更没道理）。
        if not edit_id or edit_id in seen:
            continue
        seen.add(edit_id)
        row = _row_around(html, match.start(), match.end())
        briefs.append(_brief_of_row(row, kind, edit_id))
    if not briefs:
        raise NoticeParseError(
            "通知列表页解析失败：一个条目都没解析出来（期望页面里存在 "
            "onclick=\"…open_tz('<edit_id>')…\" 的数据行）。这视为页面结构改版，"
            "拒绝返回空列表 —— 空列表会被上层当成\"没有新通知\"而静默失效。"
        )
    return briefs


def _row_around(html: str, anchor_start: int, anchor_end: int) -> str:
    """截取锚点所在的**最内层** `<tr>` 片段：往前找最近的开标签、往后找最近的闭标签。

    闭标签找不到时截到文档末尾：宁可把"残缺行"交给字段抽取（并让 0 条判据兜底），
    也不在这里抛错 —— 只要还有数据行能解析出来，本次运行就该继续。
    """
    opens = [m.start() for m in _TR_OPEN.finditer(html, 0, anchor_start)]
    start = opens[-1] if opens else 0
    close = _TR_CLOSE.search(html, anchor_end)
    end = close.end() if close is not None else len(html)
    return html[start:end]


def _brief_of_row(row: str, kind: int, edit_id: str) -> NoticeBrief:
    """从一个 `<tr>` 片段里取日期 / 标题 / 发布单位。

    单元格按位置取值（实测行内恰好 3 个 `<td>`：日期 / 标题锚点 / 发布单位），
    标题单元格的位置由"哪个单元格含 `open_tz`"确定，不写死下标 —— 站点多插一列
    （例如加个"阅读数"）时，标题与发布单位仍能对上。
    """
    cells = _TD.findall(row)
    title = ""
    for attrs, text in _ANCHOR.findall(row):
        if _OPEN_TZ.search(attrs) is None:
            continue
        # 标题优先取锚点的 title 属性（实测与锚文本一致，但属性不含内部标签），
        # 属性缺失或为空时退回锚文本。
        title = _attr(attrs, _ATTR_TITLE) or _plain(text)
        break

    title_cell = next((i for i, cell in enumerate(cells) if _OPEN_TZ.search(cell)), -1)

    published_on = ""
    for cell in (cells[:title_cell] if title_cell >= 0 else []):
        hit = _DATE.search(_plain(cell))
        if hit is not None:
            published_on = hit.group(0)
            break
    if not published_on:
        # 兜底：整行扫一次日期。抽不到就是空串（不是错误 —— 日期只用于排序与展示，
        # 真正的稳定标识是 edit_id；为它抛错会把"少一列"升级成"整个功能挂掉"）。
        hit = _DATE.search(_plain(row))
        published_on = hit.group(0) if hit is not None else ""

    dept = ""
    if 0 <= title_cell + 1 < len(cells):
        dept = _plain(cells[title_cell + 1])
    return NoticeBrief(
        kind=kind, edit_id=edit_id, published_on=published_on, title=title, dept=dept
    )


def _attr(fragment: str, pattern: re.Pattern[str]) -> str:
    """取属性值：优先双引号写法，其次单引号；没命中返回空串（调用方决定怎么兜底）。"""
    match = pattern.search(fragment)
    if match is None:
        return ""
    raw = match.group(1) if match.group(1) is not None else match.group(2)
    return unescape(raw or "").strip()


def _plain(fragment: str) -> str:
    """把一段 HTML 片段压成单行纯文本：去注释 → 解实体 → 去标签 → 归一空白。

    解实体放在去标签**之前**：`&lt;span&gt;` 这类"转义出来的标签"会在去标签这一步一并消失，
    输出里不会残留 `<span`（规格：正文与标题不得含 HTML 标签与富文本残留样式）。
    """
    text = _TAG.sub("", unescape(_COMMENT.sub("", fragment)))
    return _WS.sub(" ", text).strip()


# ===========================================================================
# 任务 3.4：详情页解析（HTML → NoticeDetail）
# ===========================================================================
#
# 站点实测结构（原始样本见 tests/fixtures/notice_detail_40773772.bin）：
#
#   <strong id="title" style="font-family:宋体;font-size:36px; color:#000080;" align="center">标题</strong>
#   …
#   <td style="font-family:仿宋_GB2312;font-size:16px;line-height:150%;" id="content" class="_c">
#     <p style="…"><span style="font-family: 仿宋,仿宋_GB2312; font-size: 16pt;">学字﹝<span>2026</span>﹞
#     <span>63</span>号</span><p style="…">…</p>…</p>
#   </td>
#   …
#   <input type="hidden" name="infoPicName" id="infoPicName" value="附件1：….xls|附件2：….doc|附件3：….doc|"/>
#   <input type="hidden" name="infoPicSaveName" id="infoPicSaveName" value="2026….xls|2026….doc|2026….doc|"/>

# 标题：优先"id/class 里含 title 的 `<strong>`"（站点写法），其次"style 里写 36px 的 `<strong>`"
# （实测 36px 是标题字号），后者只在正文区域**之前**找 —— 正文里也可能出现大字号 `<strong>`。
_TITLE_STRONG = re.compile(
    r"<strong\b[^>]*(?:id|class)\s*=\s*[\"'][^\"']*title[^\"']*[\"'][^>]*>(.*?)</strong\s*>",
    re.S | re.I,
)
_TITLE_BY_SIZE = re.compile(
    r"<strong\b[^>]*style\s*=\s*[\"'][^\"']*36px[^\"']*[\"'][^>]*>(.*?)</strong\s*>", re.S | re.I
)
# 正文区域：`<td … id="content" …>`。属性顺序不定，故 id 前后都允许有别的属性。
_CONTENT_TD = re.compile(r"<td\b[^>]*\bid\s*=\s*[\"']content[\"'][^>]*>", re.I)
# 同名标签配对用：`<td …>` 与 `</td>` 按出现顺序抵消深度。
_TD_PAIR = re.compile(r"<td\b[^>]*>|</td\s*>", re.I)
# 浏览器不显示的内容：先把 script/style 整块去掉，避免把脚本源码当正文。
_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.S | re.I)
# 块级标签当换行：Word 粘贴的正文全靠 `<p>` 分段，不去掉这些标签就分不出段落
# （文号"正文首段"的判断正是靠段落切分）。
_BREAK = re.compile(
    r"</?(?:p|div|tr|td|table|tbody|thead|tfoot|li|ul|ol|h[1-6]|blockquote|pre|br)\b[^>]*>",
    re.I,
)
_INPUT = re.compile(r"<input\b[^>]*>", re.I)
# 文号：中文机关代字 + 括号年份 + 序号 + "号"，例如 `学字﹝2026﹞63号`。
# 括号同时收全角 ﹝﹞〔〕【】（） 与半角 [] ()：站点用 ﹝﹞（U+FE5D/U+FE5E），
# 但不同年份/不同栏目页用的符号并不统一。要求整段**完全匹配**，宁可判"没有文号"
# 也不把含文号的长句子当文号（规格：文号缺失不是错误）。
_DOC_NO = re.compile(
    r"[\u4e00-\u9fff]{1,12}\s*[﹝〔\[【(（]\s*\d{2,4}\s*[﹞〕\]】)）]\s*\d{1,5}\s*号"
)
# 附件名的分隔符：站点用 `|`，且值末尾多一个分隔符（实测 `…doc|`）。
_SEGMENT_SEP = "|"


def parse_detail(html: str, edit_id: str, kind: int, url: str) -> NoticeDetail:
    """解析通知详情页（任务 3.4）：标题、文号、正文纯文本与附件清单。

    - `edit_id` / `kind` / `url` 由调用方给出：详情页里**没有**这三个信息
      （页面里不出现 editId，见 README），只能由列表页带来。
    - 正文按"`<td id="content">` 到与其配对的 `</td>`"截取，再去标签、解实体、分段、归一空白。
    - 文号缺失 → `doc_no == ""`；附件缺失 → `attachments == ()`；两者都**不是**错误（规格明文）。
    - 标题或正文区域定位不到 → `NoticeParseError`：这时如果硬返回，推出去的会是一条
      没有标题或没有正文的消息，而它已经被标记为"已推送"，等于永久漏发（D7 的取舍）。
    """
    text_html = _COMMENT.sub("", html)
    paragraphs = _paragraphs(_content_region(text_html))
    return NoticeDetail(
        kind=kind,
        edit_id=edit_id,
        url=url,
        title=_title(text_html),
        doc_no=_doc_no(paragraphs),
        body_text="\n".join(paragraphs),
        attachments=_attachments(text_html),
    )


def _title(html: str) -> str:
    """定位详情页标题；两级定位都失败即抛 `NoticeParseError`。"""
    match = _TITLE_STRONG.search(html)
    if match is not None:
        title = _plain(match.group(1))
        if title:
            return title
    content = _CONTENT_TD.search(html)
    head = html[: content.start()] if content is not None else html
    match = _TITLE_BY_SIZE.search(head)
    if match is not None:
        title = _plain(match.group(1))
        if title:
            return title
    raise NoticeParseError(
        "通知详情页解析失败：定位不到标题（期望 `<strong>` 的 id/class 含 title，"
        "或正文之前存在 font-size:36px 的 `<strong>`）。这视为页面结构改版，"
        "拒绝返回无标题的详情 —— 那样的消息推出去等于永久漏发。"
    )


def _content_region(html: str) -> str:
    """取正文区域：`<td … id="content" …>` 到与其配对的 `</td>` 之间的原始 HTML。

    用"同名标签配对计数"而不是"往后第一个 `</td>`"：正文是 Word 粘贴产物，可能内嵌表格
    （含 `<td>`），取第一个 `</td>` 会把正文**静默截断**。也不做全局 HTML 树解析 ——
    站点标签嵌套本就不合法（实测正文里 `<p>` 未闭合就嵌套下一个、末尾还多一个 `</p>`），
    只有"同名标签配对"这一条是可靠的（见 tests/fixtures/README.md）。
    """
    open_tag = _CONTENT_TD.search(html)
    if open_tag is None:
        raise NoticeParseError(
            "通知详情页解析失败：定位不到正文区域（期望 `<td … id=\"content\" …>`）。"
            "这视为页面结构改版，拒绝返回空正文。"
        )
    depth = 1
    for match in _TD_PAIR.finditer(html, open_tag.end()):
        if match.group(0).startswith("</"):
            depth -= 1
            if depth == 0:
                return html[open_tag.end() : match.start()]
        else:
            depth += 1
    raise NoticeParseError(
        "通知详情页解析失败：正文区域的 `<td id=\"content\">` 没有配对的 `</td>`（页面被截断？）。"
    )


def _paragraphs(body_html: str) -> list[str]:
    """正文 HTML → 段落列表：块级标签当换行 → 解实体 → 去标签 → 逐段归一空白。

    解实体放在去标签之前（同 `_plain`）：`&nbsp;` 变成 U+00A0 后被空白归一扫掉，
    输出里不会残留 `&nbsp;`；`&lt;span&gt;` 变成真标签后被去标签扫掉，输出里不会残留 `<span`。
    """
    broken = _BREAK.sub("\n", _SCRIPT_STYLE.sub("", body_html))
    plain = _TAG.sub("", unescape(broken))
    parts = [_WS.sub(" ", part).strip() for part in plain.split("\n")]
    return [part for part in parts if part]


def _doc_no(paragraphs: list[str]) -> str:
    """文号 = 正文**首段**里整段就是一个文号的那一段；没有就返回空串（不是错误）。

    往前多扫两段（实测文号就在第 1 段）：站点偶尔会在文号前加一行"发布日期"之类的引导行。
    整段完全匹配是刻意的严格口径 —— 正文里出现"…学字﹝2026﹞63号…"这种引用句时，
    不该把整句当文号。
    """
    for paragraph in paragraphs[:3]:
        if _DOC_NO.fullmatch(paragraph) is not None:
            return paragraph
    return ""


def _attachments(html: str) -> tuple[Attachment, ...]:
    """附件清单：解析 `infoPicName` / `infoPicSaveName` 两个 hidden input，按 `|` 切分同序配对。

    无附件（两个 input 都没有、值都为空）**不是**错误，返回空 tuple；
    但两个清单**数量不一致**时抛错 —— 那是"页面改版导致附件名与保存名错位"，
    静默按位配对会把用户指向错误的文件，属于必须响亮的失败。
    """
    fields: dict[str, str] = {}
    for tag in _INPUT.findall(html):
        name = _attr(tag, _ATTR_NAME)
        if name and name not in fields:
            fields[name] = _attr(tag, _ATTR_VALUE)  # 同名 input 取第一个（站点只有一组）
    names = _segments(fields.get("infoPicName", ""))
    saves = _segments(fields.get("infoPicSaveName", ""))
    if len(names) != len(saves):
        raise NoticeParseError(
            f"通知详情页解析失败：附件名与附件保存名数量不一致（infoPicName {len(names)} 项、"
            f"infoPicSaveName {len(saves)} 项）。两者同序配对，数量不符说明页面结构已改版，"
            "拒绝返回错位的附件清单。"
        )
    return tuple(Attachment(name=name, save_name=save) for name, save in zip(names, saves))


def _segments(value: str) -> list[str]:
    """把 hidden input 的值按 `|` 切成非空段（站点值末尾多一个 `|`，切出来是空串）。

    实体解码已在 `_attr` 里做过（属性值是 HTML 转义的），这里只切分与去空白。
    """
    return [part.strip() for part in value.split(_SEGMENT_SEP) if part.strip()]
