# -*- coding: utf-8 -*-
"""业务层持久化底座：开库、连接级 PRAGMA、按 owner 建表登记与版本读取。

归属边界见 openspec/changes/add-notice-digest/design.md 的 D6：本模块只做"知识密集但面积
极小"的底座（开库、PRAGMA、建表/迁移的登记与执行次序），领域表结构归各自的领域 schema 模块，
本模块里不出现任何业务表名。

硬规则：只依赖标准库，且**不得 import 上层业务模块，也不得 import 框架模块** ——
依赖方向是单向的，反向 import 会让底座把上层拖进同一团。

任务 2.2 的取向：版本不兼容一律**响亮失败**。`schema_version` 读到损坏值（INTEGER 列里存了
TEXT）不再让 `ValueError`/`TypeError` 漏出去，也不当 0 静默放行；`check_version` 与 `migrate`
在版本不符时先抛 `SchemaVersionError`，**一条 DDL 都不执行**，绝不自动改表或删库重建。
"""
import sqlite3
from collections.abc import Sequence
from pathlib import Path

# 忙等重试上限（毫秒）：两个连接抢写锁时先等待重试，而不是立刻抛 database is locked。
BUSY_TIMEOUT_MS = 5000

# 各 owner 的 schema 版本登记表：一行一个 owner（design.md 记作 meta.schema_version）。
_META_DDL = (
    "CREATE TABLE IF NOT EXISTS meta ("
    "owner TEXT PRIMARY KEY, "
    "schema_version INTEGER NOT NULL)"
)

# 处置提示：文案逐字含这一句（任务 2.2 与规格"状态版本不兼容"场景的硬要求），
# 上层可以原样抄给用户，不需要再拼一句话。对"库不可用"（路径是目录/文件损坏）复用同一句，
# 故它是公开常量：业务层的领域模块也要用它拼提示。
STATE_REMEDY = "删除 data/state/cea_bot.db 后重建"


class SchemaVersionError(RuntimeError):
    """状态库的 schema 版本与当前实现不符，或版本值已损坏 —— 响亮失败的专用载体。

    为什么必须是专用异常：调用方要能区分"库不兼容、必须人工处置"（重试无用、必须停下）
    与"普通读写失败"（可能只是瞬时锁冲突）。用 `RuntimeError` 泛化会让上层只能靠文案猜。

    为什么不能自动改表、也不能静默重建（规格明文 SHALL NOT）：删库重建会把所有条目打回
    "未推送"，下次定点把历史通知整批重发；反过来把不兼容/损坏的版本当成 0 静默放行，则会
    把已推送的当没推过（重发）或把建表顺序搞乱。两种"看起来恢复了"的处理都是静默失败。
    """


def _incompatible(owner: str, found: int | str, expected: int | None) -> SchemaVersionError:
    """构造带处置提示的不兼容异常（`expected=None` 表示"读到的值连合法整数都不是"）。"""
    want = "任意合法整数版本" if expected is None else str(expected)
    return SchemaVersionError(
        f"状态库 schema 版本不兼容：owner={owner!r} 已登记版本 {found!r}，"
        f"当前实现期望 {want}。本程序不会自动改表，也不会静默重建状态。"
        f"处置方式：先备份，再 {STATE_REMEDY}，让本次运行重新建表、从下一次定点开始推增量。"
    )


