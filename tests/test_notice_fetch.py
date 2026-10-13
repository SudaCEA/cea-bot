# -*- coding: utf-8 -*-
"""抓取层单测（任务 4.1 / 4.2）：用假 session 离线覆盖成功 / 404 / 超时 / 空体等路径。

真实抓取放在任务组 9 的集成验收里 —— 单元测试不碰网络（仓库约定）。
"""
import asyncio
from typing import Any

import aiohttp
import pytest

from cea_bot.notices import fetch
from cea_bot.notices.fetch import FetchError, charset_from_headers, sniff_meta_charset


class _Resp:
    """假的响应对象：支持 `async with`，并可按需在 read 时抛超时。"""

    def __init__(
        self,
        status: int = 200,
        body: bytes = b"<html>ok</html>",
        headers: dict[str, str] | None = None,
        read_timeout: bool = False,
    ) -> None:
        self.status = status
        self._body = body
        self.headers = headers if headers is not None else {"Content-Type": "text/html;charset=UTF-8"}
        self._read_timeout = read_timeout

    async def read(self) -> bytes:
        if self._read_timeout:
            raise TimeoutError("fake read timeout")
        return self._body

    async def __aenter__(self) -> "_Resp":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _Session:
    """假的 aiohttp session：按序吐出预设响应，并记录每次请求的 URL 与参数。"""

    def __init__(self, *responses: Any) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append((url, kwargs))
        assert self._responses, "假 session 收到了多余的请求"
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ---- 4.1 成功路径 ----

def test_fetch_list_returns_raw_bytes_and_declared_charset():
    session = _Session(_Resp(body=b"<html>\xd0\xa3\xc4\xda</html>"))
    page = asyncio.run(fetch.fetch_list(session, 1))
    assert page.raw == b"<html>\xd0\xa3\xc4\xda</html>"  # 原始字节，未解码
    assert page.declared_charset == "UTF-8"
    assert page.url.endswith("type=1")


def test_fetch_uses_meta_charset_when_header_missing():
    body = '<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312"></head></html>'.encode()
    session = _Session(_Resp(body=body, headers={}))
    page = asyncio.run(fetch.fetch_list(session, 2))
    assert page.declared_charset is not None
    assert page.declared_charset.lower() == "gb2312"


def test_fetch_sets_user_agent_and_timeout():
    session = _Session(_Resp())
    asyncio.run(fetch.fetch_list(session, 1, timeout=7.5))
    url, kwargs = session.calls[0]
    assert kwargs["headers"]["User-Agent"] == fetch.USER_AGENT
    assert kwargs["timeout"].total == 7.5
    assert "offset" not in url


# ---- 4.1 失败路径：都必须抛 FetchError，而不是返回空 ----

def test_http_error_raises_fetch_error():
    session = _Session(_Resp(status=404))
    with pytest.raises(FetchError) as exc:
        asyncio.run(fetch.fetch_list(session, 1))
    assert "404" in str(exc.value)
    assert "type=1" in str(exc.value)


def test_timeout_from_read_raises_fetch_error():
    session = _Session(_Resp(read_timeout=True))
    with pytest.raises(FetchError) as exc:
        asyncio.run(fetch.fetch_list(session, 1))
    assert "超时" in str(exc.value)


def test_timeout_from_get_raises_fetch_error():
    session = _Session(TimeoutError("boom"))
    with pytest.raises(FetchError) as exc:
        asyncio.run(fetch.fetch_list(session, 1))
    # `TimeoutError` 是 `OSError` 的子类：OSError 兜底分支必须排在它后面，
    # 否则超时会被误报成普通 IO 错误、丢掉"超时"这一可诊断信息。
    assert "超时" in str(exc.value)


def test_oserror_raises_fetch_error():
    """IO 层其它异常（非 aiohttp、非超时）也要收敛成 FetchError，调用方只需捕一种。"""
    session = _Session(OSError("dns boom"))
    with pytest.raises(FetchError) as exc:
        asyncio.run(fetch.fetch_list(session, 1))
    assert "OSError" in str(exc.value)


def test_client_error_raises_fetch_error():
    session = _Session(aiohttp.ClientConnectionError("conn reset"))
    with pytest.raises(FetchError) as exc:
        asyncio.run(fetch.fetch_detail(session, "40773772"))
    assert "ClientConnectionError" in str(exc.value)


def test_empty_body_raises_fetch_error():
    session = _Session(_Resp(body=b""))
    with pytest.raises(FetchError) as exc:
        asyncio.run(fetch.fetch_detail(session, "40773772"))
    assert "响应体为空" in str(exc.value)


# ---- 4.2 请求纪律 ----

def test_list_request_has_no_pager_offset():
    session = _Session(_Resp())
    asyncio.run(fetch.fetch_list(session, 3))
    url, _ = session.calls[0]
    assert "pager.offset" not in url
    assert "type=3" in url
    assert len(session.calls) == 1  # 只请求首页


def test_detail_requests_one_call_per_id():
    session = _Session(_Resp(), _Resp())
    asyncio.run(fetch.fetch_detail(session, "1"))
    asyncio.run(fetch.fetch_detail(session, "2"))
    urls = [url for url, _ in session.calls]
    assert len(urls) == 2
    assert "editId=1" in urls[0] and "editId=2" in urls[1]


# ---- 头部 / meta 解析的小函数 ----

@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("text/html;charset=UTF-8", "UTF-8"),
        ("text/html; charset=gb2312", "gb2312"),
        ('text/html; charset="utf-8"', "utf-8"),
        ("text/html", None),
        (None, None),
    ],
)
def test_charset_from_headers(header, expected):
    headers = {} if header is None else {"Content-Type": header}
    assert charset_from_headers(headers) == expected


def test_sniff_meta_charset():
    assert sniff_meta_charset(b'<meta charset="gbk">') == "gbk"
    assert sniff_meta_charset(b"<meta http-equiv='Content-Type' content='text/html; charset=gb18030'>") == "gb18030"
    assert sniff_meta_charset(b"<html>no meta here</html>") is None
