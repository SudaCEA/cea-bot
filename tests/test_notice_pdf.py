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


# ---- 7.1 文件名与 base 注入 ----

def test_pdf_file_name_rule():
    assert pdf_file_name("2026-10-13", "40773772") == "2026-10-13-40773772.pdf"


def test_pdf_file_name_sanitizes_and_falls_back():
    assert pdf_file_name("2026/10/13", "a b:c") == "20261013-abc.pdf"
    assert pdf_file_name("", "") == "未知日期-未知标识.pdf"


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
