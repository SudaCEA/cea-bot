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
from cea_bot.platform_hints import resolve_is_windows, venv_python

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
# 还是路径分隔符；**控制字符**一并剔除，且三段都算 —— C0（`\x00-\x1f`）、DEL（`\x7f`）、
# C1（`\x80-\x9f`）。规格只说"控制字符"没划范围，只剔 C0 会把 DEL 与 C1 留在文件名里：
# Windows 允许它们，于是归档名里混进不可见垃圾字符（`261001-a\x85b.pdf` 看着像 `ab`，
# 复制/检索/比对都对不上），正是本仓库最忌讳的静默失效。
# 刻意**不用** ASCII 白名单：旧的 `[^0-9A-Za-z._-]+` 会把中文标题整段删空，
# 那样"文件名带标题"就无从谈起。
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f-\x9f]+')

# 发布日期只认 `YYYY-MM-DD`（解析层的 `_DATE` 也只产出这一种形式）；非此形式一律用占位（D3）。
# 刻意写 `[0-9]` 而不是 `\d`：Python 的 `\d` 对 str 匹配的是 **Unicode Nd**，全角 `２０２６`、
# 阿拉伯-印度 `٢٠٢٦`、天城文 `२०२६` 都会被当成合法日期，产出 `２６１００１-T.pdf` 这类
# **同形异义**文件名 —— 看着像日期，排序与检索却全部错位，比落 `000000` 更糟。
# 它们本就不是 `YYYY-MM-DD` 形式，规格要的答案就是 `000000`；日期戳是给排序/检索用的 ASCII 戳。
_ISO_DATE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")

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
    收尾的理由是**名字整洁**（避免生成 `261014-通知标题. .pdf` 这类难辨认的名字），
    **不是**"防 Windows 裁名"：Windows 只裁**路径组件末端**的点与空格，而本命名恒以 `.pdf`
    结尾，故盘上名字与库里名字不可能因此不一致（实测 `尾点.pdf.` 会被裁成 `尾点.pdf`，
    但 `扩展名前. .pdf` 原样保留）。规则本身无害，留着。
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


def browser_launch_hint(exc: Exception, is_windows: bool) -> str:
    """浏览器启动失败时的**可执行**提示（纯函数，平台由参数注入）。

    只改提示文案，不改任何逻辑：启动失败最常见、也最可修的成因就是浏览器内核没装，
    故指向 `install chromium`。命令统一用解释器模块形态，见 `playwright_install_hint`。
    """
    return (
        f"Playwright 浏览器启动失败（{type(exc).__name__}: {exc}）。"
        f"请先执行：{venv_python(is_windows)} -m playwright install chromium"
    )


async def playwright_renderer(html: str, out_path: Path, *, is_windows: bool | None = None) -> None:
    """默认渲染器：Playwright 无头 Chromium，A4 + 页边距 + 打印背景。

    `is_windows` 只影响失败提示里的解释器路径（缺省 `None` = 读当前平台）；见
    `cea_bot.platform_hints`。多出的是一个**带缺省的仅关键字**参数，故仍然满足
    `Renderer` 契约（`Callable[[str, Path], Awaitable[None]]`）。

    **导入失败与启动失败收敛到同一个 `PdfRenderError`（design D1）**：`playwright` 包不可用
    （部分安装、`--no-deps`、venv 被破坏）时抛的是 `ModuleNotFoundError`，而调用方
    `handlers/notice.py` 只捕 `(PdfRenderError, OSError)`；放任它以原类型冒出，规格要求的
    "PDF 生成失败 → 只发文本 + 记告警"就会变成"整轮中止、连文本都没发"。

    导入那一行的捕获面是 **`except Exception`**（design D1 审查后修订）：只捕 `ImportError` 时，
    "包在、只是它的传递依赖（`pyee` / `greenlet`）缺失"这类导入期抛出的**其它**异常会冒穿同一条
    降级路径 —— 而导入语句不存在"磁盘满 vs 依赖不可用"的歧义，导入期任何失败**就是**
    "渲染依赖不可用"，按规格就该降级。
    收敛面仍然**只到导入这一行**：下面那两个 `except Exception` 是为 `chromium.launch()` 与
    渲染阶段写的，本次未动。
    `asyncio.CancelledError` 继承 `BaseException`，故**三处捕获面都捕不到它**，它**继续冒穿**
    （框架的优雅退出依赖它，见 `cea/adapters/base.py` 的契约）；任何一处都不得把捕获面放宽到
    `BaseException`（那样取消会被吞成 `PdfRenderError`，表现为"Ctrl+C 后停不下来"）。
    """
    # 平台事实只解析一次，导入与启动两条提示共用（避免同一个值读两遍、写两处）。
    windows = resolve_is_windows(is_windows)
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        # 捕获面是 `Exception` 而非 `ImportError`：理由见 docstring（D1 审查后修订）。
        raise PdfRenderError(playwright_missing_hint(exc, windows)) from exc

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch()
        except Exception as exc:
            raise PdfRenderError(browser_launch_hint(exc, windows)) from exc
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


def playwright_install_hint(is_windows: bool) -> str:
    """Playwright 浏览器内核的安装提示（**纯函数**，平台由参数注入）。

    统一写成 `<venv-python> -m playwright install chromium`：playwright 包内带
    `__main__.py`（实测 `python -m playwright --version` → `Version 1.63.0`），故 Windows 的
    `Scripts\\playwright.exe` 与 Linux 的 `bin/playwright` **共用同一条命令**，
    不必分平台分叉 —— 需要分平台的只有解释器路径本身（见 `cea_bot.platform_hints`）。
    """
    return (
        "安装命令：\n"
        f"  {venv_python(is_windows)} -m playwright install chromium\n"
        "（Playwright 的浏览器内核不入版本库；若下载慢，重试或先设好网络代理。）"
    )


def playwright_missing_hint(exc: Exception, is_windows: bool) -> str:
    """**无法导入** playwright 时的**可执行**提示（纯函数，平台由参数注入）。

    首行刻意写"无法导入"而不是"未安装"：`playwright` 包本身在、只是它的传递依赖
    （`pyee` / `greenlet`）缺失时，异常同样出在**导入期** —— 旧文案在这种现场会产出
    "未安装 playwright（No module named 'pyee'）"，**标签与括号里的真实异常自相矛盾**，
    把排查引向"包没装"（实际是 venv 被 `--no-deps`、半途失败的安装或部分卸载破坏）。
    括号里**原样**带出真实异常（含缺失的模块名）：排查线索不能丢。
    """
    return (
        f"无法导入 playwright（{exc}）。安装命令：\n"
        f"  {venv_python(is_windows)} -m pip install -e \".[dev]\" "
        "--index-url https://pypi.org/simple\n"
        f"{playwright_install_hint(is_windows)}"
    )
