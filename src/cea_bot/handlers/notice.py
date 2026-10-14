# -*- coding: utf-8 -*-
"""校内通知推送的编排层（任务 8.1 / 8.2）。

只做编排：抓取 → 解码 → 解析 → 增量判定 → 摘要 → PDF → 推送 → 记账。每个环节都由
`NoticeDeps` 注入，故单元测试可以完全离线地验证"编排顺序、干跑不发送、重叠跳过、
单条失败不静默、无文件能力只发文本"这些行为。

两条来自规格/设计的硬约束在这里落地：

- **串行化**（design.md D5）：框架明确允许定时任务重叠执行，且**不提供**互斥原语（runtime
  规格），故用模块级 `asyncio.Lock`：拿不到锁就**跳过本次并记日志**，不排队堆积。
- **先发后记**（D7，at-least-once）：发送成功后再 `claim_and_mark`，崩溃窗口内宁可重发也不漏发。
"""
import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp

from cea import Bot, Chat, Router
from cea_bot import store
from cea_bot.notices import fetch, parse, pdf, schema, summarize
from cea_bot.notices.fetch import RawPage
from cea_bot.notices.models import NoticeBrief, NoticeDetail
from cea_bot.notices.parse import NoticeParseError, decode_html, parse_detail, parse_list
from cea_bot.notices.pdf import PdfRenderError, pdf_file_name, render_pdf
from cea_bot.notices.schema import NoticeRow
from cea_bot.notices.summarize import SummaryOutcome

log = logging.getLogger("cea")

DEFAULT_DB_PATH = "data/state/cea_bot.db"
DEFAULT_PDF_DIR = "data/notices"
DEFAULT_TARGET_KIND = "group"

# 会话类型白名单：与 Chat.kind 的口径一致（群 / 单聊 / 频道）。
_CHAT_KINDS = ("group", "c2c", "channel")

# 确认支持"文件消息"的前端模块名（fail-closed：不在名单里的一律按不支持处理）。
_FILE_CAPABLE_MODULES = ("napcat",)

# 同一进程内的运行互斥（业务侧自建，框架不内置这类原语）。
_run_lock = asyncio.Lock()


@dataclass
class NoticeDeps:
    """注入点集合：默认全是真实实现，测试传假件即可离线跑完整编排。"""

    fetch_list: Callable[..., Awaitable[RawPage]]
    fetch_detail: Callable[..., Awaitable[RawPage]]
    summarize: Callable[..., Awaitable[SummaryOutcome]]
    render_pdf: Callable[..., Awaitable[pdf.PdfResult]]
    open_db: Callable[[str], sqlite3.Connection]
    now: Callable[[], datetime]


@dataclass(frozen=True)
class RunResult:
    """一次运行的结果：供日志与测试断言（不参与业务决策）。

    `remaining` 的口径是 `remaining == len(plan.pending) - len(pushed)`：本轮**未成功投递**的
    计划内条目数，**含**本轮处理失败的条目（失败的条目仍是 `pushed_at IS NULL`，下轮定点会重试）。
    因此它与失败日志里那句"剩余待投递"是**同一个数**，不是两个口径 —— 日志里绝不会出现
    两个可以互相矛盾解读的"剩余"。它让"还剩多少条"成为可断言的数据，而不是只能从日志里 grep。

    **仅在 `skipped=False` 时有意义**：本轮被跳过（拿不到运行锁）时根本没有产生计划，
    `remaining` 保持 `0`，此时这个 `0` 表示"未计算"，**不是**"没有积压"。
    """

    baseline: bool = False
    skipped: bool = False
    pushed: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    remaining: int = 0


def default_deps() -> NoticeDeps:
    """真实依赖（生产路径）。"""
    return NoticeDeps(
        fetch_list=fetch.fetch_list,
        fetch_detail=fetch.fetch_detail,
        summarize=summarize.summarize,
        render_pdf=render_pdf,
        open_db=store.connect,
        now=datetime.now,
    )


def supports_file_send(adapter: object) -> bool:
    """宿主前端是否支持**文件消息**（任务 8.2）。

    只有确认支持的前端才返回 True（当前即 NapCat：它实现了 file 段）；QQ 官方前端在 `send` 里
    明确丢弃文件段，其余**未知前端一律判为不支持**（fail-closed）。

    为什么 fail-closed：判成"支持"而前端实际丢弃，用户既收不到 PDF、文本里也不会出现
    "当前前端不支持文件"的说明 —— 那正是本功能要消灭的静默失效。判成"不支持"最坏只是多一句说明。

    这里按模块名判断而不是 `isinstance`：`isinstance` 要在导入期拉起 `botpy`（重依赖），
    而本函数需要在启动装配阶段就能廉价回答。将来若框架给出正式的能力标记（如 Adapter 上的
    能力属性），应改为用那个标记 —— 那需要一个改规格的变更。
    """
    if adapter is None:
        return False
    module = type(adapter).__module__.rsplit(".", 1)[-1]
    return module in _FILE_CAPABLE_MODULES


