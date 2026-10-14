# -*- coding: utf-8 -*-
"""PDF 渲染层单测（任务 7.1 / 7.2）：文件名规则、base 注入、产物校验、环境探测。

真实渲染（Playwright）属于任务组 9 的集成验收；这里用假渲染器把**校验逻辑**钉死——
尤其是"静默产出空文件"这条失败形态。
"""
import asyncio
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
    # 黑名单全集：< > : " / \ | ? * 与控制字符
    assert pdf_file_name("2026-10-01", 'a<b>c:d"e\\f|g?h*i\x07j') == "261001-abcdefghij.pdf"


def test_pdf_file_name_cannot_escape_directory():
    """路径分隔符被剔除 → 产物名仍是**单个**文件名，不会写到上级目录去。"""
    name = pdf_file_name("2026-10-01", "../../etc/passwd")
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


def test_pdf_file_name_title_falls_back_when_empty():
    """标题为空、或净化后为空 → 固定占位 `未命名`（而不是难辨认的 `261001-.pdf`）。"""
    for title in ("", "///", "   ", "...", "\x00\x01"):
        assert pdf_file_name("2026-10-01", title) == "261001-未命名.pdf"


def test_pdf_file_name_strips_trailing_dots_and_spaces():
    """标题**结尾**的点与空格被剔除：Windows 会静默丢弃它们，导致库里的名字与盘上的不一致。"""
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
