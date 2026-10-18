# -*- coding: utf-8 -*-
"""平台相关的**可执行提示**：venv 解释器相对路径的唯一真相源。

为什么单独成模块（design D3 / 任务 1.1）：`cea_bot/config.py` 与
`cea_bot/notices/pdf.py` 都要把"请执行 xxx"写成**平台正确**的路径 —— Windows 的 venv
解释器是 `.venv\\Scripts\\python`，POSIX 是 `.venv/bin/python`；写死任一种，另一种平台上
给出的提示都**不可执行**（Linux 上 `.venv\\Scripts\\python` 连文件都不存在，照做只得到一句
"No such file or directory"，把人带偏）。

**平台事实只在一个地方读**：`current_is_windows()`。其它函数一律把 `is_windows` 做成
**可注入参数**（含各调用点的 `is_windows: bool | None = None`），故 Windows 与 POSIX
两个分支都能在**任一操作系统**上被单测钉死 —— 符合本仓库"纯函数优先、单测不依赖平台"的取向。

依赖方向：本模块只 import 标准库，`cea_bot/notices/**` 依赖它**不构成**反向依赖
（反向依赖指 notices 去 import 上层业务/平台 SDK）。
"""
import os

# venv 解释器的**相对**路径（以仓库根为基准）。常量只此两处，避免第二个写法悄悄长出来。
_WINDOWS_VENV_PYTHON = ".venv\\Scripts\\python"
_POSIX_VENV_PYTHON = ".venv/bin/python"


def current_is_windows() -> bool:
    """当前进程是否运行在 Windows —— **唯一**读取平台事实的地方（边界函数，非纯函数）。

    刻意不用 `sys.platform`：这里只关心"路径分隔符与 venv 目录布局是 `nt` 还是 `posix`"，
    `os.name` 与 `pathlib` 的平台分支同源（`nt` / `posix`），口径一致。
    """
    return os.name == "nt"


def resolve_is_windows(is_windows: bool | None) -> bool:
    """把"可注入的平台参数"落成布尔：`None` = 用当前平台，显式布尔 = 原样采信。

    三个调用点（配置校验、渲染器、环境探测）都走这一条，以免某处漏判 `None`
    让注入的平台**接了没传**（那正是本仓库点名的红线）。
    """
    return current_is_windows() if is_windows is None else is_windows


def venv_python(is_windows: bool) -> str:
    """venv 解释器的相对路径（任务 1.1 里写作 `_venv_python` 的那个**纯函数**）。

    相对而非绝对：提示是给人照抄的，而脚本本就要求"在仓库根执行"
    （`start.bat` / `start.sh` 都会先切到自身所在目录），相对路径不依赖部署位置。
    """
    return _WINDOWS_VENV_PYTHON if is_windows else _POSIX_VENV_PYTHON
