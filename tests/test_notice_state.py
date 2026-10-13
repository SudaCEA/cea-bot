# -*- coding: utf-8 -*-
"""增量状态层单测（任务 5.1 / 5.2 / 5.3）：幂等登记、待推送顺序、抢占式记账、重启不重推。

全部用 `:memory:` 或 `tmp_path` 下的真实 SQLite（标准库），不起文件 mock。
"""
from pathlib import Path

import pytest

from cea_bot import store
from cea_bot.notices import schema
from cea_bot.notices.models import NoticeBrief

NOW = "2026-10-13 21:00:00"


def _brief(edit_id: str, *, date: str = "2026-10-13", title: str = "标题", dept: str = "单位", kind: int = 1) -> NoticeBrief:
    return NoticeBrief(kind=kind, edit_id=edit_id, published_on=date, title=title, dept=dept)


def _conn() -> "store.sqlite3.Connection":
    conn = store.connect(":memory:")
    schema.ensure(conn)
    return conn


# ---- 5.1 表结构与三个操作 ----

def test_ensure_creates_table_and_pending_index():
    conn = _conn()
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert "notice" in names
        assert "ix_notice_pending" in names
    finally:
        conn.close()


def test_register_seen_is_idempotent():
    conn = _conn()
    try:
        brief = _brief("40773772")
        assert schema.register_seen(conn, brief, now=NOW) is True
        assert schema.register_seen(conn, brief, now="2026-10-14 09:00:00") is False
        assert conn.execute("SELECT COUNT(*) FROM notice").fetchone()[0] == 1
        # 第二次登记不得覆盖已入账的字段（首次发现时间要留住）
        row = conn.execute("SELECT first_seen_at, title FROM notice").fetchone()
        assert row[0] == NOW and row[1] == "标题"
    finally:
        conn.close()


def test_pending_orders_newest_first():
    conn = _conn()
    try:
        for edit_id, date in (("1", "2026-10-11"), ("2", "2026-10-13"), ("3", "2026-10-12")):
            schema.register_seen(conn, _brief(edit_id, date=date), now=NOW)
        assert [row.edit_id for row in schema.pending(conn, 1)] == ["2", "3", "1"]
        assert schema.pending(conn, 2) == ()  # 栏目隔离
    finally:
        conn.close()


def test_claim_and_mark_is_single_winner():
    conn = _conn()
    try:
        schema.register_seen(conn, _brief("40773772"), now=NOW)
        assert schema.claim_and_mark(conn, 1, "40773772", now=NOW, pdf_path="data/notices/x.pdf") is True
        # 第二次抢占：rowcount 为 0（并发下只有一个赢家）
        assert schema.claim_and_mark(conn, 1, "40773772", now="2026-10-13 22:00:00") is False
        row = conn.execute("SELECT pushed_at, pdf_path FROM notice").fetchone()
        assert row[0] == NOW and row[1] == "data/notices/x.pdf"
    finally:
        conn.close()


def test_claim_unknown_id_returns_false():
    conn = _conn()
    try:
        assert schema.claim_and_mark(conn, 1, "不存在", now=NOW) is False
    finally:
        conn.close()


def test_mark_error_keeps_row_pending():
    conn = _conn()
    try:
        schema.register_seen(conn, _brief("7"), now=NOW)
        schema.mark_error(conn, 1, "7", "FetchError: boom")
        row = conn.execute("SELECT pushed_at, last_error FROM notice").fetchone()
        assert row[0] is None and "boom" in row[1]
        assert [r.edit_id for r in schema.pending(conn, 1)] == ["7"]  # 下次定点仍会重试
    finally:
        conn.close()


# ---- 5.2 首次运行建立基线 ----

def test_first_run_builds_baseline_without_pending():
    conn = _conn()
    try:
        briefs = tuple(_brief(str(i), date=f"2026-10-{10 + i:02d}") for i in range(1, 21))
        plan = schema.plan_run(conn, 1, briefs, now=NOW)
        assert plan.baseline is True
        assert plan.pending == ()
        assert conn.execute("SELECT COUNT(*) FROM notice WHERE pushed_at IS NULL").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM notice").fetchone()[0] == 20
    finally:
        conn.close()


def test_second_run_only_returns_new_entries():
    conn = _conn()
    try:
        schema.plan_run(conn, 1, (_brief("1"), _brief("2")), now=NOW)  # 首次：基线
        plan = schema.plan_run(conn, 1, (_brief("1"), _brief("2"), _brief("3")), now="2026-10-14 09:00:00")
        assert plan.baseline is False
        assert [row.edit_id for row in plan.pending] == ["3"]
    finally:
        conn.close()


def test_baseline_is_per_kind():
    """基线必须**按栏目**判定，且标记只作用于本栏目。

    回归独立审查抓到的缺陷：原先用全表计数判断"是否首次运行"，于是把 `notice_type` 从 1 改成 2
    （或将来同时订阅多个栏目）时，新栏目会被当成"不是首次运行"，20 条历史一次性灌进目标群；
    反过来，若标记基线时不限定栏目，还会把别的栏目真正待推送的条目误标为已推送（丢消息）。
    """
    conn = _conn()
    try:
        assert schema.plan_run(conn, 1, (_brief("1"), _brief("2")), now=NOW).baseline is True
        # 栏目 1 有一条真正待推送的条目
        schema.register_seen(conn, _brief("7"), now=NOW)
        # 切到栏目 2：仍是"首次运行"，不得把栏目 2 的历史当增量推送
        plan2 = schema.plan_run(conn, 2, (_brief("9", kind=2), _brief("8", kind=2)), now=NOW)
        assert plan2.baseline is True and plan2.pending == ()
        # 且栏目 1 的待推送条目没有被栏目 2 的基线"连坐"标记
        assert [row.edit_id for row in schema.pending(conn, 1)] == ["7"]
        plan3 = schema.plan_run(conn, 1, (_brief("1"), _brief("2"), _brief("7")), now=NOW)
        assert [row.edit_id for row in plan3.pending] == ["7"]
    finally:
        conn.close()


# ---- 5.3 重启后不重复推送 ----

def test_restart_does_not_repush(tmp_path: Path):
    db = tmp_path / "cea_bot.db"
    first = store.connect(db)
    try:
        schema.ensure(first)
        schema.plan_run(first, 1, (_brief("1"), _brief("2")), now=NOW)  # 基线
        schema.register_seen(first, _brief("3"), now=NOW)
        assert schema.claim_and_mark(first, 1, "3", now=NOW) is True
    finally:
        first.close()
    # 重开连接 = 进程重启
    second = store.connect(db)
    try:
        schema.ensure(second)
        plan = schema.plan_run(second, 1, (_brief("1"), _brief("2"), _brief("3")), now="2026-10-15 09:00:00")
        assert plan.baseline is False
        assert plan.pending == ()  # 三条都已推送过 → 无增量
    finally:
        second.close()


def test_new_version_of_schema_is_rejected_loudly(tmp_path: Path):
    """追加建表语句 = 抬版本：旧库必须响亮报错，而不是被自动改表（迁移计划第 2 条）。"""
    db = tmp_path / "cea_bot.db"
    conn = store.connect(db)
    try:
        schema.ensure(conn)
        with pytest.raises(store.SchemaVersionError) as exc:
            store.migrate(conn, schema.OWNER, schema.STATEMENTS + ("CREATE TABLE extra (id INTEGER)",))
        assert "删除 data/state/cea_bot.db 后重建" in str(exc.value)
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert "extra" not in names  # 一条 DDL 都没执行
    finally:
        conn.close()
