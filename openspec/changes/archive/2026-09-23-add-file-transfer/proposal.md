# add-file-transfer 提案

## Why

通知/归档场景需要传输文件（文档、报表、截图附件等），但当前框架文件能力为零：入站文件段仅经 `raw` 逃生舱口透出，出站无文件通道；QQ 官方平台文件类型暂不开放（file_type=4），NapCat 则具备完整文件能力（`file` 消息段收/发、群文件系统上传、`get_file` 下载）。本变更把文件传输建模为一等能力，NapCat 全量落地、QQ 官方前端按既定"warning + 丢弃"策略显式降级。

## What Changes

- **模型（cea/types.py）**
  - `OutMessage`：text / image / **file** 三选一互斥（校验升级）；`file` 为文件源引用（URL / base64:// / NapCat 本地路径），可附文件名
  - `Message`：新增 `files: tuple[FileRef, ...]`——入站文件段一等建模（`FileRef`：file_id、name、size、url?，frozen）
  - 新增 `FileData`（bytes 内容 + name + size）作为下载结果
- **能力句柄（cea/bot.py + cea/app.py）**
  - `Bot.fetch_file(ref) -> FileData`：下载文件内容，统一 **16MB** 大小上限（超限明确报错）
  - 能力注入：App 装配时按鸭子类型探测 adapter 是否实现 `fetch_file`，注入 Bot 可选字段；未注入时调用得到明确"平台不支持"错误
- **NapCat 前端（cea/adapters/napcat.py）**
  - 出站：file 段构造（含 name 透传）；入站：file 段收集为 `FileRef`（file_id 必备）
  - `fetch_file`：`get_file(file_id)` → base64 字段优先、http(s) url 字段降级；**已知限制**：分端部署时可能仅返回 NapCat 本地路径，明确报错不做跨机取回（真机实证后的决策）
- **QQ 官方前端（cea/adapters/qqbotpy.py）**
  - file 出站 → log.warning + 丢弃；`files` 恒空；不实现文件句柄（探测自然失败）
- **明确不做**：`notice.group_upload` 事件建模（Q3）；**群文件系统上传 `upload_group_file`**（Q1 曾纳入，真机观察 file 消息本身会同步出现在群文件列表，独立上传无增益 → 移除）；Stream API 留二期

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `messaging`：「统一接收消息模型」新增 files 字段与入站文件归一化场景；「统一发送消息模型」由 text/image 互斥升级为三选一互斥，新增文件出站场景
- `runtime`：新增「文件传输句柄」要求——fetch_file 下载（16MB 上限）、能力注入与"平台不支持"语义
- `napcat-adapter`：新增「文件消息收发」（file 段双向转换）与「文件下载」（get_file base64 优先 / http(s) URL 降级 / 分端部署明确报错）两组要求
- `qqbot-adapter`：新增「文件能力缺失处理」要求——固化 warning+丢弃 / files 恒空 / 无文件句柄行为

## Impact

- **代码**：`cea/types.py`（FileRef/FileData/两模型扩展）、`cea/bot.py`（两方法 + 可选注入字段）、`cea/app.py`（能力探测）、`cea/adapters/napcat.py`（三处扩展）、`cea/adapters/qqbotpy.py`（file 分支）、`tests/`（模型互斥/句柄注入/归一化/段构造/超限）
- **依赖**：无新增（HTTP 下载降级复用 adapter 已有 aiohttp session）
- **spec**：`messaging` 2 条 requirement 修订，`runtime`/`napcat-adapter`/`qqbot-adapter` 各追加 requirement
- **风险**：file 段渲染效果未实测（冒烟必测）；`get_file` 的 base64 字段对部分文件类型可能不填充（设计含 url 降级）；跨机本地路径不可用（已实证，文档与红线覆盖）；QQ 官方前端文件场景全部降级（handler 需容忍）