def platform_of(adapter: object) -> str:
    """取宿主的平台标识（用于构造 `Chat.platform`）；取不到时给一个中性值。"""
    platform = getattr(adapter, "platform", "")
    if isinstance(platform, str) and platform:
        return platform
    if adapter is None:
        return "notice"
    return type(adapter).__module__.rsplit(".", 1)[-1] or "notice"


def parse_targets(cfg: Mapping[str, Any], platform: str) -> tuple[Chat, ...]:
    """把 `notice_targets` 解析成会话列表。

    每项支持两种写法：`"<kind>:<id>"`（kind ∈ group / c2c / channel）或裸 `"<id>"`
    （kind 取 `notice_target_kind`，缺省 group）。裸写法是为最常见的"推到一个群"服务。
    """
    default_kind = str(cfg.get("notice_target_kind") or DEFAULT_TARGET_KIND).strip().lower()
    if default_kind not in _CHAT_KINDS:
        default_kind = DEFAULT_TARGET_KIND
    chats: list[Chat] = []
    for raw in cfg.get("notice_targets") or ():
        text = str(raw).strip()
        if not text:
            continue
        head, sep, tail = text.partition(":")
        if sep and head.strip().lower() in _CHAT_KINDS and tail.strip():
            chats.append(Chat(platform, tail.strip(), head.strip().lower()))
        else:
            chats.append(Chat(platform, text, default_kind))
    return tuple(chats)


def build_message_text(
    row: NoticeRow,
    detail: NoticeDetail,
    outcome: SummaryOutcome,
    notes: Sequence[str] = (),
) -> str:
    """组装推送文本：标题 / 文号 / 发布信息 / 详情链接 + 摘要或降级正文 + 附件清单 + 备注。"""
    lines = [f"【校内通知】{detail.title}"]
    if detail.doc_no:
        lines.append(f"文号：{detail.doc_no}")
    meta = " ".join(part for part in (row.published_on, row.dept) if part)
    if meta:
        lines.append(meta)
    lines.append(detail.url)
    lines.append("")
    lines.append(outcome.text)
    if not outcome.usable:
        reason = outcome.reason[:120]
        lines.append("")
        lines.append(f"（摘要不可用：{reason}；以上为正文原文）")
    if detail.attachments:
        lines.append("附件（未随本消息发送）：" + "；".join(item.name for item in detail.attachments))
    lines.extend(notes)
    return "\n".join(lines)


def build_router(
    cfg: Mapping[str, Any],
    *,
    platform: str = "notice",
    file_support: bool = True,
    deps: NoticeDeps | None = None,
) -> Router:
    """按 `notice_times` 逐个注册每日定点（任务 8.1）。

    这里返回 Router 而不是模块级常量：定点来自配置，而配置在启动时才读得到；
    模块级 router 会逼着导入期去读配置（那时还没有配置）。
    """
    router = Router()

    async def check_notices(bot: Bot) -> None:
        # 异常一律向上抛：框架的 log_done 会把它记进日志。这里不 try/except，
        # 因为"吞掉异常"正是本功能最危险的静默失效形态（页面改版会被读成没有新通知）。
        result = await run_once(
            bot, cfg, deps=deps, platform=platform, file_support=file_support
        )
        if result.skipped:
            log.info("通知检查重叠，本次已跳过")

    for rule in tuple(cfg.get("notice_times") or ()):
        router.schedule(str(rule))(check_notices)
    return router


async def run_once(
    bot: Bot,
    cfg: Mapping[str, Any],
    *,
    deps: NoticeDeps | None = None,
    session: aiohttp.ClientSession | None = None,
    platform: str = "notice",
    file_support: bool = True,
) -> RunResult:
    """执行一次通知检查；拿不到运行锁时**跳过**（返回 `skipped=True`）。"""
    if not cfg.get("notice_targets"):
        raise ValueError("通知推送未启用（notice_targets 为空）时不应调用 run_once")
    if _run_lock.locked():
        log.warning("上一次通知检查尚未结束，本次跳过（不排队，避免重复推送）")
        return RunResult(skipped=True)
    async with _run_lock:
        return await _run_locked(
            bot, cfg, deps=deps or default_deps(), session=session,
            platform=platform, file_support=file_support,
        )


