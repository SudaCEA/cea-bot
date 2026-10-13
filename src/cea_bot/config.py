# -*- coding: utf-8 -*-
"""配置读取与校验：定位仓库根的 config.yaml，按 adapter 分支校验。

`check_config` 是纯函数（已被测试直接依赖），`load_config` 只做文件 IO。
通知推送的运行前提是"目录可创建、可写"——那是 IO，归 `ensure_notice_dirs`（由启动路径调用），
`check_config` 自身绝不碰文件系统（tests/test_notice.py 盯住这条边界）。
"""
import contextlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from cea.app import parse_hhmm

# 仓库根 = src/cea_bot/config.py 的 parents[2]（[0]=cea_bot, [1]=src, [2]=仓库根）。
# 不能照抄 `parent`：那会指向 src/cea_bot/，让 load_config 去找
# src/cea_bot/config.yaml —— 该文件不存在，错误只在"配置缺失"文案里出现，
# 属静默失败（由 tests/test_layout.py 逼住）。
ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config.yaml"

_QQ_SETTING_URL = "https://q.qq.com/qqbot/#/developer/developer-setting"

# 通知推送的缺省值必须与 config.example.yaml 一致：启用（notice_targets 非空）而某个键缺失时，
# 这几项按缺省补齐。notice_times 是例外 —— 缺省成"永不触发"属静默失效，故启用时留空直接报错。
_DEFAULT_NOTICE_DB_PATH = "data/state/cea_bot.db"
_DEFAULT_NOTICE_PDF_DIR = "data/notices"
_DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"
# 正文/摘要的长度上限：规格要求"超过**可配置**长度上限时截断并注明"，故它可由
# `notice_max_chars` 覆盖（可选键，未写进 config.example.yaml，用法见 README）。
_DEFAULT_NOTICE_MAX_CHARS = 1500
_DEFAULT_NOTICE_TARGET_KIND = "group"
_DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
# 裸会话号可用的会话类型（与 Chat.kind 的口径一致）
_CHAT_KINDS = ("group", "c2c", "channel")
# notice_type 的合法取值（站点导航口径）：1 = 校内通知，2 = 学校发文，3 = 学术报告
_NOTICE_TYPES = (1, 2, 3)


def check_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """校验配置（纯函数，供测试）；不合法直接退出并说明。

    先按 adapter 分支校验（既有口径，逐字未改），再合并通知推送的校验结果：
    `notice_targets` 为空 = 该功能关闭，此时**不追加任何 notice 字段**，
    返回值结构与本变更之前逐字一致。
    """
    validated = _check_platform(cfg)
    validated.update(_notice_config(cfg))
    return validated


def _check_platform(cfg: dict[str, Any]) -> dict[str, Any]:
    """按 adapter 分支校验配置（纯函数，供测试）；不合法直接退出并说明。"""
    adapter = str(cfg.get("adapter") or "qqofficial").strip().lower()
    whitelist_raw = cfg.get("group_whitelist") or []
    if not isinstance(whitelist_raw, list):
        sys.exit("[配置错误] group_whitelist 应为群号字符串列表")
    group_whitelist = [str(item).strip() for item in whitelist_raw if str(item).strip()]
    if adapter == "qqofficial":
        appid = str(cfg.get("appid") or "").strip()
        secret = str(cfg.get("secret") or "").strip()
        if not appid or not secret or appid.startswith("YOUR_") or secret.startswith("YOUR_"):
            sys.exit(
                "[等待接入 apikey] adapter=qqofficial 需要机器人凭据：\n"
                f"  1. 登录 QQ 机器人开放平台：{_QQ_SETTING_URL}\n"
                "  2. 在「开发设置」页复制 AppID 与 AppSecret\n"
                "  3. 填入 config.yaml 后重新运行：.venv\\Scripts\\python -m cea_bot"
            )
        return {"adapter": adapter, "appid": appid, "secret": secret,
                "group_whitelist": group_whitelist}
    if adapter == "napcat":
        ws_url = str(cfg.get("ws_url") or "").strip()
        token = str(cfg.get("token") or "").strip()
        if not ws_url:
            sys.exit(
                "[配置缺失] adapter=napcat 需要填写 ws_url（NapCat 正向 WS 地址，"
                "如 ws://127.0.0.1:3001）。NapCat 侧配置步骤见 src/cea/adapters/napcat.py 模块说明。"
            )
        return {"adapter": adapter, "ws_url": ws_url, "token": token,
                "group_whitelist": group_whitelist}
    sys.exit(f"[配置错误] 未知 adapter: {adapter!r}，可选值：qqofficial / napcat")


