# -*- coding: utf-8 -*-
"""QQ 官方平台前端（基于 qq-botpy v1.2.1）。

Spike 1.1 结论（2026-09-20 实测）：
- 自有事件循环中 `await client.start(appid, secret)` 完全可用：
  登录→鉴权→网关连接→on_ready→心跳均正常；start 协程可干净取消。
- 取消后需调用 `await client.close()` 关闭 http session，否则出现
  "Unclosed client session" 噪音。

Spike 1.2 结论（2026-09-20 真机群聊实测）：
- 入站：群 @ 机器人的 content 不含 `<@BOT_ID>` 前缀，直接是消息文本（开头可能带一个空格）；
  strip_at_prefix 保留作防御（频道 <@id> 形态仍可能出现）
- 出站：`<@{member_openid}>` 的渲染效果待 at 功能实际使用时再确认
"""
import asyncio
import logging
import re
import time
from typing import Any

import botpy
from botpy.api import BotAPI
from botpy.message import C2CMessage as QQC2CMessage
from botpy.message import GroupMessage as QQGroupMessage
from botpy.message import Message as QQChannelMessage

from cea.adapters.base import pump_outbound
from cea.types import Chat, Message, OutMessage, User

log = logging.getLogger("cea")

# 入站消息为 SDK 的 QQChannelMessage | QQGroupMessage | QQC2CMessage；
# botpy 无类型标注（按 Any 处理），故 _push 的消息参数以 Any 标注
# 入站 @ 前缀的防御性匹配（频道 <@id> / <@!id>；实测群消息 content 不带 @ 前缀，保留作防御）
_AT_PREFIX = re.compile(r"^<@!?\w+>\s*")


def strip_at_prefix(text: str | None) -> str:
    """剥离文本开头连续的 @ 机器人标记，返回纯文本。"""
    if not text:
        return ""
    t = text.strip()
    while True:
        m = _AT_PREFIX.match(t)
        if not m:
            break
        t = t[m.end():]
    return t.strip()


class SeqCounter:
    """被动回复的 msg_seq 计数器：对同一 (会话, reply_to) 的第 n 次发送返回 n。

    平台要求相同 msg_id + msg_seq 重复发送会失败；被动回复窗口 5 分钟，
    条目超限时按时间戳惰性清理。
    """

    def __init__(self, ttl: float = 300.0) -> None:
        self._ttl = ttl
        self._data: dict[tuple[str, str], tuple[int, float]] = {}

    def next(self, chat_id: str, reply_to: str) -> int:
        now = time.monotonic()
        key = (chat_id, reply_to)
        seq, _ = self._data.get(key, (0, now))
        seq += 1
        self._data[key] = (seq, now)
        if len(self._data) > 512:
            self._data = {k: v for k, v in self._data.items() if now - v[1] <= self._ttl}
        return seq


