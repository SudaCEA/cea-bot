## ADDED Requirements

### Requirement: 文件消息收发
NapCat 前端 SHALL 将出站消息中的文件源引用转换为 `file` 消息段发送（文件名 SHALL 随段透传）；入站消息事件中的 `file` 段 SHALL 收集为文件引用（文件 ID、文件名、大小、可选 URL）填充统一消息的 files 字段；缺少文件 ID 的文件段 SHALL 不进入 files 但保留于原始事件。

#### Scenario: 文件出站段构造
- **WHEN** 出站消息仅含文件源引用（可附文件名）
- **THEN** 转换为 `file` 段并经对应会话类型的发送接口发出

#### Scenario: 入站文件收集
- **WHEN** 收到的消息段中存在携带文件 ID 的文件段
- **THEN** 统一消息的 files 字段包含该文件引用

#### Scenario: 缺文件 ID 透出
- **WHEN** 收到的文件段缺少文件 ID
- **THEN** 该段不进入 files 字段，完整事件仍可经 raw 访问

### Requirement: 文件下载
NapCat 前端 SHALL 实现文件下载：以文件 ID 调用 `get_file`，响应含 base64 字段时 SHALL 优先解码返回；否则响应含 http(s) 下载 URL 时 SHALL 下载后返回；否则 SHALL 抛出带原因的明确错误（含 get_file 仅返回 NapCat 本地路径的分端部署场景）；内容超过 16MB 上限 SHALL 报超限错误；HTTP 层异常 SHALL 包装为带原因的明确错误而非穿透。

#### Scenario: base64 下载
- **WHEN** `get_file` 响应包含 base64 字段且未超限
- **THEN** 解码后返回文件字节内容与元信息

#### Scenario: URL 降级下载
- **WHEN** `get_file` 响应无 base64 字段但含 http(s) 下载 URL
- **THEN** 前端经 HTTP 下载该 URL 并返回文件字节内容与元信息

#### Scenario: 分端部署本地路径明确报错
- **WHEN** `get_file` 响应无 base64 字段且 url 为 NapCat 本地路径（非 http）
- **THEN** 抛出说明分端部署限制的明确错误，不将本地路径误作 URL 请求

#### Scenario: 下载失败明确报错
- **WHEN** `get_file` 失败、HTTP 下载出错或内容超限
- **THEN** 抛出带原因的明确错误，不影响其他消息处理