def _notice_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """校验通知推送配置（纯函数，不碰文件系统）；未启用时返回空字典。

    `notice_targets` 非空即视为启用该功能，此时校验：密钥存在、`notice_times` 为合法
    "HH:MM" 列表、`notice_type` ∈ {1,2,3}。目录是否可创建可写属 IO，由
    `ensure_notice_dirs` 负责（D12：配置期快速失败）。
    """
    targets_raw = cfg.get("notice_targets") or []
    if not isinstance(targets_raw, list):
        sys.exit("[配置错误] notice_targets 应为会话 ID 字符串列表，如 [\"12345\"]；留空表示关闭该功能")
    notice_targets = [str(item).strip() for item in targets_raw if str(item).strip()]
    if not notice_targets:
        return {}  # 关闭该功能：返回值结构与本变更之前完全一致
    api_key = str(cfg.get("deepseek_api_key") or "").strip()
    if not api_key or api_key.startswith("YOUR_"):
        sys.exit(
            "[配置缺失] 已配置 notice_targets（启用校内通知推送），但 deepseek_api_key 未填写：\n"
            "  1. 到 https://platform.deepseek.com 申请 API key\n"
            "  2. 填入 config.yaml 的 deepseek_api_key（该文件含密钥且已被 .gitignore 排除，勿提交）\n"
            "  3. 或把 notice_targets 清空以关闭该功能"
        )
    # 密钥只进返回值供业务层调用模型：下面的返回值与任何日志/异常文案都不得包含它（D12）。
    return {
        "notice_targets": notice_targets,
        "notice_type": _notice_type(cfg),
        "notice_times": _notice_times(cfg),
        "notice_db_path": str(cfg.get("notice_db_path") or _DEFAULT_NOTICE_DB_PATH).strip(),
        "notice_pdf_dir": str(cfg.get("notice_pdf_dir") or _DEFAULT_NOTICE_PDF_DIR).strip(),
        "notice_max_chars": _notice_max_chars(cfg),
        "notice_target_kind": _notice_target_kind(cfg),
        "deepseek_base_url": _deepseek_base_url(cfg),
        "deepseek_api_key": api_key,
        "deepseek_model": str(cfg.get("deepseek_model") or _DEFAULT_DEEPSEEK_MODEL).strip(),
        "notice_dry_run": _notice_dry_run(cfg),
    }


def _notice_times(cfg: dict[str, Any]) -> list[str]:
    """校验检查定点：复用框架的 `parse_hhmm`（不重写正则），并归一化为 "HH:MM"。"""
    raw = cfg.get("notice_times")
    if not isinstance(raw, list) or not raw:
        sys.exit(
            "[配置错误] notice_times 应为非空的 \"HH:MM\" 字符串列表（如 [\"09:00\", \"15:00\"]）；"
            "已配置 notice_targets 时留空会让定时检查永不触发"
        )
    times: list[str] = []
    for item in raw:
        text = str(item).strip()
        try:
            hh, mm = parse_hhmm(text)
        except ValueError as exc:
            sys.exit(f"[配置错误] notice_times 含非法定点：{exc}；应为 24 小时制 \"HH:MM\"")
        times.append(f"{hh:02d}:{mm:02d}")
    return times


def _notice_type(cfg: dict[str, Any]) -> int:
    """校验栏目取值 ∈ {1,2,3}（站点口径：1 = 校内通知，2 = 学校发文，3 = 学术报告）。"""
    raw = cfg.get("notice_type", 1)
    text = str(raw).strip()
    if not text.isdigit() or int(text) not in _NOTICE_TYPES:
        sys.exit(
            f"[配置错误] notice_type 取值非法：{raw!r}；可选 1（校内通知）/ 2（学校发文）/ 3（学术报告）"
        )
    return int(text)


def _notice_max_chars(cfg: dict[str, Any]) -> int:
    """可选的正文/摘要长度上限（缺省 1500 字）；必须是正整数。

    配置成 0 或负数等于"把正文截成空" —— 那是静默失效，故直接拒绝而不是当成"不限制"。
    """
    raw = cfg.get("notice_max_chars", _DEFAULT_NOTICE_MAX_CHARS)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        sys.exit(f"[配置错误] notice_max_chars 应为正整数（字符数），收到：{raw!r}")
    if value <= 0:
        sys.exit(f"[配置错误] notice_max_chars 应为正整数（字符数），收到：{raw!r}")
    return value


