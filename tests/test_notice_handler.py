# -*- coding: utf-8 -*-
"""编排层单测（任务 8.1 / 8.2）：编排顺序、干跑不发送、重叠跳过、单条失败不静默、无文件能力降级。

依赖全部注入假件：列表/详情用**真实 fixture 字节**（解析层是真的），摘要/PDF 用假实现。
出站消息直接读框架的 `App.outbound` 队列 —— 断言的是**总线里到底有没有消息**，不是"调用过 send"。
"""
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from cea import App
from cea.adapters.napcat import NapCatAdapter
from cea.adapters.qqbotpy import QQBotpyAdapter
from cea_bot import store
from cea_bot.handlers import basic
from cea_bot.handlers import notice as handler
from cea_bot.notices import schema
from cea_bot.notices.fetch import FetchError, RawPage
from cea_bot.notices.parse import NoticeParseError
from cea_bot.notices.pdf import PdfRenderError, PdfResult
from cea_bot.notices.summarize import SummaryOutcome

FIXTURES = Path(__file__).parent / "fixtures"
# 列表页样本声明 gb2312、实际 UTF-8；这里**故意按 gb2312 声明**，让编码回退分支也被走到。
LIST_RAW = (FIXTURES / "notice_list_type1.bin").read_bytes()
DETAIL_RAW = (FIXTURES / "notice_detail_40773772.bin").read_bytes()

NEW_ID = "99999999"
NEW_ROW = (
    '<TR style="FONT-WEIGHT: normal;">'
    '<td class="listTableLine222" align="center" nowrap>2026-10-14</td>'
    f'<td class="listTableLine222"><a onclick="open_tz(\'{NEW_ID}\')" '
    'title="关于新增测试通知的通知">关于新增测试通知的通知</a></td>'
    '<td class="listTableLine222" align="center">测试单位</td>'
    "</TR>"
)
# 详情页样例缺少正文区域（`<td id="content">`）→ 解析必须抛错
BROKEN_DETAIL = "<html><body><strong id='title'>标题</strong></body></html>".encode()


def cfg_for(tmp_path: Path, **overrides: object) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "notice_targets": ["12345"],
        "notice_type": 1,
        "notice_times": ["09:00", "15:00"],
        "notice_db_path": str(tmp_path / "state.db"),
        "notice_pdf_dir": str(tmp_path / "pdf"),
        "deepseek_api_key": "sk-not-a-real-secret",
        "deepseek_model": "deepseek-flash",
        "notice_max_chars": 1500,
        "notice_dry_run": False,
    }
    cfg.update(overrides)
    return cfg


def make_deps(
    *,
    list_raw: bytes = LIST_RAW,
    detail_raw: bytes = DETAIL_RAW,
    usable: bool = True,
    pdf_error: Exception | None = None,
    detail_error: Exception | None = None,
    detail_error_ids: frozenset[str] = frozenset(),
) -> tuple[handler.NoticeDeps, list[tuple[str, object]]]:
    """构造注入依赖；`calls` 记录调用顺序，用于断言编排顺序。

    `detail_error_ids` 让**指定条目**的详情抓取失败（用于验证"失败条目不占投递名额"）。
    """
    calls: list[tuple[str, object]] = []

    async def fetch_list(session: Any, kind: int, **kwargs: Any) -> RawPage:
        calls.append(("fetch_list", kind))
        return RawPage(url="http://file.suda.edu.cn/list", raw=list_raw, declared_charset="gb2312")

    async def fetch_detail(session: Any, edit_id: str, **kwargs: Any) -> RawPage:
        calls.append(("fetch_detail", edit_id))
        if edit_id in detail_error_ids:
            raise FetchError(f"详情抓取失败（测试构造）：{edit_id}")
        if detail_error is not None:
            raise detail_error
        return RawPage(url=f"http://file.suda.edu.cn/detail?editId={edit_id}", raw=detail_raw, declared_charset="utf-8")

    async def do_summarize(detail: Any, cfg: Any, brief: Any = None, **kwargs: Any) -> SummaryOutcome:
        calls.append(("summarize", detail.edit_id))
        if usable:
            return SummaryOutcome(text="要点：\n- 甲", usable=True)
        return SummaryOutcome(text=f"{detail.title}\n\n{detail.body_text}", usable=False, reason="模型调用超时")

    async def do_render(html: str, out_path: Path, **kwargs: Any) -> PdfResult:
        calls.append(("render", out_path.name))
        if pdf_error is not None:
            raise pdf_error
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"%PDF-1.4\n" + b"x" * 200)
        return PdfResult(path=out_path, bytes_len=out_path.stat().st_size)

    deps = handler.NoticeDeps(
        fetch_list=fetch_list,
        fetch_detail=fetch_detail,
        summarize=do_summarize,
        render_pdf=do_render,
        open_db=store.connect,
        now=lambda: datetime(2026, 10, 14, 9, 0, 0),
    )
    return deps, calls


def _drain(app: App) -> list[Any]:
    messages = []
    while not app.outbound.empty():
        messages.append(app.outbound.get_nowait())
    return messages


def _rows(db_path: str) -> list[tuple[Any, ...]]:
    conn = store.connect(db_path)
    try:
        return conn.execute(
            "SELECT edit_id, pushed_at, pdf_path, last_error FROM notice ORDER BY edit_id"
        ).fetchall()
    finally:
        conn.close()


# ---- 8.1 首次运行建立基线：不发消息 ----

def test_first_run_builds_baseline_and_sends_nothing(tmp_path: Path):
    cfg = cfg_for(tmp_path)
    deps, calls = make_deps()
    app = App(adapter=None)
    result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps))
    assert result.baseline is True and result.pushed == ()
    assert _drain(app) == []  # 一条消息都不许发
    assert [name for name, _ in calls] == ["fetch_list"]  # 基线只抓列表
    rows = _rows(cfg["notice_db_path"])
    assert len(rows) == 20 and all(row[1] is not None for row in rows)


