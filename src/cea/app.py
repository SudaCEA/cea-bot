# -*- coding: utf-8 -*-
"""应用与分发核心：路由注册、inbound 分发、共享时钟、生命周期。

均为可独立测试的纯函数 + 一个 App 组合根；不含任何平台依赖。
"""
import asyncio
import logging
from collections.abc import Callable, Collection, Coroutine
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cea.adapters.base import Adapter
from cea.bot import Bot, log_done
from cea.types import Message, OutMessage

log = logging.getLogger("cea")

Handler = Callable[..., Coroutine[Any, Any, None]]


@dataclass
class Job:
    """定时任务：每日 hh:mm 触发一次（本地时区）。"""

    fn: Callable[[Bot], Coroutine[Any, Any, None]]
    hh: int
    mm: int
    last_key: tuple[int, int, int, int, int] | None = None  # 最近一次触发的 (年,月,日,时,分)


class Router:
    """装饰器路由收集器：只存数据，不做运行逻辑；由 App.include_router 挂载。"""

    def __init__(self) -> None:
        self.commands: list[tuple[tuple[str, ...], Handler]] = []
        self.message_handlers: list[Handler] = []
        self.jobs: list[Job] = []

    def command(self, *words: str) -> Callable[[Handler], Handler]:
        """注册命令 handler：绑定一个或多个触发词别名。"""
        if not words:
            raise ValueError("command 至少需要一个触发词")

        def deco(fn: Handler) -> Handler:
            self.commands.append((tuple(words), fn))
            return fn

        return deco

    def on_message(self) -> Callable[[Handler], Handler]:
        """注册全量消息 handler：每条入站消息都会收到。"""

        def deco(fn: Handler) -> Handler:
            self.message_handlers.append(fn)
            return fn

        return deco

    def schedule(self, rule: str) -> Callable[[Handler], Handler]:
        """注册定时任务：rule 为每日 "HH:MM"（本地时区）。"""
        hh, mm = parse_hhmm(rule)

        def deco(fn: Handler) -> Handler:
            self.jobs.append(Job(fn=fn, hh=hh, mm=mm))
            return fn

        return deco


def parse_hhmm(rule: str) -> tuple[int, int]:
    parts = rule.split(":")
    if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        raise ValueError(f'schedule 规则应为 "HH:MM"，收到：{rule!r}')
    hh, mm = int(parts[0]), int(parts[1])
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise ValueError(f"schedule 规则超出范围：{rule!r}")
    return hh, mm


def match_commands(routers: list[Router], text: str) -> tuple[Handler, list[str]] | None:
    """命令匹配：对剥离 @ 后文本的首词做精确匹配（无子串模糊）。

    按路由挂载顺序取第一个命中；返回 (handler, 参数列表) 或 None。
    """
    if not text:
        return None
    parts = text.split()
    if not parts:
        return None
    head, args = parts[0], parts[1:]
    for router in routers:
        for words, fn in router.commands:
            if head in words:
                return fn, args
    return None


def due_jobs(jobs: list[Job], now: datetime) -> list[Job]:
    """返回 now 时刻到期的任务并标记触发时间。

    - 同一分钟内只触发一次；
    - 进程离线错过的触发点不补跑（恢复后时间已过，自然跳过）；
    - 到期即触发，不检查上一次是否结束（允许执行重叠）。
    """
    key = (now.year, now.month, now.day, now.hour, now.minute)
    fired: list[Job] = []
    for job in jobs:
        if job.hh == now.hour and job.mm == now.minute and job.last_key != key:
            job.last_key = key
            fired.append(job)
    return fired