def _notice_target_kind(cfg: dict[str, Any]) -> str:
    """裸会话号（如 "12345"）按哪种会话推送：`group`（缺省）/ `c2c` / `channel`。

    必须在**校验阶段**落进返回值：下游只拿得到校验后的字典，键被丢掉就等于这个配置项
    完全失效（"参数接了没传"，本仓库点名的红线）。
    """
    raw = str(cfg.get("notice_target_kind") or _DEFAULT_NOTICE_TARGET_KIND).strip().lower()
    if raw not in _CHAT_KINDS:
        sys.exit(
            f"[配置错误] notice_target_kind 取值非法：{raw!r}；可选 group（群）/ c2c（私聊）/ channel（频道）"
        )
    return raw


def _deepseek_base_url(cfg: dict[str, Any]) -> str:
    """摘要服务的 OpenAI 兼容端点（缺省官方端点）；可指向自建服务（合规逃生口）。

    同样必须落进返回值，否则用户以为"正文不外发了"，实际仍打官方端点 —— 那是最坏的一种静默。
    """
    raw = str(cfg.get("deepseek_base_url") or _DEFAULT_DEEPSEEK_BASE_URL).strip().rstrip("/")
    if not raw.startswith(("http://", "https://")):
        sys.exit(f"[配置错误] deepseek_base_url 应以 http:// 或 https:// 开头，收到：{raw!r}")
    return raw


def _notice_dry_run(cfg: dict[str, Any]) -> bool:
    """校验试运行开关：只接受语义明确的写法。

    不能直接用 `bool(...)` 兜底 —— `bool("false")` 为 True，会让"引号括起来的 false"
    静默变成"永远干跑"（或反向偏差）。这类静默偏差一律拒绝。
    """
    raw = cfg.get("notice_dry_run", False)
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in ("true", "false"):
        return text == "true"
    if text in ("0", "1"):
        return text == "1"
    sys.exit(f"[配置错误] notice_dry_run 取值非法：{raw!r}；应为 YAML 布尔值 true / false")


def ensure_notice_dirs(cfg: dict[str, Any]) -> tuple[Path, Path] | None:
    """创建并探测通知推送的运行目录（IO 边界，由启动路径调用）。

    `check_config` 是纯函数、不碰文件系统；"状态库所在目录与 PDF 产物目录可创建、可写"
    这一运行前提在本函数里检查（含真实写入探测），失败时 sys.exit 给出可执行提示。
    未启用（`notice_targets` 为空）时不碰文件系统，返回 None；否则返回
    (状态库所在目录, PDF 产物目录)。
    """
    if not cfg.get("notice_targets"):
        return None
    db_dir = Path(str(cfg.get("notice_db_path") or _DEFAULT_NOTICE_DB_PATH)).parent
    pdf_dir = Path(str(cfg.get("notice_pdf_dir") or _DEFAULT_NOTICE_PDF_DIR))
    _ensure_writable_dir(db_dir, "notice_db_path")
    _ensure_writable_dir(pdf_dir, "notice_pdf_dir")
    return db_dir, pdf_dir


def _ensure_writable_dir(path: Path, key: str) -> None:
    """确认目录存在且可写；不可创建或不可写一律 sys.exit，提示里点出对应的配置键。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        sys.exit(
            f"[配置错误] {key} 指向的目录无法创建：{path}（{type(exc).__name__}: {exc}）\n"
            "  1. 确认该路径不是已存在的文件，且父目录可访问\n"
            f"  2. 或在 config.yaml 中把 {key} 改为可写路径"
        )
    if not os.access(path, os.W_OK):
        sys.exit(_unwritable_hint(path, key))
    try:
        fd, probe_name = tempfile.mkstemp(dir=path, prefix=".cea_write_probe_")
        os.close(fd)
    except OSError as exc:
        sys.exit(_unwritable_hint(path, key, exc))
    with contextlib.suppress(OSError):
        # 仅清理探测文件；删不掉不影响"可写"这一结论，故此处不升级为错误。
        Path(probe_name).unlink()


def _unwritable_hint(path: Path, key: str, exc: OSError | None = None) -> str:
    """不可写时的可执行提示（含配置键名，便于直接定位到要改哪一行）。"""
    detail = f"（{type(exc).__name__}: {exc}）" if exc is not None else ""
    return (
        f"[配置错误] {key} 指向的目录不可写：{path}{detail}\n"
        "  1. 检查目录权限（Windows：属性里的只读/安全设置；POSIX：chmod u+w）\n"
        f"  2. 或在 config.yaml 中把 {key} 改为可写路径"
    )


def load_config() -> dict[str, Any]:
    """读取 config.yaml 并校验；未找到配置文件时明确提示。"""
    if not CONFIG_PATH.exists():
        sys.exit(f"[配置缺失] 未找到 {CONFIG_PATH}，请参考 config.example.yaml 创建。")
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return check_config(cfg)