async def _run_locked(
    bot: Bot,
    cfg: Mapping[str, Any],
    *,
    deps: NoticeDeps,
    session: aiohttp.ClientSession | None,
    platform: str,
    file_support: bool,
) -> RunResult:
    kind = int(cfg["notice_type"])
    chats = parse_targets(cfg, platform)
    dry_run = bool(cfg.get("notice_dry_run"))
    pdf_dir = Path(str(cfg.get("notice_pdf_dir") or DEFAULT_PDF_DIR))
    db_path = str(cfg.get("notice_db_path") or DEFAULT_DB_PATH)
    # 目录必须存在才能开库：`store.connect` 刻意不做隐式 IO（design.md D6），
    # 启动路径的 `config.ensure_notice_dirs` 负责**校验并给出可执行提示**；
    # 这里再做一次幂等创建，免得"运行中目录被删掉"变成一句难懂的 sqlite 报错。
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        conn = deps.open_db(db_path)
    except (sqlite3.Error, OSError) as exc:
        # 状态库打不开（路径是目录/文件损坏/无权限）也要给出可执行提示，
        # 而不是把 sqlite 的原始异常丢给用户（规格："状态不可用"要明确报错并给处置方式）。
        raise schema.state_unavailable(db_path, exc) from exc
    try:
        try:
            schema.ensure(conn)
        except (sqlite3.Error, OSError) as exc:
            # 库能打开但结构不对（旧库/被改过的库）也要给处置提示，不能裸抛 sqlite 异常。
            raise schema.state_unavailable(db_path, exc) from exc
        owned_session = session is None
        active = session if session is not None else aiohttp.ClientSession()
        try:
            page = await deps.fetch_list(active, kind)
            html = decode_html(page.raw, page.declared_charset)
            briefs = tuple(parse_list(html, kind))
            missing_dates = sum(1 for brief in briefs if not brief.published_on)
            if missing_dates:
                # 部分改版的静默风险：日期抽不到不会让解析抛错（内容仍在详情页），
                # 但排序会失效、PDF 文件名退化成 `000000-…`。这里必须留痕，否则"看起来正常"。
                log.warning(
                    "列表页有 %d/%d 条缺少发布日期：页面可能部分改版（排序与 PDF 文件名会退化）",
                    missing_dates, len(briefs),
                )
            now = deps.now().strftime("%Y-%m-%d %H:%M:%S")
            plan = schema.plan_run(conn, kind, briefs, now=now)
            if plan.baseline:
                log.info("首次运行：已登记 %d 条并建立基线（不推送历史条目）", len(briefs))
                return RunResult(baseline=True)
            pushed: list[str] = []
            failed: list[str] = []
            # 单次投递上限（0 = 不限制）。统计的是**成功投递**的条数：失败条目不计入名额，
            # 否则一个持续失败的最新条目会每轮都占掉名额，把更旧的条目永远挤在后面（饥饿）。
            limit = int(cfg.get("notice_max_per_run") or 0)
            # 真实的"本轮因上限截断"标志：只在**循环因上限 break** 时置 True。
            # 不能从 `len(pushed) >= limit` 反推：cap=2 且 3 条待推送、其中第 1 条失败时，
            # 本轮 3 条会被全部处理完（上限没有推迟任何条目），反推却会误报"已达上限"。
            # 判据写成 `limit > 0`：上限 0（缺省 = 不限制）**永不截断**，负数同样不截断
            # （负数在生产路径已被 `check_config` 拒绝；这里只是让不变量显式，避免出现
            # "本次投递 0 条（上限 -1）"这类自相矛盾的日志）。
            truncated = False
            for row in plan.pending:
                if limit > 0 and len(pushed) >= limit:
                    truncated = True
                    break
                try:
                    await _push_one(
                        bot, cfg, row, kind, chats, pdf_dir=pdf_dir, dry_run=dry_run,
                        file_support=file_support, deps=deps, session=active, conn=conn, now=now,
                    )
                except (fetch.FetchError, NoticeParseError) as exc:
                    # 单条失败不拖垮整轮：记库 + 记日志，下次定点重试（该条不记账）。
                    log.error("通知 %s 处理失败，本次跳过（下次定点重试）：%s", row.edit_id, exc)
                    schema.mark_error(conn, kind, row.edit_id, f"{type(exc).__name__}: {exc}")
                    failed.append(row.edit_id)
                    continue
                pushed.append(row.edit_id)
            remaining = len(plan.pending) - len(pushed)
            if truncated:
                # 只有真的因上限截断（还有条目被推迟到下次）才报这一行。
                log.info(
                    "本次投递 %d 条（上限 %d），剩余 %d 条待下次定点",
                    len(pushed), limit, remaining,
                )
            if failed:
                # 失败条目也算在 `remaining` 里（下轮重试），故这里把口径写全，
                # 免得读者把"剩余待投递"与上一行的"剩余"读成两倍的积压。
                log.warning(
                    "本次有 %d 条处理失败（下次定点重试）：%s；剩余待投递 %d 条（含本轮失败 %d 条）",
                    len(failed), "、".join(failed), remaining, len(failed),
                )
            return RunResult(pushed=tuple(pushed), failed=tuple(failed), remaining=remaining)
        finally:
            if owned_session:
                await active.close()
    finally:
        conn.close()


