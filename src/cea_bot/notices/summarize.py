# -*- coding: utf-8 -*-
"""摘要层（任务 6.1 / 6.2）：把正文交给对话模型整理成要点，并保证"失败必降级、绝不发空"。

定位（design.md D3）：**文本提取由本地解析完成**（正文是 HTML，本地提取零成本、可离线测），
模型只用来"把三四千字的正文整理成可读的要点"。故本模块不是主路径的依赖：模型不可用时，
降级为发送截断后的正文原文，并留下明确告警（规格：SHALL NOT 发送空消息）。

安全与合规（D12）：只发送**正文文本**与附件**名称**，不发送附件内容；密钥只从配置读取，
不写进任何日志或异常文案。
"""
import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import aiohttp

from cea_bot.notices.models import NoticeBrief, NoticeDetail

log = logging.getLogger("cea")

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_MAX_CHARS = 1500
LLM_TIMEOUT = 60.0

TRUNCATED_MARK = "\n……（已截断）"

# 疑似密钥片段：日志与推送文本都可能带上游响应的片段，落盘前统一打码。
# （规格要求密钥不得出现在任何日志里；上游若回显请求头，不设防就会泄。）
_KEYLIKE = re.compile(r"sk-[A-Za-z0-9_\-]{4,}")


def redact(text: str) -> str:
    """把疑似密钥片段打码（`sk-xxxx` → `sk-***`），保留其余诊断信息。"""
    return _KEYLIKE.sub("sk-***", text)

SYSTEM_PROMPT = (
    "你是校内通知的编辑助手。请把用户给出的通知正文整理成简洁要点，"
    "只依据给定的正文，不要补充或推测任何未出现的信息。"
    "输出格式：先一行「要点：」，再 3–6 条以「- 」开头的短句；"
    "若正文含截止时间、报送对象、联系人/电话、材料清单，逐条列出。不要复述全文。"
)

# 注入点：`(prompt, cfg) -> 原始响应文本`。默认实现直连 OpenAI 兼容接口；
# 单元测试传假件即可离线覆盖"成功 / 超时 / 报错 / 返回不可用"四条路径。
Client = Callable[[str, Mapping[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class SummaryOutcome:
    """摘要结果：`usable=True` 时 `text` 是摘要；否则 `text` 是**降级后的正文**（永不为空）。"""

    text: str
    usable: bool
    reason: str = ""
    truncated: bool = False


def truncate(text: str, limit: int) -> tuple[str, bool]:
    """按上限截断并注明；`limit <= 0` 视为不限制。"""
    if limit <= 0 or len(text) <= limit:
        return text, False
    return text[:limit].rstrip() + TRUNCATED_MARK, True


def build_prompt(detail: NoticeDetail, brief: NoticeBrief | None = None, *, body: str) -> str:
    """构造提示词：标题 / 文号 / 日期与单位 / 附件**名称** / 正文（可为截断后的）。"""
    lines = [f"标题：{detail.title}"]
    if detail.doc_no:
        lines.append(f"文号：{detail.doc_no}")
    if brief is not None:
        meta = " ".join(part for part in (brief.published_on, brief.dept) if part)
        if meta:
            lines.append(f"发布：{meta}")
    if detail.attachments:
        lines.append("附件（仅名称）：" + "；".join(item.name for item in detail.attachments))
    lines.append("")
    lines.append("正文：")
    lines.append(body)
    return "\n".join(lines)


def parse_summary(payload: dict[str, Any] | str) -> str | None:
    """从响应里取摘要文本；任何缺字段 / 空内容 / 非法 JSON 一律返回 None（不抛穿）。"""
    data: Any = payload
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str):
        return None
    return content.strip() or None


def compose_fallback(detail: NoticeDetail, body: str, *, truncated: bool) -> str:
    """降级文本：标题 + 文号 + 正文（已截断则注明）。永不为空（标题由解析层保证非空）。"""
    head = [detail.title]
    if detail.doc_no:
        head.append(detail.doc_no)
    text = "\n".join(head) + "\n\n" + body
    return text if text.strip() else detail.title


async def summarize(
    detail: NoticeDetail,
    cfg: Mapping[str, Any],
    brief: NoticeBrief | None = None,
    *,
    client: Client | None = None,
) -> SummaryOutcome:
    """生成摘要；失败/超时/返回不可用一律**降级为正文原文**并带上告警原因。

    降级的存在意义是"模型挂了也不影响收到通知"，但降级**必须留痕**：调用方据此记警告日志，
    否则用户会以为看到的是摘要。
    """
    limit = int(cfg.get("notice_max_chars") or DEFAULT_MAX_CHARS)
    body, truncated = truncate(detail.body_text, limit)
    fallback = compose_fallback(detail, body, truncated=truncated)
    call = client or http_client
    try:
        raw = await call(build_prompt(detail, brief, body=body), cfg)
    except (TimeoutError, asyncio.TimeoutError) as exc:
        return SummaryOutcome(fallback, False, f"模型调用超时（{type(exc).__name__}）", truncated)
    except Exception as exc:  # 网络 / HTTP / JSON 一律降级，但原因必须带出去
        return SummaryOutcome(
            fallback, False, f"模型调用失败（{type(exc).__name__}: {redact(str(exc))}）", truncated
        )
    summary = parse_summary(raw)
    if summary is None:
        return SummaryOutcome(fallback, False, "模型返回内容不可用（缺字段或空内容）", truncated)
    # 摘要同样受长度上限约束（规格：摘要与正文超上限都要截断并注明）。
    # 注意这里必须用 truncate 的**返回值**：只取标志位而返回原文，
    # 就会出现"标志说截断了、消息里却是全文"的静默不一致。
    summary, summary_truncated = truncate(summary, limit)
    return SummaryOutcome(summary, True, "", summary_truncated or truncated)


def chat_completions_url(cfg: Mapping[str, Any]) -> str:
    """请求 URL 由配置决定（纯函数，便于验证"配置真的被用上"）。"""
    base = str(cfg.get("deepseek_base_url") or DEFAULT_BASE_URL).rstrip("/")
    return f"{base}/chat/completions"


async def http_client(prompt: str, cfg: Mapping[str, Any]) -> str:
    """默认客户端：`aiohttp` 直连 OpenAI 兼容接口（不引 openai SDK，design.md D3）。"""
    payload = {
        "model": str(cfg.get("deepseek_model") or DEFAULT_MODEL),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {cfg.get('deepseek_api_key') or ''}",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=LLM_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            chat_completions_url(cfg), json=payload, headers=headers
        ) as resp:
            text = await resp.text()
            if resp.status != 200:
                # 只带状态码与响应体片段（先打码）：密钥在请求头里，正常不会出现在响应体中，
                # 但上游若回显请求头，这里就是唯一的泄漏路径。
                raise RuntimeError(f"HTTP {resp.status}: {redact(text[:200])}")
            return text
