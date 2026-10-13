# add-napcat-adapter 设计

## Context

框架侧（见 `openspec/specs/`）：Adapter 契约仅 `run(inbound, outbound)` + `send(OutMessage)` 两方法（结构化 Protocol）；`base.py` 提供 `pump_outbound` 通用发送消费者；qqbotpy 前端确立了"平台错误记日志不外抛、平台限制自查自报"的先例。运行时为单事件循环 asyncio，botpy 已引入 aiohttp（含 WS 客户端能力）。

NapCat 侧（调研结论，源 `.ref/NapCatDocs`）：OneBot 11 协议。正向 WS（NapCat 作服务端）单连接双工——事件帧（无 `echo`）与 API 响应帧（有 `echo`，`{status, retcode, data, echo}`）共用通道；API 调用帧为 `{action, params, echo}`。消息格式配置为 array（消息段结构化数组）。`message.group` / `message.private` 事件字段：`message_id`(int)、`user_id`、`group_id`、`self_id`、`sender{user_id, nickname, card, role}`、`message`(段数组)、`raw_message`。

## Goals / Non-Goals

**Goals:**

- 单一 NapCat 前端覆盖 message.group / message.private 的收与发（text / image / at / reply 四类建模段）
- 图片源三态（URL / 本地路径 / base64://）与官方前端 URL-only 形成能力对照，模型零改动
- 配置化前端装配，官方/NapCat 可互换且向后兼容
- 归一化与段构造实现为纯函数，单测可不依赖真实 WS

**Non-Goals:**

- 文件类消息收发、文件上传/下载（`upload_group_file` / `get_file` / Stream API）——记于 TODO.md，未来独立变更
- notice / request 事件族、合并转发、markdown、语音视频收发
- 多前端同时运行；发送节流/风控对策（沿用框架"不内置限流原语"哲学，注释提示自行实现）
- 频道会话（NapCat 不存在，kind=channel 拒绝即可）

## Decisions

**D1. 正向 WS（我方作客户端）而非反向 WS / HTTP。** 单连接双工收事件+调 API，无需自建 ws/http 服务器，最贴合 `run()` 单协程契约。反向 WS 需宿主起服务器，HTTP 需"上报+调用"双通道。备选均更重，无收益。

**D2. aiohttp `ws_connect`，不引入 websockets 库。** botpy 依赖树已含 aiohttp，零新增依赖。

**D3. `send()` 内 await 响应（带超时）而非 fire-and-forget。** pump 逐条 await `send()`，天然形成"发一条确认一条"的 FIFO 节流；失败可知可记日志（spec 要求）。echo 用自增整数，`dict[echo, Future]` 关联。超时（30s）按失败记日志丢弃。备选 fire-and-forget 无法感知失败，违背 spec。

**D4. 消息格式固定 array。** 依赖 NapCat 侧 `messagePostFormat: "array"` 配置（接入文档写明）；string 格式需解析 CQ 码，放弃。

**D5. at_me 判定用事件自带 `self_id` 与 @ 段 `qq` 比对。** 无需 `get_login_info` 预查；连接后首个事件起即自洽。

**D6. 消息 ID 全程字符串化。** 入站 `str(message_id)` 进统一模型；出站 reply 段 `data.id` 直接传字符串（NapCat 段定义即字符串型）。消除 int/str 边界转换（对齐官方前端 msg_seq 类的平台细节内化原则）。

**D7. 断线重连：`run()` 内 while 循环，5 秒间隔，直至 `stopping` 置位。** 重连期间 `send()` 若无可用连接则等待（复用停止信号 + 连接就绪事件），等待超时按失败丢弃记日志——不静默、不崩溃、不丢序（队列顺序始终由 pump 保证）。

**D8. token 鉴权默认走 `Authorization: Bearer <token>` 请求头。** OneBot 11 亦支持 `?access_token=` 查询参数，作为实测不通时的回退（实现注释注明）。

**D9. 装配工厂放 `main.py`：`build_adapter(cfg) -> Adapter`。** `adapter` 键缺省 `"qqofficial"`；`napcat` 分支校验 `ws_url` 必填、`token` 可选；未知值 `sys.exit` 并列可选值。凭据校验随分支走（选 napcat 时不再要求 appid/secret）。

## Risks / Trade-offs

- [真实 QQ 号风控（高频发消息可能冻结）] → 低频通知场景无碍；模块 docstring 注明风险与"需要限流请自行实现"；不做内置节流
- [bot 与 NapCat 跨机部署时本地路径不可达] → 接入文档注明跨机用 base64:// 或 URL；文件传输变更时再统一解决
- [入站图片 URL 约 2 小时过期] → 只影响"缓存 URL 择日转发"玩法；即时转发不受影响；`raw` 中保留 file_id 供后续 `get_image` 刷新
- [echo 响应依赖 NapCat 行为（偶发不回包）] → 30s 超时兜底，失败记日志不影响总线
- [array 格式依赖 NapCat 配置正确] → 接入文档作为前置步骤写明；格式错配时文本段缺失会表现为空 text，日志可见

## Migration Plan

1. NapCat 侧（一次性）：WebUI → 网络配置 → 新建 WS 服务端（host/port、启用 token、`messagePostFormat: array`）
2. 本仓库：`config.yaml` 增加 `adapter: napcat` + `ws_url` / `token`；启动后以 `ping` / `echo` 命令真机验证收发
3. 回滚：`adapter` 改回 `qqofficial`（或删除该键）即恢复原路径，无数据迁移

## Open Questions

- reply 段 `data.id` 传字符串是否在所有 NapCat 版本被接受（段文档为字符串型，预计可用；若实测需要 int，仅需在出站转换处加一步转换，不影响 spec）