# ---- 8.1 增量推送：文本 + PDF 两条 ----

def test_new_notice_pushes_text_and_pdf(tmp_path: Path):
    cfg = cfg_for(tmp_path)
    first_deps, _ = make_deps()
    app = App(adapter=None)
    asyncio.run(handler.run_once(app.bot, cfg, deps=first_deps))  # 建立基线
    _drain(app)

    deps, calls = make_deps(list_raw=LIST_RAW + NEW_ROW.encode())
    result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps))
    assert result.pushed == (NEW_ID,) and result.failed == ()
    messages = _drain(app)
    assert len(messages) == 2
    text_msg, file_msg = messages
    assert text_msg.file is None and text_msg.image is None
    # 标题取自**详情页**（列表页标题会被站点截断），日期与单位取自列表行
    assert "关于开展苏州大学第十九届学生标兵选拔工作的通知" in text_msg.text
    assert "2026-10-14" in text_msg.text and "测试单位" in text_msg.text
    assert "要点：" in text_msg.text and "- 甲" in text_msg.text
    assert "附件1：苏州大学学生标兵推荐人选名单汇总表" in text_msg.text  # 附件清单只列名称
    assert file_msg.text == "" and file_msg.file is not None
    # 产物名 `YYMMDD-<标题>.pdf`（主规格「PDF 产物命名」）：标题取**列表行**的
    # `row.title`（"关于新增测试通知的通知"），与详情页标题刻意不同，传错标题会立刻暴露。
    assert file_msg.file_name == "261014-关于新增测试通知的通知.pdf"
    assert Path(file_msg.file).is_file()
    # 编排顺序：抓列表 → 抓详情 → 摘要 → 渲染
    assert [name for name, _ in calls] == ["fetch_list", "fetch_detail", "summarize", "render"]
    assert calls[3][1] == "261014-关于新增测试通知的通知.pdf"  # 渲染器收到的 out_path 就带标题


def test_pdf_out_path_uses_list_row_title(tmp_path: Path):
    """tasks 1.2：调用点必须把 `row.title` 传进命名 —— 用假渲染器捕获 `out_path` 作证据。

    同时钉住状态库里记下的 `pdf_path` 与盘上产物一致（库名 = 盘名，不留静默偏差）。
    """
    cfg = cfg_for(tmp_path)
    app = App(adapter=None)
    asyncio.run(handler.run_once(app.bot, cfg, deps=make_deps()[0]))  # 基线
    _drain(app)

    deps, calls = make_deps(list_raw=LIST_RAW + NEW_ROW.encode())
    asyncio.run(handler.run_once(app.bot, cfg, deps=deps))

    assert [payload for name, payload in calls if name == "render"] == [
        "261014-关于新增测试通知的通知.pdf"
    ]
    row = [row for row in _rows(cfg["notice_db_path"]) if row[0] == NEW_ID][0]
    assert row[2] == str(Path(cfg["notice_pdf_dir"]) / "261014-关于新增测试通知的通知.pdf")
    assert Path(row[2]).is_file()


def test_existing_pdf_target_is_overwritten_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """design D4 / 规格 Scenario「同日同标题重名」：名字里没有 editId，撞名必然发生。

    覆盖已存在文件**之前**必须记 WARNING（含 `edit_id` 与路径），不得静默覆盖 ——
    否则归档目录里会悄悄少一份，而且没有任何痕迹。

    告警文案刻意**只陈述事实**（"目标文件已存在"），不得断言原因：命中该分支不止"同日同标题"
    一种情形（同一条通知重试也会命中），且告警在渲染**之前**发出，随后渲染失败时覆盖并未发生。
    故这里额外断言文案里没有因果断言。
    """
    cfg = cfg_for(tmp_path)
    app = App(adapter=None)
    asyncio.run(handler.run_once(app.bot, cfg, deps=make_deps()[0]))  # 基线
    _drain(app)

    # 预期路径写死成字面量：从被测实现反推名字，会让"标题传错"这类缺陷漏过去。
    target = Path(cfg["notice_pdf_dir"]) / "261014-关于新增测试通知的通知.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"%PDF-1.4\n" + "先到的那一份".encode())

    deps, _ = make_deps(list_raw=LIST_RAW + NEW_ROW.encode())
    with caplog.at_level(logging.WARNING, logger="cea"):
        result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps))

    assert result.pushed == (NEW_ID,)  # 撞名只留痕，不中断推送
    hits = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING and "已存在" in record.getMessage()
    ]
    assert len(hits) == 1, [record.getMessage() for record in caplog.records]
    assert NEW_ID in hits[0] and str(target) in hits[0]  # 告警含 edit_id 与路径
    assert "撞名" not in hits[0]  # 只陈述事实，不断言原因（见 docstring）
    # 告警对应的"覆盖"确实发生了：文件被本次渲染重写（不是只喊了一声）
    assert target.read_bytes() == b"%PDF-1.4\n" + b"x" * 200


def test_push_marks_row_and_survives_restart(tmp_path: Path):
    cfg = cfg_for(tmp_path)
    deps, _ = make_deps()
    app = App(adapter=None)
    asyncio.run(handler.run_once(app.bot, cfg, deps=deps))  # 基线
    deps2, _ = make_deps(list_raw=LIST_RAW + NEW_ROW.encode())
    asyncio.run(handler.run_once(app.bot, cfg, deps=deps2))
    _drain(app)
    # 第三次：同一条不再推（重启后也一样，因为状态在文件库里）
    deps3, _ = make_deps(list_raw=LIST_RAW + NEW_ROW.encode())
    result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps3))
    assert result.pushed == () and _drain(app) == []