async def _push_one(
    bot: Bot,
    cfg: Mapping[str, Any],
    row: NoticeRow,
    kind: int,
    chats: tuple[Chat, ...],
    *,
    pdf_dir: Path,
    dry_run: bool,
    file_support: bool,
    deps: NoticeDeps,
    session: aiohttp.ClientSession,
    conn: sqlite3.Connection,
    now: str,
) -> None:
    """处理并推送一条通知（含摘要与 PDF），最后抢占式记账。"""
    detail_page = await deps.fetch_detail(session, row.edit_id)
    detail_html = decode_html(detail_page.raw, detail_page.declared_charset)
    detail = parse_detail(detail_html, row.edit_id, kind, detail_page.url)
    brief = NoticeBrief(
        kind=row.kind, edit_id=row.edit_id, published_on=row.published_on,
        title=row.title, dept=row.dept,
    )
    outcome = await deps.summarize(detail, cfg, brief)
    if not outcome.usable:
        log.warning("通知 %s 的摘要降级为正文原文：%s", row.edit_id, outcome.reason)

    # 产物名 `YYMMDD-<标题>.pdf`（相对主规格「PDF 产物命名」）。标题取**列表行**的
    # `row.title`：它随状态库一起落库，不需要为命名多抓一次详情。
    # `pdf_target` 是本条**打算**写入的路径，`pdf_path` 是"真的渲染成功、可用于归档与发送"的
    # 路径（失败时置 None）—— 两者分开命名，省得让 `Path | None` 的类型收窄在调用点上打架。
    pdf_target = pdf_dir / pdf_file_name(row.published_on, row.title)
    if pdf_target.exists():
        # design D4：名字里不再有 editId，同日同标题必然撞名。渲染会**覆盖**已有文件，
        # 故覆盖前必须留痕（含 edit_id 与路径），不得静默覆盖 —— 否则归档里悄悄少一份。
        log.warning(
            "通知 %s 的 PDF 目标文件已存在，本次渲染将覆盖它（同日同标题撞名）：%s",
            row.edit_id, pdf_target,
        )
    pdf_path: Path | None = pdf_target
    pdf_ok = False
    try:
        await deps.render_pdf(detail_html, pdf_target)
        pdf_ok = True
    except (PdfRenderError, OSError) as exc:
        # 规格：PDF 生成失败 SHALL 降级为只发送文本并记录告警，且该条仍按"已推送"记账。
        # `OSError` 也要收：`render_pdf` 的目录创建/写盘/读文件头都在渲染器之外，
        # 磁盘满或权限问题会以 OSError 冒出；漏掉它就会变成"整轮中止、连文本都没发"。
        log.warning("通知 %s 的 PDF 生成失败，降级为只发文本：%s", row.edit_id, exc)
        pdf_path = None

    notes: list[str] = []
    if not pdf_ok:
        notes.append("PDF 生成失败，本条仅发送文本")
    elif not file_support:
        notes.append(f"当前平台前端不支持文件消息，本条仅发送文本（PDF 已存本地：{pdf_path}）")
    text = build_message_text(row, detail, outcome, notes)

    if dry_run:
        log.info(
            "[干跑] 通知 %s 将推送给 %d 个会话，文本 %d 字：%s",
            row.edit_id, len(chats), len(text), text.replace("\n", " ｜ ")[:300],
        )
        if pdf_path is not None:
            log.info("[干跑] 通知 %s 的 PDF 路径：%s", row.edit_id, pdf_path)
    else:
        for chat in chats:
            await bot.send(chat, text=text)
            if pdf_path is not None and file_support:
                await bot.send(chat, file=str(pdf_path), file_name=pdf_path.name)

    if not schema.claim_and_mark(
        conn, kind, row.edit_id, now=now, pdf_path=str(pdf_path) if pdf_path else None
    ):
        # 抢占失败只可能因为该行已被别的运行置为非 NULL。无论干跑与否，调用方都仍把它计入
        # "成功投递"（也就计入上限名额）—— 这与规格一致，不是重复计数。文案必须区分两种事实：
        # 真实运行下本进程确实已经把消息发出去了；干跑下**没有发送**（只记了"[干跑] …将推送…"），
        # 但仍照既有语义记账。下轮它不会再出现在 increment 里，不存在重发。
        delivered = "本次为干跑（未发送）" if dry_run else "本进程已投递成功"
        log.warning("通知 %s 已被其它运行记账；%s，仍按已推送计数", row.edit_id, delivered)