def connect(path: str | Path) -> sqlite3.Connection:
    """打开（必要时创建）SQLite 库，并设好连接级 PRAGMA。

    `":memory:"` 与文件路径都支持。已知实测事实：内存库开不了 WAL —— `PRAGMA journal_mode`
    在内存库上回读 `"memory"`、在文件库上回读 `"wal"`。因此这里只"请求 WAL"、不校验回读值
    （在内存库上校验必然失败），文件库的回读值由 tests/test_store.py 实测断言；两处断言分开写。

    开库即自动提交（`isolation_level=None`）：单条语句自成一个事务，不会被隐式开启却忘记
    提交的事务卡住 —— 那种情况下表"建了却读不到"，属典型静默失败。
    """
    conn = sqlite3.connect(str(path))
    conn.isolation_level = None
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection, owner: str, statements: Sequence[str]) -> int:
    """按 owner 依次执行建表语句并登记版本，返回登记后的版本号（= 语句条数）。

    `statements` 一律写 `CREATE TABLE IF NOT EXISTS ...`，所以**重复调用是幂等的**：
    表已存在不会报错，版本号不漂移，已有数据也不受影响。版本号取"已执行的语句条数"，
    即该 owner 迁到了第几步；"实现期望的版本"就等于该 owner 的语句条数，供兼容性检查比对。

    版本门槛在**任何 DDL 之前**：该 owner 已登记版本既不等于 0（未登记，全新库）也不等于
    `len(statements)` 时，先抛 `SchemaVersionError`，`statements` 一条都不执行 —— 这是
    "不自动改表"的硬保证（顺序反了就成了"先建半张表再报错"，比不检查更糟）。
    """
    _ensure_meta(conn)
    version = len(statements)
    check_version(conn, owner, version)  # 只读校验；不兼容时在这里就抛，下面一行都不会执行
    for statement in statements:
        conn.execute(statement)
    conn.execute(
        "INSERT INTO meta (owner, schema_version) VALUES (?, ?) "
        "ON CONFLICT(owner) DO UPDATE SET schema_version = excluded.schema_version",
        (owner, version),
    )
    return version


def schema_version(conn: sqlite3.Connection, owner: str) -> int:
    """读取该 owner 已登记的 schema 版本；从未登记过则返回 0（= 尚未建表）。

    版本按 owner 分行存放，故读取必须带 owner（任务书里简写作 `schema_version(conn)`）。
    登记表缺失时先建表再读，返回 0 —— 不抛 "no such table"，避免把"库是旧的"报成指向不明的错误。

    损坏值必须响亮失败：SQLite 的 INTEGER 列**不禁止**存 TEXT（类型亲和性只在能无损转换时
    才转），所以 `meta.schema_version` 里可能是 `'oops'` 这类值，`int(...)` 会抛
    `ValueError`/`TypeError`。这里把它转成 `SchemaVersionError`：既不放任 `ValueError` 漏出去
    （调用方多半只捕业务异常，会让损坏状态以栈回溯的形式炸在无关的地方），也不静默当成 0
    （那等于宣称"库是全新的"，会掩盖损坏并触发重建/重发）。
    """
    _ensure_meta(conn)
    row = conn.execute(
        "SELECT schema_version FROM meta WHERE owner = ?", (owner,)
    ).fetchone()
    if row is None:
        return 0
    raw = row[0]
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise _incompatible(owner, raw, None) from exc


def check_version(conn: sqlite3.Connection, owner: str, expected: int) -> None:
    """校验该 owner 已登记的版本是否与当前实现期望一致；不一致即抛 `SchemaVersionError`。

    三种判定（与规格"状态版本不兼容"场景一一对应）：

    - **未登记（版本 0）→ 放行**：全新库，调用方接着 `migrate` 建表登记即可；
    - **已登记且 == expected → 放行**；
    - **其它（比期望小、比期望大、版本值损坏）→ 抛错**，附上"删除 data/state/cea_bot.db 后重建"
      的处置提示。本函数**只读**：不改表、不重建、不把不兼容的版本"顺手"改成 expected。
    """
    version = schema_version(conn, owner)  # 损坏值在这里已转成 SchemaVersionError
    if version == 0 or version == expected:
        return
    raise _incompatible(owner, version, expected)


def _ensure_meta(conn: sqlite3.Connection) -> None:
    """确保版本登记表存在（幂等；写入与读取两条路径都先调用它）。"""
    conn.execute(_META_DDL)