# ---- 8.1 干跑：不发送但记账 ----

def test_dry_run_sends_nothing_but_marks_pushed(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    cfg = cfg_for(tmp_path)
    deps, _ = make_deps()
    app = App(adapter=None)
    asyncio.run(handler.run_once(app.bot, cfg, deps=deps))  # 基线
    cfg["notice_dry_run"] = True
    deps2, _ = make_deps(list_raw=LIST_RAW + NEW_ROW.encode())
    with caplog.at_level(logging.INFO, logger="cea"):
        result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps2))
    assert result.pushed == (NEW_ID,)
    assert _drain(app) == []  # 干跑不投递任何消息
    assert any("[干跑]" in record.getMessage() for record in caplog.records)
    row = [row for row in _rows(cfg["notice_db_path"]) if row[0] == NEW_ID][0]
    assert row[1] is not None  # 已记账


# ---- 8.1 重叠运行：跳过而不是并发推送 ----

def test_overlapping_run_is_skipped(tmp_path: Path):
    cfg = cfg_for(tmp_path)
    deps, _ = make_deps()
    app = App(adapter=None)

    async def scenario() -> handler.RunResult:
        await handler._run_lock.acquire()  # 模拟"上一次检查尚未结束"
        try:
            return await handler.run_once(app.bot, cfg, deps=deps)
        finally:
            handler._run_lock.release()

    result = asyncio.run(scenario())
    assert result.skipped is True
    assert _drain(app) == []
    assert handler._run_lock.locked() is False  # 正常路径下不遗留锁


# ---- 8.1 PDF 失败降级：只发文本，且该条仍按已推送记账 ----

def test_pdf_failure_degrades_to_text_only(tmp_path: Path):
    cfg = cfg_for(tmp_path)
    deps, _ = make_deps()
    app = App(adapter=None)
    asyncio.run(handler.run_once(app.bot, cfg, deps=deps))  # 基线
    deps2, _ = make_deps(list_raw=LIST_RAW + NEW_ROW.encode(), pdf_error=PdfRenderError("浏览器启动失败"))
    result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps2))
    messages = _drain(app)
    assert result.pushed == (NEW_ID,) and len(messages) == 1
    assert "PDF 生成失败" in messages[0].text
    row = [row for row in _rows(cfg["notice_db_path"]) if row[0] == NEW_ID][0]
    assert row[1] is not None and row[2] is None


def test_pdf_oserror_degrades_to_text_only(tmp_path: Path):
    """`OSError`（磁盘满/权限）也必须降级为只发文本，而不是中止整轮。

    回归独立审查抓到的缺陷：handler 原先只捕 `PdfRenderError`，而 `render_pdf` 的目录创建、
    写盘与文件头读取都在渲染器之外，磁盘满会以 `OSError` 冒出 —— 结果是整轮中止、连文本都没发。
    """
    cfg = cfg_for(tmp_path)
    deps, _ = make_deps()
    app = App(adapter=None)
    asyncio.run(handler.run_once(app.bot, cfg, deps=deps))  # 基线
    deps2, _ = make_deps(
        list_raw=LIST_RAW + NEW_ROW.encode(), pdf_error=OSError(28, "No space left on device")
    )
    result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps2))
    messages = _drain(app)
    assert result.pushed == (NEW_ID,) and result.failed == ()
    assert len(messages) == 1 and "PDF 生成失败" in messages[0].text
    row = [row for row in _rows(cfg["notice_db_path"]) if row[0] == NEW_ID][0]
    assert row[1] is not None  # 仍按已推送记账（不会无限重试）


def test_missing_dates_are_reported_loudly(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """列表页"部分改版"（日期列抽不到）必须留痕，而不是静默降级。

    回归独立审查抓到的缺陷：原先只有"0 条"才算结构异常，日期缺失会静默变成空串
    （排序失效、PDF 文件名退化成 `000000-…`）且没有任何告警。
    """
    import re as _re

    doctored = _re.sub(rb"nowrap>\d{4}-\d{2}-\d{2}</td>", b"nowrap></td>", LIST_RAW)
    assert doctored != LIST_RAW, "前置条件：样本里的日期单元格必须被改动过"
    cfg = cfg_for(tmp_path)
    deps, _ = make_deps(list_raw=doctored)
    app = App(adapter=None)
    with caplog.at_level(logging.WARNING, logger="cea"):
        result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps))
    assert result.baseline is True  # 仍然正常建立基线
    assert any("缺少发布日期" in record.getMessage() for record in caplog.records)


# ---- 2.1 归一化后的日期不影响"最近优先"（走真实投递路径） ----

# 两条待推送记录：较早的全角日期 + 较新的 ASCII 日期。两者都早于 fixture 的 20 条基线，
# 且相差 4 天 —— 只要较早那条因日期写法被排到队首，下面的投递序列就会立刻反过来。
OLDER_ID = "77000001"
LATER_ID = "77000002"
LATER_PDF = "261005-较新的日期通知.pdf"


def _notice_row(edit_id: str, published_on: str, title: str) -> str:
    """按站点实测结构造一条列表行；`published_on` 传**页面原文**（全角写法原样放进去）。"""
    return (
        '<TR style="FONT-WEIGHT: normal;">'
        f'<td class="listTableLine222" align="center" nowrap>{published_on}</td>'
        f'<td class="listTableLine222"><a onclick="open_tz(\'{edit_id}\')" '
        f'title="{title}">{title}</a></td>'
        '<td class="listTableLine222" align="center">测试单位</td>'
        "</TR>"
    )


