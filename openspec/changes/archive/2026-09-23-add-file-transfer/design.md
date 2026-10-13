# add-file-transfer 设计

## Context

框架现状：双总线 + Adapter 契约（run/send 两方法）；`OutMessage` text/image 互斥；`Message` 的文件段仅经 `raw` 透出；`Bot` 句柄只有 outbound 队列引用，够不着 adapter——这是文件句柄设计的核心约束。NapCat 侧调研结论（`.ref/NapCatDocs`，OpenAPI 4.18.28 实证）：`file` 段收发均支持；`get_file(file_id|file)` 响应含 `file`(本地路径)/`url`(下载URL)/`file_size`/`file_name`/`base64` 五字段；`upload_group_file(group_id, file, name, folder_id)` 接受路径或 URL；跨机本地路径不可用已真机实证（retcode=1200）。

已定决策输入：Q1 群文件系统纳入；Q2 大小上限 16MB；Q3 `notice.group_upload` 不建模；官方前端按"warning + 丢弃"降级（上轮既定策略）。

## Goals / Non-Goals

**Goals:**

- 文件作为一等建模：出站三选一、入站 `files` 引用、下载回字节
- NapCat 全量落地（file 段 + get_file + upload_group_file），跨机可用（base64/URL 通道）
- QQ 官方前端显式降级且行为固化进 spec
- 错误语义清晰：句柄方法抛异常（请求-响应语义），与总线 send 的"记日志不外抛"区分

**Non-Goals:**

- Stream API（upload/download_file_stream，>100MB 流式）——二期
- `notice.group_upload` 事件建模——Q3 决策
- 官方前端任何文件能力——平台不支持
- 文件缓存/去重、分片断点续传

## Decisions

**D1. `OutMessage` 三选一互斥，不做 media 泛化。** text/image/file 三个显式字段 + `__post_init__` 校验"恰好一项"。显式字段的类型提示与 docstring 可读性最好；泛化 media 需要引入子类型枚举，收益低。

**D2. 能力注入用鸭子类型探测，Adapter 协议保持两方法不变。** `App.__init__` 里 `getattr(adapter, "fetch_file", None)` / `getattr(adapter, "upload_group_file", None)`，非 None 则绑定到 `Bot` 的可选字段。备选：协议强制实现（qqbotpy 被迫写假实现）/ 定义第二个 `FileCapableAdapter` Protocol——前者污染契约，后者徒增类型层。探测方案让"平台不支持"成为装配期事实而非协议负担。

**D3. 错误语义三分。** ① 能力缺失 → `NotImplementedError("平台不支持…")`（Bot 方法内判断注入字段为 None 时抛）；② 超限 → 新增 `FileTooLargeError(ValueError)`（bot.py 定义，常量 `MAX_FILE_BYTES = 16 * 1024 * 1024`）；③ 平台调用失败 → `RuntimeError`（带 retcode/wording）。均抛给 handler，由业务决定处理——与 send 的总线语义（永不外抛）刻意不同，spec 已区分。

**D4. `fetch_file` 降级链：base64 → url → 报错。** `get_file` 响应 base64 字段优先（免二次网络请求、跨机直通）；为空则用 `url` 字段经 adapter 现有 aiohttp session 下载；两者皆无抛 `RuntimeError`。每步检查字节数 ≤ 16MB，超限即抛（base64 解码前先按 4/3 估长预检，HTTP 下载流式计数）。

**D5.（真机后废止）群文件上传移除。** Q1 曾纳入 `upload_group_file`；真机观察发现 QQ 的群内文件消息本身会同步出现在群文件列表中，独立上传能力无实际增益，按用户决策整体移除（Bot / NapCat 前端 / spec / 任务同步撤除）。

**D5b. `fetch_file` 降级链收敛 + 分端部署限制显式化（真机实证）。** 首轮真机发现：分端部署时 `get_file` 对群文件仅返回 NapCat 本地路径（base64 空、url 非 http），本地路径被误作 URL 请求且 aiohttp 异常穿透 handler 导致静默失败。修复：降级链收敛为 base64 → http(s) url → 明确报错（非 http 的 url 直接判定为本地路径并说明分端限制，曾计划的入站段 url_hint 降级按用户决策砍除）；aiohttp.ClientError 包装为 RuntimeError。跨机取回文件内容的完整方案（Stream API）留二期。

**D6. `FileRef`/`FileData` frozen dataclass 放 `types.py`。** `FileRef(file_id, name="", size=0, url=None)`；`FileData(data: bytes, name: str, size: int)`。与既有模型同风格（frozen、平字段）。

**D7. `normalize_event` 收集规则：file 段必须有 `file_id` 才进 `files`。** 无 file_id 的段无法被 `fetch_file` 使用，收集了也是死引用；raw 恒透出兜底。`name`/`file_size`/`url` 尽力填充。

**D8. 官方前端 file 分支位置：`send()` 入口处判 `msg.file` → `log.warning` + return。** 与 image 的 URL-only 校验同层（adapter 自治），`OutMessage` 校验只管三选一，不掺平台语义。

## Risks / Trade-offs

- [`get_file` 的 base64 字段对部分文件类型可能不填充] → D4 降级链覆盖；真机冒烟覆盖 图片/文档 两类样本
- [file 段在群/私聊的渲染效果未实测] → 冒烟必测项；失败不影响文本/图片路径
- [16MB 全量进内存] → 上限即保护；超限场景二期 Stream API
- [`upload_group_file` 的 base64 源兼容性未知] → 真机 spike；文档按实测收敛
- [官方前端 handler 遇 file 全部静默丢弃（仅日志）] → spec 固化 + docstring 提示 handler 需容忍

## Migration Plan

纯新增能力，无数据迁移。实现后：NapCat 前端文件能力即用；切回官方前端文件场景自动降级。回滚 = revert 变更即可（模型字段新增均有默认值，老 handler 不受影响）。

## Open Questions

（无——Q1/Q2/Q3 已在探索期定案）
