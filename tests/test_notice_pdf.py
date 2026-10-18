# -*- coding: utf-8 -*-
"""PDF 渲染层单测（任务 7.1 / 7.2）：文件名规则、base 注入、产物校验、环境探测。

真实渲染（Playwright）属于任务组 9 的集成验收；这里用假渲染器把**校验逻辑**钉死——
尤其是"静默产出空文件"这条失败形态。

`playwright` 的**导入失败**与**启动失败**两条收敛路径（变更
`fix-pdf-degrade-when-playwright-unavailable` 的 design D1/D2）用 `sys.modules` 注入假模块来钉：
`monkeypatch.setitem(sys.modules, "playwright.async_api", None)` 造导入失败，
假模块造"导入成功但 launch / 渲染阶段失败 —— 包括 `CancelledError`"。
**绝不真卸载 playwright**（那会破坏 venv）。
"""
import asyncio
import sys
import types
from pathlib import Path

import pytest

from cea_bot.notices import pdf
from cea_bot.notices.pdf import PdfRenderError, PdfResult, pdf_file_name, render_pdf, with_base_href
from cea_bot.platform_hints import current_is_windows, venv_python

PLATFORM_CASES = [
    (True, ".venv\\Scripts\\python", ".venv/bin/python"),
    (False, ".venv/bin/python", ".venv\\Scripts\\python"),
]
PLATFORM_IDS = ["windows", "posix"]


def _fake_playwright_module(
    *,
    launch_error: BaseException | None = None,
    pdf_error: BaseException | None = None,
    import_error: BaseException | None = None,
) -> types.ModuleType | types.SimpleNamespace:
    """构造一个假的 `playwright.async_api` 模块，用于让导入 / launch / 渲染阶段抛**指定**的异常。

    `sys.modules` 里塞 `None` 只能造出"导入就失败（`ImportError`）"这一种形态；"导入成功、但
    启动或渲染时失败"必须靠假模块（真卸载 playwright 会破坏 venv，不在选项里）。
    `import_error` 给的是"取模块属性即抛该异常"的假模块，用于探**导入那一行**的捕获面宽度。
    """
    if import_error is not None:
        class _RaisingModule(types.ModuleType):
            """取 `async_playwright` 即抛 `import_error`：模拟"导入这一行"冒出非 `ImportError` 的异常。

            只对那一个名字抛，其余照常 `AttributeError` —— 否则连 `hasattr(module, "__file__")`
            这类探测（pytest 报错时要走）都会被带上，失败现场会退化成 pytest 的 INTERNALERROR。
            """

            def __getattr__(self, item: str) -> object:
                if item == "async_playwright":
                    raise import_error
                raise AttributeError(item)

        return _RaisingModule("playwright.async_api")

    class _Page:
        async def set_content(self, html: str, wait_until: str | None = None) -> None:
            return None

        async def pdf(self, **kwargs: object) -> None:
            if pdf_error is not None:
                raise pdf_error

    class _Browser:
        async def new_page(self) -> _Page:
            return _Page()

        async def close(self) -> None:
            return None

    class _Chromium:
        async def launch(self) -> _Browser:
            if launch_error is not None:
                raise launch_error
            return _Browser()

    class _Playwright:
        def __init__(self) -> None:
            self.chromium = _Chromium()

    class _ContextManager:
        async def __aenter__(self) -> _Playwright:
            return _Playwright()

        async def __aexit__(self, *exc_info: object) -> bool:
            return False

    return types.SimpleNamespace(async_playwright=_ContextManager)


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


# ---- 变更 fix-pdf-degrade-when-playwright-unavailable：D1 收敛 / D2 取消冒穿 ----
#
# 这一节钉的是**真实导入路径**（既有的编排级用例注入的是假 `render_pdf`，覆盖不到这里 ——
# 那正是缺陷长期潜伏的原因，design D5）。

