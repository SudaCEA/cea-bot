# add-group-whitelist 任务清单

## 1. 分发层过滤（cea/app.py）

- [x] 1.1 `App.__init__` 新增 `group_whitelist: Collection[str] | None = None` 参数，归一化为 `frozenset[str]`
- [x] 1.2 `_dispatch` 在 spawn 前过滤：kind=group 且白名单非空且 chat.id 不在白名单 → `log.debug` + continue

## 2. 配置通路（main.py + config）

- [x] 2.1 `check_config` 解析 `group_whitelist`（list → str 列表，缺省 `[]`）并透传 `build_app` 所需；`main()` 构造 `App` 时传参
- [x] 2.2 `config.example.yaml` / `config.yaml` 增加 `group_whitelist` 项与语义注释（空=不过滤）；config.yaml 预填测试群号 348909747

## 3. 测试与回归

- [x] 3.1 单测：白名单内放行、非白名单丢弃（handler 不触发）、空白名单不过滤、私聊不受限（复用 test_framework 的 dispatch 测试手法）
- [x] 3.2 全量 pytest + mypy 零错误
- [x] 3.3 真机确认：白名单内群正常响应、白名单外群静默（用户实测通过，2026-09-23）
