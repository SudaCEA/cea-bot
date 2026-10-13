## Purpose

基于 OneBot 11 协议对接 NapCat（NTQQ 客户端封装），提供群聊与单聊的消息收发前端，使业务层零改动即可运行于 NapCat 平台，并获得本地图片直发与无频控主动消息能力。

## ADDED Requirements

### Requirement: 连接与鉴权
NapCat 前端 SHALL 以 WebSocket 客户端连接配置的正向 WS 地址；配置了 token 时 SHALL 携带鉴权凭据；连接建立后 SHALL 持续接收数据帧直至停止信号置位；连接异常中断时 SHALL 按固定间隔自动重连。

#### Scenario: 正常连接
- **WHEN** NapCat 服务端在配置地址可用且 token 匹配
- **THEN** 前端建立 WebSocket 连接并开始接收事件

#### Scenario: 断线重连
- **WHEN** WebSocket 连接异常断开且未收到停止信号
- **THEN** 前端按固定间隔重试连接直至恢复

### Requirement: 入站消息归一化
NapCat 前端 SHALL 将群聊消息事件与私聊消息事件归一化为统一消息对象：会话类型分别映射为 group 与 c2c，会话 ID 与发送者 ID SHALL 为 QQ 号字符串；发送者名称群聊 SHALL 优先群名片、私聊 SHALL 取昵称；纯文本 SHALL 由文本段拼接；图片 SHALL 保留平台可访问 URL 列表；是否 @ 机器人 SHALL 依据 @ 段指向自身账号判定；被引用消息 ID SHALL 取自引用段；统一消息 SHALL 保留原始事件作逃生舱口。元事件（心跳/生命周期）SHALL 被忽略，不产生统一消息。

#### Scenario: 群聊文本消息
- **WHEN** 收到群聊消息事件且消息段仅含文本段
- **THEN** 业务层收到会话类型 group 的统一消息，text 为各文本段拼接结果

#### Scenario: 群聊 @ 机器人
- **WHEN** 消息段中存在指向机器人自身 QQ 号的 @ 段
- **THEN** 归一化消息的 at_me 为真

#### Scenario: 图片消息
- **WHEN** 消息段中存在携带平台 URL 的图片段
- **THEN** 归一化消息的 images 包含该 URL

#### Scenario: 引用回复消息
- **WHEN** 消息段中存在引用段
- **THEN** 归一化消息的 reply_to 为被引用消息的 ID

#### Scenario: 心跳帧忽略
- **WHEN** 收到心跳或生命周期元事件帧
- **THEN** 不产生任何统一消息投递

#### Scenario: 未建模消息段透出
- **WHEN** 收到的消息段超出文本/图片/@/引用的建模范围（如文件段）
- **THEN** 统一消息仍正常生成，完整原始事件可经 raw 字段访问

### Requirement: 出站消息转换
NapCat 前端 SHALL 将统一出站消息转换为消息段数组发送：携带 reply_to 时引用段 SHALL 置于段数组首位；@ 列表 SHALL 逐个转换为 @ 段；图片 SHALL 转换为图片段，且图片源 SHALL 接受 URL、本地路径与 base64:// 三种形态；会话类型 group 与 c2c SHALL 分别路由至群消息与私聊消息发送接口；会话类型 channel SHALL 因平台不支持而记日志丢弃。

#### Scenario: 文本出站
- **WHEN** 出站消息仅含文本
- **THEN** 转换为仅含文本段的消息并经对应会话类型的发送接口发出

#### Scenario: 被动回复置首
- **WHEN** 出站消息携带 reply_to
- **THEN** 引用段位于消息段数组首位，其余段随后

#### Scenario: 本地图片出站
- **WHEN** 出站消息的图片源为本地文件路径
- **THEN** 图片段直接携带该路径并由 NapCat 读取发送

#### Scenario: 频道会话拒绝
- **WHEN** 出站消息的目标会话类型为 channel
- **THEN** 该消息被记日志丢弃，后续消息不受影响

### Requirement: 请求关联与错误处理
NapCat 前端对每个 API 调用 SHALL 携带唯一请求标识并按标识关联响应；响应指示失败时 SHALL 记录日志且不阻断总线中后续消息；发送路径异常 SHALL 记日志不外抛。

#### Scenario: 发送失败不崩溃
- **WHEN** 某条消息的发送接口返回失败（如目标会话不存在）
- **THEN** 失败被记录到日志，后续消息照常按序发送

#### Scenario: 在途请求正确配对
- **WHEN** 同时存在多个在途 API 调用
- **THEN** 各响应按请求标识正确配对，互不串扰
