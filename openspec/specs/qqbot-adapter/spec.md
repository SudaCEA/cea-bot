# qqbot-adapter Specification

## Purpose

基于官方 qq-botpy SDK 实现 QQ 平台前端：完成统一消息模型与 QQ 平台事件/接口之间的双向转换，并明确平台限制。

## Requirements

### Requirement: 平台接入
QQ 前端 SHALL 使用官方 qq-botpy SDK，以 AppID + AppSecret 鉴权，连接 WebSocket 网关并维持心跳；凭据 SHALL 从 config.yaml 读取；SDK 版本 SHALL 与已安装版本（1.2.1）一致。

#### Scenario: 凭据缺失快速失败
- **WHEN** config.yaml 中凭据为占位符或缺失
- **THEN** 启动即报错退出，并提示获取凭据的地址与步骤

### Requirement: 事件归一化
QQ 前端 SHALL 将群 @ 消息、单聊消息、频道 @ 消息归一化为统一消息对象，会话类型分别映射为 group、c2c、channel；@ 机器人前缀 SHALL 从文本中剥离；图片消息 URL SHALL 保留到 images 字段。

#### Scenario: 群 @ 文本
- **WHEN** 收到群内 @ 机器人的文本消息事件
- **THEN** 生成会话类型 group 的统一消息，text 为剥离 @ 后内容

#### Scenario: 单聊消息
- **WHEN** 收到用户单聊消息事件
- **THEN** 生成会话类型 c2c 的统一消息

#### Scenario: 频道 @ 消息
- **WHEN** 收到频道内 @ 机器人消息事件
- **THEN** 生成会话类型 channel 的统一消息

### Requirement: 发送转换
出站消息 SHALL 按会话类型路由到对应平台接口（群/C2C/频道）；携带 reply_to 时 SHALL 作为被动回复传递消息 ID，且对同一 reply_to 的第 n 次发送 SHALL 递增使用消息序号（msg_seq=n）；@ 列表 SHALL 转换为平台 @ 语法后并入内容；图片 SHALL 先经富媒体文件接口上传再随消息发送。

#### Scenario: 被动回复序号递增
- **WHEN** 对同一条入站消息连续回复两次
- **THEN** 两次发送的 msg_seq 分别为 1 与 2，均被平台接受

#### Scenario: 图片出站走富媒体接口
- **WHEN** 出站消息携带图片 URL
- **THEN** 前端先上传/登记富媒体，再发送携带该媒体的消息

### Requirement: 平台限制处理
图片出站 SHALL 仅接受 URL（群/C2C 平台限制），本地路径 SHALL 触发明确报错提示；平台发送失败（含被动回复超出 5 分钟窗口、主动消息频控）SHALL 记录日志且不导致进程崩溃。

#### Scenario: 本地图片路径报错
- **WHEN** 出站消息的 image 为本地文件路径
- **THEN** 抛出明确异常说明群/C2C 图片仅支持 URL

#### Scenario: 发送失败不崩溃
- **WHEN** 平台接口返回错误（如回复窗口已过）
- **THEN** 错误被记录到日志，框架继续处理后续消息

### Requirement: 文件能力缺失处理
QQ 官方前端 SHALL 对文件能力显式降级：文件出站 SHALL 记警告日志并丢弃（不发起平台调用）；入站文件的 files 字段 SHALL 恒为空（文件段仅存在于原始事件）；文件下载能力 SHALL 未实现（能力探测失败，业务层得到"平台不支持"错误）。降级行为 SHALL 不影响文本与图片的正常收发。

#### Scenario: 文件出站丢弃
- **WHEN** 出站消息携带文件源
- **THEN** 记录警告日志并丢弃，不调用平台接口

#### Scenario: 入站无文件字段
- **WHEN** 收到任何入站消息
- **THEN** files 字段恒为空，文件信息仅可经原始事件访问

#### Scenario: 文件句柄不可用
- **WHEN** 业务层在官方前端下调用文件下载
- **THEN** 得到明确的"平台不支持"错误
