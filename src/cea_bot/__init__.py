# -*- coding: utf-8 -*-
"""CEA Bot 应用层：平台相关组装，只依赖框架 cea，不反向被依赖。

- `config.py`    配置读取与校验（纯函数 + IO 边界）
- `assembly.py`  平台前端装配工厂（对两个前端延迟 import）
- `handlers/`    业务层：只用装饰器挂纯异步函数，不碰平台细节
- `__main__.py`  `python -m cea_bot` 入口（薄壳，不写逻辑）
"""
