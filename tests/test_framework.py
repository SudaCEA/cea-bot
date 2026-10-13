# -*- coding: utf-8 -*-
"""框架单测：命令匹配 / 出站互斥 / msg_seq / 时钟 / 异常隔离 / 优雅退出。

不依赖平台 SDK 的行为；用 asyncio.run 包裹协程场景（无需 pytest-asyncio）。
运行：.venv\\Scripts\\python -m pytest tests -q
"""
import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from cea import App, FileData, FileRef, Router
from cea.app import due_jobs, match_commands, parse_hhmm
from cea.bot import MAX_FILE_BYTES, FileTooLargeError
from cea.adapters.qqbotpy import QQBotpyAdapter, SeqCounter
from cea.types import Chat, Message, OutMessage, User

CHAT = Chat("qq", "g1", "group")


def make_msg(text: str = "") -> Message:
    return Message(id="m1", chat=CHAT, sender=User("u1"), text=text)


# ---- 出站模型：text/image 互斥 ----

def test_out_message_text_image_mutex():
    with pytest.raises(ValueError):
        OutMessage(chat=CHAT, text="hi", image="http://example.com/a.png")


def test_out_message_requires_content():
    with pytest.raises(ValueError):
        OutMessage(chat=CHAT)


# ---- 出站模型：file 三选一互斥 ----

def test_out_message_file_only_ok():
    msg = OutMessage(chat=CHAT, file="base64://AAA", file_name="a.pdf")
    assert msg.file == "base64://AAA" and msg.file_name == "a.pdf"


def test_out_message_text_file_mutex():
    with pytest.raises(ValueError):
        OutMessage(chat=CHAT, text="x", file="base64://AAA")


def test_out_message_image_file_mutex():
    with pytest.raises(ValueError):
        OutMessage(chat=CHAT, image="http://example.com/a.png", file="base64://AAA")


# ---- 文件句柄：能力注入 / 平台不支持 / 超限 ----

class _FakeFileAdapter:
    def __init__(self):
        self.fetched: list[str] = []

    async def fetch_file(self, file_id: str) -> FileData:
        self.fetched.append(file_id)
        return FileData(data=b"hello", name="a.txt", size=5)


def test_file_handle_injected_and_forwarded():
    async def scenario():
        app = App(adapter=_FakeFileAdapter())
        return await app.bot.fetch_file(FileRef(file_id="f1"))

    data = asyncio.run(scenario())
    assert data.data == b"hello" and data.name == "a.txt" and data.size == 5


def test_file_handle_unsupported_without_injection():
    async def scenario():
        app = App(adapter=None)
        with pytest.raises(NotImplementedError):
            await app.bot.fetch_file("f1")

    asyncio.run(scenario())


def test_fetch_file_accepts_str_and_over_limit():
    class _Oversized(_FakeFileAdapter):
        async def fetch_file(self, file_id: str) -> FileData:
            if file_id == "big":
                return FileData(
                    data=b"x" * (MAX_FILE_BYTES + 1), name="big", size=MAX_FILE_BYTES + 1
                )
            return await super().fetch_file(file_id)

    async def scenario():
        app = App(adapter=_Oversized())
        data = await app.bot.fetch_file("plain-id")  # 字符串引用直通
        with pytest.raises(FileTooLargeError):
            await app.bot.fetch_file("big")  # Bot 侧 16MB 预检
        return data

    data = asyncio.run(scenario())
    assert data.data == b"hello"


def test_bot_send_rejects_mutex():
    async def scenario():
        app = App(adapter=None)
        with pytest.raises(ValueError):
            await app.bot.send(CHAT, text="x", image="http://example.com/a.png")

    asyncio.run(scenario())


def test_bot_send_enqueues_out_message():
    async def scenario():
        app = App(adapter=None)
        await app.bot.send(CHAT, text="hi", reply_to="m1", at=("u9",))
        return app.outbound.get_nowait()

    msg = asyncio.run(scenario())
    assert msg.text == "hi" and msg.reply_to == "m1" and msg.at == ("u9",)
    assert msg.image is None and msg.chat is CHAT


# ---- 命令匹配：精确首词 + 参数切分 ----