@pytest.mark.parametrize(
    "older_raw_date",
    [
        "２０２６－１０－０１",  # 中文输入法全角状态：数字与连字符**同时**全角（1.2 的形态）
        "２０２６-１０-０１",  # 只有数字全角（proposal / backlog 里"排到队首"的旧缺陷形态）
    ],
    ids=["fullwidth-hyphen", "fullwidth-digits"],
)
def test_normalized_date_keeps_recency_order_pdf_name_and_missing_date_quiet(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, older_raw_date: str
):
    """任务 2.1 / 规格 Scenario「归一化后的日期不影响"最近优先"」：走**真实投递路径**验证。

    为什么不直接比较两个日期字符串：被测行为是"编排层按 `published_on DESC` 投递"，
    日期只在解析层被归一化一次；只有断言**总线里的投递序列**，才能证明它没被日期写法带偏。

    两种全角形态都要跑，因为它们在**旧实现**里失败的方式不同：
    - 数字与连字符同时全角：旧 `_DATE` 不认全角连字符 → 整条落成"缺少发布日期"
      （排序恰好垫底，但 PDF 名回落 `000000-`、`missing_dates` 会告警）；
    - 只有数字全角：旧 `_DATE` 把原串认下并原样落库 → 全角串在 TEXT 二进制比较里大于
      ASCII，**排到队首**、抢在较新条目之前，PDF 名同样回落 `000000-`。
    两种形态的断言一致：较新者在前、较早那条的 PDF 名用归一化后的日期、且不出现
    "缺少发布日期"告警（后者同时是"解析层必然产出非空 ASCII 日期"的编排层回归）。
    """
    cfg = cfg_for(tmp_path)
    app = App(adapter=None)
    asyncio.run(handler.run_once(app.bot, cfg, deps=make_deps()[0]))  # 基线：fixture 的 20 条
    _drain(app)

    older_pdf = "261001-较早的全角日期通知.pdf"
    page = (
        LIST_RAW
        + _notice_row(OLDER_ID, older_raw_date, "较早的全角日期通知").encode()
        + _notice_row(LATER_ID, "2026-10-05", "较新的日期通知").encode()
    )
    deps, calls = make_deps(list_raw=page)
    with caplog.at_level(logging.WARNING, logger="cea"):
        caplog.clear()
        result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps))

    # ① 投递序列：较新的 ASCII 条目在前（旧实现会把全角日期那条排到队首）。
    assert result.pushed == (LATER_ID, OLDER_ID)
    delivered = [msg.file_name for msg in _drain(app) if msg.file is not None]
    assert delivered == [LATER_PDF, older_pdf]  # 断言总线里的真实顺序，不是函数返回值自证
    # ② 假渲染器实际收到的 out_path：较早那条按归一化后的 `2026-10-01` 命名，不回落 `000000-`。
    rendered = [payload for name, payload in calls if name == "render"]
    assert rendered == [LATER_PDF, older_pdf]
    assert not rendered[1].startswith("000000-")
    # ③ 归一化后的条目**不进** `missing_dates`（旧实现会把"同时全角"那种形态计成缺日期并告警）。
    missing = [r.getMessage() for r in caplog.records if "缺少发布日期" in r.getMessage()]
    assert missing == []


# ---- 8.1 摘要降级：正文原文照发 ----

def test_summary_degradation_still_sends_body(tmp_path: Path):
    cfg = cfg_for(tmp_path)
    deps, _ = make_deps()
    app = App(adapter=None)
    asyncio.run(handler.run_once(app.bot, cfg, deps=deps))  # 基线
    deps2, _ = make_deps(list_raw=LIST_RAW + NEW_ROW.encode(), usable=False)
    asyncio.run(handler.run_once(app.bot, cfg, deps=deps2))
    text = _drain(app)[0].text
    assert "摘要不可用" in text and "模型调用超时" in text
    assert "特此通知" in text  # 降级后是正文原文


# ---- 8.2 无文件发送能力：只发文本并说明 ----

def test_without_file_support_sends_text_only(tmp_path: Path):
    cfg = cfg_for(tmp_path)
    deps, _ = make_deps()
    app = App(adapter=None)
    asyncio.run(handler.run_once(app.bot, cfg, deps=deps))  # 基线
    deps2, _ = make_deps(list_raw=LIST_RAW + NEW_ROW.encode())
    asyncio.run(handler.run_once(app.bot, cfg, deps=deps2, file_support=False))
    messages = _drain(app)
    assert len(messages) == 1
    assert "不支持文件消息" in messages[0].text


# ---- 8.1 单条失败不静默：记库、进 failed、下次重试 ----

def test_detail_fetch_failure_is_recorded_not_swallowed(tmp_path: Path):
    cfg = cfg_for(tmp_path)
    deps, _ = make_deps()
    app = App(adapter=None)
    asyncio.run(handler.run_once(app.bot, cfg, deps=deps))  # 基线
    deps2, _ = make_deps(list_raw=LIST_RAW + NEW_ROW.encode(), detail_error=FetchError("boom"))
    result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps2))
    assert result.pushed == () and result.failed == (NEW_ID,)
    assert _drain(app) == []
    row = [row for row in _rows(cfg["notice_db_path"]) if row[0] == NEW_ID][0]
    assert row[1] is None and "boom" in row[3]  # 未记账 + 留了错误原因 → 下次重试


def test_detail_parse_failure_is_recorded(tmp_path: Path):
    cfg = cfg_for(tmp_path)
    deps, _ = make_deps()
    app = App(adapter=None)
    asyncio.run(handler.run_once(app.bot, cfg, deps=deps))  # 基线
    deps2, _ = make_deps(list_raw=LIST_RAW + NEW_ROW.encode(), detail_raw=BROKEN_DETAIL)
    result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps2))
    assert result.failed == (NEW_ID,) and _drain(app) == []
    row = [row for row in _rows(cfg["notice_db_path"]) if row[0] == NEW_ID][0]
    assert row[1] is None and "NoticeParseError" in row[3]