def test_render_pdf_converges_missing_playwright_to_pdf_render_error(tmp_path: Path, monkeypatch):
    """design D5①：`playwright.async_api` 不可导入时，`render_pdf` 抛的必须是 `PdfRenderError`。

    修的就是这条：导入原先在 `try` **之外**，抛的是 `ModuleNotFoundError`，而调用方
    `handlers/notice.py` 只捕 `(PdfRenderError, OSError)` → 异常冒穿整轮 → 连文本都不发 ——
    与主规格「PDF 生成失败 → 只发送文本并记录告警」直接冲突。

    用**生产入口** `render_pdf(...)`（不传 `renderer`，走 `renderer or playwright_renderer`），
    故这条用例覆盖的正是生产路径；`sys.modules["playwright.async_api"] = None` 让
    `from playwright.async_api import ...` 抛 `ImportError`（CPython 对 None 条目的既定语义，
    与"包真的缺失"同类），**不必真卸载 playwright**（那会破坏 venv）。
    """
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    with pytest.raises(PdfRenderError) as exc:
        asyncio.run(render_pdf("<html/>", tmp_path / "a.pdf"))
    message = str(exc.value)
    # ① 类型就是收敛后的 PdfRenderError。裸 `ModuleNotFoundError` 会在这里直接让用例失败
    #    （它不是 `PdfRenderError` 的子类，`pytest.raises` 捕不到它）；下面一行把口径写明。
    assert type(exc.value) is PdfRenderError
    assert not isinstance(exc.value, ModuleNotFoundError)
    # ② 文案必须是**可执行的**：含本平台 venv 解释器路径，而不是裸 traceback。
    current = venv_python(current_is_windows())
    assert f"{current} -m pip install -e" in message
    assert f"{current} -m playwright install chromium" in message


@pytest.mark.parametrize("is_windows, expected, wrong", PLATFORM_CASES, ids=PLATFORM_IDS)
def test_renderer_missing_playwright_hint_uses_injected_platform(
    tmp_path: Path, monkeypatch, is_windows: bool, expected: str, wrong: str
):
    """导入失败的提示必须把注入的平台**真的用上**（回归："参数接了没传"）。

    直接调 `playwright_renderer`（`render_pdf` 不接平台参数）：断言含本平台解释器路径
    **且不含**另一平台的，后者才是"写死 Windows"的回归网。
    """
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    with pytest.raises(PdfRenderError) as exc:
        asyncio.run(pdf.playwright_renderer("<html/>", tmp_path / "a.pdf", is_windows=is_windows))
    message = str(exc.value)
    assert f"{expected} -m playwright install chromium" in message
    assert wrong not in message


def test_playwright_renderer_converges_launch_failure_to_pdf_render_error(tmp_path: Path, monkeypatch):
    """D1 的另一半：**启动失败**与导入失败收敛到同一个 `PdfRenderError`（文案仍是启动提示）。

    导入成功但 `chromium.launch()` 失败（内核没装、驱动被删）也要降级，而不是冒穿整轮；
    文案走 `browser_launch_hint`，与"依赖缺失"那条（含 pip 安装命令）可区分。
    """
    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        _fake_playwright_module(launch_error=RuntimeError("Executable doesn't exist")),
    )
    with pytest.raises(PdfRenderError) as exc:
        asyncio.run(render_pdf("<html/>", tmp_path / "a.pdf"))
    message = str(exc.value)
    assert "RuntimeError" in message and "Executable doesn't exist" in message
    assert "playwright install chromium" in message


