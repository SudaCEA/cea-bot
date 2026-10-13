# -*- coding: utf-8 -*-
"""CEA Bot 组装入口。

基于框架 cea/（平台无关核心）+ 平台前端适配器：
  - cea/adapters/qqbotpy.py  QQ 官方开放平台（缺省）
  - cea/adapters/napcat.py   NapCat / OneBot 11（正向 WS）

config.yaml 顶层 `adapter` 选择前端：qqofficial（缺省）| napcat，
各前端所需参数见 config.example.yaml。

业务代码写在 handlers/ 下（用 @router.command / @router.on_message / @router.schedule
装饰纯异步函数），本文件只负责组装，不写业务逻辑。
"""
import asyncio
import sys
from pathlib import Path
from typing import Any

import yaml

from cea.adapters.base import Adapter

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"

_QQ_SETTING_URL = "https://q.qq.com/qqbot/#/developer/developer-setting"


def check_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """按 adapter 分支校验配置（纯函数，供测试）；不合法直接退出并说明。"""
    adapter = str(cfg.get("adapter") or "qqofficial").strip().lower()
    whitelist_raw = cfg.get("group_whitelist") or []
    if not isinstance(whitelist_raw, list):
        sys.exit("[配置错误] group_whitelist 应为群号字符串列表")
    group_whitelist = [str(item).strip() for item in whitelist_raw if str(item).strip()]
    if adapter == "qqofficial":
        appid = str(cfg.get("appid") or "").strip()
        secret = str(cfg.get("secret") or "").strip()
        if not appid or not secret or appid.startswith("YOUR_") or secret.startswith("YOUR_"):
            sys.exit(
                "[等待接入 apikey] adapter=qqofficial 需要机器人凭据：\n"
                f"  1. 登录 QQ 机器人开放平台：{_QQ_SETTING_URL}\n"
                "  2. 在「开发设置」页复制 AppID 与 AppSecret\n"
                "  3. 填入 config.yaml 后重新运行：.venv\\Scripts\\python main.py"
            )
        return {"adapter": adapter, "appid": appid, "secret": secret,
                "group_whitelist": group_whitelist}
    if adapter == "napcat":
        ws_url = str(cfg.get("ws_url") or "").strip()
        token = str(cfg.get("token") or "").strip()
        if not ws_url:
            sys.exit(
                "[配置缺失] adapter=napcat 需要填写 ws_url（NapCat 正向 WS 地址，"
                "如 ws://127.0.0.1:3001）。NapCat 侧配置步骤见 cea/adapters/napcat.py 模块说明。"
            )
        return {"adapter": adapter, "ws_url": ws_url, "token": token,
                "group_whitelist": group_whitelist}
    sys.exit(f"[配置错误] 未知 adapter: {adapter!r}，可选值：qqofficial / napcat")


def load_config() -> dict[str, Any]:
    """读取 config.yaml 并校验；未找到配置文件时明确提示。"""
    if not CONFIG_PATH.exists():
        sys.exit(f"[配置缺失] 未找到 {CONFIG_PATH}，请参考 config.example.yaml 创建。")
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return check_config(cfg)


def build_adapter(cfg: dict[str, Any]) -> Adapter:
    """按配置装配平台前端（QQ 官方 / NapCat 可互换，业务层无感）。"""
    if cfg["adapter"] == "napcat":
        from cea.adapters.napcat import NapCatAdapter

        return NapCatAdapter(cfg["ws_url"], token=cfg["token"])
    from cea.adapters.qqbotpy import QQBotpyAdapter

    return QQBotpyAdapter(cfg["appid"], cfg["secret"])


def main() -> None:
    from cea import App
    from handlers import basic

    cfg = load_config()
    app = App(adapter=build_adapter(cfg), group_whitelist=cfg["group_whitelist"])
    app.include_router(basic.router)
    asyncio.run(app.run())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass  # 优雅退出已在 app.run() 内完成，吞掉中断避免 traceback 噪音