def test_list_structural_change_propagates(tmp_path: Path):
    """列表解析 0 条 = 页面改版：必须向上抛（框架会记日志），而不是当"没有新通知"。"""
    cfg = cfg_for(tmp_path)
    deps, _ = make_deps(list_raw=b"<html><body><p>nothing</p></body></html>")
    app = App(adapter=None)
    with pytest.raises(NoticeParseError):
        asyncio.run(handler.run_once(app.bot, cfg, deps=deps))
    assert _drain(app) == []


def test_startup_state_check_exits_on_unavailable_state(tmp_path: Path):
    """"状态不可用"（路径是目录 / 文件损坏）也要在启动期给出可执行提示，而不是裸 sqlite 异常。

    回归独立审查抓到的缺口：`ensure_notice_dirs` 只校验**父目录**可写，
    "库路径本身是目录"能穿过启动校验，直到 `store.connect` 才炸出原始异常。
    """
    from cea_bot.__main__ import _check_notice_state

    as_dir = tmp_path / "as_dir"
    as_dir.mkdir()
    with pytest.raises(SystemExit) as exc:
        _check_notice_state({"notice_db_path": str(as_dir)})
    assert "状态库不可用" in str(exc.value)
    assert "删除 data/state/cea_bot.db 后重建" in str(exc.value)

    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"this is definitely not a sqlite database" * 20)
    with pytest.raises(SystemExit) as exc2:
        _check_notice_state({"notice_db_path": str(corrupt)})
    assert "状态库不可用" in str(exc2.value)


def test_run_once_reports_unavailable_state(tmp_path: Path):
    """运行期同样收敛：状态库打不开时抛带处置提示的异常，而不是 sqlite 的原始错误。"""
    as_dir = tmp_path / "state_dir"
    as_dir.mkdir()
    cfg = cfg_for(tmp_path, notice_db_path=str(as_dir))
    deps, _ = make_deps()
    app = App(adapter=None)
    with pytest.raises(schema.StateUnavailableError) as exc:
        asyncio.run(handler.run_once(app.bot, cfg, deps=deps))
    assert "删除 data/state/cea_bot.db 后重建" in str(exc.value)
    assert _drain(app) == []


def test_state_shape_mismatch_is_reported(tmp_path: Path):
    """库是合法 SQLite 但**结构不对**（旧库/被改过的库）也要给处置提示。

    回归最终审查的残留发现：`CREATE TABLE IF NOT EXISTS` 对同名旧表是静默放行，
    随后在列名上炸出 `sqlite3.OperationalError: no such column` —— 属于规格里的"状态不可用"，
    不能裸抛。两种触发方式都覆盖：旧 `meta` 表缺列、旧 `notice` 表缺列（版本号恰好等于期望值）。
    """
    from cea_bot.__main__ import _check_notice_state

    # ① meta 表缺 schema_version 列
    bad_meta = tmp_path / "old_meta.db"
    conn = store.connect(str(bad_meta))
    try:
        conn.execute("CREATE TABLE meta (owner TEXT PRIMARY KEY)")
    finally:
        conn.close()
    with pytest.raises(SystemExit) as exc:
        _check_notice_state({"notice_db_path": str(bad_meta)})
    assert "状态库不可用" in str(exc.value)
    assert "删除 data/state/cea_bot.db 后重建" in str(exc.value)

    # ② notice 表是旧结构（版本号刚好等于当前期望值，绕过版本校验后在索引语句上炸）
    bad_notice = tmp_path / "old_notice.db"
    conn = store.connect(str(bad_notice))
    try:
        conn.execute("CREATE TABLE meta (owner TEXT PRIMARY KEY, schema_version INTEGER NOT NULL)")
        conn.execute("INSERT INTO meta VALUES ('notice', ?)", (len(schema.STATEMENTS),))
        conn.execute("CREATE TABLE notice (edit_id TEXT PRIMARY KEY)")
    finally:
        conn.close()
    with pytest.raises(SystemExit) as exc2:
        _check_notice_state({"notice_db_path": str(bad_notice)})
    assert "状态库不可用" in str(exc2.value)

    # 运行期同样收敛（不是只有启动期）
    cfg = cfg_for(tmp_path, notice_db_path=str(bad_meta))
    deps, _ = make_deps()
    app = App(adapter=None)
    with pytest.raises(schema.StateUnavailableError) as exc3:
        asyncio.run(handler.run_once(app.bot, cfg, deps=deps))
    assert "删除 data/state/cea_bot.db 后重建" in str(exc3.value)


def test_supports_file_send_fails_closed_for_unknown_frontend():
    """未知前端必须判为**不支持文件**（fail-closed）：误判成"支持"会让 PDF 被前端静默丢弃。"""

    class UnknownAdapter:
        async def run(self, inbound: Any, outbound: Any) -> None: ...

        async def send(self, msg: Any) -> None: ...

    assert handler.supports_file_send(UnknownAdapter()) is False


def test_validated_config_reaches_wiring(tmp_path: Path):
    """经 `check_config` 之后，可选键必须真的流向消费点（回归："参数接了没传"）。"""
    from cea_bot.config import check_config
    from cea_bot.notices import summarize as summary_mod

    cfg = check_config(
        {
            "appid": "a",
            "secret": "s",
            "notice_targets": ["999"],
            "notice_target_kind": "c2c",
            "deepseek_base_url": "http://127.0.0.1:9/",
            "notice_times": ["09:00"],
            "deepseek_api_key": "sk-not-a-real-secret",
        }
    )
    chats = handler.parse_targets(cfg, "napcat")
    assert [(c.kind, c.id) for c in chats] == [("c2c", "999")]
    assert summary_mod.chat_completions_url(cfg) == "http://127.0.0.1:9/chat/completions"


