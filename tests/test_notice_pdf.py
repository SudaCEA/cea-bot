# -*- coding: utf-8 -*-
"""PDF 渲染层单测（任务 7.1 / 7.2）：文件名规则、base 注入、产物校验、环境探测。

真实渲染（Playwright）属于任务组 9 的集成验收；这里用假渲染器把**校验逻辑**钉死——
尤其是"静默产出空文件"这条失败形态。
"""
import asyncio
import sys
from pathlib import Path

import pytest

from cea_bot.notices import pdf
from cea_bot.notices.pdf import PdfRenderError, PdfResult, pdf_file_name, render_pdf, with_base_href


def _writer(payload: bytes):
    async def render(html: str, out_path: Path) -> None:
        out_path.write_bytes(payload)

    return render


def _noop():
    async def render(html: str, out_path: Path) -> None:
        return None

    return render


# ---- 7.1 产物命名（主规格「PDF 产物命名」）+ base 注入 ----

def test_pdf_file_name_rule():
    """正常命名：`YYMMDD-<标题>.pdf` —— 站点内部的 editId 不再出现在文件名里。"""
    assert pdf_file_name("2026-10-01", "通知标题") == "261001-通知标题.pdf"


def test_pdf_file_name_keeps_chinese_title_intact():
    """回归本变更的核心：旧净化是 ASCII 白名单 `[^0-9A-Za-z._-]+`，会把中文标题整段删空。

    换成黑名单后，中文标题必须一字不少地落进文件名（不能退化成 `261013-.pdf`）。
    """
    title = "关于开展苏州大学第十九届学生标兵选拔工作的通知"
    assert pdf_file_name("2026-10-13", title) == f"261013-{title}.pdf"


def test_pdf_file_name_strips_illegal_chars():
    """规格 Scenario「标题含非法字符」：`/` 等非法字符被**剔除**，而不是把整段标题删空。"""
    assert pdf_file_name("2026-10-01", "关于XX/YY的通知") == "261001-关于XXYY的通知.pdf"
    # 黑名单全集：< > : " / \ | ? * 与 C0 控制字符（DEL 与 C1 另有用例，见下）
    assert pdf_file_name("2026-10-01", 'a<b>c:d"e\\f|g?h*i\x07j') == "261001-abcdefghij.pdf"


def test_pdf_file_name_strips_del_and_c1_control_chars():
    """规格："控制字符 SHALL 被剔除" —— 而控制字符不止 C0（`\\x00-\\x1f`）。

    DEL（`\\x7f`）与 C1（`\\x80-\\x9f`）同在 Unicode 控制字符之列；只剔 C0 时它们会**残留**到
    文件名里（Windows 允许），归档名于是混进不可见垃圾字符 —— `261001-a\\x85b.pdf` 看着像
    `261001-ab.pdf`，实际复制、检索、比对全都对不上。三例都必须被剔除。
    """
    assert pdf_file_name("2026-10-01", "a\x7fb") == "261001-ab.pdf"
    assert pdf_file_name("2026-10-01", "a\x85b") == "261001-ab.pdf"
    assert pdf_file_name("2026-10-01", "a\x9fb") == "261001-ab.pdf"


def test_pdf_file_name_cannot_escape_directory():
    """路径分隔符被剔除 → 产物名仍是**单个**文件名，不会写到上级目录去。

    第一条是**字面量**断言，刻意加的：只断言"没有分隔符"与 `Path(name).name == name` 时，
    一个把 `.` 也当非法字符剔掉的实现（产出 `261001-etcpasswd.pdf`）照样通过 —— 那是**静默改名**，
    不是"拒绝穿越"。字面量把"只剔分隔符、其余原样保留"这条口径钉死。
    """
    name = pdf_file_name("2026-10-01", "../../etc/passwd")
    assert name == "261001-....etcpasswd.pdf"
    assert "/" not in name and "\\" not in name
    assert Path(name).name == name


def test_pdf_file_name_truncates_long_title():
    """规格 Scenario「标题过长」：标题部分截断到 80 字符（`YYMMDD-` 与 `.pdf` 不计入）。"""
    name = pdf_file_name("2026-10-01", "通" * 120)
    assert name == "261001-" + "通" * 80 + ".pdf"
    assert len(name) == 7 + 80 + 4  # 路径余量按这个上界算


def test_pdf_file_name_missing_or_non_iso_date():
    """规格 Scenario「发布日期缺失」：非 `YYYY-MM-DD` 一律用 `000000-` 开头，不留空、不中断。"""
    for published_on in ("", "2026/10/13", "2026-10", "2026-1-3", "2026-10-01 09:00"):
        assert pdf_file_name(published_on, "通知标题") == "000000-通知标题.pdf"