def test_import_time_non_import_error_converges_to_pdf_render_error(tmp_path: Path, monkeypatch):
    """F2（design D1 审查后修订）：导入期抛的**非** `ImportError`（部分安装）也必须收敛。

    只捕 `ImportError` 时的实测后果：异常冒穿 `render_pdf` → 调用方 `handlers/notice.py` 的
    `(PdfRenderError, OSError)` 接不住 → **整轮中止、连文本都不发**（原规格违反在这个更窄的
    触发类上照样复现）。假模块只在取 `async_playwright` 时抛 `RuntimeError`，故失败的正是
    `from playwright.async_api import async_playwright` 这一行。

    判据：① 冒出的是 `PdfRenderError`（`RuntimeError` 会让 `pytest.raises` 直接失败 —— 注意
    `PdfRenderError` 自己也继承 `RuntimeError`，故用 `type(...) is` 而不是 `isinstance`）；
    ② 真异常被 `raise ... from exc` 链上（`__cause__`），排查线索不丢；③ 文案仍是可执行的。
    """
    original = RuntimeError("部分安装：greenlet 缺失")
    monkeypatch.setitem(
        sys.modules, "playwright.async_api", _fake_playwright_module(import_error=original)
    )
    with pytest.raises(PdfRenderError) as exc:
        asyncio.run(render_pdf("<html/>", tmp_path / "a.pdf"))
    assert type(exc.value) is PdfRenderError, (
        f"导入期非 ImportError 未收敛，冒出的是 {type(exc.value).__name__}"
    )
    assert exc.value.__cause__ is original
    # 真异常的**消息原样**落在提示里（排查线索不能丢）：`{exc}` 就是它的 `str()`。
    assert "部分安装：greenlet 缺失" in str(exc.value)
    current = venv_python(current_is_windows())
    assert f"{current} -m playwright install chromium" in str(exc.value)


def test_missing_hint_says_cannot_import_not_not_installed():
    """F1 回归：**传递依赖**缺失时首行不得说成"未安装 playwright"。

    实测现场：playwright 包**能**导入、但它内部的 `pyee` / `greenlet` 缺失时，导入期抛的是
    `ModuleNotFoundError: No module named 'pyee'`；旧文案在这种现场产出"未安装 playwright
    （No module named 'pyee'）"—— 标签与括号里的真实异常**自相矛盾**，把排查引向"包没装"，
    而真实成因是 venv 被 `--no-deps`、半途失败的安装或部分卸载破坏。改为"无法导入 playwright"
    后标签与异常一致，且异常仍**原样**带出（排查线索不能丢）。
    """
    message = pdf.playwright_missing_hint(ModuleNotFoundError("No module named 'pyee'"), True)
    assert "无法导入 playwright" in message
    assert "No module named 'pyee'" in message
    assert "未安装 playwright" not in message
    # 改词不得动摇既有的可执行文案（平台分支另有 test_playwright_missing_hint_is_platform_correct 钉）。
    assert "playwright install chromium" in message


def test_cancelled_error_propagates_through_render_path(tmp_path: Path, monkeypatch):
    """design D2 的**证据**（不是心里推理）：`asyncio.CancelledError` 必须继续冒穿渲染路径。

    `CancelledError` 在 Python 3.8+ 继承 `BaseException`，故 `except Exception` 捕不到它 ——
    框架的优雅退出**依赖这一点**（`cea/adapters/base.py` 的契约：run() 被取消即停止重连并收尾）。
    若收敛时把捕获面写成 `except BaseException`，取消就会被吞成 `PdfRenderError`，
    表现为"Ctrl+C 后不退出 / 停不下来"，而单测仍全绿。

    三处捕获面各探一次：**导入**（本变更新加的那个 `except ImportError`）、**launch** 与**渲染**
    （既有的两个 `except Exception`）。用 `except BaseException` 显式兜住：要断言的正是
    **穿过边界的异常类型恰好是 `CancelledError`**。
    """
    for stage, kwargs in (
        ("import", {"import_error": asyncio.CancelledError()}),
        ("launch", {"launch_error": asyncio.CancelledError()}),
        ("render", {"pdf_error": asyncio.CancelledError()}),
    ):
        monkeypatch.setitem(
            sys.modules, "playwright.async_api", _fake_playwright_module(**kwargs)
        )
        try:
            asyncio.run(render_pdf("<html/>", tmp_path / f"{stage}.pdf"))
        except BaseException as exc:  # noqa: BLE001 - 刻意兜住 BaseException：就是要看它有没有被降级
            assert type(exc) is asyncio.CancelledError, f"{stage} 阶段的取消被吞成了 {type(exc).__name__}"
        else:
            pytest.fail(f"{stage} 阶段的 CancelledError 没有冒穿渲染路径")