def test_run_once_when_disabled_raises(tmp_path: Path):
    cfg = cfg_for(tmp_path, notice_targets=[])
    deps, _ = make_deps()
    app = App(adapter=None)
    with pytest.raises(ValueError):
        asyncio.run(handler.run_once(app.bot, cfg, deps=deps))


def test_startup_state_check_exits_on_version_mismatch(tmp_path: Path):
    """启动期就要发现状态库不兼容（规格场景写的是"启动时明确报错"）。

    回归独立审查抓到的缺陷：原先只在第一个定点触发时才 `schema.ensure`，
    从启动到首个定点之间运维看不到任何提示。
    """
    from cea_bot.__main__ import _check_notice_state

    cfg = {"notice_db_path": str(tmp_path / "s.db")}
    _check_notice_state(cfg)  # 全新库：建表通过
    conn = store.connect(cfg["notice_db_path"])
    try:
        conn.execute("UPDATE meta SET schema_version = 99 WHERE owner = 'notice'")
    finally:
        conn.close()
    with pytest.raises(SystemExit) as exc:
        _check_notice_state(cfg)
    assert "删除 data/state/cea_bot.db 后重建" in str(exc.value)
    assert "状态库不可用" in str(exc.value)


def test_run_once_creates_missing_state_dir(tmp_path: Path):
    """状态库所在目录不存在时自行创建。

    回归一个集成测试当场暴露的缺陷：直接调用 `run_once` 而目录缺失时，
    原先会抛出难懂的 `sqlite3.OperationalError: unable to open database file`
    （单测都用了已存在的 `tmp_path` 父目录，所以没暴露）。生产启动路径由
    `config.ensure_notice_dirs` 先校验，运行层仍应能自愈。
    """
    cfg = cfg_for(tmp_path, notice_db_path=str(tmp_path / "深" / "一层" / "state.db"))
    deps, _ = make_deps()
    app = App(adapter=None)
    result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps))
    assert result.baseline is True
    assert Path(cfg["notice_db_path"]).is_file()


# ---- 8.2 定时注册与前端能力探测 ----

def test_build_router_registers_one_job_per_time(tmp_path: Path):
    cfg = cfg_for(tmp_path)
    router = handler.build_router(cfg)
    assert len(router.jobs) == 2
    assert [(job.hh, job.mm) for job in router.jobs] == [(9, 0), (15, 0)]
    app = App(adapter=None)
    app.include_router(basic.router)
    app.include_router(router)
    assert len(app._jobs) == 3  # 既有 noon_report + 两个通知定点


def test_build_router_without_times_registers_nothing(tmp_path: Path):
    assert len(handler.build_router(cfg_for(tmp_path, notice_times=[])).jobs) == 0


def test_supports_file_send_matches_frontend_capability():
    assert handler.supports_file_send(NapCatAdapter("ws://x")) is True
    assert handler.supports_file_send(QQBotpyAdapter("a", "s")) is False
    assert handler.supports_file_send(None) is False


def test_platform_of_uses_adapter_marker():
    assert handler.platform_of(NapCatAdapter("ws://x")) == "napcat"
    assert handler.platform_of(QQBotpyAdapter("a", "s")) == "qq"
    assert handler.platform_of(None) == "notice"


# ---- 目标会话解析 ----

def test_parse_targets_supports_kind_prefix_and_default():
    chats = handler.parse_targets({"notice_targets": ["12345", "c2c:999", "群:1"]}, "napcat")
    assert [(c.kind, c.id, c.platform) for c in chats] == [
        ("group", "12345", "napcat"),
        ("c2c", "999", "napcat"),
        ("group", "群:1", "napcat"),  # kind 不认识 → 整串当 id（不误吞）
    ]


def test_parse_targets_honours_default_kind():
    chats = handler.parse_targets({"notice_targets": ["1"], "notice_target_kind": "c2c"}, "napcat")
    assert chats[0].kind == "c2c"


def test_no_silent_commit_when_losing_claim(tmp_path: Path):
    """schema 层并发语义：抢占失败不**重复记账**，且该条立刻离开 `pending`（at-least-once 的基础）。

    口径说明（避免误读成编排层语义）：编排层仍会把该条计入 `RunResult.pushed` —— 抢占失败
    只可能因为该行已被别的运行置为非 NULL，而（真实运行下）本进程这条消息确实已经发出去了；
    干跑不发送，但仍照既有语义记账。若把它排除在 `pushed` 之外，
    `remaining = len(pending) - len(pushed)` 反而会把一个已推送的条目算成"仍待推送"
    （见 design.md D6 的 remaining 口径）。
    """
    cfg = cfg_for(tmp_path)
    conn = store.connect(cfg["notice_db_path"])
    try:
        schema.ensure(conn)
        assert schema.register_seen(conn, _brief_one(), now="2026-10-13 09:00:00") is True
        assert schema.claim_and_mark(conn, 1, NEW_ID, now="2026-10-14 09:00:00") is True
        assert schema.claim_and_mark(conn, 1, NEW_ID, now="2026-10-14 09:01:00") is False
        assert schema.pending(conn, 1) == ()  # 已记账 → 不在增量里
    finally:
        conn.close()


def _brief_one():
    from cea_bot.notices.models import NoticeBrief

    return NoticeBrief(kind=1, edit_id=NEW_ID, published_on="2026-10-14", title="t", dept="d")


# ---- 单次投递上限（notice_max_per_run）----

