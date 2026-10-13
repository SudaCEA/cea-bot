# -*- coding: utf-8 -*-
"""包布局断言：配置路径必须指向仓库根，而不是 src/cea_bot/。

这是逼出 `Path(__file__).resolve().parents[2]` 的测试。若照抄 `parent`，
`CONFIG_PATH` 会变成 src/cea_bot/config.yaml；该错误只在"配置缺失"文案里
出现，很容易被当成合法报错放过，故用测试钉住。
"""
from pathlib import Path

from cea_bot import config


def test_config_path_points_to_repo_root():
    """CONFIG_PATH 的父目录是仓库根（含 pyproject.toml 与 src/cea_bot）。"""
    assert config.CONFIG_PATH.name == "config.yaml"
    repo_root = config.CONFIG_PATH.parent
    assert (repo_root / "pyproject.toml").is_file()
    assert (repo_root / "src" / "cea_bot").is_dir()
    # 反向钉住：不能是模块自己所在的那一级（src/cea_bot/）
    assert repo_root != Path(config.__file__).resolve().parent
