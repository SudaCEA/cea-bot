# -*- coding: utf-8 -*-
"""平台提示单测（任务 1.1 / 1.2）：venv 解释器路径**只有两种形态**，且平台可注入。

平台事实不在被测函数里读：`venv_python(is_windows)` 是纯函数，`resolve_is_windows(可选值)`
只做一次边界转换，故 Windows 与 POSIX 两个分支在**任一操作系统**上都能被钉死
（本仓库"纯函数优先、单测不依赖平台"的取向）。

运行：.venv\\Scripts\\python -m pytest tests -q（POSIX：.venv/bin/python -m pytest tests -q）
"""
import re

from cea_bot.config import _credentials_hint
from cea_bot.notices import pdf
from cea_bot.platform_hints import current_is_windows, resolve_is_windows, venv_python

_WINDOWS_PY = ".venv\\Scripts\\python"
_POSIX_PY = ".venv/bin/python"

# 文案里所有以 `.venv` 开头的路径 token（到空白或引号为止）：用于"没有第三种写法"的断言。
_VENV_TOKEN = re.compile(r"\.venv[^\s\"']*")


def _all_hint_texts() -> list[str]:
    """两个平台分支下、全部提示构造函数产出的文案（1.2 的四处提示 + 凭据提示）。"""
    texts: list[str] = []
    for is_windows in (True, False):
        texts.append(_credentials_hint(is_windows))
        texts.append(pdf.playwright_install_hint(is_windows))
        texts.append(pdf.playwright_missing_hint(ImportError("未安装 playwright"), is_windows))
        texts.append(pdf.browser_launch_hint(RuntimeError("boom"), is_windows))
    return texts


def test_venv_python_windows_branch():
    """分支一：Windows 的 venv 解释器是 `.venv\\Scripts\\python`。"""
    assert venv_python(True) == _WINDOWS_PY


def test_venv_python_posix_branch():
    """分支二：POSIX（Ubuntu）的 venv 解释器是 `.venv/bin/python`。"""
    assert venv_python(False) == _POSIX_PY


def test_resolve_is_windows_injection_wins_over_current_platform():
    """显式注入必须**原样采信**：否则本机是 Windows 时 POSIX 分支永远测不到。"""
    assert resolve_is_windows(True) is True
    assert resolve_is_windows(False) is False


def test_resolve_is_windows_defaults_to_current_platform():
    """缺省（None）才回落到当前平台，且 `current_is_windows` 是唯一读平台事实的地方。"""
    assert resolve_is_windows(None) is current_is_windows()
    assert isinstance(current_is_windows(), bool)


def test_hint_texts_only_contain_the_two_venv_forms():
    """回归：提示里**只允许**出现 `.venv\\Scripts\\python` 与 `.venv/bin/python`。

    第三种写法（`.venv/Scripts/python`、`.venv\\Scripts\\playwright`、`.venv/bin/python3`……）
    正是"提示不可执行"的成因本身，故按 token 断言，而不是各处散写 `in` 断言。
    注意 `_VENV_TOKEN` 只命中以 `.venv` 开头的 token：`Scripts/python` 这类不带 `.venv`
    前缀的裸写法根本不会被匹配，不能拿来当反例。
    末尾的阳性对照防的是**空过**：一条 venv 路径都没有时，上面的子集断言会无意义地成立。
    """
    texts = _all_hint_texts()
    for text in texts:
        assert set(_VENV_TOKEN.findall(text)) <= {_WINDOWS_PY, _POSIX_PY}, text
    assert all(_VENV_TOKEN.findall(text) for text in texts)
