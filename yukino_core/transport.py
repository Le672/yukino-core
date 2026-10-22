"""OneBot v11 forward WebSocket transport.

The reader only correlates action responses and enqueues events. User callbacks
run in a different task, so a callback can safely await a reply action. Actions
are attempted once: a lost connection or deadline after a write is deliberately
reported as uncertain, never replayed on a later connection.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import random
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import aiohttp

_LOG = logging.getLogger(__name__)
_CQ = re.compile(r"\[CQ:([A-Za-z0-9_-]+)((?:,[^\]]*)?)\]")


class OneBotError(Exception):
    """A definite rejection or an action whose delivery cannot be established."""

    def __init__(
        self, message: str, *, code: str | int = "onebot_error", uncertain: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.uncertain = bool(uncertain)


def _id(value: Any) -> str:
    return "" if value is None else str(value)


def _cq_unescape(value: str) -> str:
    # Decode ampersands last to avoid decoding a doubly escaped CQ delimiter.
    return (
        value.replace("&#91;", "[")
        .replace("&#93;", "]")
        .replace("&#44;", ",")
        .replace("&amp;", "&")
    )


def _segments(message: Any) -> list[dict[str, Any]]:
    if isinstance(message, str):
        result: list[dict[str, Any]] = []
        offset = 0
        for match in _CQ.finditer(message):
            if match.start() > offset:
                result.append(
                    {"type": "text", "data": {"text": _cq_unescape(message[offset : match.start()])}}
                )
            data = {}
            for field in match.group(2).lstrip(",").split(","):
                key, separator, value = field.partition("=")
                if separator and key:
                    data[key] = _cq_unescape(value)
            result.append({"type": match.group(1), "data": data})
            offset = match.end()
        if offset < len(message):
            result.append({"type": "text", "data": {"text": _cq_unescape(message[offset:])}})
        return result
    if isinstance(message, Mapping):
        message = [message]
    if not isinstance(message, list):
        return []
    result = []
    for segment in message:
        if not isinstance(segment, Mapping) or not isinstance(segment.get("type"), str):
            continue
        data = segment.get("data", {})
        if not isinstance(data, Mapping):
            continue
        result.append({"type": segment["type"], "data": dict(data)})
    return result


def normalize_event(raw: Any, self_id: str = "") -> dict[str, Any] | None:
    """Normalize supported messages and notices; reject other protocol events.

    Media becomes a descriptive placeholder only; no media content is inferred.
    The original segment data is retained for command/plugin consumers.
    """
    if isinstance(raw, Mapping) and raw.get("post_type") == "notice":
        return normalize_notice(raw, self_id)
    if not isinstance(raw, Mapping) or raw.get("post_type") != "message":
        return None
    message_type = raw.get("message_type")
    if message_type not in ("private", "group"):
        return None
    sender = raw.get("sender")
    if not isinstance(sender, Mapping):
        sender = {}
    own_id = _id(self_id or raw.get("self_id", ""))
    user_id = _id(raw.get("user_id", sender.get("user_id", "")))
    if not user_id or (own_id and user_id == own_id):
        return None
    group_id = _id(raw.get("group_id", "")) if message_type == "group" else ""
    if message_type == "group" and not group_id:
        return None
    session_id = f"g:{group_id}" if message_type == "group" else f"p:{user_id}"
    segments = _segments(raw.get("message", raw.get("raw_message", "")))
    text_parts = []
    mentioned = False
    reply_to = ""
    placeholders = {
        "image": "[图片]",
        "record": "[语音]",
        "video": "[视频]",
        "file": "[文件]",
        "face": "[表情]",
        "mface": "[表情]",
        "forward": "[合并转发]",
        "json": "[卡片]",
        "xml": "[卡片]",
    }
    for segment in segments:
        kind, data = segment["type"], segment["data"]
        if kind == "text":
            text_parts.append(str(data.get("text", "")))
        elif kind == "at":
            target = _id(data.get("qq", ""))
            if own_id and target == own_id:
                mentioned = True
            elif target:
                text_parts.append("@全体" if target == "all" else f"@{target}")
        elif kind == "reply":
            if not reply_to:
                reply_to = _id(data.get("id", ""))
        elif kind in placeholders:
            text_parts.append(placeholders[kind])
        else:
            text_parts.append("[消息片段]")
    message_id = _id(raw.get("message_id", ""))
    if message_id:
        event_id = f"onebot:{own_id}:{session_id}:{message_id}"
    else:
        canonical = json.dumps(dict(raw), sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        event_id = f"onebot:{own_id}:{session_id}:sha256:{digest}"
    try:
        timestamp = int(raw.get("time", 0))
    except (TypeError, ValueError, OverflowError):
        timestamp = 0
    return {
        "event_id": event_id,
        "session_id": session_id,
        "message_id": message_id,
        "user_id": user_id,
        "group_id": group_id,
        "self_id": own_id,
        "message_type": message_type,
        "text": "".join(text_parts).strip(),
        "display_name": str(sender.get("card") or sender.get("nickname") or user_id),
        "segments": segments,
        "mentioned": mentioned,
        "reply_to": reply_to,
        "time": timestamp,
        "is_bot": sender.get('is_bot') is True or raw.get('is_bot') is True,
    }


def normalize_notice(raw: Mapping, self_id: str = "") -> dict[str, Any] | None:
    """Validate target identities before routing a OneBot/NapCat notice.

    NapCat private pokes carry sender_id (actor) separately from user_id
    (conversation peer). Only normalized, bounded fields are retained.
    """
    def qq(value):
        value = _id(value)
        return value if re.fullmatch(r"[1-9][0-9]{0,19}", value) else ""

    own = qq(self_id or raw.get("self_id"))
    if not own or (raw.get("self_id") is not None and qq(raw["self_id"]) != own):
        return None
    kind, sub = raw.get("notice_type"), raw.get("sub_type", "")
    allowed = {
        "group_increase": {"approve", "invite"},
        "group_decrease": {"leave", "kick", "kick_me"},
        "group_admin": {"set", "unset"},
        "group_card": {""},
        "notify": {"poke", "title"},
    }
    if kind not in allowed or sub not in allowed[kind]:
        return None
    group = qq(raw.get("group_id"))
    if raw.get("group_id") not in (None, "", 0, "0") and not group:
        return None
    if not group and (kind != "notify" or sub != "poke"):
        return None
    actor = qq(raw.get("sender_id") if not group and "sender_id" in raw else raw.get("user_id"))
    if not actor or actor == own:
        return None
    target = qq(raw.get("target_id")) if sub == "poke" else actor
    if sub == "poke" and target != own:
        return None
    if isinstance(raw.get("time"), bool):
        return None
    try:
        timestamp = int(raw.get("time", 0))
    except (TypeError, ValueError, OverflowError):
        return None
    if timestamp <= 0:
        return None
    message_type = "group" if group else "private"
    session_id = f"g:{group}" if group else f"p:{actor}"
    names = {"group_increase": "成员入群", "group_decrease": "成员退群", "group_admin": "管理员变更",
             "group_card": "群名片变更", "poke": "戳一戳", "title": "群头衔变更"}
    result = {
        "post_type": "notice", "notice_type": kind, "sub_type": sub,
        "session_id": session_id, "message_type": message_type, "message_id": "",
        "self_id": own, "user_id": actor, "target_id": target, "group_id": group,
        "operator_id": qq(raw.get("operator_id")), "time": timestamp,
        "text": f"[{names.get(sub, names.get(kind, kind))}] {actor}",
        "display_name": actor, "segments": [], "mentioned": False, "reply_to": "",
        "is_bot": raw.get("is_bot") is True,
    }
    for field in ("title", "card_old", "card_new"):
        value = raw.get(field, "")
        result[field] = value[:200] if isinstance(value, str) else ""
    # OneBot notices have no guaranteed unique message_id. Semantic identity
    # and server timestamp deduplicate reconnect deliveries; same-second pokes
    # from the same actor intentionally coalesce rather than cause reply storms.
    digest = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode("utf-8")).hexdigest()
    result["event_id"] = f"onebot-notice:{own}:{session_id}:{digest}"
    return result


@dataclass(slots=True)
class _Pending:
    future: asyncio.Future[Any]
    sent: bool = False


class OneBotClient:
    """Persistent forward WebSocket client; start() does not await connectivity."""

    def __init__(
        self,
        url: str,
        token: str,
        on_event: Callable[[dict[str, Any]], Awaitable[None] | None],
        self_id: str = "",
        action_timeout: float = 15.0,
        *,
        event_queue_size: int = 128,
        reconnect_min: float = 0.5,
        reconnect_max: float = 30.0,
    ) -> None:
        if action_timeout <= 0 or event_queue_size < 1 or reconnect_min <= 0 or reconnect_max < reconnect_min:
            raise ValueError("Invalid transport timeout, queue size, or reconnect bounds")
        self.url = url
        self.token = token or ""
        self.on_event = on_event
        self.self_id = _id(self_id)
        self.action_timeout = float(action_timeout)
        self._reconnect_min = float(reconnect_min)
        self._reconnect_max = float(reconnect_max)
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=event_queue_size)
        self._pending: dict[str, _Pending] = {}
        self._send_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._dispatch_task: asyncio.Task[None] | None = None
        self._running = False
        self._state = "stopped"
        self._attempts = 0
        self._connections = 0
        self._events_received = 0
        self._events_dispatched = 0
        self._events_dropped = 0
        self._dispatch_errors = 0
        self._action_timeouts = 0
        self._last_connected_at: float | None = None
        self._last_error = ""

    def _safe_endpoint(self) -> str:
        try:
            parts = urlsplit(self.url)
            host = parts.hostname or ""
            if ":" in host:
                host = f"[{host}]"
            port = f":{parts.port}" if parts.port is not None else ""
            return f"{parts.scheme}://{host}{port}{parts.path}"
        except ValueError:
            return "<invalid endpoint>"

    def _safe_error(self, error: Any) -> str:
        value = str(error).replace(self.url, self._safe_endpoint())
        if self.token:
            value = value.replace(self.token, "[redacted]")
        return value[:500]

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self._state,
            "connected": bool(self._ws is not None and not self._ws.closed),
            "url": self._safe_endpoint(),
            "self_id": self.self_id,
            "pending_actions": len(self._pending),
            "event_queue": self._events.qsize(),
            "event_queue_limit": self._events.maxsize,
            "connection_attempts": self._attempts,
            "reconnects": max(0, self._connections - 1),
            "events_received": self._events_received,
            "events_dispatched": self._events_dispatched,
            "events_dropped": self._events_dropped,
            "dispatch_errors": self._dispatch_errors,
            "action_timeouts": self._action_timeouts,
            "last_connected_at": self._last_connected_at,
            "last_error": self._last_error,
        }

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._running:
                return
            self._running = True
            self._state = "connecting"
            self._dispatch_task = asyncio.create_task(self._dispatch(), name="onebot-event-dispatch")
            self._runner_task = asyncio.create_task(self._run(), name="onebot-websocket")

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            self._running = False
            self._state = "stopping"
            self._fail_pending("Transport stopped", "stopped")
            current = asyncio.current_task()
            tasks = [task for task in (self._runner_task, self._dispatch_task) if task is not None and task is not current]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._runner_task = None
            self._dispatch_task = None
            self._ws = None
            while not self._events.empty():
                self._events.get_nowait()
                self._events.task_done()
            self._state = "stopped"

    def _fail_pending(self, message: str, code: str) -> None:
        for pending in tuple(self._pending.values()):
            if not pending.future.done():
                pending.future.set_exception(OneBotError(message, code=code, uncertain=pending.sent))
        self._pending.clear()

    async def _run(self) -> None:
        delay = self._reconnect_min
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15, sock_connect=10)) as session:
                self._session = session
                while self._running:
                    self._state = "connecting" if not self._connections else "reconnecting"
                    self._attempts += 1
                    try:
                        async with session.ws_connect(
                            self.url,
                            headers=headers,
                            timeout=aiohttp.ClientWSTimeout(ws_close=3),
                            heartbeat=30,
                            max_msg_size=4 * 1024 * 1024,
                        ) as ws:
                            self._ws = ws
                            self._state = "connected"
                            self._connections += 1
                            self._last_connected_at = time.time()
                            self._last_error = ""
                            delay = self._reconnect_min
                            await self._read(ws)
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        self._last_error = self._safe_error(error)
                    finally:
                        self._ws = None
                        self._fail_pending("OneBot connection lost; action was not replayed", "connection_lost")
                    if self._running:
                        self._state = "reconnecting"
                        await asyncio.sleep(delay + random.uniform(0, delay * 0.2))
                        delay = min(self._reconnect_max, delay * 2)
        finally:
            self._session = None
            self._ws = None
            self._fail_pending("Transport stopped", "stopped")
            self._state = "stopped"

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for message in ws:
            if message.type not in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                if message.type == aiohttp.WSMsgType.ERROR:
                    self._last_error = self._safe_error(ws.exception() or "WebSocket error")
                continue
            try:
                raw = json.loads(message.data)
            except (ValueError, UnicodeError, TypeError):
                continue
            if not isinstance(raw, dict):
                continue
            if "echo" in raw:
                pending = self._pending.get(str(raw["echo"]))
                if pending is None or pending.future.done():
                    continue
                retcode = raw.get("retcode", 0)
                if raw.get("status") == "failed" or retcode not in (0, "0", None):
                    detail = raw.get("message") or raw.get("wording") or "OneBot rejected the action"
                    pending.future.set_exception(
                        OneBotError(
                            self._safe_error(detail),
                            code=retcode if retcode not in (0, "0", None) else "action_failed",
                            uncertain=False,
                        )
                    )
                else:
                    pending.future.set_result(raw.get("data"))
                continue
            event = normalize_event(raw, self.self_id)
            if event is None:
                continue
            if not self.self_id and event["self_id"]:
                self.self_id = event["self_id"]
            self._events_received += 1
            try:
                self._events.put_nowait(event)
            except asyncio.QueueFull:
                # Never await queue space here: callbacks may be awaiting an RPC
                # whose response this same reader must still receive.
                self._events_dropped += 1

    async def _dispatch(self) -> None:
        while self._running:
            event = await self._events.get()
            try:
                result = self.on_event(event)
                if inspect.isawaitable(result):
                    await result
                self._events_dispatched += 1
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._dispatch_errors += 1
                self._last_error = self._safe_error(error)
                _LOG.warning("OneBot event callback failed: %s", self._last_error)
            finally:
                self._events.task_done()

    async def call(self, action: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        if not isinstance(action, str) or not action:
            raise OneBotError("An action name is required", code="invalid_action")
        if params is not None and not isinstance(params, dict):
            raise OneBotError("Action parameters must be a dictionary", code="invalid_params")
        duration = self.action_timeout if timeout is None else float(timeout)
        if duration <= 0:
            raise OneBotError("Action timeout must be positive", code="invalid_timeout")
        ws = self._ws
        if not self._running or ws is None or ws.closed:
            raise OneBotError("OneBot is not connected", code="not_connected")
        echo = uuid.uuid4().hex
        pending = _Pending(asyncio.get_running_loop().create_future())
        try:
            async with asyncio.timeout(duration):
                async with self._send_lock:
                    if not self._running or self._ws is not ws or ws.closed:
                        raise OneBotError("OneBot is not connected", code="not_connected")
                    self._pending[echo] = pending
                    pending.sent = True
                    try:
                        await ws.send_json({"action": action, "params": params or {}, "echo": echo})
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        raise OneBotError(
                            self._safe_error(error), code="send_failed", uncertain=True
                        ) from error
                return await asyncio.shield(pending.future)
        except TimeoutError as error:
            self._action_timeouts += 1
            raise OneBotError(
                "OneBot action timed out; it was not replayed", code="action_timeout", uncertain=pending.sent
            ) from error
        finally:
            self._pending.pop(echo, None)
            if not pending.future.done():
                pending.future.cancel()
            elif not pending.future.cancelled():
                # Consume a simultaneous disconnect exception even if send_json
                # raised first, avoiding an unobserved Future exception.
                pending.future.exception()

    async def send_message(self, message_type: str, target_id: str, segments: list[dict[str, Any]], timeout=None) -> Any:
        if message_type not in ("private", "group"):
            raise OneBotError("Message type must be private or group", code="invalid_target")
        if not _id(target_id):
            raise OneBotError("A target ID is required", code="invalid_target")
        if not isinstance(segments, list) or not segments:
            raise OneBotError("Message must be a nonempty segment array", code="invalid_message")
        for segment in segments:
            if not isinstance(segment, dict) or not isinstance(segment.get("type"), str) or not isinstance(segment.get("data"), dict):
                raise OneBotError("Invalid OneBot message segment", code="invalid_message")
        target_key = "group_id" if message_type == "group" else "user_id"
        return await self.call(
            f"send_{message_type}_msg", {target_key: _id(target_id), "message": segments}, timeout=timeout
        )
