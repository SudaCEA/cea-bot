# -*- coding: utf-8 -*-
"""摘要层单测（任务 6.1 / 6.2）：提示词只带正文与附件名、坏响应不抛穿、失败必降级且不空、截断留痕。

模型调用一律用假客户端注入，**不联网**；真实调用属于任务组 9 的集成验收。
"""
import asyncio
import json

import pytest

from cea_bot.notices import summarize
from cea_bot.notices.models import Attachment, NoticeBrief, NoticeDetail
from cea_bot.notices.summarize import SummaryOutcome, parse_summary, truncate

SECRET = "sk-not-a-real-secret-value"


def _detail(body: str = "各学院（部）：现将有关事项通知如下。特此通知。", *, attachments: tuple[Attachment, ...] = ()) -> NoticeDetail:
    return NoticeDetail(
        kind=1,
        edit_id="40773772",
        url="http://file.suda.edu.cn/x",
        title="关于开展学生标兵选拔工作的通知",
        doc_no="学字﹝2026﹞63号",
        body_text=body,
        attachments=attachments,
    )


def _cfg(**overrides: object) -> dict[str, object]:
    cfg: dict[str, object] = {
        "deepseek_api_key": SECRET,
        "deepseek_model": "deepseek-flash",
        "notice_max_chars": 1500,
    }
    cfg.update(overrides)
    return cfg


def _client_returning(payload: object, captured: list[str] | None = None):
    async def call(prompt: str, cfg: object) -> str:
        if captured is not None:
            captured.append(prompt)
        return payload if isinstance(payload, str) else json.dumps(payload)

    return call


def _client_raising(exc: BaseException):
    async def call(prompt: str, cfg: object) -> str:
        raise exc

    return call


# ---- 6.1 提示词与响应解析 ----

def test_build_prompt_contains_body_and_attachment_names_only():
    detail = _detail(
        attachments=(Attachment(name="附件1：名单汇总表.xls", save_name="2026101313375370673178543.xls"),)
    )
    prompt = summarize.build_prompt(detail, NoticeBrief(kind=1, edit_id="1", published_on="2026-10-13", title="t", dept="党委学生工作部"), body=detail.body_text)
    assert detail.title in prompt
    assert detail.doc_no in prompt
    assert "2026-10-13" in prompt and "党委学生工作部" in prompt
    assert "附件1：名单汇总表.xls" in prompt  # 附件**名称**要给模型
    assert "2026101313375370673178543.xls" not in prompt  # 保存名不外发
    assert detail.body_text in prompt


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        "{}",
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": 42}}]},
        [1, 2, 3],
    ],
)
def test_parse_summary_rejects_unusable_payloads(payload):
    assert parse_summary(payload) is None


def test_parse_summary_extracts_and_strips_content():
    assert parse_summary({"choices": [{"message": {"content": "  要点：\n- 甲  "}}]}) == "要点：\n- 甲"
    assert parse_summary(json.dumps({"choices": [{"message": {"content": "摘要"}}]})) == "摘要"


def test_summarize_success_returns_summary():
    captured: list[str] = []
    outcome = asyncio.run(
        summarize.summarize(_detail(), _cfg(), client=_client_returning({"choices": [{"message": {"content": "要点：- 甲"}}]}, captured))
    )
    assert outcome.usable is True
    assert outcome.text == "要点：- 甲"
    assert outcome.reason == ""
    # 合规：密钥只用于请求头，不得出现在提示词里
    assert SECRET not in captured[0]


# ---- 6.2 降级与截断 ----

def test_timeout_degrades_to_body_with_reason():
    outcome = asyncio.run(summarize.summarize(_detail(), _cfg(), client=_client_raising(asyncio.TimeoutError())))
    assert outcome.usable is False
    assert "超时" in outcome.reason
    assert outcome.text.strip()  # 绝不发空
    assert "特此通知" in outcome.text


def test_client_error_degrades_to_body_with_reason():
    outcome = asyncio.run(summarize.summarize(_detail(), _cfg(), client=_client_raising(RuntimeError("HTTP 500: oops"))))
    assert outcome.usable is False
    assert "模型调用失败" in outcome.reason
    assert "HTTP 500" in outcome.reason
    assert SECRET not in outcome.reason  # 密钥绝不出现在告警里


def test_unusable_payload_degrades():
    outcome = asyncio.run(summarize.summarize(_detail(), _cfg(), client=_client_returning("{}")))
    assert outcome.usable is False
    assert "不可用" in outcome.reason


def test_truncate_marks_and_limits():
    text, truncated = truncate("甲" * 2000, 100)
    assert truncated is True
    assert text.startswith("甲" * 100)
    assert summarize.TRUNCATED_MARK.strip() in text
    assert truncate("短正文", 100) == ("短正文", False)
    assert truncate("短正文", 0) == ("短正文", False)  # limit<=0 = 不限制


def test_long_body_is_truncated_and_marked():
    detail = _detail(body="乙" * 5000)
    outcome = asyncio.run(summarize.summarize(detail, _cfg(notice_max_chars=200), client=_client_raising(RuntimeError("x"))))
    assert outcome.truncated is True
    assert "已截断" in outcome.text
    assert len(outcome.text) < 1000  # 确实被截断，而不是原样带出 5000 字


def test_long_summary_is_truncated_and_marked():
    """摘要**成功**路径同样受长度上限约束。

    回归一个被独立审查抓到的缺陷：原先写成 `_, summary_truncated = truncate(summary, limit)`
    并把**未截断的原文**返回，导致"标志说截断了、消息里却是全文"的静默不一致。
    """
    long_summary = "要点：\n" + "- 甲" * 3000
    outcome = asyncio.run(
        summarize.summarize(
            _detail(),
            _cfg(notice_max_chars=200),
            client=_client_returning({"choices": [{"message": {"content": long_summary}}]}),
        )
    )
    assert outcome.usable is True
    assert outcome.truncated is True
    assert "已截断" in outcome.text
    assert len(outcome.text) <= 200 + len(summarize.TRUNCATED_MARK) + 2
    assert len(outcome.text) < 1000  # 未被整段带出


def test_empty_body_still_produces_non_empty_text():
    outcome = asyncio.run(summarize.summarize(_detail(body=""), _cfg(), client=_client_raising(RuntimeError("x"))))
    assert outcome.usable is False
    assert outcome.text.strip() == "关于开展学生标兵选拔工作的通知\n学字﹝2026﹞63号".strip() or outcome.text.strip()


def test_summary_outcome_is_frozen():
    outcome = SummaryOutcome("t", True)
    with pytest.raises(Exception):
        outcome.text = "改不动"  # type: ignore[misc]


def test_redact_masks_keylike_text():
    """密钥打码：上游若回显请求头，日志与推送文本都不能带出原始密钥。"""
    assert summarize.redact("HTTP 401: {'key': 'sk-abcdef123456'}") == "HTTP 401: {'key': 'sk-***'}"
    assert summarize.redact("没有密钥") == "没有密钥"


def test_degradation_reason_redacts_keylike_text():
    outcome = asyncio.run(
        summarize.summarize(_detail(), _cfg(), client=_client_raising(RuntimeError("bad sk-SECRET123456")))
    )
    assert outcome.usable is False
    assert "sk-SECRET123456" not in outcome.reason
    assert "sk-***" in outcome.reason


def test_chat_completions_url_uses_configured_base():
    assert summarize.chat_completions_url({}) == "https://api.deepseek.com/chat/completions"
    assert (
        summarize.chat_completions_url({"deepseek_base_url": "http://127.0.0.1:9/"})
        == "http://127.0.0.1:9/chat/completions"
    )
