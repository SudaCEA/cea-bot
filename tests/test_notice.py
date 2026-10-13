# -*- coding: utf-8 -*-
"""通知推送（notice-digest）配置校验单测。

覆盖任务组 1.3 的六项口径：
① 未启用（`notice_targets` 为空）时放行，且返回结构与本变更之前逐字一致
② 缺 `deepseek_api_key` 时快速失败 ③ `notice_times` 非法时快速失败
④ `notice_type` 越界时快速失败 ⑤ 合法配置返回含 notice 字段的字典
⑥ `ensure_notice_dirs` 建得出目录、目录不可用/不可写时明确报错。

约定：`check_config` 是纯函数（不碰文件系统，见 `test_check_config_creates_no_dirs`）；
失败一律 SystemExit（不放宽成任意异常）。本文件无协程场景，故无需 asyncio.run 包裹。
"""
from pathlib import Path

import pytest

import cea_bot.config as config_module
from cea_bot.config import check_config, ensure_notice_dirs

# 既有的 qqofficial 必需键（与 tests/test_napcat.py 同口径）
_QQ = {"appid": "a", "secret": "s"}

# 一份"已启用通知推送"的合法配置
_ENABLED = {
    "notice_targets": ["12345"],
    "notice_type": 1,
    "notice_times": ["09:00", "15:00"],
    "notice_db_path": "data/state/cea_bot.db",
    "notice_pdf_dir": "data/notices",
    "deepseek_api_key": "sk-not-a-real-key",
    "deepseek_model": "deepseek-flash",
    "notice_dry_run": False,
}


def _enabled(**overrides: object) -> dict[str, object]:
    """构造"已启用通知推送"的原始配置（键值可覆盖）。"""
    return {**_QQ, **_ENABLED, **overrides}


# ---- ① 未启用：放行且返回结构逐字不变 ----


def test_notice_disabled_keeps_return_shape():
    """notice_targets 为空 → 放行，且返回值与本变更之前逐字一致（不追加 notice 字段）。"""
    assert check_config({**_QQ, "notice_targets": []}) == {
        "adapter": "qqofficial",
        "appid": "a",
        "secret": "s",
        "group_whitelist": [],
    }


def test_notice_absent_keeps_return_shape():
    """完全没有 notice 键（旧 config.yaml）→ 同样放行、结构不变。"""
    assert check_config(dict(_QQ)) == check_config({**_QQ, "notice_targets": []})


def test_notice_disabled_napcat_branch_unchanged():
    """napcat 分支同样不受影响（既有键一字不动）。"""
    cfg = check_config(
        {"adapter": "napcat", "ws_url": "ws://127.0.0.1:3001", "token": "t", "notice_targets": []}
    )
    assert cfg == {
        "adapter": "napcat",
        "ws_url": "ws://127.0.0.1:3001",
        "token": "t",
        "group_whitelist": [],
    }


def test_notice_targets_not_list_fails_fast():
    """notice_targets 类型错误 → 快速失败（带可执行提示）。"""
    with pytest.raises(SystemExit) as exc:
        check_config({**_QQ, "notice_targets": "12345"})
    assert "notice_targets" in str(exc.value)


# ---- ② 缺密钥：快速失败，且不回显密钥 ----


@pytest.mark.parametrize("bad_key", ["", "   ", "YOUR_DEEPSEEK_API_KEY"])
def test_notice_requires_deepseek_key(bad_key):
    with pytest.raises(SystemExit) as exc:
        check_config(_enabled(deepseek_api_key=bad_key))
    msg = str(exc.value)
    assert "deepseek_api_key" in msg
    if bad_key.strip():  # 密钥（含占位值）不得回显到提示文案里
        assert bad_key.strip() not in msg


# ---- ③ notice_times 非法：快速失败 ----


@pytest.mark.parametrize(
    "bad_times",
    [[], "09:00", ["9点"], ["25:00"], ["09:60"], ["09:00", "24:00"]],
    ids=["empty", "not-list", "garbage", "hour-out-of-range", "minute-out-of-range", "second-bad"],
)
def test_notice_times_invalid_fails_fast(bad_times):
    with pytest.raises(SystemExit) as exc:
        check_config(_enabled(notice_times=bad_times))
    assert "notice_times" in str(exc.value)


# ---- ④ notice_type 越界：快速失败 ----