# 三条合成新增：id 与日期反向，故待推送顺序（发布时间降序）为 88000001 → 88000002 → 88000003
EXTRA_ROWS = "".join(
    '<TR style="FONT-WEIGHT: normal;">'
    f'<td class="listTableLine222" align="center" nowrap>2026-10-{day}</td>'
    f'<td class="listTableLine222"><a onclick="open_tz(\'{eid}\')" title="新增通知{eid}">新增通知{eid}</a></td>'
    '<td class="listTableLine222" align="center">测试单位</td>'
    "</TR>"
    for eid, day in (("88000001", 14), ("88000002", 13), ("88000003", 12))
)
EXTRA_IDS = ("88000001", "88000002", "88000003")


def _baseline(tmp_path: Path, **overrides: object) -> tuple[App, dict[str, Any]]:
    """先跑一次建立基线（不推送），返回可复用的 app 与 cfg。"""
    cfg = cfg_for(tmp_path, **overrides)
    app = App(adapter=None)
    deps, _ = make_deps()
    assert asyncio.run(handler.run_once(app.bot, cfg, deps=deps)).baseline is True
    _drain(app)
    return app, cfg


def test_max_per_run_limits_delivery_and_keeps_rest_pending(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    app, cfg = _baseline(tmp_path, notice_max_per_run=1)
    deps, _ = make_deps(list_raw=LIST_RAW + EXTRA_ROWS.encode())
    with caplog.at_level(logging.INFO, logger="cea"):
        result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps))
    assert result.pushed == ("88000001",)  # 只推最新的一条
    assert result.remaining == 2
    assert result.failed == ()
    assert len(_drain(app)) == 2  # 文本 + PDF，仅一条通知
    rows = {row[0]: row[1] for row in _rows(cfg["notice_db_path"])}
    assert rows["88000001"] is not None
    assert rows["88000002"] is None and rows["88000003"] is None  # 未投递 → 仍待推送（不丢）
    # 上限日志必须**三个数**都对得上（N / M / K）。只钉"上限"+"剩余 2 条"会放过"上限 99"。
    assert "本次投递 1 条（上限 1），剩余 2 条待下次定点" in [
        r.getMessage() for r in caplog.records
    ]


def test_remaining_items_are_delivered_on_following_runs(tmp_path: Path):
    """超出上限的条目由后续检查继续投递（规格：「不丢失、不重复」）。"""
    app, cfg = _baseline(tmp_path, notice_max_per_run=1)
    doctored = LIST_RAW + EXTRA_ROWS.encode()
    results = []
    for _ in range(3):
        deps, _ = make_deps(list_raw=doctored)
        results.append(asyncio.run(handler.run_once(app.bot, cfg, deps=deps)))
        _drain(app)
    assert [r.pushed for r in results] == [("88000001",), ("88000002",), ("88000003",)]
    assert [r.remaining for r in results] == [2, 1, 0]
    rows = {row[0]: row[1] for row in _rows(cfg["notice_db_path"])}
    assert all(rows[eid] is not None for eid in EXTRA_IDS)  # 三条都最终投递


def test_max_per_run_zero_delivers_all(tmp_path: Path):
    app, cfg = _baseline(tmp_path, notice_max_per_run=0)
    deps, _ = make_deps(list_raw=LIST_RAW + EXTRA_ROWS.encode())
    result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps))
    assert result.pushed == EXTRA_IDS
    assert result.remaining == 0
    assert len(_drain(app)) == 2 * len(EXTRA_IDS)


def test_max_per_run_failure_does_not_consume_budget(tmp_path: Path):
    """失败条目不计入上限名额：最新一条失败时，本轮仍会投递下一条（避免饥饿）。"""
    app, cfg = _baseline(tmp_path, notice_max_per_run=1)
    deps, _ = make_deps(
        list_raw=LIST_RAW + EXTRA_ROWS.encode(), detail_error_ids=frozenset({"88000001"})
    )
    result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps))
    assert result.failed == ("88000001",)
    assert result.pushed == ("88000002",)  # 名额没被失败的那条吃掉
    assert result.remaining == 2  # 3 条待推送 − 1 条成功
    rows = {row[0]: row for row in _rows(cfg["notice_db_path"])}
    assert rows["88000001"][1] is None and "FetchError" in rows["88000001"][3]  # 未记账 + 留错误原因
    assert rows["88000002"][1] is not None


def test_dry_run_honours_max_per_run(tmp_path: Path):
    """干跑套用同一上限与同一计划；否则它无法用来预演真实行为。"""
    app = App(adapter=None)
    cfg = cfg_for(tmp_path, notice_max_per_run=1, notice_dry_run=True)
    deps, _ = make_deps()
    asyncio.run(handler.run_once(app.bot, cfg, deps=deps))  # 基线（干跑下同样只建基线）
    _drain(app)
    deps2, _ = make_deps(list_raw=LIST_RAW + EXTRA_ROWS.encode())
    result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps2))
    assert result.pushed == ("88000001",) and result.remaining == 2
    assert _drain(app) == []  # 干跑不投递任何消息


def test_max_per_run_above_pending_delivers_all(tmp_path: Path):
    """2.1③ 缺口：上限**大于**待推送条数时必须全部投递、`remaining=0`、无失败。

    既有的上限用例只覆盖 cap=0（不限制）与 cap=1（严格小于待推送数），
    没有覆盖"非零上限 ≥ 待推送数"这条边界。
    """
    app, cfg = _baseline(tmp_path, notice_max_per_run=5)
    deps, _ = make_deps(list_raw=LIST_RAW + EXTRA_ROWS.encode())
    result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps))
    assert result.pushed == EXTRA_IDS  # 3 条全部投递
    assert result.remaining == 0
    assert result.failed == ()
    assert len(_drain(app)) == 2 * len(EXTRA_IDS)


