# -*- coding: utf-8 -*-
"""`cea_bot.store`（业务层持久化底座）单测 —— 任务 2.1 + 2.2。

覆盖口径：
① 内存库（`":memory:"`）与文件库（`tmp_path`）都能开，PRAGMA 断言的是**实测回读值**：
   `journal_mode` 在内存库上实测 `"memory"`、文件库上实测 `"wal"`（两处分开断言，不硬凑成一个）；
   `foreign_keys` 由缺省 0 变成 1 —— 这是"PRAGMA 真被执行了"的可区分证据（缺省值本就不同）；
   `busy_timeout` 回读等于模块常量（注意：本机 SQLite 的缺省忙等恰好也是 5000，
   故它的"真的执行"不由回读值单独证明，而由上面两个可区分指标与文件库的 WAL 磁盘证据共同证明）。
② 文件库的 WAL 是磁盘上真的 WAL，不是 PRAGMA 自说自话：库文件头偏移 18/19（读/写版本）为 2，
   且打开期间存在 `-wal` 边车文件。
③ `migrate` 建出的表真的可查（含外键约束真的在拦，同样是可区分证据）。
④ `schema_version` 返回值：未登记 → 0；登记后 = 语句条数；不同 owner 互不串号。
⑤ 重复 `migrate` 幂等：版本不漂移、`meta` 不出现第二行、已有数据不被清掉。
⑥ 底座只依赖标准库、且不含上层/框架 import（把任务书的红线变成可执行断言）。
⑦ **任务 2.2：版本不兼容的响亮失败** —— 比期望小 / 比期望大 / 版本值损坏（INTEGER 列里存 TEXT）
   三种情况都必须抛**专用** `SchemaVersionError`（不是 `ValueError`）且文案含处置提示；
   版本相等与未登记的全新库必须放行；抛错后已有表与数据必须原样留存（没被 drop/重建）；
   `migrate` 在不兼容版本上必须在**任何 DDL 之前**抛错（目标表断言仍不存在）。

本文件无协程场景，故不用 `asyncio.run(...)` 包裹；也不引入 pytest-asyncio。
"""
import ast
import sqlite3
import sys
from pathlib import Path

import pytest

import cea_bot.store as store_module
from cea_bot.store import (
    BUSY_TIMEOUT_MS,
    SchemaVersionError,
    check_version,
    connect,
    migrate,
    schema_version,
)

# 建两张表：member 带外键，用来证明 foreign_keys=ON 是"真的在拦"而不是开关摆着好看。
_DDL = (
    "CREATE TABLE IF NOT EXISTS unit (id INTEGER PRIMARY KEY, name TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS member ("
    "id INTEGER PRIMARY KEY, "
    "unit_id INTEGER NOT NULL REFERENCES unit(id))",
)

# 任务书红线口径，与验收命令 `Select-String -Path src\cea_bot\store.py -Pattern "handlers|notices|import cea"` 一致。
_FORBIDDEN_TOKENS = ("handlers", "notices", "import cea")


def _store_source() -> str:
    """读回 store.py 的真实源码文本（用模块自身的路径定位，不写死相对路径）。"""
    assert store_module.__file__ is not None
    return Path(store_module.__file__).read_text(encoding="utf-8")


# ---- ① 连接与 PRAGMA ----


def test_memory_connect_pragmas() -> None:
    """内存库：能开，且 journal_mode 实测为 "memory"（内存库开不了 WAL）。"""
    conn = connect(":memory:")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS
    finally:
        conn.close()


def test_memory_connect_returns_usable_connection() -> None:
    """内存库不只是"能开"：DDL 与读写立刻可用（自动提交，不靠隐式 commit）。"""
    conn = connect(":memory:")
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO t (id, name) VALUES (?, ?)", (1, "学生工作部"))
        assert conn.execute("SELECT name FROM t WHERE id = 1").fetchone()[0] == "学生工作部"
    finally:
        conn.close()