def test_pdf_file_name_rejects_non_ascii_digits_in_date():
    """日期戳只认 **ASCII** 数字：Unicode Nd 是 ASCII 的同形异义字，必须落 `000000-`。

    全角 `２０２６-１０-０１`、阿拉伯-印度 `٢٠٢٦-١٠-٠١`、天城文 `२०२६-१०-०१` 都属于 Unicode Nd，
    用 `\\d` 写正则时会被当成合法日期，产出 `２６１００１-通知标题.pdf` —— 名字看着像日期，
    排序与检索却全部错位（同形异义字），比落 `000000` 更糟。它们本就不是规格要求的
    `YYYY-MM-DD` 形式（`YYMMDD` 是给排序/检索用的 ASCII 戳），故一律回落到占位。
    """
    for published_on in ("２０２６-１０-０１", "٢٠٢٦-١٠-٠١", "२०२६-१०-०१"):
        assert pdf_file_name(published_on, "通知标题") == "000000-通知标题.pdf"
    # 混排（ASCII 数字 + Unicode 数字）同样不是合法形式，不得只"半认"
    assert pdf_file_name("２０２6-10-01", "通知标题") == "000000-通知标题.pdf"


def test_pdf_file_name_title_falls_back_when_empty():
    """标题为空、或净化后为空 → 固定占位 `未命名`（而不是难辨认的 `261001-.pdf`）。"""
    for title in ("", "///", "   ", "...", "\x00\x01"):
        assert pdf_file_name("2026-10-01", title) == "261001-未命名.pdf"


def test_pdf_file_name_strips_trailing_dots_and_spaces():
    """标题**结尾**的点与空格被剔除：理由是**名字整洁**，避免 `261014-通知标题. .pdf` 这类难辨认的名字。

    理由**不是**"防 Windows 静默裁名"：Windows 只裁**路径组件末端**的点与空格，而本命名恒以
    `.pdf` 结尾，故"库里名字与盘上名字不一致"在这套命名下不可能发生（实测盘上 `尾点.pdf.` 会被
    裁成 `尾点.pdf`，但 `扩展名前. .pdf` 原样保留）。规则本身无害且让名字更整洁，故保留。
    """
    assert pdf_file_name("2026-10-01", "通知标题. ") == "261001-通知标题.pdf"
    assert pdf_file_name("2026-10-01", "通知标题..  ") == "261001-通知标题.pdf"
    # 中间的点与空格是合法字符，必须保留
    assert pdf_file_name("2026-10-01", "1.2 关于 XX 的通知") == "261001-1.2 关于 XX 的通知.pdf"
    # 截断正好落在一个点上：收尾必须在**截断之后**做，顺序反了就会漏
    assert pdf_file_name("2026-10-01", "通" * 79 + ". 尾巴") == "261001-" + "通" * 79 + ".pdf"


def test_with_base_href_injects_after_head():
    html = "<html><head><title>t</title></head><body>x</body></html>"
    out = with_base_href(html, "http://file.suda.edu.cn")
    assert '<base href="http://file.suda.edu.cn/">' in out
    assert out.index("<base") < out.index("<title>")
    # 没有 <head> 时前置注入，仍能生效
    assert with_base_href("<p>x</p>", "http://h").startswith('<base href="http://h/">')


# ---- 7.1 产物校验 ----

def test_render_pdf_creates_dirs_and_returns_result(tmp_path: Path):
    target = tmp_path / "nested" / "a.pdf"
    result = asyncio.run(render_pdf("<html/>", target, renderer=_writer(b"%PDF-1.7\nrest")))
    assert isinstance(result, PdfResult)
    assert result.path == target and result.bytes_len == len(b"%PDF-1.7\nrest")
    assert target.parent.is_dir()


def test_empty_artifact_raises(tmp_path: Path):
    with pytest.raises(PdfRenderError) as exc:
        asyncio.run(render_pdf("<html/>", tmp_path / "a.pdf", renderer=_writer(b"")))
    assert "空文件" in str(exc.value)


def test_missing_artifact_raises(tmp_path: Path):
    with pytest.raises(PdfRenderError) as exc:
        asyncio.run(render_pdf("<html/>", tmp_path / "a.pdf", renderer=_noop()))
    assert "不存在" in str(exc.value)


def test_non_pdf_artifact_raises(tmp_path: Path):
    with pytest.raises(PdfRenderError) as exc:
        asyncio.run(render_pdf("<html/>", tmp_path / "a.pdf", renderer=_writer("<html>不是 PDF</html>".encode())))
    assert "不是 PDF" in str(exc.value)


