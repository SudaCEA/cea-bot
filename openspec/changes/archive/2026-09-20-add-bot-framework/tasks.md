# Tasks: add-bot-framework

## 1. Spike（动工前验证）

- [x] 1.1 Spike：自有事件循环中 `await client.start(appid, secret)` 连接网关，观察心跳/重连/on_ready 约 2 分钟；若异常，降级线程桥接方案并同步修订 design.md
- [x] 1.2 Spike：真机群内验证 @ 收发——入站 content 的 @ 前缀形态（normalize 剥离规则）、出站 `@` 语法的渲染效果；结论记录到 adapter 实现注释

## 2. 消息模型与总线（messaging）

- [x] 2.1 `cea/types.py`：frozen dataclass `User` / `Chat` / `Message` / `OutMessage`，出站 text/image 互斥校验（违规抛明确异常）
- [x] 2.2 `cea/adapters/base.py`：Adapter Protocol（`run(inbound, outbound)` + `send(OutMessage)`），框架核心零平台依赖

## 3. 框架核心（dispatching / runtime）

- [x] 3.1 `cea/app.py`：App 路由表与 `include_router`、inbound 分发循环（每命中一个 handler 即 create_task 并发、异常落日志不中断）
- [x] 3.2 Router 装饰器：`command(*触发词)`（首词精确匹配 + 空白切参）、`on_message()`、`schedule("HH:MM")`
- [x] 3.3 共享时钟协程：秒级 tick 扫描到期任务、到期 create_task 注入 bot、错过不补、执行可重叠
- [x] 3.4 `cea/bot.py`：Bot 句柄——`send()` 投递 outbound、`create_task(fn)`（注入 bot、强引用、done_callback 记异常）、`stopping` Event
- [x] 3.5 生命周期：`app.run()` 并发拉起 adapter/分发器/时钟，中断信号触发优雅退出（stopping 置位 → cancel → 限时等待）

## 4. QQ 官方前端（qqbot-adapter）

- [x] 4.1 `cea/adapters/qqbotpy.py` 接收侧：`client.start()` 接入，三类事件（群@/单聊/频道@）normalize——剥离 @ 前缀、images 保留 URL、reply_to 提取、投递 inbound
- [x] 4.2 发送侧：按会话类型路由平台接口；`(chat, reply_to)` → msg_seq 递增计数；@ 列表转平台语法；图片 URL 走富媒体接口（file_type=1），本地路径快速报错；平台错误（超时窗/频控）记日志不崩溃
- [x] 4.3 配置：config.yaml 读取 appid/secret，凭据缺失或占位时 fail-fast 并给出获取指引

## 5. 业务迁移

- [x] 5.1 `handlers/basic.py`：echo 迁为 `@router.on_message`，另加 `@router.command("ping")` 与 `@router.schedule` 示例各一个（作为用法范本）
- [x] 5.2 `main.py` 重构为组装入口：读配置 → 建 adapter 与 App → include_router → `app.run()`；config.yaml 格式不变

## 6. 验证

- [x] 6.1 单测（fake adapter / fake clock）：命令匹配与参数切分、text/image 互斥、msg_seq 递增、时钟到期触发与错过跳过、异常隔离
- [x] 6.2 真机冒烟：启动连网关、群 @ 文本回复、URL 图片回复、定时任务触发、Ctrl+C 优雅退出日志完整
