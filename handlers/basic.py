# -*- coding: utf-8 -*-
"""基础业务示例：echo、ping、每日播报——同时作为框架用法范本。"""
import logging

from cea import Bot, Chat, Message, Router

log = logging.getLogger("cea")

router = Router()

# 最近活跃会话（业务侧自管状态），供定时播报演示主动消息
_last_chat: dict[str, Chat] = {}


@router.on_message()
async def echo(bot: Bot, msg: Message) -> None:
    """回复收到的任意文本/图片消息。"""
    if msg.text:
        await bot.send(msg.chat, text=f"你好，我是 CEA Bot，收到：{msg.text}", reply_to=msg.id)
    elif msg.images:
        await bot.send(msg.chat, text="收到图片啦", reply_to=msg.id)
    _last_chat["chat"] = msg.chat


@router.command("ping")
async def ping(bot: Bot, msg: Message, args: list[str]) -> None:
    """示例命令：ping [任意参数...] → pong。"""
    suffix = f"（{len(args)} 个参数）" if args else ""
    await bot.send(msg.chat, text=f"pong{suffix}", reply_to=msg.id)


@router.schedule("12:00")
async def noon_report(bot: Bot) -> None:
    """每日 12:00 播报：演示共享时钟定时任务 + bot.send 主动消息。"""
    chat = _last_chat.get("chat")
    if chat is None:
        log.info("定时播报触发，但尚无活跃会话可发送")
        return
    await bot.send(chat, text="午间播报：CEA Bot 运行中")