def test_file_connect_pragmas_and_wal_on_disk(tmp_path: Path) -> None:
    """文件库：journal_mode 实测 "wal"，且 WAL 在磁盘上真的生效。"""
    db = tmp_path / "cea_bot.db"
    conn = connect(db)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS
        migrate(conn, "notice", _DDL)  # 落一次真实写入，让库文件头与边车文件就位
        assert (tmp_path / "cea_bot.db-wal").exists()
        header = db.read_bytes()[:20]
        assert header[:16] == b"SQLite format 3\x00"
        # 文件头偏移 18/19 = 读版本/写版本：2 = WAL，1 = 传统回滚日志。
        assert (header[18], header[19]) == (2, 2)
    finally:
        conn.close()


def test_foreign_keys_are_enforced() -> None:
    """foreign_keys=ON 是真在拦：插入悬空外键必须抛 IntegrityError，不得静默写入。"""
    conn = connect(":memory:")
    try:
        migrate(conn, "notice", _DDL)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO member (id, unit_id) VALUES (1, 999)")
    finally:
        conn.close()


# ---- ③ migrate 建表后可查 ----


def test_migrate_creates_queryable_tables() -> None:
    """`:memory:` 建两张表后可写入、可查回，且表名真的存在（不是"没报错"就算数）。"""
    conn = connect(":memory:")
    try:
        assert migrate(conn, "notice", _DDL) == 2
        conn.execute("INSERT INTO unit (id, name) VALUES (?, ?)", (1, "学生工作部"))
        conn.execute("INSERT INTO member (id, unit_id) VALUES (?, ?)", (1, 1))
        assert conn.execute("SELECT name FROM unit WHERE id = 1").fetchone()[0] == "学生工作部"
        assert conn.execute("SELECT COUNT(*) FROM member").fetchone()[0] == 1
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"unit", "member", "meta"} <= names
    finally:
        conn.close()


# ---- ④ schema_version 返回值 ----


def test_schema_version_unregistered_owner_is_zero() -> None:
    """从未 migrate 过的 owner：返回 0（而不是抛 "no such table"）。"""
    conn = connect(":memory:")
    try:
        assert schema_version(conn, "notice") == 0
    finally:
        conn.close()


def test_schema_version_registered_and_isolated_per_owner() -> None:
    """登记后返回语句条数；不同 owner 的版本互不串号，且能直接读 meta 表核对。"""
    conn = connect(":memory:")
    try:
        assert migrate(conn, "notice", _DDL) == 2
        assert schema_version(conn, "notice") == 2
        assert schema_version(conn, "other") == 0  # 未登记的 owner 不沾别人的版本
        row = conn.execute(
            "SELECT schema_version FROM meta WHERE owner = ?", ("notice",)
        ).fetchone()
        assert row[0] == 2, "版本必须落在 meta.schema_version 上，而不是函数自己记着"
        migrate(conn, "other", ("CREATE TABLE IF NOT EXISTS other_t (id INTEGER PRIMARY KEY)",))
        assert schema_version(conn, "other") == 1
        assert schema_version(conn, "notice") == 2  # 登记另一个 owner 不影响本 owner
    finally:
        conn.close()


# ---- ⑤ 重复 migrate 幂等 ----


def test_migrate_is_idempotent() -> None:
    """重复 migrate：不报错、版本不漂移、meta 不出现第二行、已有数据不被清掉。"""
    conn = connect(":memory:")
    try:
        assert migrate(conn, "notice", _DDL) == 2
        conn.execute("INSERT INTO unit (id, name) VALUES (1, '留存')")
        assert migrate(conn, "notice", _DDL) == 2
        assert schema_version(conn, "notice") == 2
        assert conn.execute("SELECT COUNT(*) FROM meta WHERE owner = 'notice'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM unit").fetchone()[0] == 1
    finally:
        conn.close()


def test_migrate_idempotent_across_reconnect(tmp_path: Path) -> None:
    """文件库：重开连接（模拟进程重启）后版本仍可读回，重复 migrate 仍幂等。"""
    db = tmp_path / "cea_bot.db"
    first = connect(db)
    try:
        migrate(first, "notice", _DDL)
    finally:
        first.close()
    second = connect(db)
    try:
        assert schema_version(second, "notice") == 2
        assert migrate(second, "notice", _DDL) == 2
        assert schema_version(second, "notice") == 2
        assert second.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 1
    finally:
        second.close()