class QQBotpyAdapter:
    """官方 SDK 前端：实现 Adapter 契约（run / send）。"""

    platform = "qq"

    def __init__(self, appid: str, secret: str) -> None:
        self._appid = appid
        self._secret = secret
        self._client: botpy.Client | None = None
        self._inbound: asyncio.Queue[Message] | None = None
        self._seq = SeqCounter()

    # ---- Adapter 契约 ----

    async def run(
        self,
        inbound: asyncio.Queue[Message],
        outbound: asyncio.Queue[OutMessage],
    ) -> None:
        """连接 QQ 网关并双向搬运消息；随宿主任务取消而退出。"""
        self._inbound = inbound
        client = _build_client(self)
        self._client = client
        pump = asyncio.create_task(pump_outbound(outbound, self.send))
        try:
            await client.start(self._appid, self._secret)
        finally:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass
            await client.close()  # 关闭 aiohttp session（Spike 1.1 结论）
            log.info("QQ 前端已断开")

    async def send(self, msg: OutMessage) -> None:
        """发送一条出站消息。平台错误记日志不外抛（消费者持续运行）。"""
        if msg.file:
            log.warning(
                "官方平台不支持文件消息（file_type=4 未开放），丢弃：%s",
                msg.file_name or msg.file,
            )
            return
        if msg.image and not msg.image.startswith(("http://", "https://")):
            # 明确报错：群/C2C 富媒体接口仅接受 URL（平台限制）
            log.error("发送失败：图片仅支持 URL，收到非 URL：%s", msg.image)
            return
        api = self._client.api if self._client is not None else None
        if api is None:
            log.error("发送失败：QQ 前端尚未连接，丢弃出站消息")
            return
        try:
            if msg.chat.kind == "group":
                await self._send_group(api, msg)
            elif msg.chat.kind == "c2c":
                await self._send_c2c(api, msg)
            elif msg.chat.kind == "channel":
                await self._send_channel(api, msg)
            else:
                log.error("发送失败：未知会话类型 %r", msg.chat.kind)
        except Exception as exc:  # 超回复窗口 / 频控 / 网络错误等一律不崩溃
            log.error("发送失败（chat=%s kind=%s）：%r", msg.chat.id, msg.chat.kind, exc)

    # ---- 发送路由 ----

    async def _send_group(self, api: BotAPI, msg: OutMessage) -> None:
        if msg.image:
            media = await api.post_group_file(
                group_openid=msg.chat.id, file_type=1, url=msg.image
            )
            await api.post_group_message(
                group_openid=msg.chat.id,
                msg_type=7,
                media=media,
                **self._reply_kwargs(msg),
            )
            return
        await api.post_group_message(
            group_openid=msg.chat.id,
            msg_type=0,
            content=self._compose(msg),
            **self._reply_kwargs(msg),
        )

    async def _send_c2c(self, api: BotAPI, msg: OutMessage) -> None:
        if msg.image:
            media = await api.post_c2c_file(openid=msg.chat.id, file_type=1, url=msg.image)
            await api.post_c2c_message(
                openid=msg.chat.id, msg_type=7, media=media, **self._reply_kwargs(msg)
            )
            return
        await api.post_c2c_message(
            openid=msg.chat.id, msg_type=0, content=msg.text, **self._reply_kwargs(msg)
        )

    async def _send_channel(self, api: BotAPI, msg: OutMessage) -> None:
        # 频道 post_message 没有 msg_seq 参数（仅群/C2C 有），回复只带 msg_id
        kwargs = {"channel_id": msg.chat.id}
        if msg.image:
            kwargs["image"] = msg.image  # 频道接口支持 image URL
        else:
            kwargs["content"] = self._compose(msg)
        if msg.reply_to:
            kwargs["msg_id"] = msg.reply_to
        await api.post_message(**kwargs)

    def _reply_kwargs(self, msg: OutMessage) -> dict[str, Any]:
        """被动回复参数（群/C2C 专用）：msg_id + 递增的 msg_seq；主动消息则不带。"""
        if not msg.reply_to:
            return {}
        return {
            "msg_id": msg.reply_to,
            "msg_seq": self._seq.next(msg.chat.id, msg.reply_to),
        }

    @staticmethod
    def _compose(msg: OutMessage) -> str:
        """@ 列表转平台语法并拼接文本（图片消息忽略 at）。"""
        parts = [f"<@{openid}>" for openid in msg.at]
        if msg.text:
            parts.append(msg.text)
        return " ".join(parts)

    # ---- 接收归一化 ----

    def _push(self, kind: str, m: Any) -> None:
        """把 SDK 消息对象归一化为统一 Message 并投递 inbound。"""
        if self._inbound is None:
            return
        if kind == "group":
            chat = Chat(self.platform, m.group_openid or "", "group")
            sender = User(id=m.author.member_openid or "", name="")
        elif kind == "c2c":
            chat = Chat(self.platform, m.author.user_openid or "", "c2c")
            sender = User(id=m.author.user_openid or "", name="")
        else:  # channel
            chat = Chat(self.platform, m.channel_id or "", "channel")
            sender = User(id=m.author.id or "", name=m.author.username or "")
        images = tuple(a.url for a in m.attachments if getattr(a, "url", None))
        reply_to = m.message_reference.message_id if m.message_reference else None
        self._inbound.put_nowait(
            Message(
                id=m.id or "",
                chat=chat,
                sender=sender,
                text=strip_at_prefix(m.content),
                images=images,
                at_me=True,  # 三类事件均由 @ 机器人触发或用户直聊机器人
                reply_to=reply_to,
                raw=m,
            )
        )


def _build_client(adapter: QQBotpyAdapter) -> botpy.Client:
    """构造绑定归一化回调的 SDK Client。"""

    class _Client(botpy.Client):
        async def on_ready(self) -> None:
            log.info("机器人「%s」已上线（QQ 前端就绪）", self.robot.name)

        async def on_at_message_create(self, message: QQChannelMessage) -> None:
            adapter._push("channel", message)

        async def on_group_at_message_create(self, message: QQGroupMessage) -> None:
            adapter._push("group", message)

        async def on_c2c_message_create(self, message: QQC2CMessage) -> None:
            adapter._push("c2c", message)

    return _Client(intents=botpy.Intents(public_guild_messages=True, public_messages=True))
