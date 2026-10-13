# -*- coding: utf-8 -*-
"""通知增量状态：表结构、幂等登记、待推送查询与抢占式记账（任务 5.1 / 5.2 / 5.3）。

归属边界（design.md D6）：连接、PRAGMA、建表登记归 `cea_bot.store`（业务层底座）；
**领域**（notice 表、索引、增量语义的 SQL）归本模块。本模块不 import handler。

增量语义的三条 SQL 就是全部原理：

1. `register_seen`：`INSERT … ON CONFLICT DO NOTHING` —— 同一标识只入账一次，天然幂等；
2. `pending`：`WHERE pushed_at IS NULL` —— "还没推过"就是增量；
3. `claim_and_mark`：`UPDATE … WHERE pushed_at IS NULL` + `rowcount` —— 并发下只有一个赢家。

崩溃语义取 **at-least-once**（design.md D7）：顺序是"先发送成功、再记账"，若在两者之间进程
被杀，下次会重发一次；反过来（先记账后发）就是**漏发**，代价更高。
"""
import sqlite3
from dataclasses import dataclass

from cea_bot import store
from cea_bot.notices.models import NoticeBrief

# 版本登记的 owner 名：`store` 按 owner 分行登记 schema 版本。
OWNER = "notice"

# 建表语句即 schema 版本口径（`store.migrate` 用"语句条数"当版本号）：
# 追加语句 = 抬版本 = 旧库会**响亮报错**并要求处置，绝不自动改表（design.md Migration Plan）。
STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS notice (
        kind INTEGER NOT NULL,
        edit_id TEXT NOT NULL,
        title TEXT NOT NULL,
        dept TEXT NOT NULL DEFAULT '',
        published_on TEXT NOT NULL DEFAULT '',
        first_seen_at TEXT NOT NULL,
        pushed_at TEXT,
        pdf_path TEXT,
        last_error TEXT,
        PRIMARY KEY (kind, edit_id)
    ) STRICT""",
    """CREATE INDEX IF NOT EXISTS ix_notice_pending
        ON notice(published_on DESC, edit_id DESC) WHERE pushed_at IS NULL""",
)


@dataclass(frozen=True)
class NoticeRow:
    """库里的一行通知（增量判定与推送都基于它）。"""

    kind: int
    edit_id: str
    title: str
    dept: str
    published_on: str
    pushed_at: str | None


@dataclass(frozen=True)
class RunPlan:
    """一次运行的增量计划。

    `baseline=True` 表示"状态为空、本次只建立基线"：条目已登记并标记为已推送，`pending` 为空。
    """

    baseline: bool
    pending: tuple[NoticeRow, ...]


def ensure(conn: sqlite3.Connection) -> None:
    """建表并校验版本；版本不兼容时由 `store` 抛 `SchemaVersionError`（响亮失败）。"""
    store.migrate(conn, OWNER, STATEMENTS)


class StateUnavailableError(RuntimeError):
    """状态库不可用：路径是目录、文件损坏、无权限等 —— 与版本不兼容一样必须响亮。

    规格把"状态不可用"与"版本不兼容"并列要求"明确报错并给出处置提示"，故这里给出
    同一套可执行提示（复用 `store.STATE_REMEDY`），而不是把 sqlite 的原始异常丢给用户。
    """


def state_unavailable(path: object, exc: BaseException) -> StateUnavailableError:
    """把"打不开/读不了状态库"的底层异常收敛成带处置提示的业务异常。"""
    return StateUnavailableError(
        f"状态库无法打开或不可用（{type(exc).__name__}: {exc}）：{path}。"
        f"处置方式：先备份，再 {store.STATE_REMEDY}。"
    )


def open_state(path: object) -> sqlite3.Connection:
    """打开状态库并建表：底层不可用抛 `StateUnavailableError`，版本不符抛 `SchemaVersionError`。

    收敛面必须覆盖 `connect` **与** `ensure` 两段：库文件是合法 SQLite 但**结构不对**时
    （例如旧版 `meta` 表缺 `schema_version` 列、或旧版 `notice` 表缺列），
    `CREATE TABLE IF NOT EXISTS` 会静默放行，随后在列名上炸出 `sqlite3.OperationalError`。
    那同样属于规格里的"状态不可用"，必须给出处置提示而不是裸异常。
    """
    try:
        conn = store.connect(str(path))
    except (sqlite3.Error, OSError) as exc:
        raise state_unavailable(path, exc) from exc
    try:
        ensure(conn)
    except store.SchemaVersionError:
        conn.close()
        raise
    except (sqlite3.Error, OSError) as exc:
        conn.close()
        raise state_unavailable(path, exc) from exc
    return conn


def register_seen(conn: sqlite3.Connection, brief: NoticeBrief, *, now: str) -> bool:
    """登记"见过这条通知"；返回 True 表示本次新入账（False = 之前已在账上）。"""
    cursor = conn.execute(
        "INSERT INTO notice (kind, edit_id, title, dept, published_on, first_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(kind, edit_id) DO NOTHING",
        (brief.kind, brief.edit_id, brief.title, brief.dept, brief.published_on, now),
    )
    return cursor.rowcount == 1


def pending(conn: sqlite3.Connection, kind: int) -> tuple[NoticeRow, ...]:
    """待推送集合：新发布的排前面（`published_on` 降序，同日期按标识降序）。"""
    rows = conn.execute(
        "SELECT kind, edit_id, title, dept, published_on, pushed_at FROM notice "
        "WHERE kind = ? AND pushed_at IS NULL "
        "ORDER BY published_on DESC, edit_id DESC",
        (int(kind),),
    ).fetchall()
    return tuple(NoticeRow(*row) for row in rows)


def claim_and_mark(
    conn: sqlite3.Connection,
    kind: int,
    edit_id: str,
    *,
    now: str,
    pdf_path: str | None = None,
) -> bool:
    """抢占式记账：把 `pushed_at` 从 NULL 改成非 NULL 的那一次返回 True。

    `WHERE pushed_at IS NULL` 是关键：并发（或重叠运行）下第二次更新 `rowcount` 为 0，
    调用方据此知道"别人已经推过"，不会再发一遍。
    """
    cursor = conn.execute(
        "UPDATE notice SET pushed_at = ?, pdf_path = ?, last_error = NULL "
        "WHERE kind = ? AND edit_id = ? AND pushed_at IS NULL",
        (now, pdf_path, int(kind), edit_id),
    )
    return cursor.rowcount == 1


def mark_error(conn: sqlite3.Connection, kind: int, edit_id: str, message: str) -> None:
    """记录一条通知的处理失败原因（不改 `pushed_at`：下次定点会重试）。"""
    conn.execute(
        "UPDATE notice SET last_error = ? WHERE kind = ? AND edit_id = ?",
        (message[:500], int(kind), edit_id),
    )


def plan_run(
    conn: sqlite3.Connection, kind: int, briefs: tuple[NoticeBrief, ...], *, now: str
) -> RunPlan:
    """登记本次扫到的条目并给出增量计划（任务 5.1 / 5.2）。

    首次运行**按栏目判定**（该栏目在 `notice` 表里没有任何行）**只建立基线**：把首页条目全部登记
    并标记为已推送，`pending` 为空。没有这一步，上线那一刻会把首页 20 条历史通知一次性全推出去
    （design.md D8）。

    "按栏目"是关键：若用全表计数，用户把 `notice_type` 从 1 改成 2（或将来同时订阅多个栏目）时，
    新栏目会被当成"不是首次运行"，于是 20 条历史一次性灌进目标群。同理，标记基线也必须限定
    在**本栏目**内，否则会把别的栏目真正待推送的条目误标为已推送（那是丢消息）。
    """
    fresh = (
        conn.execute("SELECT COUNT(*) FROM notice WHERE kind = ?", (int(kind),)).fetchone()[0] == 0
    )
    for brief in briefs:
        register_seen(conn, brief, now=now)
    if fresh:
        conn.execute(
            "UPDATE notice SET pushed_at = ? WHERE kind = ? AND pushed_at IS NULL",
            (now, int(kind)),
        )
        return RunPlan(baseline=True, pending=())
    return RunPlan(baseline=False, pending=pending(conn, kind))
