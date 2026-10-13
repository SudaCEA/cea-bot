# -*- coding: utf-8 -*-
"""CEA Bot 组装入口。

基于框架 src/cea/（平台无关核心）+ 平台前端适配器：
  - src/cea/adapters/qqbotpy.py  QQ 官方开放平台（缺省）
  - src/cea/adapters/napcat.py   NapCat / OneBot 11（正向 WS）

config.yaml 顶层 `adapter` 选择前端：qqofficial（缺省）| napcat，
各前端所需参数见 config.example.yaml。

业务代码写在 cea_bot/handlers/ 下（用 @router.command / @router.on_message /
@router.schedule 装饰纯异步函数），本文件只负责组装，不写业务逻辑。
"""
import asyncio

from cea_bot.assembly import build_adapter
from cea_bot.config import load_config


def main() -> None:
    from cea import App
    from cea_bot.handlers import basic

    cfg = load_config()
    app = App(adapter=build_adapter(cfg), group_whitelist=cfg["group_whitelist"])
    app.include_router(basic.router)
    asyncio.run(app.run())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass  # 优雅退出已在 app.run() 内完成，吞掉中断避免 traceback 噪音
