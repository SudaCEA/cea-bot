# -*- coding: utf-8 -*-
"""统一消息模型：平台无关的收发数据结构（全部不可变）。

收发均只保留五类能力：文本、图像、文件、@、回复。
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class User:
    """消息发送者。id 为平台侧用户标识（openid / user_id）。"""

    id: str
    name: str = ""


@dataclass(frozen=True)
class Chat:
    """会话标识。kind 取值：group(群) / c2c(单聊) / channel(频道子频道)。"""

    platform: str
    id: str
    kind: str


@dataclass(frozen=True)
class FileRef:
    """入站文件引用：file_id 用于后续下载（Bot.fetch_file）。"""

    file_id: str
    name: str = ""
    size: int = 0
    url: str | None = None


@dataclass(frozen=True)
class FileData:
    """文件下载结果。"""

    data: bytes
    name: str = ""
    size: int = 0


@dataclass(frozen=True)
class Message:
    """入站消息（由平台前端归一化产出，投递到 inbound 总线）。

    text 已剥离 @ 机器人前缀；raw 保留平台原始对象作逃生舱口。
    """

    id: str
    chat: Chat
    sender: User
    text: str = ""
    images: tuple[str, ...] = ()
    files: tuple[FileRef, ...] = ()
    at_me: bool = False
    reply_to: str | None = None
    raw: Any = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class OutMessage:
    """出站消息（投递到 outbound 总线，由平台前端消费）。

    text / image / file 三选一互斥（同时提供多项违规抛 ValueError）；
    image 为图片源引用、file 为文件源引用（可附 file_name），两者接受的形态随前端
    adapter 能力（如 QQ 官方前端图片仅支持 URL 且不支持文件，NapCat 三态皆可）；
    at 为附加 @ 目标列表；reply_to 非空表示被动回复，否则为主动消息。
    """

    chat: Chat
    text: str = ""
    image: str | None = None
    file: str | None = None
    file_name: str = ""
    at: tuple[str, ...] = ()
    reply_to: str | None = None

    def __post_init__(self) -> None:
        kinds = sum((bool(self.text), bool(self.image), bool(self.file)))
        if kinds > 1:
            raise ValueError("OutMessage.text / image / file 三选一互斥：单条消息仅支持其一")
        if kinds == 0:
            raise ValueError("OutMessage 需要 text / image / file 至少其一")
