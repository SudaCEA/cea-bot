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
) -> tuple[handler.NoticeDeps, list[tuple[str, object]]]:
    """构造注入依赖；`calls` 记录调用顺序，用于断言编排顺序。"""
    calls: list[tuple[str, object]] = []

    async def fetch_list(session: Any, kind: int, **kwargs: Any) -> RawPage:
        calls.append(("fetch_list", kind))
        return RawPage(url="http://file.suda.edu.cn/list", raw=list_raw, declared_charset="gb2312")

    async def fetch_detail(session: Any, edit_id: str, **kwargs: Any) -> RawPage:
        calls.append(("fetch_detail", edit_id))
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
    assert file_msg.file_name.endswith(".pdf") and file_msg.file_name.startswith("2026-10-14-")
    assert Path(file_msg.file).is_file()
    # 编排顺序：抓列表 → 抓详情 → 摘要 → 渲染
    assert [name for name, _ in calls] == ["fetch_list", "fetch_detail", "summarize", "render"]


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
    （排序失效、PDF 文件名退化成"未知日期"）且没有任何告警。
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
    """`claim_and_mark` 抢占失败时不得把该条算作"本轮已推"（并发语义在编排层的体现）。"""
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