def test_renderer_error_propagates(tmp_path: Path):
    async def boom(html: str, out_path: Path) -> None:
        raise PdfRenderError("浏览器启动失败")

    with pytest.raises(PdfRenderError) as exc:
        asyncio.run(render_pdf("<html/>", tmp_path / "a.pdf", renderer=boom))
    assert "浏览器启动失败" in str(exc.value)


def test_renderer_receives_html_with_base(tmp_path: Path):
    seen: list[str] = []

    async def spy(html: str, out_path: Path) -> None:
        seen.append(html)
        out_path.write_bytes(b"%PDF-1.4")

    asyncio.run(render_pdf("<html><head></head></html>", tmp_path / "a.pdf", renderer=spy, site_root="http://file.suda.edu.cn"))
    assert '<base href="http://file.suda.edu.cn/">' in seen[0]


# ---- 7.2 环境探测 ----

def test_render_env_error_is_actionable_or_ready():
    """本机已装浏览器时返回 None；未装时必须给出**可执行的**安装提示（不裸 traceback）。"""
    message = asyncio.run(pdf.render_env_error())
    assert message is None or "playwright install chromium" in message


@pytest.mark.parametrize(
    "is_windows, expected, wrong",
    [(True, ".venv\\Scripts\\python", ".venv/bin/python"),
     (False, ".venv/bin/python", ".venv\\Scripts\\python")],
    ids=["windows", "posix"],
)
def test_playwright_install_hint_is_platform_correct(is_windows: bool, expected: str, wrong: str):
    """安装提示按平台生成（两个分支各一条用例，任一本机都能跑）。

    命令统一是 `-m playwright install chromium`（包内带 `__main__.py`），故 Windows 的
    `Scripts\\playwright.exe` 与 Linux 的 `bin/playwright` 共用同一条命令 —— 分平台的只有
    解释器路径。断言"含本平台路径"**且**"不含另一平台路径"，后者才是"写死 Windows"的回归网。
    """
    message = pdf.playwright_install_hint(is_windows)
    assert f"{expected} -m playwright install chromium" in message
    assert wrong not in message


@pytest.mark.parametrize(
    "is_windows, expected, wrong",
    [(True, ".venv\\Scripts\\python", ".venv/bin/python"),
     (False, ".venv/bin/python", ".venv\\Scripts\\python")],
    ids=["windows", "posix"],
)
def test_playwright_missing_hint_is_platform_correct(is_windows: bool, expected: str, wrong: str):
    """未安装 playwright 的提示：安装与 pip 两条命令都用本平台的解释器路径。"""
    message = pdf.playwright_missing_hint(ImportError("No module named 'playwright'"), is_windows)
    assert f"{expected} -m pip install -e" in message
    assert f"{expected} -m playwright install chromium" in message
    assert wrong not in message


@pytest.mark.parametrize(
    "is_windows, expected, wrong",
    [(True, ".venv\\Scripts\\python", ".venv/bin/python"),
     (False, ".venv/bin/python", ".venv\\Scripts\\python")],
    ids=["windows", "posix"],
)
def test_browser_launch_hint_is_platform_correct(is_windows: bool, expected: str, wrong: str):
    """浏览器启动失败提示：保留异常类型与消息，且给本平台可执行的补救命令。"""
    message = pdf.browser_launch_hint(RuntimeError("Executable doesn't exist"), is_windows)
    assert "RuntimeError" in message and "Executable doesn't exist" in message
    assert f"{expected} -m playwright install chromium" in message
    assert wrong not in message


@pytest.mark.parametrize(
    "is_windows, expected",
    [(True, ".venv\\Scripts\\python"), (False, ".venv/bin/python")],
    ids=["windows", "posix"],
)
def test_render_env_error_uses_injected_platform(is_windows: bool, expected: str, monkeypatch):
    """探测入口必须把注入的平台**真的用上**（回归："参数接了没传"）。

    `sys.modules["playwright.async_api"] = None` 让 `from playwright.async_api import ...`
    抛 ImportError（CPython 对 None 条目的既定语义），于是不必卸载真实依赖就能钉住
    "未安装 playwright"这一分支的文案 —— 也顺带证明该分支的提示确实走到了平台参数。
    """
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    message = asyncio.run(pdf.render_env_error(is_windows=is_windows))
    assert message is not None
    assert f"{expected} -m pip install -e" in message
    assert f"{expected} -m playwright install chromium" in message