class App:
    """组合根：持有两条总线、路由表与运行时任务集。"""

    def __init__(
        self, adapter: Adapter | None, group_whitelist: Collection[str] | None = None
    ) -> None:
        # adapter=None 仅用于测试（无平台前端时只跑分发器与时钟）
        self.adapter = adapter
        # 群白名单：None/空集 = 不过滤（兼容既有行为）；仅约束 kind=group
        self._group_whitelist: frozenset[str] = frozenset(group_whitelist) if group_whitelist else frozenset()
        self.inbound: asyncio.Queue[Message] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutMessage] = asyncio.Queue()
        self.stopping = asyncio.Event()
        self._routers: list[Router] = []
        self._tasks: set[asyncio.Task] = set()
        self.bot = Bot(outbound=self.outbound, tasks=self._tasks, stopping=self.stopping)
        # 文件能力注入：鸭子类型探测，前端实现则绑定（否则调用得到"平台不支持"）
        if (fetcher := getattr(adapter, "fetch_file", None)) is not None:
            self.bot.file_fetcher = fetcher

    # ---- 注册 ----

    def include_router(self, router: Router) -> None:
        self._routers.append(router)

    @property
    def _jobs(self) -> list[Job]:
        return [job for router in self._routers for job in router.jobs]

    # ---- 运行 ----

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(log_done)
        return task

    def _filtered(self, msg: Message) -> bool:
        """群白名单过滤：非白名单群消息在分发前丢弃（debug 日志）。"""
        if not self._group_whitelist or msg.chat.kind != "group":
            return False
        if msg.chat.id in self._group_whitelist:
            return False
        log.debug("消息来自非白名单群 %s，已丢弃", msg.chat.id)
        return True

    async def _dispatch(self) -> None:
        """inbound 分发循环：每命中一个 handler 即独立协程并发执行。"""
        log.info("分发器启动")
        while True:  # 退出依赖任务取消（_shutdown 统一 cancel）
            msg = await self.inbound.get()
            if self._filtered(msg):
                continue
            for router in self._routers:
                for fn in router.message_handlers:
                    self._spawn(fn(self.bot, msg))
            hit = match_commands(self._routers, msg.text)
            if hit is not None:
                fn, args = hit
                self._spawn(fn(self.bot, msg, args))

    async def _clock(self, tick: float = 1.0) -> None:
        """共享时钟：单协程秒级 tick，到期任务并发启动（错过不补）。"""
        log.info("共享时钟启动")
        while True:
            for job in due_jobs(self._jobs, datetime.now()):
                log.info("定时任务到期：%s", getattr(job.fn, "__name__", job.fn))
                self._spawn(job.fn(self.bot))
            await asyncio.sleep(tick)

    async def run(self) -> None:
        """启动应用：adapter（收发）+ 分发器 + 时钟，直到中断信号。"""
        if not logging.getLogger().handlers:
            logging.basicConfig(
                level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            )
        if self.adapter is None:
            log.warning("未配置 adapter（测试模式），仅运行分发器与时钟")
        else:
            self._spawn(self.adapter.run(self.inbound, self.outbound))
        self._spawn(self._dispatch())
        self._spawn(self._clock())
        log.info("应用启动，共 %d 个命令 / %d 个消息处理器 / %d 个定时任务",
                 sum(len(r.commands) for r in self._routers),
                 sum(len(r.message_handlers) for r in self._routers),
                 len(self._jobs))

        interrupted = False
        try:
            await asyncio.gather(*list(self._tasks))
        except asyncio.CancelledError:  # Ctrl+C：asyncio.run 取消主任务
            interrupted = True
        except KeyboardInterrupt:
            interrupted = True
        finally:
            await self._shutdown()
        if interrupted:
            raise KeyboardInterrupt

    async def _shutdown(self) -> None:
        """优雅退出：置位停止信号 → 明确丢弃未发消息 → cancel 全部任务 → 限时等待。"""
        if self.stopping.is_set():
            return
        self.stopping.set()
        log.info("开始优雅退出…")

        dropped = 0
        while not self.outbound.empty():
            self.outbound.get_nowait()
            dropped += 1
        if dropped:
            log.warning("退出时丢弃 %d 条未发送消息", dropped)

        current = asyncio.current_task()
        for task in list(self._tasks):
            if task is not current:
                task.cancel()
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=5)
        log.info("退出完成")
