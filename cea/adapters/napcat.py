# -*- coding: utf-8 -*-
"""NapCat（OneBot 11）平台前端：正向 WS 接入，实现 Adapter 契约（run / send）。

接入前提（NapCat 侧一次性配置，WebUI → 网络配置 → 新建 → WebSocket 服务端）：
  - 监听 host / port（如 0.0.0.0:3001），bot 侧 config.yaml 填 ws_url: ws://<host>:<port>
  - 公网部署务必启用 token（bot 侧填相同值；鉴权走 Authorization: Bearer 请求头，
    若服务端不识别可改用 ?access_token= 查询参数）
  - 消息格式 messagePostFormat 必须为 array（本前端按消息段数组解析）

能力与限制：
  - 收发范围：message.group / message.private 的 text / image / at / reply 四类建模段；
    其余消息段（file / face / record / json 等）不建模，经 raw 原始事件透出
  - 图片源接受 URL / 本地路径 / base64:// 三态（NapCat 能力）；注意跨机部署时
    本地路径仅对 NapCat 所在机器有效，跨机请用 base64:// 或 URL
  - 文件：file 段出站（file_name 透传）、入站收集为 FileRef（file_id 必备）；
    fetch_file 经 get_file 取回字节（base64 优先 / http(s) URL 降级），统一受 16MB
    上限保护（超限抛 FileTooLargeError）；已知限制：分端部署时 get_file 可能仅返回
    NapCat 本地路径，此时明确报错，不做跨机取回
  - 不支持频道会话（kind=channel 记日志丢弃）
  - 风控提示：NapCat 使用真实 QQ 号收发，无平台频控，但高频发送可能触发腾讯风控；
    框架不内置限流（与既有约定一致），需要限速请业务层自行实现

断线语义：框架以任务取消表达停止（见 cea/adapters/base.py 契约）——run() 被取消即
停止重连并收尾；未取消时按 reconnect_interval 固定间隔自动重连。
"""
import asyncio
import base64
import json
import logging
from collections.abc import Callable
from typing import Any

import aiohttp

from cea.adapters.base import pump_outbound
from cea.bot import FileTooLargeError, MAX_FILE_BYTES
from cea.types import Chat, FileData, FileRef, Message, OutMessage, User

log = logging.getLogger("cea")

_WAIT_CONNECTED_TIMEOUT = 30.0  # send() 等待 WS（重）连接的最长时间
_CALL_TIMEOUT = 30.0            # 单次 API 调用等待响应帧的最长时间


# ---- 纯函数：事件归一化 / 段构造（无 IO，便于单测） ----

def normalize_event(event: dict[str, Any]) -> Message | None:
    """把 OneBot 11 事件帧归一化为统一消息；不建模的事件返回 None。

    - post_type=meta_event（心跳/生命周期）→ None
    - post_type=message 且 message_type=group/private → 统一 Message
    - 其余 post_type（notice/request/message_sent 等）暂不建模 → None
    """
    if event.get("post_type") != "message":
        return None
    message_type = event.get("message_type")
    if message_type not in ("group", "private"):
        log.debug("忽略未建模的消息事件：%s", message_type)
        return None

    self_id = str(event.get("self_id") or "")
    raw_segments = event.get("message")
    if isinstance(raw_segments, str):
        # messagePostFormat 配置为 string 时的兜底：整段按文本处理（含 CQ 码），日志可见
        log.warning("收到字符串格式消息（应配置 messagePostFormat=array），按原样作为文本处理")
        segments: list[Any] = [{"type": "text", "data": {"text": raw_segments}}]
    elif isinstance(raw_segments, list):
        segments = raw_segments
    else:
        segments = []

    text_parts: list[str] = []
    images: list[str] = []
    files: list[FileRef] = []
    at_me = False
    reply_to: str | None = None
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        data = seg.get("data") or {}
        seg_type = seg.get("type")
        if seg_type == "text":
            text_parts.append(str(data.get("text") or ""))
        elif seg_type == "image":
            url = data.get("url") or data.get("path")  # 无 URL 时回退 NapCat 本地路径
            if url:
                images.append(str(url))
        elif seg_type == "file" and data.get("file_id"):
            files.append(
                FileRef(
                    file_id=str(data["file_id"]),
                    name=str(data.get("name") or data.get("file_name") or ""),
                    size=_to_int(data.get("file_size")),
                    url=str(data["url"]) if data.get("url") else None,
                )
            )
        elif seg_type == "at" and str(data.get("qq") or "") == self_id:
            at_me = True
        elif seg_type == "reply" and reply_to is None:
            reply_to = str(data.get("id") or "") or None

    sender_info = event.get("sender") or {}
    if message_type == "group":
        chat = Chat("napcat", str(event.get("group_id") or ""), "group")
        name = str(sender_info.get("card") or sender_info.get("nickname") or "")  # 群名片优先
    else:
        chat = Chat("napcat", str(event.get("user_id") or ""), "c2c")
        name = str(sender_info.get("nickname") or "")

    return Message(
        id=str(event.get("message_id") or ""),
        chat=chat,
        sender=User(str(event.get("user_id") or ""), name),
        text="".join(text_parts).strip(),
        images=tuple(images),
        files=tuple(files),
        at_me=at_me,
        reply_to=reply_to,
        raw=event,
    )