def test_command_match_and_args():
    router = Router()

    @router.command("ping", "pinG")
    async def handler(bot, msg, args):  # noqa: ARG001
        pass

    hit = match_commands([router], "  ping   a  b ")
    assert hit is not None
    fn, args = hit
    assert fn.__name__ == "handler"
    assert args == ["a", "b"]


def test_command_no_substring_match():
    router = Router()

    @router.command("ping")
    async def handler(bot, msg, args):  # noqa: ARG001
        pass

    assert match_commands([router], "pingpong") is None
    assert match_commands([router], "") is None


def test_command_priority_by_router_order():
    r1, r2 = Router(), Router()

    @r1.command("go")
    async def first(bot, msg, args):  # noqa: ARG001
        pass

    @r2.command("go")
    async def second(bot, msg, args):  # noqa: ARG001
        pass

    hit = match_commands([r1, r2], "go")
    assert hit is not None and hit[0].__name__ == "first"


def test_parse_hhmm_validates():
    assert parse_hhmm("09:05") == (9, 5)
    with pytest.raises(ValueError):
        parse_hhmm("9点")
    with pytest.raises(ValueError):
        parse_hhmm("25:00")
    with pytest.raises(ValueError):
        parse_hhmm("09:60")


# ---- 分发：群白名单过滤 ----

def _dispatch_seen(app: App, *messages: Message) -> list[str]:
    """把若干消息灌入分发器，返回业务层实际收到的 "kind:chat_id" 列表。"""
    seen: list[str] = []

    async def scenario() -> list[str]:
        router = Router()

        @router.on_message()
        async def collect(bot, msg):  # noqa: ARG001
            seen.append(f"{msg.chat.kind}:{msg.chat.id}")

        app.include_router(router)
        task = asyncio.create_task(app._dispatch())
        for message in messages:
            await app.inbound.put(message)
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return seen

    return asyncio.run(scenario())


def test_whitelist_allows_listed_group():
    app = App(adapter=None, group_whitelist=["g1"])
    msg = Message(id="m1", chat=Chat("qq", "g1", "group"), sender=User("u1"), text="hi")
    assert _dispatch_seen(app, msg) == ["group:g1"]


def test_whitelist_blocks_other_groups():
    app = App(adapter=None, group_whitelist=["g1"])
    blocked = Message(id="m1", chat=Chat("qq", "g2", "group"), sender=User("u1"), text="hi")
    allowed = Message(id="m2", chat=Chat("qq", "g1", "group"), sender=User("u1"), text="hi")
    assert _dispatch_seen(app, blocked, allowed) == ["group:g1"]


def test_whitelist_empty_no_filter():
    app = App(adapter=None)  # 未配置白名单 → 不过滤（兼容既有行为）
    msg = Message(id="m1", chat=Chat("qq", "g9", "group"), sender=User("u1"), text="hi")
    assert _dispatch_seen(app, msg) == ["group:g9"]


def test_whitelist_private_unaffected():
    app = App(adapter=None, group_whitelist=["g1"])
    msg = Message(id="m1", chat=Chat("qq", "c9", "c2c"), sender=User("u1"), text="hi")
    assert _dispatch_seen(app, msg) == ["c2c:c9"]


# ---- 分发：异常隔离 ----

def test_dispatch_exception_isolation():
    async def scenario():
        app = App(adapter=None)
        router = Router()
        done: list[str] = []

        @router.on_message()
        async def ok(bot, msg):  # noqa: ARG001
            done.append(msg.text)

        @router.on_message()
        async def boom(bot, msg):  # noqa: ARG001
            raise RuntimeError("boom")

        app.include_router(router)
        dispatcher = asyncio.create_task(app._dispatch())
        await app.inbound.put(make_msg("hello"))
        await asyncio.sleep(0.05)
        dispatcher.cancel()
        try:
            await dispatcher
        except asyncio.CancelledError:
            pass
        return done

    assert asyncio.run(scenario()) == ["hello"]


# ---- 被动回复 msg_seq 递增 ----

def test_seq_counter_increments_per_key():
    counter = SeqCounter()
    assert counter.next("g1", "m1") == 1
    assert counter.next("g1", "m1") == 2
    assert counter.next("g1", "m2") == 1
    assert counter.next("g2", "m1") == 1