@pytest.mark.parametrize("bad_type", [0, 4, -1, "x", None], ids=["0", "4", "-1", "str", "none"])
def test_notice_type_out_of_range_fails_fast(bad_type):
    with pytest.raises(SystemExit) as exc:
        check_config(_enabled(notice_type=bad_type))
    msg = str(exc.value)
    assert "notice_type" in msg
    # 栏目标签用站点导航口径（回归：曾错写成"学校发文/部门发文/其他栏目"）
    assert "校内通知" in msg and "学术报告" in msg


# ---- ⑤ 合法配置：返回含 notice 字段的字典 ----


def test_notice_enabled_returns_notice_fields():
    cfg = check_config(_enabled())
    assert cfg["adapter"] == "qqofficial"  # 既有字段仍在
    assert cfg["appid"] == "a" and cfg["secret"] == "s"
    assert cfg["notice_targets"] == ["12345"]
    assert cfg["notice_type"] == 1
    assert cfg["notice_times"] == ["09:00", "15:00"]
    assert cfg["notice_db_path"] == "data/state/cea_bot.db"
    assert cfg["notice_pdf_dir"] == "data/notices"
    assert cfg["deepseek_api_key"] == "sk-not-a-real-key"
    assert cfg["deepseek_model"] == "deepseek-flash"
    assert cfg["notice_dry_run"] is False


def test_notice_enabled_normalizes_and_defaults():
    """定点归一化为 "HH:MM"；缺省键按 config.example.yaml 的缺省值补齐。"""
    cfg = check_config(
        {
            **_QQ,
            "notice_targets": [" 12345 ", ""],  # 去空白、丢空项
            "notice_times": ["9:5", "21:00"],
            "deepseek_api_key": "sk-not-a-real-key",
        }
    )
    assert cfg["notice_targets"] == ["12345"]
    assert cfg["notice_times"] == ["09:05", "21:00"]
    assert cfg["notice_type"] == 1
    assert cfg["notice_db_path"] == "data/state/cea_bot.db"
    assert cfg["notice_pdf_dir"] == "data/notices"
    assert cfg["deepseek_model"] == "deepseek-flash"
    assert cfg["notice_dry_run"] is False


def test_notice_enabled_with_napcat_branch():
    """adapter=napcat 且启用通知推送：两个分支的字段都在。"""
    cfg = check_config(
        {
            "adapter": "napcat",
            "ws_url": "ws://127.0.0.1:3001",
            "token": "t",
            **_ENABLED,
        }
    )
    assert cfg["adapter"] == "napcat" and cfg["ws_url"] == "ws://127.0.0.1:3001"
    assert cfg["notice_times"] == ["09:00", "15:00"]


def test_check_config_creates_no_dirs(tmp_path):
    """check_config 是纯函数：即使启用，也不得替调用方创建目录（IO 归 ensure_notice_dirs）。"""
    db = tmp_path / "state" / "cea_bot.db"
    pdf = tmp_path / "notices"
    cfg = check_config(_enabled(notice_db_path=str(db), notice_pdf_dir=str(pdf)))
    assert cfg["notice_db_path"] == str(db) and cfg["notice_pdf_dir"] == str(pdf)
    assert not pdf.exists()
    assert not db.parent.exists()


# ---- ⑤b 试运行开关：只接受语义明确的写法（不让 bool("false") 静默变 True）----

@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, True), (False, False), ("true", True), ("FALSE", False), (" false ", False), (1, True), (0, False)],
    ids=["bool-true", "bool-false", "str-true", "str-FALSE", "str-padded", "int-1", "int-0"],
)
def test_notice_dry_run_accepts_explicit_booleans(raw, expected):
    assert check_config(_enabled(notice_dry_run=raw))["notice_dry_run"] is expected


@pytest.mark.parametrize("raw", ["yes", "no", "maybe", 2, "", None], ids=["yes", "no", "maybe", "2", "empty", "none"])
def test_notice_dry_run_rejects_ambiguous_values(raw):
    with pytest.raises(SystemExit) as exc:
        check_config(_enabled(notice_dry_run=raw))
    assert "notice_dry_run" in str(exc.value)


# ---- ⑥ ensure_notice_dirs：建目录 / 不可创建 / 不可写 ----


def test_ensure_notice_dirs_creates_dirs(tmp_path):
    db = tmp_path / "state" / "cea_bot.db"
    pdf = tmp_path / "notices"
    result = ensure_notice_dirs(_enabled(notice_db_path=str(db), notice_pdf_dir=str(pdf)))
    assert result == (db.parent, pdf)
    assert db.parent.is_dir() and pdf.is_dir()
    # 写入探测文件必须清理干净，不留残留
    assert list(pdf.iterdir()) == []
    assert list(db.parent.iterdir()) == []


