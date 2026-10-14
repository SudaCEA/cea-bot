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

# 文件名非法字符**黑名单**（design D2）：`< > : " / \ | ? *` 在 Windows 上非法、`/` 与 `\`
# 还是路径分隔符，控制字符（`\x00-\x1f`）一并剔除。
# 刻意**不用** ASCII 白名单：旧的 `[^0-9A-Za-z._-]+` 会把中文标题整段删空，
# 那样"文件名带标题"就无从谈起。
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')

# 发布日期只认 `YYYY-MM-DD`（解析层的 `_DATE` 也只产出这一种形式）；非此形式一律用占位（D3）。
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

# 标题长度上限（design D3）：Windows MAX_PATH 为 260，扣掉绝对目录、`YYMMDD-`(7) 与 `.pdf`(4)
# 仍有充足余量；校园通知标题通常远短于此。
_TITLE_MAX = 80

# 净化后标题为空、以及日期缺失/非 ISO 时的固定占位（design D3）。
# 留空会生成 `261001-.pdf` / `-标题.pdf` 这类难辨认的名字，故一律用占位而非空串。
_DATE_FALLBACK = "000000"
_TITLE_FALLBACK = "未命名"

Renderer = Callable[[str, Path], Awaitable[None]]


class PdfRenderError(RuntimeError):
    """PDF 渲染失败：浏览器不可用、渲染报错、产物为空或不是 PDF。"""


@dataclass(frozen=True)
class PdfResult:
    """渲染成功的产物：路径与字节数（字节数用于"非空"断言与日志）。"""

    path: Path
    bytes_len: int


def _date_stamp(published_on: str) -> str:
    """把 ISO 发布日期压成 `YYMMDD`（design D1）；缺失或非 `YYYY-MM-DD` 形式时用 `000000`（D3）。

    只校验**形式**，不校验日期是否真实存在（`2026-13-45` 形式合法即照转）：解析层只会产出
    `_DATE` 命中的串，这里再叠一层日历校验只会多出一条无人依赖的失败分支。
    """
    match = _ISO_DATE.match(published_on.strip())
    if match is None:
        return _DATE_FALLBACK
    return f"{match.group(1)[2:]}{match.group(2)}{match.group(3)}"


def _title_part(title: str) -> str:
    """标题净化：剔非法字符 → 截断到 80 字符 → 去掉**结尾**的点与空格（design D2 / D3）。

    顺序是有意的：截断本身可能把结尾变成一个空格或点，先截断再收尾才不会漏。
    结尾的点与空格会被 Windows 静默丢弃，那会让"库里记下的名字"与"磁盘上的名字"不一致 ——
    本仓库最忌讳的静默失效，故在命名阶段就掐掉。
    """
    return _UNSAFE.sub("", title)[:_TITLE_MAX].rstrip(". ") or _TITLE_FALLBACK


def pdf_file_name(published_on: str, title: str) -> str:
    """产物文件名规则：`YYMMDD-<标题>.pdf`（design D1）。

    中文标题完整保留；非法字符、结尾的点与空格被剔除；标题截断到 80 字符；净化后为空用
    `未命名`；日期缺失或非 `YYYY-MM-DD` 用 `000000`。
    **纯函数**：同输入必得同输出，不碰文件系统 —— 同名覆盖的告警在调用点做（design D4）。
    """
    return f"{_date_stamp(published_on)}-{_title_part(title)}.pdf"


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
