# -*- coding: utf-8 -*-
"""NapCat 前端单测：事件归一化 / 段构造 / 装配选择（均纯函数，不依赖真实 WS）。

运行：.venv\\Scripts\\python -m pytest tests -q
"""
import asyncio
import base64 as b64mod

import cea.adapters.napcat as napcat_module
import pytest

from cea.adapters.napcat import NapCatAdapter, build_segments, normalize_event
from cea.bot import FileTooLargeError
from cea.types import Chat, FileData, FileRef, OutMessage
from main import build_adapter, check_config


def _group_event(segments: list[dict], **overrides: object) -> dict:
    event = {
        "time": 1,
        "self_id": 10001,
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "message_id": 42,
        "user_id": 20002,
        "group_id": 30003,
        "message": segments,
        "raw_message": "",
        "sender": {"user_id": 20002, "nickname": "昵称", "card": "群名片", "role": "member"},
    }
    event.update(overrides)
    return event


# ---- 事件归一化 ----

def test_group_text_normalized():
    event = _group_event(
        [{"type": "at", "data": {"qq": 10001}}, {"type": "text", "data": {"text": " ping"}}]
    )
    msg = normalize_event(event)
    assert msg is not None
    assert msg.id == "42"
    assert msg.chat.platform == "napcat" and msg.chat.kind == "group" and msg.chat.id == "30003"
    assert msg.sender.id == "20002" and msg.sender.name == "群名片"  # 群名片优先于昵称
    assert msg.text == "ping"  # 拼接并去除首尾空白
    assert msg.at_me is True and msg.reply_to is None


def test_at_me_only_for_self():
    event = _group_event([{"type": "at", "data": {"qq": 99999}}, {"type": "text", "data": {"text": "hi"}}])
    msg = normalize_event(event)
    assert msg is not None and msg.at_me is False


def test_image_url_collected_with_path_fallback():
    event = _group_event(
        [
            {"type": "image", "data": {"url": "https://example.com/a.png", "file_id": "f1"}},
            {"type": "image", "data": {"path": "C:/cache/b.png", "file_id": "f2"}},
            {"type": "image", "data": {"file_id": "f3"}},  # 无 url 无 path → 跳过
        ]
    )
    msg = normalize_event(event)
    assert msg is not None
    assert msg.images == ("https://example.com/a.png", "C:/cache/b.png")


def test_reply_to_extracted():
    event = _group_event(
        [
            {"type": "reply", "data": {"id": 7}},
            {"type": "at", "data": {"qq": 10001}},
            {"type": "text", "data": {"text": "看看这条"}},
        ]
    )
    msg = normalize_event(event)
    assert msg is not None and msg.reply_to == "7" and msg.at_me is True


def test_heartbeat_ignored():
    assert normalize_event(
        {"post_type": "meta_event", "meta_event_type": "heartbeat",
         "self_id": 10001, "time": 1, "status": {}, "interval": 30000}
    ) is None


def test_notice_event_ignored():
    event = {"post_type": "notice", "notice_type": "group_increase", "self_id": 10001,
             "group_id": 30003, "user_id": 20002, "time": 1}
    assert normalize_event(event) is None


def test_unmodeled_segment_kept_in_raw():
    event = _group_event(
        [
            {"type": "text", "data": {"text": "收到文件"}},
            {"type": "file", "data": {"file_id": "F1", "name": "doc.pdf", "size": 1024}},
        ]
    )
    msg = normalize_event(event)
    assert msg is not None and msg.text == "收到文件"
    assert msg.raw is event and msg.raw["message"][1]["data"]["file_id"] == "F1"


def test_private_maps_c2c():
    event = {
        "time": 1, "self_id": 10001, "post_type": "message", "message_type": "private",
        "sub_type": "friend", "message_id": 9, "user_id": 20002,
        "message": [{"type": "text", "data": {"text": "在吗"}}],
        "raw_message": "在吗", "sender": {"user_id": 20002, "nickname": "昵称"},
    }
    msg = normalize_event(event)
    assert msg is not None
    assert msg.chat.kind == "c2c" and msg.chat.id == "20002"
    assert msg.sender.name == "昵称" and msg.text == "在吗" and msg.at_me is False