# ---- ⑥ 红线：只依赖标准库、不含上层/框架 import ----


def test_store_source_has_no_forbidden_import_tokens() -> None:
    """store.py 源码里不得出现 `handlers` / `notices` / `import cea`（与验收命令同一口径）。"""
    source = _store_source()
    for token in _FORBIDDEN_TOKENS:
        assert token not in source, f"store.py 不应出现 {token!r}"


def test_store_imports_only_stdlib() -> None:
    """模块里的顶层 import 根模块必须全在标准库名单内（不放宽成"看起来是标准库"）。"""
    tree = ast.parse(_store_source())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    assert roots, "没解析到任何 import，说明源码没读到（不能算通过）"
    assert roots <= sys.stdlib_module_names, f"非标准库依赖：{sorted(roots - sys.stdlib_module_names)}"


# ---- ⑦ 任务 2.2：版本不兼容的响亮失败 ----


# 处置提示必须逐字出现在文案里（任务 2.2 的硬要求，也是规格"状态版本不兼容"场景的 Then）。
_REMEDY_HINT = "删除 data/state/cea_bot.db 后重建"

# 比 `_DDL` 多一条语句：用来把"实现期望版本"抬到 3，与库里的 2 / 99 都不等，逼出"不兼容"分支。
_DDL_V3 = _DDL + ("CREATE TABLE IF NOT EXISTS extra (id INTEGER PRIMARY KEY)",)


def _tamper_version(conn: sqlite3.Connection, owner: str, value: object) -> None:
    """直接改 `meta` 伪造版本（含写入 TEXT 的损坏值），模拟旧库或被手工改坏的库。

    这不是"绕过实现"：SQLite 的 INTEGER 列按类型亲和性只在**能无损转换**时才转成整数，
    所以 `'oops'` 真的会以 TEXT 落库、真的会以 `str` 读回 —— 由用例里的类型断言证明。
    """
    conn.execute("UPDATE meta SET schema_version = ? WHERE owner = ?", (value, owner))