# ---- adapter 发送路由（fake api 捕获参数）----

class _FakeAPI:
    def __init__(self):
        self.calls = []

    async def post_group_message(self, **kwargs):
        self.calls.append(("group", kwargs))

    async def post_c2c_message(self, **kwargs):
        self.calls.append(("c2c", kwargs))

    async def post_message(self, **kwargs):
        self.calls.append(("channel", kwargs))


def _adapter_with_fake_api():
    adapter = QQBotpyAdapter("appid", "secret")
    adapter._client = SimpleNamespace(api=_FakeAPI())
    return adapter


def test_group_reply_msg_seq_increments():
    async def scenario():
        adapter = _adapter_with_fake_api()
        chat = Chat("qq", "g1", "group")
        await adapter.send(OutMessage(chat=chat, text="a", reply_to="m1"))
        await adapter.send(OutMessage(chat=chat, text="b", reply_to="m1"))
        return adapter._client.api.calls

    calls = asyncio.run(scenario())
    assert [c[1].get("msg_seq") for c in calls] == [1, 2]
    assert all(c[1].get("msg_id") == "m1" for c in calls)
    assert all(c[1].get("msg_type") == 0 for c in calls)


def test_channel_reply_omits_msg_seq():
    """频道 post_message 无 msg_seq 参数，回复只带 msg_id（回归：曾误传导致 TypeError）。"""

    async def scenario():
        adapter = _adapter_with_fake_api()
        await adapter.send(OutMessage(chat=Chat("qq", "ch1", "channel"), text="hi", reply_to="m1"))
        return adapter._client.api.calls

    kind, kwargs = asyncio.run(scenario())[0]
    assert kind == "channel"
    assert kwargs["msg_id"] == "m1" and "msg_seq" not in kwargs
    assert kwargs["content"] == "hi"


# ---- 共享时钟：到期触发 / 同分钟去重 / 错过不补 ----

def test_clock_due_dedupe_and_skip_missed():
    router = Router()

    @router.schedule("09:00")
    async def job(bot):  # noqa: ARG001
        pass

    first = due_jobs(router.jobs, datetime(2026, 9, 20, 9, 0, 0))
    assert first == [router.jobs[0]]
    assert due_jobs(router.jobs, datetime(2026, 9, 20, 9, 0, 30)) == []  # 同分钟不重复
    assert due_jobs(router.jobs, datetime(2026, 9, 20, 9, 1, 0)) == []  # 过点不补
    assert due_jobs(router.jobs, datetime(2026, 9, 21, 9, 0, 0)) == [router.jobs[0]]  # 次日再触发


# ---- create_task 注入 bot 与优雅退出 ----

def test_create_task_injects_bot_and_shutdown_cancels():
    async def scenario():
        app = App(adapter=None)
        seen: dict = {}

        async def worker(bot):
            seen["bot"] = bot
            await asyncio.Event().wait()  # 常驻直到被取消

        app.bot.create_task(worker, name="worker")
        await asyncio.sleep(0.05)
        assert seen["bot"] is app.bot
        assert any(t.get_name() == "worker" for t in app._tasks)
        await app._shutdown()
        return app

    app = asyncio.run(scenario())
    assert app.stopping.is_set()
    assert all(t.done() for t in app._tasks)


def test_run_graceful_shutdown_on_cancel():
    """模拟 Ctrl+C：asyncio.run 会取消主任务，run() 应完成收尾并抛出 KeyboardInterrupt。

    注意必须在同一任务内直接 await app.run()（对应真实场景中 run() 即主任务）：
    KeyboardInterrupt 在子任务中抛出会被事件循环立即上抛，无法被 await 方捕获。
    """

    async def scenario():
        app = App(adapter=None)
        main_task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        loop.call_later(0.2, main_task.cancel)  # 定时打断，模拟 Ctrl+C
        raised = None
        try:
            await app.run()
        except KeyboardInterrupt:
            raised = "KeyboardInterrupt"
        return app, raised

    app, raised = asyncio.run(scenario())
    assert raised == "KeyboardInterrupt"
    assert app.stopping.is_set()
    assert all(t.done() for t in app._tasks)