# ---- 段构造 ----

def test_segments_text_only():
    msg = OutMessage(chat=Chat("napcat", "30003", "group"), text="hi")
    assert build_segments(msg) == [{"type": "text", "data": {"text": "hi"}}]


def test_segments_reply_first_then_at_then_text():
    msg = OutMessage(chat=Chat("napcat", "30003", "group"), text="hi", reply_to="7", at=("u1", "u2"))
    assert build_segments(msg) == [
        {"type": "reply", "data": {"id": "7"}},  # 引用段置首
        {"type": "at", "data": {"qq": "u1"}},
        {"type": "at", "data": {"qq": "u2"}},
        {"type": "text", "data": {"text": "hi"}},
    ]


def test_segments_image_sources_verbatim():
    """URL / 本地路径 / base64 三态源原样进入 file 字段（NapCat 负责读取）。"""
    for source in ("https://example.com/a.png", "D:/shots/a.png", "base64://AAAA"):
        msg = OutMessage(chat=Chat("napcat", "30003", "group"), image=source)
        assert build_segments(msg) == [{"type": "image", "data": {"file": source}}]


# ---- 装配选择 ----

def test_check_config_default_qqofficial():
    cfg = check_config({"appid": "a", "secret": "s"})
    assert cfg["adapter"] == "qqofficial"


def test_check_config_napcat_branch():
    cfg = check_config({"adapter": "napcat", "ws_url": "ws://127.0.0.1:3001", "token": "t"})
    assert cfg["adapter"] == "napcat" and cfg["ws_url"] == "ws://127.0.0.1:3001" and cfg["token"] == "t"


def test_check_config_napcat_requires_ws_url():
    with pytest.raises(SystemExit):
        check_config({"adapter": "napcat"})


def test_check_config_unknown_adapter_fails_fast():
    with pytest.raises(SystemExit):
        check_config({"adapter": "irc"})


def test_build_adapter_napcat():
    adapter = build_adapter({"adapter": "napcat", "ws_url": "ws://x", "token": "t"})
    assert isinstance(adapter, NapCatAdapter)
    assert adapter._ws_url == "ws://x" and adapter._token == "t"


def test_build_adapter_qqofficial():
    from cea.adapters.qqbotpy import QQBotpyAdapter

    assert isinstance(build_adapter({"adapter": "qqofficial", "appid": "a", "secret": "s"}), QQBotpyAdapter)


def test_build_adapter_default_is_qqofficial():
    cfg = check_config({"appid": "a", "secret": "s"})
    from cea.adapters.qqbotpy import QQBotpyAdapter

    assert isinstance(build_adapter(cfg), QQBotpyAdapter)


# ---- 文件：入站收集 / 出站段构造 ----

def test_file_segment_collected():
    event = _group_event(
        [
            {"type": "text", "data": {"text": "拿文件"}},
            {
                "type": "file",
                "data": {"file_id": "F1", "name": "doc.pdf", "file_size": "2048",
                          "url": "https://e/doc.pdf"},
            },
        ]
    )
    msg = normalize_event(event)
    assert msg is not None
    assert msg.files == (FileRef(file_id="F1", name="doc.pdf", size=2048,
                                  url="https://e/doc.pdf"),)


def test_file_segment_without_file_id_skipped():
    event = _group_event([{"type": "file", "data": {"name": "x"}}])
    msg = normalize_event(event)
    assert msg is not None and msg.files == ()  # 无 file_id 不收集，raw 兜底


def test_segments_file_with_and_without_name():
    msg = OutMessage(chat=Chat("napcat", "30003", "group"),
                     file="https://e/a.pdf", file_name="a.pdf")
    assert build_segments(msg) == [
        {"type": "file", "data": {"file": "https://e/a.pdf", "name": "a.pdf"}}
    ]
    msg2 = OutMessage(chat=Chat("napcat", "30003", "group"), file="base64://AAA")
    assert build_segments(msg2) == [{"type": "file", "data": {"file": "base64://AAA"}}]


