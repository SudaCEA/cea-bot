# -*- coding: utf-8 -*-
"""pytest 全局前置检查：把"忘记安装"翻译成可执行的下一步。

可移植性（任意 cwd 都能跑）来自 `pip install -e ".[dev]"` —— src-layout 下
`src/` 不在解释器的模块搜索路径中，只有安装后才能 import。这里**不做**任何
路径注入手脚：那既是对抗打包，也会掩盖"忘记安装"这个真问题。

唯一职责：`import cea` 失败时给出安装提示并以非 0 退出（快速失败）。
捕获必须精确：只有 `cea` 自身缺失才算"未安装"；`cea` 内部缺 `aiohttp` 之类
真实故障（`exc.name != "cea"`）必须原样抛出，不能被伪装成"未安装"。

放在 `pytest_configure` 而不是模块顶层：conftest 导入期抛异常会被 pytest 包成
`ConftestImportFailure` → `UsageError`，输出一大段裸 traceback；钩子里抛
`pytest.exit` 则由 pytest 以干净的一行收尾。
"""
import sys

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """会话开始即确认框架包可导入；未安装则给指引并快速失败。"""
    try:
        import cea  # noqa: F401
    except ModuleNotFoundError as exc:
        if exc.name != "cea":
            raise
        _HINT = (
            "导入 cea 失败：项目尚未安装到当前解释器。\n"
            '请先执行：.venv\\Scripts\\python -m pip install -e ".[dev]" '
            "--index-url https://pypi.org/simple"
        )
        # 自行输出提示：本钩子在部分路径下（conftest 于收集期才被加载）由
        # call_historic 回放，此时 pytest 不代为打印 pytest.exit 的 msg
        # （见 _pytest/main.py 的 initstate 判断），只写 pytest.exit 会让
        # 提示静默丢失、只剩一个非 0 退出码。
        sys.stderr.write(f"\n{_HINT}\n\n")
        pytest.exit(_HINT, returncode=1)
