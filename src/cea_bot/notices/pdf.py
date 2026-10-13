# -*- coding: utf-8 -*-
"""PDF 渲染层（任务 7.1 / 7.2）：把详情页正文用无头浏览器渲染成 PDF。

为什么用浏览器引擎（design.md D2）：正文是 Word 粘贴的富文本（`font-family: 仿宋`、
`font-size: 16pt`、表格、未闭合标签），只有真浏览器引擎能保真；纯 Python 生成器会丢掉排版。

两个刻意的实现选择：

- **用 async API**：Playwright 的 sync API 在事件循环里会直接抛错，而本功能的调用方
  （定时任务）就在事件循环里，故必须 `async_playwright`。渲染器做成可注入的形参，
  单元测试用假渲染器即可离线验证文件名规则、目录创建与失败处理。
- **注入 `<base href>`**：详情页的 CSS/图片用相对 URL，脱离站点上下文会丢样式；
  注入 `<base>` 后相对资源能正常解析（站点本来就在线，抓取与渲染是同一时刻的事）。
"""
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from cea_bot.notices.fetch import SITE_ROOT

if TYPE_CHECKING:  # 只为类型标注：playwright 在运行期是延迟导入的（保持 import 廉价）
    from playwright.async_api import PdfMargins

log = logging.getLogger("cea")

# A4 页边距：中文公文的正文较密，左右留 16mm、上下 18mm。
A4_MARGIN: "PdfMargins" = {
    "top": "18mm",
    "bottom": "18mm",
    "left": "16mm",
    "right": "16mm",
}

# 文件名里只保留可移植字符：日期与标识正常都是 `YYYY-MM-DD` 与数字，其余一律剔掉。
_UNSAFE = re.compile(r"[^0-9A-Za-z._-]+")

Renderer = Callable[[str, Path], Awaitable[None]]


class PdfRenderError(RuntimeError):
    """PDF 渲染失败：浏览器不可用、渲染报错、产物为空或不是 PDF。"""


@dataclass(frozen=True)
class PdfResult:
    """渲染成功的产物：路径与字节数（字节数用于"非空"断言与日志）。"""

    path: Path
    bytes_len: int


def pdf_file_name(published_on: str, edit_id: str) -> str:
    """产物文件名规则：`<日期>-<editId>.pdf`；异常字符剔除，缺失时用中文占位。"""
    date = _UNSAFE.sub("", published_on) or "未知日期"
    identifier = _UNSAFE.sub("", edit_id) or "未知标识"
    return f"{date}-{identifier}.pdf"


def with_base_href(html: str, site_root: str = SITE_ROOT) -> str:
    """在文档头部注入 `<base href="<站点根>/">`，让相对 URL 的 CSS/图片能解析。"""
    tag = f'<base href="{site_root.rstrip("/")}/">'
    head = re.search(r"<head\b[^>]*>", html, re.I)
    if head is not None:
        return html[: head.end()] + tag + html[head.end():]
    return tag + html


async def render_pdf(
    html: str,
    out_path: Path,
    *,
    renderer: Renderer | None = None,
    site_root: str = SITE_ROOT,
) -> PdfResult:
    """渲染 PDF 并校验产物；渲染器可注入（单测用假件）。

    校验三件事，缺一不可：文件存在、**非空**、文件头是 `%PDF-`。
    只检查"renderer 没抛错"是不够的——静默产出 0 字节文件正是本仓库点名的失败形态。
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    render = renderer or playwright_renderer
    await render(with_base_href(html, site_root), out_path)
    if not out_path.exists():
        raise PdfRenderError(f"渲染结束但产物不存在：{out_path}")
    size = out_path.stat().st_size
    if size <= 0:
        raise PdfRenderError(f"渲染产物为空文件：{out_path}")
    with out_path.open("rb") as handle:
        header = handle.read(5)
    if header != b"%PDF-":
        raise PdfRenderError(f"渲染产物不是 PDF（文件头为 {header!r}）：{out_path}")
    return PdfResult(path=out_path, bytes_len=size)


async def playwright_renderer(html: str, out_path: Path) -> None:
    """默认渲染器：Playwright 无头 Chromium，A4 + 页边距 + 打印背景。"""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch()
        except Exception as exc:
            raise PdfRenderError(
                f"Playwright 浏览器启动失败（{type(exc).__name__}: {exc}）。"
                "请先执行：.venv\\Scripts\\playwright install chromium"
            ) from exc
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="load")
            await page.pdf(
                path=str(out_path), format="A4", margin=A4_MARGIN, print_background=True
            )
        except PdfRenderError:
            raise
        except Exception as exc:
            # 渲染阶段的任何失败都收敛成 PdfRenderError：磁盘满、Target closed、渲染超时、
            # 路径不可写…… 若放任它们以原类型冒出，调用方只捕 PdfRenderError 的降级分支就会漏掉，
            # 结果是"PDF 失败 → 整轮中止 → 连文本都没发"。
            raise PdfRenderError(
                f"通知 PDF 渲染失败（{type(exc).__name__}: {exc}）：{out_path}"
            ) from exc
        finally:
            await browser.close()


async def render_env_error() -> str | None:
    """探测渲染环境：就绪返回 None，否则返回**可执行的**提示（不裸 traceback，任务 7.2）。"""
    install_hint = (
        "安装命令：\n"
        "  .venv\\Scripts\\playwright install chromium\n"
        "（Playwright 的浏览器内核不入版本库；若下载慢，重试或先设好网络代理。）"
    )
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        return f"未安装 playwright（{exc}）。安装命令：\n  .venv\\Scripts\\python -m pip install -e \".[dev]\" --index-url https://pypi.org/simple\n{install_hint}"
    try:
        async with async_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
    except Exception as exc:
        return f"无法初始化 Playwright（{type(exc).__name__}: {exc}）。{install_hint}"
    if not executable.exists():
        return f"Playwright 浏览器内核不存在：{executable}\n{install_hint}"
    return None