def _table_names(conn: sqlite3.Connection) -> set[str]:
    """当前库里所有表名（用来证明"库里发生了什么"而不是只看异常类型）。"""
    return {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def test_check_version_older_than_expected_raises() -> None:
    """① 已登记版本比期望小 → 抛 SchemaVersionError，文案含处置提示，且版本没被偷偷改掉。"""
    conn = connect(":memory:")
    try:
        assert migrate(conn, "notice", _DDL[:1]) == 1
        with pytest.raises(SchemaVersionError) as exc:
            check_version(conn, "notice", 2)
        assert _REMEDY_HINT in str(exc.value), f"文案缺处置提示：{exc.value}"
        assert schema_version(conn, "notice") == 1, "check_version 必须只读，不得顺手改版本"
        check_version(conn, "other", 2)  # 对照：别的 owner 未登记，仍放行（不串号）
    finally:
        conn.close()


def test_check_version_newer_than_expected_raises() -> None:
    """② 已登记版本比期望大（库比代码新）→ 同样抛错，不"降级使用"。"""
    conn = connect(":memory:")
    try:
        assert migrate(conn, "notice", _DDL_V3) == 3
        with pytest.raises(SchemaVersionError) as exc:
            check_version(conn, "notice", 2)
        assert _REMEDY_HINT in str(exc.value), f"文案缺处置提示：{exc.value}"
        assert schema_version(conn, "notice") == 3
    finally:
        conn.close()


def test_corrupt_version_raises_schema_version_error_not_value_error() -> None:
    """③ 版本值是 TEXT `'oops'` → 抛 SchemaVersionError（**不是** ValueError，也不当 0 放行）。"""
    conn = connect(":memory:")
    try:
        migrate(conn, "notice", _DDL)
        _tamper_version(conn, "notice", "oops")
        stored = conn.execute(
            "SELECT schema_version FROM meta WHERE owner = ?", ("notice",)
        ).fetchone()[0]
        assert isinstance(stored, str) and stored == "oops", (
            f"前置条件不成立：损坏值没真的以 TEXT 落库（读回 {stored!r}），"
            "那这个用例就没有在测损坏值"
        )
        with pytest.raises(SchemaVersionError) as exc:
            schema_version(conn, "notice")
        assert _REMEDY_HINT in str(exc.value), f"文案缺处置提示：{exc.value}"
        assert not isinstance(exc.value, ValueError), "损坏值不得让 ValueError 漏出去"
        with pytest.raises(SchemaVersionError) as exc2:
            check_version(conn, "notice", 2)
        assert _REMEDY_HINT in str(exc2.value), f"文案缺处置提示：{exc2.value}"
        assert not isinstance(exc2.value, ValueError), "损坏值不得让 ValueError 漏出去"
    finally:
        conn.close()


def test_check_version_equal_passes() -> None:
    """④ 已登记版本 == 期望 → 放行（不抛任何异常）。"""
    conn = connect(":memory:")
    try:
        assert migrate(conn, "notice", _DDL) == 2
        check_version(conn, "notice", 2)
        assert schema_version(conn, "notice") == 2
    finally:
        conn.close()


def test_check_version_allows_fresh_db_then_migrate_creates_tables() -> None:
    """⑤ 未登记的全新库（版本 0）→ 放行，且随后 migrate 真的能建表登记。"""
    conn = connect(":memory:")
    try:
        check_version(conn, "notice", 2)
        assert schema_version(conn, "notice") == 0
        assert migrate(conn, "notice", _DDL) == 2
        conn.execute("INSERT INTO unit (id, name) VALUES (1, '全新库')")
        assert conn.execute("SELECT COUNT(*) FROM unit").fetchone()[0] == 1
        assert {"unit", "member", "meta"} <= _table_names(conn)
    finally:
        conn.close()


def test_version_error_keeps_existing_tables_and_data() -> None:
    """⑥ 抛错之后：已有表与数据原样留存（没有 drop，也没有重建），版本值也没被顺手改掉。"""
    conn = connect(":memory:")
    try:
        migrate(conn, "notice", _DDL)
        conn.execute("INSERT INTO unit (id, name) VALUES (1, '留存')")
        _tamper_version(conn, "notice", 99)
        with pytest.raises(SchemaVersionError) as exc:
            migrate(conn, "notice", _DDL)
        assert _REMEDY_HINT in str(exc.value), f"文案缺处置提示：{exc.value}"
        assert {"unit", "member"} <= _table_names(conn), "已有表不见了：疑似被 drop"
        assert conn.execute("SELECT COUNT(*) FROM unit").fetchone()[0] == 1, "数据被清掉了"
        assert schema_version(conn, "notice") == 99, "不兼容的版本值不得被顺手改成期望值"
        assert conn.execute("SELECT COUNT(*) FROM meta WHERE owner = 'notice'").fetchone()[0] == 1
    finally:
        conn.close()


def test_migrate_incompatible_version_executes_no_ddl() -> None:
    """⑦ migrate 在不兼容版本上抛错，且**一条 DDL 都没执行**：目标表仍不存在、表集合一字未变。

    这是"不自动改表"的硬保证：校验若晚于建表，就会出现"建了半张表才报错"的半成品库。
    """
    conn = connect(":memory:")
    try:
        migrate(conn, "notice", _DDL)  # 版本 2
        _tamper_version(conn, "notice", 99)
        before = _table_names(conn)
        with pytest.raises(SchemaVersionError) as exc:
            migrate(conn, "notice", _DDL_V3)  # 期望 3，库里 99 → 不兼容，一条都不许执行
        assert _REMEDY_HINT in str(exc.value), f"文案缺处置提示：{exc.value}"
        assert "extra" not in _table_names(conn), "目标表被建出来了：DDL 在校验之前执行了"
        assert _table_names(conn) == before, "库里的表集合变了：不止一条 DDL 被执行"
        assert schema_version(conn, "notice") == 99
    finally:
        conn.close()
