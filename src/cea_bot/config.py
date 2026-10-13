# -*- coding: utf-8 -*-
"""配置读取与校验：定位仓库根的 config.yaml，按 adapter 分支校验。

`check_config` 是纯函数（已被测试直接依赖），`load_config` 只做文件 IO。
"""
import sys
from pathlib import Path
from typing import Any

import yaml

# 仓库根 = src/cea_bot/config.py 的 parents[2]（[0]=cea_bot, [1]=src, [2]=仓库根）。
# 不能照抄 `parent`：那会指向 src/cea_bot/，让 load_config 去找
# src/cea_bot/config.yaml —— 该文件不存在，错误只在"配置缺失"文案里出现，
# 属静默失败（由 tests/test_layout.py 逼住）。
ROOT = Path(__file__).resolve().parents[2]
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
                "  3. 填入 config.yaml 后重新运行：.venv\\Scripts\\python -m cea_bot"
            )
        return {"adapter": adapter, "appid": appid, "secret": secret,
                "group_whitelist": group_whitelist}
    if adapter == "napcat":
        ws_url = str(cfg.get("ws_url") or "").strip()
        token = str(cfg.get("token") or "").strip()
        if not ws_url:
            sys.exit(
                "[配置缺失] adapter=napcat 需要填写 ws_url（NapCat 正向 WS 地址，"
                "如 ws://127.0.0.1:3001）。NapCat 侧配置步骤见 src/cea/adapters/napcat.py 模块说明。"
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
