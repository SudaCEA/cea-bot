# -*- coding: utf-8 -*-
"""Bot 句柄：handler 与常驻任务访问框架能力的唯一入口。

设计约定：
- send() 只投递 outbound 总线，不做平台细节；
- fetch_file() 为请求-响应语义：异常抛给调用方（与 send 的总线语义不同）。能力由
  App 装配时从平台前端注入（鸭子类型探测），前端未实现时抛 NotImplementedError
  表示"平台不支持"；文件大小受 MAX_FILE_BYTES 统一保护；
- create_task() 注入 bot 实例，强引用持有至结束；不内置并发限制原语（Semaphore 等），
  需要限流的任务请自行实现；
- stopping 是协作式退出信号，优雅退出时框架会先置位再 cancel 兜底。
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any

from cea.types import Chat, FileData, FileRef, OutMessage

log = logging.getLogger("cea")

MAX_FILE_BYTES = 16 * 1024 * 1024  # 文件传输统一大小上限（16MB）


class FileTooLargeError(ValueError):
    """文件超过 MAX_FILE_BYTES 传输上限。"""


def log_done(task: asyncio.Task) -> None:
    """统一的任务收尾回调：被取消视为正常；异常落日志。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("任务 %s 异常退出：%r", task.get_name(), exc, exc_info=exc)


@dataclass
class Bot:
    """轻量句柄（框架中唯一的小类），不承载路由/分发等框架逻辑。"""

    outbound: asyncio.Queue[OutMessage]
    tasks: set[asyncio.Task]
    stopping: asyncio.Event
    # 文件能力注入字段：由 App 装配时探测 adapter 后填充；None 即"平台不支持"
    file_fetcher: Callable[[str], Awaitable[FileData]] | None = None

    async def send(
        self,
        chat: Chat,
        *,
        text: str = "",
        image: str | None = None,
        file: str | None = None,
        file_name: str = "",
        at: Sequence[str] = (),
        reply_to: str | None = None,
    ) -> None:
        """发送消息：被动回复传 reply_to，主动消息不传。

        协程形式（handler 统一 `await bot.send(...)`），但内部仅做队列投递
        （put_nowait），立即返回、不阻塞事件循环；实际平台调用由前端异步完成。
        """
        self.outbound.put_nowait(
            OutMessage(
                chat=chat,
                text=text,
                image=image,
                file=file,
                file_name=file_name,
                at=tuple(at),
                reply_to=reply_to,
            )
        )

    async def fetch_file(self, ref: FileRef | str) -> FileData:
        """下载文件内容：入参为文件引用或文件 ID，返回 FileData。

        超过 MAX_FILE_BYTES 抛 FileTooLargeError；平台不支持抛 NotImplementedError；
        平台调用失败抛 RuntimeError。
        """
        if self.file_fetcher is None:
            raise NotImplementedError("当前平台前端不支持文件下载")
        file_id = ref.file_id if isinstance(ref, FileRef) else ref
        data = await self.file_fetcher(file_id)
        if len(data.data) > MAX_FILE_BYTES or data.size > MAX_FILE_BYTES:
            raise FileTooLargeError(f"文件超过 {MAX_FILE_BYTES // 1024 // 1024}MB 上限")
        return data

    def create_task(
        self, fn: Callable[["Bot"], Coroutine[Any, Any, None]], *, name: str | None = None
    ) -> asyncio.Task:
        """启动常驻协程：fn 收到 bot 实例，持续运行至 stopping 置位或被取消。"""

        async def runner() -> None:
            await fn(self)

        task = asyncio.create_task(runner(), name=name or getattr(fn, "__name__", "bot-task"))
        self.tasks.add(task)  # 强引用，防止任务被 GC
        task.add_done_callback(log_done)
        return task