def test_max_per_run_above_pending_logs_no_truncation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """上限没有推迟任何条目时**不得**打印上限日志（与下面 F1 假阳性同源）。"""
    app, cfg = _baseline(tmp_path, notice_max_per_run=5)
    deps, _ = make_deps(list_raw=LIST_RAW + EXTRA_ROWS.encode())
    with caplog.at_level(logging.INFO, logger="cea"):
        asyncio.run(handler.run_once(app.bot, cfg, deps=deps))
    messages = [r.getMessage() for r in caplog.records]
    assert not any("已达单次投递上限" in msg for msg in messages)
    assert not any("本次投递" in msg and "上限" in msg for msg in messages)


def test_max_per_run_not_truncated_when_all_processed_despite_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """F1 假阳性：`cap=2` + 3 条待推送 + 第 1 条（最新一条）失败 → 本轮 3 条**全部处理完**。

    上限没有推迟任何条目（失败不占名额，后两条把名额用满后 `pending` 也已耗尽），
    故不得出现任何"已达单次投递上限"/"本次投递…上限"的日志。
    回归实现缺陷：原先用 `len(pushed) >= limit and remaining` 反推截断，这里会误报"已达上限 2"。
    """
    app, cfg = _baseline(tmp_path, notice_max_per_run=2)
    deps, _ = make_deps(
        list_raw=LIST_RAW + EXTRA_ROWS.encode(), detail_error_ids=frozenset({"88000001"})
    )
    with caplog.at_level(logging.INFO, logger="cea"):
        result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps))
    assert result.failed == ("88000001",)
    assert result.pushed == ("88000002", "88000003")  # 名额没被失败的那条吃掉，且 3 条都处理完了
    assert result.remaining == 1  # 3 条待推送 − 2 条成功（失败那条仍待推送）
    messages = [r.getMessage() for r in caplog.records]
    assert not any("已达单次投递上限" in msg for msg in messages)
    assert not any("本次投递" in msg and "上限" in msg for msg in messages)


def test_failure_log_reports_ids_and_remaining_scope(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """2.3② 缺口 + tasks 2.3 后半句：**未达上限但有失败条目**时也必须打印失败日志。

    断言这一行同时交代失败条目 id 与"剩余"的口径（明确剩余里含本轮失败的那些条）。
    """
    app, cfg = _baseline(tmp_path)  # 不设上限（缺省 0 = 不限制）
    deps, _ = make_deps(
        list_raw=LIST_RAW + EXTRA_ROWS.encode(),
        detail_error_ids=frozenset({"88000001", "88000002"}),
    )
    with caplog.at_level(logging.INFO, logger="cea"):
        result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps))
    assert result.failed == ("88000001", "88000002") and result.pushed == ("88000003",)
    assert result.remaining == 2  # 3 条待推送 − 1 条成功
    messages = [r.getMessage() for r in caplog.records]
    assert (
        "本次有 2 条处理失败（下次定点重试）：88000001、88000002；"
        "剩余待投递 2 条（含本轮失败 2 条）" in messages
    )
    # 没设上限 → 不存在上限日志，也就不存在第二个"剩余"数
    assert not any("本次投递" in msg and "上限" in msg for msg in messages)


def test_truncated_with_failure_reports_one_consistent_remaining(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """F2：上限截断与失败同时发生时，两行日志报的"剩余"必须是**同一个数**（不是双倍）。"""
    app, cfg = _baseline(tmp_path, notice_max_per_run=1)
    deps, _ = make_deps(
        list_raw=LIST_RAW + EXTRA_ROWS.encode(), detail_error_ids=frozenset({"88000001"})
    )
    with caplog.at_level(logging.INFO, logger="cea"):
        result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps))
    assert result.failed == ("88000001",) and result.pushed == ("88000002",)
    assert result.remaining == 2
    messages = [r.getMessage() for r in caplog.records]
    assert "本次投递 1 条（上限 1），剩余 2 条待下次定点" in messages
    assert (
        "本次有 1 条处理失败（下次定点重试）：88000001；"
        "剩余待投递 2 条（含本轮失败 1 条）" in messages
    )


def test_failure_log_single_remaining_figure_for_two_failures(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """F7.5 / F2：`cap=1` + 2 条失败 + 1 条成功时，失败日志点明"含本轮失败 2 条"。

    该轮 `pending` 已被处理完（上限没有推迟任何条目），因此日志里**只有一个**"剩余"口径，
    不会被读成"剩余 2 条 + 失败 2 条 = 积压 4 条"。
    """
    app, cfg = _baseline(tmp_path, notice_max_per_run=1)
    deps, _ = make_deps(
        list_raw=LIST_RAW + EXTRA_ROWS.encode(),
        detail_error_ids=frozenset({"88000001", "88000002"}),
    )
    with caplog.at_level(logging.INFO, logger="cea"):
        result = asyncio.run(handler.run_once(app.bot, cfg, deps=deps))
    assert result.failed == ("88000001", "88000002") and result.pushed == ("88000003",)
    assert result.remaining == 2
    messages = [r.getMessage() for r in caplog.records]
    # 只看汇总行（每条失败条目另有一行 ERROR「通知 X 处理失败，本次跳过…」，不算汇总）
    failure_lines = [msg for msg in messages if msg.startswith("本次有")]
    assert len(failure_lines) == 1
    assert failure_lines[0].startswith(
        "本次有 2 条处理失败（下次定点重试）：88000001、88000002；"
        "剩余待投递 2 条（含本轮失败 2 条）"
    )
    assert "含本轮失败 2 条" in failure_lines[0]
    remaining_lines = [msg for msg in messages if "剩余" in msg]
    assert len(remaining_lines) == 1  # 只有一个"剩余"数，不存在互相矛盾的两个口径
