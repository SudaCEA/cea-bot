# -*- coding: utf-8 -*-
"""通知站点抓取层（IO 边界，任务 4.1 / 4.2）。

设计要点：

- 只用**已有的** `aiohttp`（本变更唯一新增依赖是 PDF 渲染，见 design.md D2）；
  `session` 可注入，故单元测试用假件即可离线覆盖成功 / 404 / 超时三条路径。
- 失败一律转成 `FetchError`（带 URL 与原因），**不吞、不返回空**：把"抓取失败"伪装成
  "没有新通知"是本功能最严重的静默失效（规格：列表不可访问 SHALL 记录错误并中止本次运行）。
- 请求纪律（任务 4.2）：列表**只请求首页**（绝不带 `pager.offset`），详情每条只请求一次；
  重试与退避由调用方决定 —— 框架不内置限流/调度原语，抓取频率属业务自管（design.md D5）。
- 响应体以**原始字节**交出，解码归 `parse.decode_html`（D10）：两件事分开，编码回退才好测。
"""
import re
from dataclasses import dataclass
from typing import Mapping

import aiohttp

# `<meta charset="gbk">` 与 `content="…; charset=gb18030"` 两种写法的共同特征：
# `charset` + `=` + 可选的引号 + 编码名。编码名的字符集按 RFC 2978 收紧到字母数字与 `_-.`。
_CHARSET_DECL = re.compile(r"charset\s*=\s*[\"']?([A-Za-z0-9_.\-]+)", re.I)
# 站点根与两条 URL 规则（实测见 tests/fixtures/README.md）。
SITE_ROOT = "http://file.suda.edu.cn"
LIST_PATH = "/defaultroot/SInformAction.do?action=init&type={kind}"
DETAIL_PATH = "/defaultroot/gov/info_view_my.jsp?whir_new_verifyCode=1&editId={edit_id}"

DEFAULT_TIMEOUT = 20.0
# 站点对无 UA 的请求更保守，给一个明确的、可识别的 UA。
USER_AGENT = "Mozilla/5.0 (compatible; CEABot/0.1; notice-digest)"


class FetchError(RuntimeError):
    """抓取失败：网络错误、超时、非 200 响应、空响应体。"""


@dataclass(frozen=True)
class RawPage:
    """一页原始响应：**未解码的字节** + 声明的字符集。

    `declared_charset` 取值顺序（HTTP 优先于页面内声明，与 HTML 规范一致）：
    HTTP 头的 `charset` → 页面 `<meta>` 的声明 → `None`。若两者都缺，`decode_html`
    会自行回退尝试 UTF-8 / GB18030。
    """

    url: str
    raw: bytes
    declared_charset: str | None = None


async def fetch_list(
    session: aiohttp.ClientSession, kind: int, *, timeout: float = DEFAULT_TIMEOUT
) -> RawPage:
    """抓取指定栏目的列表**首页**（任务 4.2：不翻页、不带 offset）。"""
    return await _get(session, SITE_ROOT + LIST_PATH.format(kind=int(kind)), timeout=timeout)


async def fetch_detail(
    session: aiohttp.ClientSession, edit_id: str, *, timeout: float = DEFAULT_TIMEOUT
) -> RawPage:
    """抓取一条通知的详情页。"""
    return await _get(session, SITE_ROOT + DETAIL_PATH.format(edit_id=edit_id), timeout=timeout)


async def _get(session: aiohttp.ClientSession, url: str, *, timeout: float) -> RawPage:
    """一次 GET，返回原始字节；任何异常都收敛成 `FetchError`。"""
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=timeout), headers={"User-Agent": USER_AGENT}
        ) as resp:
            if resp.status != 200:
                raise FetchError(f"抓取失败（HTTP {resp.status}）：{url}")
            raw = await resp.read()
    except FetchError:
        raise
    except aiohttp.ClientError as exc:
        raise FetchError(f"抓取失败（{type(exc).__name__}: {exc}）：{url}") from exc
    except TimeoutError as exc:  # asyncio.TimeoutError 在 3.11+ 即 TimeoutError
        raise FetchError(f"抓取超时（{timeout}s）：{url}") from exc
    except OSError as exc:
        # 必须排在 TimeoutError 之后：`TimeoutError` 是 `OSError` 的子类，
        # 顺序反了会把超时误报成普通 IO 错误。这里兜住其余 IO 层异常
        # （ConnectionResetError、DNS/证书层的 OSError 等），让调用方只需捕 FetchError。
        raise FetchError(f"抓取失败（{type(exc).__name__}: {exc}）：{url}") from exc
    if not raw:
        raise FetchError(f"抓取失败（响应体为空）：{url}")
    return RawPage(
        url=url,
        raw=raw,
        declared_charset=charset_from_headers(getattr(resp, "headers", None))
        or sniff_meta_charset(raw),
    )


def charset_from_headers(headers: Mapping[str, str] | None) -> str | None:
    """从 HTTP 头取 `charset`；取不到返回 None（不抛错 —— 缺声明是常态，回退链能兜住）。"""
    if not headers:
        return None
    content_type = ""
    for key, value in headers.items():
        if str(key).lower() == "content-type":
            content_type = str(value)
            break
    if not content_type:
        return None
    for part in content_type.split(";")[1:]:
        name, _, value = part.partition("=")
        if name.strip().lower() == "charset":
            return value.strip().strip("\"'") or None
    return None


def sniff_meta_charset(raw: bytes, *, window: int = 4096) -> str | None:
    """从响应体前若干字节里嗅 `charset=…`（HTTP 头缺声明时的兜底）。

    只扫前 4KB：规范要求 `<meta>` 出现在文档头部，扫全文档没有意义。
    用 latin-1 解码只为了**找 ASCII 片段**，不参与最终解码，故不会引入编码假设。

    两种写法都要认：`<meta charset="gbk">`（HTML5）与
    `<meta http-equiv="Content-Type" content="…; charset=gb18030">`（旧写法）。
    """
    text = raw[:window].decode("latin-1", errors="ignore")
    match = _CHARSET_DECL.search(text)
    return match.group(1) if match is not None else None
