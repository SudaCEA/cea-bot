# -*- coding: utf-8 -*-
"""平台前端装配：按已校验的配置造出 Adapter（工厂，供测试直接调用）。"""
from typing import Any

from cea.adapters.base import Adapter


def build_adapter(cfg: dict[str, Any]) -> Adapter:
    """按配置装配平台前端（QQ 官方 / NapCat 可互换，业务层无感）。"""
    if cfg["adapter"] == "napcat":
        from cea.adapters.napcat import NapCatAdapter

        return NapCatAdapter(cfg["ws_url"], token=cfg["token"])
    from cea.adapters.qqbotpy import QQBotpyAdapter

    return QQBotpyAdapter(cfg["appid"], cfg["secret"])
