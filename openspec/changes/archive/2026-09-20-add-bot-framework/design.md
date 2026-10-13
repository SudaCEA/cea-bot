# Design: add-bot-framework

## Context

现有 `main.py` 直接依赖 qq-botpy 的 `botpy.Client`（v1.2.1，已装 .venv），事件回调与业务耦合。参考源码在 `.ref/botpy`。关键既有事实：`Client.start(appid, secret)` 是可 await 的协程入口；三类公域消息对象（Message/GroupMessage/C2CMessage）均有公开 `reply()`；群/C2C 富媒体接口仅接受 URL。

## Goals / Non-Goals

**Goals:**
- 单事件循环内完成：网关连接、双总线、分发、调度、优雅退出。
- 业务层只面对 `(bot, msg)` 纯函数与统一消息模型。
- 平台前端可替换（未来 OneBot/NapCat 等）。

**Non-Goals:**
- 不做多实例/多账号、分布式；不做消息持久化与重放。
- 不内置 markdown / ark / keyboard / embed（平台需申请权限，后议）。
- 不做群内保序（同会话串行化）——v1 消息并发处理，保序留待有需求再加。
- 不内置 Semaphore 等并发限制原语（用户决定：任务自行控制）。

## Decisions

1. **单事件循环，await `client.start()` 而非 `client.run()`**
   `run()` 只是 `asyncio.run(start())` 的包装；在自己 `asyncio.run(main())` 中 `await start()` 后，两条总线就是普通 `asyncio.Queue`，无需线程桥接（janus）或 `run_coroutine_threadsafe`。
   *备选*：botpy 跑独立线程 + 桥接队列——复杂度高，否决。
   *前置 spike*：验证 start 路径下心跳/重连正常（见 Risks）。

2. **Adapter 为 Protocol（结构化契约），不做继承树**
   `run(inbound, outbound)` + `send(OutMessage)` 两个方法即接入。框架核心不 import 任何平台 SDK；`cea/adapters/qqbotpy.py` 是唯一知道 qq-botpy 的地方。

3. **两个 frozen dataclass（Message/OutMessage），而非单一类型**
   收发字段差异明显（收方有 sender/at_me/raw，发方有 at/reply_to），两个类型让 IDE 与类型检查更准。`raw` 保留平台原始 payload 作逃生舱。

4. **text / image 互斥校验放框架层（出站入队前）**
   群聊侧图文混排受平台限制，v1 明确二选一并快速失败，避免"发了但平台行为不明"。

5. **Router + App：注册与运行分离**
   `Router` 只是装饰器收集器（纯数据），`App.include_router()` 合并路由表；`handlers/*.py` 每模块自建 `router`，`main.py` 组装。比全局单例干净，比 FastAPI 的依赖注入体系轻得多。

6. **命令匹配：首词精确匹配 + 空白切参**
   SDK 自带 `botpy.ext.command_util.Commands` 是子串模糊匹配（"ping" 会命中 "pingpong"），弃用。自实现：`text.split()` 首词 ∈ 触发词集 → `args = text.split()[1:]`。

7. **分发并发：每命中一次 `asyncio.create_task`**
   简单、隔离好。任务集持强引用 + done_callback 清理并记异常。同会话保序不在 v1（见 Non-Goals）。

8. **共享时钟：单协程秒级 tick**
   一个 `while True: await sleep(1)` 的 ticker，扫描 `{next_run, fn}` 表，到期即 `create_task(fn(bot))` 并推算下次触发；进程离线期间的触发点直接跳过（不补跑）。不引 APScheduler。

9. **Bot 是唯一的小类（dataclass 句柄）**
   `send() / create_task() / stopping` 三件事，本质是 outbound 队列与任务集的门面。框架其余全部函数式。`stopping` 用 `asyncio.Event`：协作式退出首选 `while not bot.stopping`，兜底 `cancel()`。

10. **msg_seq 计数器放 adapter 内部**
    以 `(chat_id, reply_to)` 为 key 的普通 dict 计数，5 分钟窗口过期条目惰性清理，业务层无感。

## Risks / Trade-offs

- [`client.start()` 在自有 loop 中行为未知] → 实施前先跑 10 行 spike：连接 + 心跳 + on_ready 观察 2 分钟；失败则降级为线程桥接方案（总线换线程安全队列）。
- [群 @ 的收发语法未确认]（收：content 前缀形态；发：`<@openid>` 是否渲染为 @）→ spike 2：真机群内实测，normalize 与 at 转换以实测为准。
- [主动消息月度额度极低（单群约 4 条）] → 框架不拦截，文档与 README 明示；定时任务设计者自行评估频控。
- [FIFO 单消费者发送吞吐有限] → 单 bot 场景足够；遇到瓶颈再在 adapter 内并行化（不破坏顺序语义的前提下较难，接受）。
- [异常静默] → 所有框架级任务挂 done_callback 落日志；日志走 botpy 的 get_logger。

## Migration Plan

1. 新建 `cea/` 包与 `handlers/basic.py`；旧 `main.py` echo 逻辑迁为 `handlers/basic.py` 的 `@router.on_message`。
2. `main.py` 收缩为：读配置 → 组装 adapter + App → include_router → `app.run()`。config.yaml 格式不变。
3. 回滚：旧单文件版保留在 git 历史中， revert 即可。

## Open Questions

- 命令触发词大小写敏感性：v1 定为敏感（精确匹配语义最简单），有需求再加 `fold_case` 参数。
- schedule 是否需要 "HH:MM" 之外的表达式（间隔/周几）：v1 只做每日 HH:MM，出现真实需求再扩。
