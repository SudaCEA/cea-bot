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
import sys
from typing import Any

from cea_bot.assembly import build_adapter
from cea_bot.config import ensure_notice_dirs, load_config


def _check_notice_state(cfg: dict[str, Any]) -> None:
    """启动期校验增量状态库：版本不兼容时**立刻**退出，而不是等第一个定点才发现。

    规格里"状态版本不兼容"的场景写的是**启动时**明确报错并给出处置方式；`schema.ensure`
    会抛 `SchemaVersionError`（文案含"删除 data/state/cea_bot.db 后重建"），这里把它转成
    干净的退出提示。该调用只做只读校验与建表，**不会**自动改表、也不会重建状态。
    """
    from cea_bot import store
    from cea_bot.notices import schema

    path = str(cfg.get("notice_db_path") or "data/state/cea_bot.db")
    try:
        conn = schema.open_state(path)
    except (store.SchemaVersionError, schema.StateUnavailableError) as exc:
        # 规格把"状态不可用"与"版本不兼容"并列要求"明确报错并给出处置提示"：
        # 两种都收敛成一行可执行的退出文案，而不是把 sqlite 的原始异常丢出去。
        sys.exit(f"[状态库不可用] {exc}")
    conn.close()


def main() -> None:
    from cea import App
    from cea_bot.handlers import basic
    from cea_bot.handlers import notice as notice_handler

    cfg = load_config()
    notice_enabled = bool(cfg.get("notice_targets"))
    if notice_enabled:
        # 运行前提（状态库与产物目录可创建可写）属 IO，由这里在装配前一次性检查并快速失败；
        # `check_config` 自身保持纯函数（见 config.py 的模块 docstring）。
        ensure_notice_dirs(cfg)
        _check_notice_state(cfg)
    adapter = build_adapter(cfg)
    app = App(adapter=adapter, group_whitelist=cfg["group_whitelist"])
    app.include_router(basic.router)
    if notice_enabled:
        # 只在启用时挂载：未配置 notice_targets 的部署不注册任何定时任务、不联网。
        app.include_router(
            notice_handler.build_router(
                cfg,
                platform=notice_handler.platform_of(adapter),
                file_support=notice_handler.supports_file_send(adapter),
            )
        )
    asyncio.run(app.run())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass  # 优雅退出已在 app.run() 内完成，吞掉中断避免 traceback 噪音
