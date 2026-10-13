# add-napcat-adapter 提案

## Why

当前唯一平台前端是 QQ 官方开放平台（qq-botpy），存在两大硬限制：群/C2C 无法发送文件类消息（file_type=4 暂不开放），且主动消息受严格频控（群月度额度、频道每日 2 条），自动通知类功能落地困难。接入 NapCat（OneBot 11 协议的 NTQQ 客户端封装）作为第二平台前端，可消除这两项限制（文件收发可用、主动消息无平台频控），同时验证框架"换平台不改业务"的核心设计。

## What Changes

- 新增 `cea/adapters/napcat.py`：实现 Adapter 契约（run/send）的 NapCat 前端
  - 连接方式：正向 WebSocket（NapCat 作服务端），token 鉴权，消息格式固定使用 array
  - 入站归一化：OneBot `message.group` / `message.private` 事件 → 统一 `Message`（text/images/at_me/reply_to/raw）；文件等未建模消息段经 `raw` 逃生舱口透出
  - 出站转换：`OutMessage` → OneBot 消息段数组（reply 段置首 + at 段 + text/image 段），按 kind 路由 `send_group_msg` / `send_private_msg`；`echo` 关联请求与响应
  - image 字段接受 URL / 本地路径 / `base64://` 三态（NapCat 能力，与官方前端的 URL-only 形成对照）
  - 发送失败（status != ok / 连接异常）记日志不外抛，对齐现有前端约定
- `main.py` 组装与 `config.yaml` 扩展：新增 `adapter` 选择项（`qqofficial` | `napcat`）与 NapCat 参数（`ws_url` / `token`），未知值快速失败
- `types.py` 文档补充：`OutMessage.image` 语义说明（接受的形态随 adapter 能力）
- 明确不做（见 TODO.md）：文件类消息收发、文件上传/下载（`upload_group_file`、`get_file`、Stream API）、notice/request 事件族、多前端同时运行

## Capabilities

### New Capabilities

- `napcat-adapter`: NapCat（OneBot 11）平台前端——正向 WS 连接与鉴权、入站事件归一化、出站消息段转换、发送错误处理。

### Modified Capabilities

- `runtime`: 新增"前端装配选择"要求——应用按 config.yaml 的 `adapter` 项装配对应平台前端，未知值快速失败（原先装配关系为硬编码单一前端）。

## Impact

- **代码**：新增 `cea/adapters/napcat.py`（约 200 行级，含纯函数归一化/段构造以便单测）；`main.py` 装配分支；`cea/types.py` docstring；`config.example.yaml` / `config.yaml` 模板
- **依赖**：复用 botpy 已引入的 aiohttp（WS 客户端），无新依赖
- **spec**：新增 `openspec/specs/napcat-adapter/`；`openspec/specs/runtime/` 追加 requirement
- **测试**：`tests/` 新增归一化与段构造单测（纯函数），以及配置装配选择测试
- **风险**：真实 QQ 号存在风控（低频通知无碍）；NapCat 无频道会话（kind=channel 报错丢弃）；入站图片 URL 约 2 小时过期
