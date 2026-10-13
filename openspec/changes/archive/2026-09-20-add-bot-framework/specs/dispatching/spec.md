# Purpose

定义 FastAPI 风格的事件注册与分发行为：业务以装饰器声明式挂载，框架负责匹配、并发与异常隔离。

## ADDED Requirements

### Requirement: 装饰器注册
框架 SHALL 提供三类路由装饰器：命令注册（支持一个 handler 绑定多个触发词别名）、全量消息注册（接收所有消息）、定时任务注册； SHALL 支持将多个路由集合（Router）按序挂载到应用，挂载顺序决定匹配优先级。

#### Scenario: 挂载多个路由集合
- **WHEN** 应用挂载多个 Router 并启动
- **THEN** 所有 Router 中注册的 handler 均生效

### Requirement: 命令精确匹配
命令 SHALL 对"剥离 @ 后文本的第一个词"做精确匹配（不做子串模糊匹配）；匹配成功的命令 handler SHALL 收到剩余文本按空白切分后的参数列表；无命中的消息 SHALL 不触发任何命令 handler。

#### Scenario: 命令与参数切分
- **WHEN** 用户发送 "@bot ping hello world"
- **THEN** ping 命令 handler 被调用，参数列表为 ["hello", "world"]

#### Scenario: 触发词别名
- **WHEN** 命令注册了 "帮助" 与 "help" 两个触发词，用户发送 "help"
- **THEN** 该命令 handler 被调用

#### Scenario: 未命中不触发
- **WHEN** 消息首词不匹配任何注册命令
- **THEN** 不调用任何命令 handler（全量消息 handler 仍可收到该消息）

### Requirement: 并发执行与异常隔离
每条消息命中的每个 handler SHALL 以独立协程并发执行，相互不阻塞；单个 handler 的异常 SHALL 被记录且不影响其他 handler 与框架继续运行。

#### Scenario: 异常隔离
- **WHEN** 某个 handler 执行时抛出异常
- **THEN** 异常被记录到日志，后续消息仍被正常分发处理

#### Scenario: 并发不阻塞
- **WHEN** 同一条消息命中多个 handler，其中一个为长耗时协程
- **THEN** 其余 handler 不等待其完成即开始执行

### Requirement: 处理函数签名
handler SHALL 接收两个参数：bot 句柄与统一消息对象；命令 handler 额外接收参数列表。

#### Scenario: 命令 handler 注入
- **WHEN** 命令消息命中 handler
- **THEN** handler 收到 bot 句柄、统一消息对象与参数列表