def test_ensure_notice_dirs_disabled_is_noop(tmp_path):
    """未启用时不碰文件系统。"""
    pdf = tmp_path / "notices"
    assert ensure_notice_dirs({"notice_targets": [], "notice_pdf_dir": str(pdf)}) is None
    assert not pdf.exists()


def test_ensure_notice_dirs_fails_when_path_blocked(tmp_path):
    """路径被同名普通文件占住 → 目录建不出来，必须明确报错并点出配置键。"""
    blocked = tmp_path / "notices"
    blocked.write_text("我是文件，不是目录", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        ensure_notice_dirs(
            _enabled(notice_db_path=str(tmp_path / "state" / "cea_bot.db"), notice_pdf_dir=str(blocked))
        )
    msg = str(exc.value)
    assert "notice_pdf_dir" in msg
    assert str(blocked) in msg


def test_ensure_notice_dirs_reports_unwritable(tmp_path, monkeypatch):
    """目录存在但不可写 → 明确报错（不放行、不静默）。

    Windows 下只读属性不阻止在目录内建文件（实测 os.access 仍为 True），
    故此处替换 os.access 判据来逼出该分支（只对 PDF 目录返回不可写，
    状态库目录仍走真实判据）；真实路径探测由其它用例覆盖。
    """
    pdf = tmp_path / "notices"
    pdf.mkdir()
    real_access = config_module.os.access
    monkeypatch.setattr(
        config_module.os,
        "access",
        lambda path, mode: False if Path(path) == pdf else real_access(path, mode),
    )
    with pytest.raises(SystemExit) as exc:
        ensure_notice_dirs(
            _enabled(notice_db_path=str(tmp_path / "state" / "cea_bot.db"), notice_pdf_dir=str(pdf))
        )
    msg = str(exc.value)
    assert "notice_pdf_dir" in msg
    assert "不可写" in msg


def test_ensure_notice_dirs_reports_write_probe_failure(tmp_path, monkeypatch):
    """os.access 说可写但真实写入失败（如 POSIX 只读、ACL 拒绝）→ 同样明确报错。"""
    pdf = tmp_path / "notices"
    real_mkstemp = config_module.tempfile.mkstemp

    def _boom(*args: object, **kwargs: object) -> tuple[int, str]:
        if Path(str(kwargs.get("dir", ""))) == pdf:
            raise PermissionError(13, "Permission denied")
        return real_mkstemp(*args, **kwargs)  # 其余目录（状态库）仍走真实探测

    monkeypatch.setattr(config_module.tempfile, "mkstemp", _boom)
    with pytest.raises(SystemExit) as exc:
        ensure_notice_dirs(
            _enabled(notice_db_path=str(tmp_path / "state" / "cea_bot.db"), notice_pdf_dir=str(pdf))
        )
    msg = str(exc.value)
    assert "notice_pdf_dir" in msg
    assert "不可写" in msg


# ---- ⑤c 可选键必须落进校验后的返回值（回归："参数接了没传"）----
#
# 下游（handler / 摘要层）只拿得到 `check_config` 的返回值；可选键若没被写进返回字典，
# 配置项就是**完全失效**的死键，而 README 还在承诺它生效。这里逐键钉住。


def test_optional_keys_survive_validation():
    cfg = check_config(_enabled(notice_target_kind="c2c", deepseek_base_url="http://127.0.0.1:9/"))
    assert cfg["notice_target_kind"] == "c2c"
    assert cfg["deepseek_base_url"] == "http://127.0.0.1:9"  # 尾部斜杠归一


def test_optional_keys_have_defaults():
    cfg = check_config(_enabled())
    assert cfg["notice_target_kind"] == "group"
    assert cfg["deepseek_base_url"] == "https://api.deepseek.com"
    assert cfg["notice_max_chars"] == 1500


@pytest.mark.parametrize("bad_kind", ["私聊", "gc", "group2", "c2c2"], ids=["cjk", "gc", "group2", "c2c2"])
def test_notice_target_kind_invalid_fails_fast(bad_kind):
    with pytest.raises(SystemExit) as exc:
        check_config(_enabled(notice_target_kind=bad_kind))
    assert "notice_target_kind" in str(exc.value)


@pytest.mark.parametrize("bad_url", ["api.deepseek.com", "ftp://x", "   "], ids=["no-scheme", "ftp", "blank"])
def test_deepseek_base_url_invalid_fails_fast(bad_url):
    with pytest.raises(SystemExit) as exc:
        check_config(_enabled(deepseek_base_url=bad_url))
    assert "deepseek_base_url" in str(exc.value)