# ---- 文件：fetch_file 下载与 upload_group_file 上传 ----

class _FakeOBClient:
    def __init__(self, response):
        self._response = response
        self.calls: list[tuple] = []

    async def call(self, action, params, timeout=30.0):
        self.calls.append((action, params))
        return self._response


def _adapter_with_client(response):
    adapter = NapCatAdapter("ws://x")
    adapter._connected.set()
    adapter._client = _FakeOBClient(response)
    return adapter


def test_fetch_file_base64_hit():
    adapter = _adapter_with_client(
        {"retcode": 0,
         "data": {"base64": b64mod.b64encode(b"hello").decode(), "file_name": "a.txt"}}
    )
    data = asyncio.run(adapter.fetch_file("F1"))
    assert data.data == b"hello" and data.name == "a.txt" and data.size == 5


def test_fetch_file_url_fallback():
    class _Content:
        def __init__(self, chunks):
            self._chunks = chunks

        async def iter_chunked(self, size):
            for chunk in self._chunks:
                yield chunk

    class _Resp:
        status = 200

        def __init__(self, chunks):
            self.content = _Content(chunks)

    class _GetCtx:
        def __init__(self, resp):
            self._resp = resp

        async def __aenter__(self):
            return self._resp

        async def __aexit__(self, *exc):
            return False

    class _Session:
        def __init__(self, chunks):
            self._chunks = chunks
            self.requested: list[str] = []

        def get(self, url):
            self.requested.append(url)
            return _GetCtx(_Resp(self._chunks))

    session = _Session([b"ab", b"cd"])
    adapter = _adapter_with_client(
        {"retcode": 0, "data": {"url": "http://e/f", "file_name": "f.bin"}}
    )
    adapter._session = session
    data = asyncio.run(adapter.fetch_file("F1"))
    assert data.data == b"abcd" and data.size == 4
    assert session.requested == ["http://e/f"]


def test_fetch_file_missing_fields_raises():
    adapter = _adapter_with_client({"retcode": 0, "data": {}})
    with pytest.raises(RuntimeError):
        asyncio.run(adapter.fetch_file("F1"))


def test_fetch_file_platform_error():
    adapter = _adapter_with_client({"retcode": 1200, "wording": "file gone"})
    with pytest.raises(RuntimeError):
        asyncio.run(adapter.fetch_file("F1"))


def test_fetch_file_over_limit(monkeypatch):
    monkeypatch.setattr(napcat_module, "MAX_FILE_BYTES", 8)
    adapter = _adapter_with_client(
        {"retcode": 0, "data": {"base64": b64mod.b64encode(b"123456789").decode()}}
    )
    with pytest.raises(FileTooLargeError):
        asyncio.run(adapter.fetch_file("F1"))


def test_fetch_file_local_path_url_raises():
    """分端部署已知限制：get_file 仅返回 NapCat 本地路径 → 明确报错，不误当 URL 请求。"""
    adapter = _adapter_with_client(
        {"retcode": 0, "data": {"url": "/root/.config/QQ/NapCat/temp/a.md", "file_name": "a.md"}}
    )
    with pytest.raises(RuntimeError, match="本地路径"):
        asyncio.run(adapter.fetch_file("F1"))


def test_official_adapter_drops_file():
    from cea.adapters.qqbotpy import QQBotpyAdapter

    async def scenario():
        adapter = QQBotpyAdapter("appid", "secret")
        adapter._client = None  # 若 file 分支失守触达平台调用会 AttributeError
        await adapter.send(OutMessage(chat=Chat("qq", "30003", "group"),
                                      file="http://e/a.pdf", file_name="a.pdf"))

    asyncio.run(scenario())  # 不抛异常 = 丢弃路径生效
