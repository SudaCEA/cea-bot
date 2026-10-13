# Proposal: add-bot-framework

## Why

当前骨架 `main.py` 是单文件 echo demo，业务逻辑、平台收发、事件分发耦合在一起，无法承载"响应命令 + 主动发消息"的机器人能力扩展。需要一套轻量框架把平台接入抽象掉，让业务以纯函数形式挂载。

## What Changes

- 新增 `cea/` 框架包（预计 300~400 行，仅用 asyncio 标准库，零新依赖）：
  - 统一消息模型：仅保留 文本 / 图像 / @ / 回复 四类收发能力（frozen dataclass）。
  - 双总线：`inbound` / `outbound` 两条 `asyncio.Queue`；平台前端消费 outbound，后端分发器消费 inbound。
  - 平台前端抽象：`Adapter` Protocol，官方 SDK（qq-botpy）实现为首个 adapter；单事件循环（`await client.start(...)` 嵌入自有 loop）。
  - FastAPI 风格注册：`Router` + 装饰器（`@router.command` / `@router.on_message` / `@router.schedule`），`app.include_router()` 挂载。
  - `Bot` 句柄注入 handler：`bot.send()` 主动发消息（统一走 outbound 总线）、`bot.create_task(fn)` 注入 bot 实例跑常驻协程、`bot.stopping` 优雅退出信号。
  - 共享时钟调度：`@router.schedule(rule)` 单时钟协程，到期并发触发，错过不补；**不内置 Semaphore**，并发限制由用户任务自行处理。
- 重构 `main.py` 为组装入口：选 adapter、include_router、`app.run()`；echo 逻辑迁入 `handlers/basic.py`。
- 函数式优先：handler 全部为 `async def (bot, msg)` 纯函数，`Bot` 是唯一的小类（句柄，不含框架逻辑）。

## Capabilities

### New Capabilities

- `messaging`: 统一消息模型与双向总线——Message/OutMessage/Chat/User 数据结构、inbound/outbound 总线语义、Adapter 抽象契约（normalize 收、逐条发）。
- `dispatching`: 事件分发——Router 装饰器注册、command 精确前缀匹配与参数切分、handler 并发执行与异常隔离。
- `runtime`: Bot 运行时——Bot 句柄（send/create_task/stopping）、共享时钟 schedule 语义、优雅退出流程。
- `qqbot-adapter`: QQ 官方平台前端——qq-botpy 接入、事件 normalize 规则、发送消息转换与平台限制（被动回复 5 分钟窗口、群/C2C 图片仅 URL、主动消息频控交由调用方感知）。

### Modified Capabilities

（无——首次建立框架能力，`openspec/specs/` 下尚无既有能力。）

## Impact

- **代码**：新增 `cea/` 包与 `handlers/` 目录；`main.py` 重构为组装入口（现有 echo 行为保留）。
- **依赖**：无新增（asyncio / dataclasses 标准库；qq-botpy 已装）。
- **风险点（design 中展开）**：`client.start()` 嵌入自有 loop 需 spike 验证；群 @ 的收发语法需确认；主动消息平台额度低，仅作能力预留。