def build_segments(msg: OutMessage) -> list[dict[str, Any]]:
    """统一出站消息 → OneBot 消息段数组（引用段置首，随后 @，最后文本或图片）。"""
    segments: list[dict[str, Any]] = []
    if msg.reply_to:
        segments.append({"type": "reply", "data": {"id": str(msg.reply_to)}})
    for qq in msg.at:
        segments.append({"type": "at", "data": {"qq": str(qq)}})
    if msg.image:
        segments.append({"type": "image", "data": {"file": msg.image}})
    elif msg.file:
        seg_data: dict[str, Any] = {"file": msg.file}
        if msg.file_name:
            seg_data["name"] = msg.file_name
        segments.append({"type": "file", "data": seg_data})
    else:
        segments.append({"type": "text", "data": {"text": msg.text}})
    return segments


def _to_int(value: Any) -> int:
    """宽松转 int（file_size 等字段可能是字符串）；失败返回 0。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_qq_id(value: str) -> int | str:
    """QQ 号/群号转 int（OneBot 11 标准为整型）；非纯数字原样透传作兜底。"""
    return int(value) if value.isdigit() else value


# ---- 正向 WS 客户端 ----

class _OneBotClient:
    """单条 NapCat 正向 WS 连接：读帧分发 + 按 echo 关联 API 调用。"""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        ws_url: str,
        token: str,
        on_event: Callable[[dict[str, Any]], None],
    ) -> None:
        self._session = session
        self._ws_url = ws_url
        self._token = token
        self._on_event = on_event
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._echo = 0
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def connect(self) -> None:
        """建立 WS 连接；token 非空时携带 Bearer 请求头。"""
        # OneBot 11 亦支持 ?access_token= 查询参数鉴权；服务端不识别请求头时可改用
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else None
        self._ws = await self._session.ws_connect(self._ws_url, headers=headers)

    async def reader(self) -> None:
        """持续读帧：事件帧回调 on_event，响应帧按 echo 唤醒等待者；连接断开即返回。"""
        ws = self._ws
        if ws is None:
            return
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    frame = json.loads(msg.data)
                except (ValueError, TypeError):
                    log.warning("忽略无法解析的 NapCat 帧：%r", msg.data[:200])
                    continue
                echo = frame.get("echo")
                fut = self._pending.pop(str(echo), None) if echo is not None else None
                if fut is not None and not fut.done():
                    fut.set_result(frame)  # API 响应帧
                elif "post_type" in frame:
                    self._on_event(frame)  # 事件帧（无 echo）
                # 其余帧（未知格式）忽略
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

    async def call(
        self, action: str, params: dict[str, Any], timeout: float = _CALL_TIMEOUT
    ) -> dict[str, Any]:
        """发起一次 API 调用并等待对应 echo 的响应帧；断开/超时抛异常。"""
        if self._ws is None or self._ws.closed:
            raise ConnectionError("NapCat WS 未连接")
        self._echo += 1
        echo = str(self._echo)
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[echo] = fut
        try:
            await self._ws.send_json({"action": action, "params": params, "echo": echo})
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"NapCat 响应超时（action={action}，{timeout:.0f}s）") from None
        finally:
            self._pending.pop(echo, None)

    async def close(self) -> None:
        """关闭连接；未决请求以连接错误收场，等待方据此记日志而不悬挂。"""
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("NapCat WS 已断开"))
        self._pending.clear()
        if self._ws is not None:
            await self._ws.close()
            self._ws = None


# ---- 平台前端 ----

class NapCatAdapter:
    """NapCat 前端：正向 WS 重连循环 + 事件归一化投递 + 出站消息段发送。"""

    platform = "napcat"

    def __init__(
        self, ws_url: str, token: str = "", *, reconnect_interval: float = 5.0
    ) -> None:
        self._ws_url = ws_url
        self._token = token
        self._reconnect_interval = reconnect_interval
        self._inbound: asyncio.Queue[Message] | None = None
        self._connected = asyncio.Event()
        self._client: _OneBotClient | None = None
        self._session: aiohttp.ClientSession | None = None

    # ---- Adapter 契约 ----

    async def run(
        self,
        inbound: asyncio.Queue[Message],
        outbound: asyncio.Queue[OutMessage],
    ) -> None:
        """连接 NapCat 并双向搬运消息；随宿主任务取消而退出（取消即不再重连）。"""
        self._inbound = inbound
        session = self._session = aiohttp.ClientSession()
        pump = asyncio.create_task(pump_outbound(outbound, self.send))
        log.info("NapCat 前端启动，目标 %s", self._ws_url)
        try:
            while True:
                client = _OneBotClient(session, self._ws_url, self._token, self._push_event)
                self._client = client
                try:
                    await client.connect()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning(
                        "NapCat WS 连接失败：%r，%.0f 秒后重试",
                        exc, self._reconnect_interval,
                    )
                    self._client = None
                    await asyncio.sleep(self._reconnect_interval)
                    continue
                self._connected.set()
                log.info("NapCat WS 已连接")
                try:
                    await client.reader()
                finally:
                    self._connected.clear()
                    self._client = None
                await client.close()
                log.warning("NapCat WS 断开，%.0f 秒后重连", self._reconnect_interval)
                await asyncio.sleep(self._reconnect_interval)
        finally:
            self._connected.clear()
            self._client = None
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass
            await session.close()
            self._session = None
            log.info("NapCat 前端已退出")

    async def send(self, msg: OutMessage) -> None:
        """发送一条出站消息；任何失败记日志不外抛（发送消费者持续运行）。"""
        kind = msg.chat.kind
        if kind == "channel":
            log.error("发送失败：NapCat 不支持频道会话（chat=%s），丢弃", msg.chat.id)
            return
        try:
            client = await self._acquire_client()
        except RuntimeError as exc:
            log.error("发送失败：%s，丢弃出站消息", exc)
            return
        segments = build_segments(msg)
        if kind == "group":
            action = "send_group_msg"
            params: dict[str, Any] = {"group_id": _as_qq_id(msg.chat.id), "message": segments}
        elif kind == "c2c":
            action = "send_private_msg"
            params = {"user_id": _as_qq_id(msg.chat.id), "message": segments}
        else:
            log.error("发送失败：未知会话类型 %r（chat=%s），丢弃", kind, msg.chat.id)
            return
        try:
            response = await client.call(action, params)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 断开/超时/平台错误一律不外抛
            log.error("发送失败（chat=%s kind=%s）：%r", msg.chat.id, kind, exc)
            return
        if response.get("retcode") != 0:
            log.error(
                "发送失败（action=%s chat=%s）：status=%s retcode=%s wording=%s",
                action, msg.chat.id, response.get("status"),
                response.get("retcode"), response.get("wording") or response.get("message"),
            )

    # ---- 文件能力（可选扩展：App 装配时鸭子探测注入 Bot） ----

    async def _acquire_client(self) -> _OneBotClient:
        """等待（重）连接并返回可用客户端；超时/未连接抛 RuntimeError。"""
        if not self._connected.is_set():
            # 等待（重）连接恢复；App 退出时宿主任务被取消，此等待随之结束
            try:
                await asyncio.wait_for(self._connected.wait(), _WAIT_CONNECTED_TIMEOUT)
            except asyncio.TimeoutError:
                raise RuntimeError(
                    f"NapCat WS 未连接（等待 {_WAIT_CONNECTED_TIMEOUT:.0f}s 超时）"
                ) from None
        client = self._client
        if client is None:
            raise RuntimeError("NapCat WS 未连接")
        return client

    async def fetch_file(self, file_id: str) -> FileData:
        """下载文件内容：get_file → base64 优先 / http(s) URL 降级；超过 16MB 抛 FileTooLargeError。

        已知限制：分端部署时 get_file 可能仅返回 NapCat 本地路径（无 base64 且 url 非
        http），此时明确报错，不做跨机取回。
        """
        client = await self._acquire_client()
        response = await client.call("get_file", {"file_id": file_id})
        if response.get("retcode") != 0:
            raise RuntimeError(
                f"get_file 失败：{response.get('wording') or response.get('message')}"
            )
        info = response.get("data") or {}
        name = str(info.get("file_name") or "")
        encoded = info.get("base64")
        if encoded:
            estimated = len(encoded) * 3 // 4  # base64 估长预检，避免无谓解码大内容
            if estimated > MAX_FILE_BYTES:
                raise FileTooLargeError(f"文件约 {estimated // 1024 // 1024}MB，超过 16MB 上限")
            payload = base64.b64decode(encoded)
            return FileData(data=payload, name=name, size=len(payload))
        url = str(info.get("url") or "")
        if not url.startswith(("http://", "https://")):
            # 非 http 下载 URL（通常是 NapCat 本地路径）：分端部署无法直接读取，明确报错
            raise RuntimeError(
                f"get_file 未返回可下载内容（url={url or '空'} 为 NapCat 本地路径，"
                "分端部署暂不支持跨机取回）"
            )
        if self._session is None:
            raise RuntimeError("NapCat 前端尚未启动，无法经 HTTP 下载文件")
        try:
            async with self._session.get(url) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"文件下载 HTTP {resp.status}")
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > MAX_FILE_BYTES:
                        raise FileTooLargeError("文件超过 16MB 上限（下载中途中止）")
                    chunks.append(chunk)
        except asyncio.CancelledError:
            raise
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"文件下载失败：{exc!r}") from exc
        payload = b"".join(chunks)
        return FileData(data=payload, name=name, size=len(payload))

    # ---- 接收 ----

    def _push_event(self, frame: dict[str, Any]) -> None:
        """事件帧回调（WS 读协程内联调用）：归一化后投递 inbound。"""
        if self._inbound is None:
            return
        message = normalize_event(frame)
        if message is not None:
            self._inbound.put_nowait(message)
