# -*- coding: utf-8 -*-
"""平台前端抽象契约。框架核心（cea 包除 adapters 外）不 import 任何平台 SDK。

新增平台实现只需提供 run()/send() 两个方法：
- run(): 连接平台，收到事件归一化为 Message 后投递 inbound；
         同时（通常借助 pump_outbound）FIFO 消费 outbound 逐条调用 send()。
- send(): 把一条 OutMessage 发送到平台。
"""
import asyncio
from typing import Awaitable, Callable, Protocol

from cea.types import Message, OutMessage


class Adapter(Protocol):
    """平台前端契约（结构化类型，无需显式继承）。"""

    async def run(
        self,
        inbound: asyncio.Queue[Message],
        outbound: asyncio.Queue[OutMessage],
    ) -> None:
        """连接平台并双向搬运消息；随宿主任务取消而退出。"""
        ...

    async def send(self, msg: OutMessage) -> None:
        """发送一条出站消息。实现应自行捕获平台错误（记日志，不外抛）。"""
        ...


async def pump_outbound(
    outbound: asyncio.Queue[OutMessage],
    send: Callable[[OutMessage], Awaitable[None]],
) -> None:
    """发送总线通用消费者：按入队顺序（FIFO）逐条调用 send。

    随宿主任务取消而退出；send 的异常由实现方自行消化，此处不兜底。
    """
    while True:
        msg = await outbound.get()
        await send(msg)
