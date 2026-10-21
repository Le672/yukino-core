from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import random
import re
import time
from collections import defaultdict
from pathlib import Path

from . import __version__
from .provider import ProviderError, build_prompt
from .transport import OneBotError
from .native_plugins import NativeExtensions, NATIVE_IDS
from .notice_plugins import NoticeExtensions
from .emoji_collector import EmojiCollector


def text_segment(text): return [{"type": "text", "data": {"text": text}}]


_EMOJI_REQUEST = re.compile(
    r"^(?:(?:请|麻烦|拜托|帮我|给我|给我们|给大家|能不能|能否|可不可以|可以|能)\s*)*"
    r"(?:发(?:送|来|给我)?|来|整|甩|分享|想要|想看|我想要|给)"
    r"(?P<theme>[^。！？?!\n]{0,40}?)(?:表情包|表情|贴图|emoji)"
    r"(?P<tail>[^。！？?!\n]{0,12})[。！？?!]*$", re.I,
)
_EMOJI_MARKERS = (
    re.compile(r"\[\[emoji:([^\]\r\n]{0,100})\]\]", re.I),
    re.compile(r"!\[([^\]\r\n]{0,120})\]\(([^)\r\n]{1,500})\)"),
    re.compile(r"\[([^\[\]\r\n]{1,100}\.(?:jpe?g|png|gif|webp))\]", re.I),
)


def _emoji_query(label):
    label = re.sub(r"\.(?:jpe?g|png|gif|webp)$", "", str(label).strip(), flags=re.I)
    if re.fullmatch(r"img_[A-Za-z0-9_-]+", label):
        return label
    label = re.sub(r"(?:表情包|表情|贴图|emoji)", "", label, flags=re.I)
    label = re.sub(r"^(?:(?:给我|给我们|给大家|一下|一点|一些|一个|一张|几个|几张|点|些|个|张|枚|套|的|\d{1,2})\s*)+", "", label)
    label = label.strip(" 的呀啊吧嘛呗呢啦哦喔~～，,。.!！?？:：'\"[]（）()")
    label = re.sub(r"^(?:带有|关于|有|带)", "", label)
    if label in {"随机", "随便", "任意", "随便来点", "随便的"}:
        return ""
    return re.sub(r"[：:，,_]+", " ", label.replace("猫猫", "猫").replace("狗狗", "狗"))[:100].strip()


def _emoji_forbidden(text):
    return bool(re.search(
        r"(?:不要|不用|不必|不想|不需要|停止|别)(?:(?:再|给我|给我们|给大家|继续|随便|乱|任何|使用|用|要)|\s){0,6}"
        r"(?:发|来|给|表情|贴图|emoji|图片)", text, re.I,
    ))


def emoji_request(text, trigger_names=()):
    """Return an explicit request's theme, including '' for random, or None."""
    text = text.strip()[:200]
    for name in sorted(trigger_names, key=len, reverse=True):
        text = re.sub(r"^" + re.escape(name) + r"[\s，,:：!！?？]+", "", text, flags=re.I)
    if _emoji_forbidden(text):
        return None
    if re.search(r"(?:是什么|什么意思|怎么做|怎么制作|如何制作|为什么|的意思|如何使用|怎么用)", text):
        return None
    match = _EMOJI_REQUEST.fullmatch(text)
    if not match or not re.fullmatch(
        r"(?:(?:给我|看看|看|行吗|好吗|可以吗|一个|一张|几个|几张|点|张|个)|[呀啊吧嘛呗呢啦哦喔了的~～，,\s])*",
        match["tail"],
    ):
        return None
    return _emoji_query(match["theme"])


class Kernel:
    def __init__(self, config, store, resources, provider, plugins):
        self.config, self.store, self.resources = config, store, resources
        self.provider, self.plugins = provider, plugins
        self.transport = None
        self.qq_status = {"online": None, "user_id": config.self_id}
        self.started = time.monotonic()
        self.pending = defaultdict(list)
        self.workers = {}
        self.epochs = defaultdict(int)
        self.send_locks = defaultdict(asyncio.Lock)
        self.last_queued = {}
        self.generation_gate = asyncio.Lock()
        self.closing = False
        self.pending_count = 0
        self.active = 0
        self.metrics = dict(received=0, replied=0, ignored=0, duplicates=0, failures=0,
                            merged=0, last_latency_ms=0, last_model_ms=0, local_commands=0,
                            local_images=0, image_unavailable=0, notices=0, notice_replied=0)
        self.native = NativeExtensions(self)
        self.notices = NoticeExtensions(self)
        self.collector = EmojiCollector(self)

    async def triggered(self, event):
        cfg = self.config.data
        if event['user_id'] in cfg['user_blocklist']: return False
        if cfg['ignore_bots'] and event.get('is_bot'): return False
        if event["message_type"] == "private":
            return cfg["private_enabled"] and (not cfg['private_allowlist'] or event['user_id'] in cfg['private_allowlist'])
        if not cfg["group_enabled"]: return False
        if event['group_id'] in cfg['group_blocklist']: return False
        if cfg["group_allowlist"] and event["group_id"] not in cfg["group_allowlist"]: return False
        if cfg['group_reply_all']: return True
        if cfg['mention_enabled'] and event.get("mentioned"): return True
        if cfg['reply_trigger_enabled'] and event.get("reply_to") and await self.store.is_bot_message(event["session_id"], event["reply_to"]): return True
        text = event["text"].lstrip()
        if cfg['trigger_prefix_enabled'] and any(re.match(re.escape(name) + r"(?:[\s，,:：!！?？]|$)", text, re.I) for name in cfg["trigger_names"]): return True
        # Explicit slash commands are a direct invocation, not ambient group talk.
        return self.plugins.match(text)[0] is not None or self.native.keyword_match(event) is not None

    async def on_event(self, event):
        if self.closing: return
        self.metrics["received"] += 1
        if not await self.store.ingest_event(event):
            self.metrics["duplicates"] += 1
            return
        if not getattr(self.config, "receive_enabled", True):
            self.metrics["ignored"] += 1
            await self.store.mark_event(event["event_id"], "receive_disabled")
            return
        if event.get("post_type") == "notice":
            self.metrics["notices"] += 1
            pid = self.notices.plugin_id(event)
            try:
                await self.notices.handle(event)
                if pid: self.plugins.errors.pop(pid, None)
            except asyncio.CancelledError:
                await self.store.mark_event(event["event_id"], "notice_interrupted", "服务停止；事件不会自动重放")
                raise
            except Exception:
                self.metrics["failures"] += 1
                if pid: self.plugins.errors[pid] = "事件互动执行失败，请核对参数与数据目录"
                await self.store.mark_event(event["event_id"], "notice_error", "事件互动执行失败")
            return
        collecting = self.collector.observe(event)
        if collecting and not any(segment.get('type') == 'text' and str(segment.get('data', {}).get('text', '')).strip()
                                  for segment in event.get('segments', [])):
            await self.store.mark_event(event['event_id'], 'emoji_observed')
            return
        if not await self.triggered(event):
            self.metrics["ignored"] += 1
            await self.store.mark_event(event["event_id"], "ignored")
            return
        # Commands run locally, ahead of the model queue. No extra model request.
        item, command = self.plugins.match(event["text"])
        if item:
            self.metrics["local_commands"] += 1
            if not self.plugins.enabled(item["id"]):
                await self.reply(event, "这个功能暂时关着，去管理面板就能打开。")
                return
            await self.command(event, item, command)
            return
        matched,keyword_text=self.native.keyword_reply(event)
        if matched:
            if keyword_text is not None:await self.reply(event,keyword_text)
            else:await self.store.mark_event(event['event_id'],'keyword_cooldown')
            return
        image_query = emoji_request(event["text"], self.config.data["trigger_names"])
        if image_query is not None:
            self.metrics["local_commands"] += 1
            await self._send_emoji(event, image_query)
            return
        now = time.monotonic()
        if now - self.last_queued.get(event['session_id'], -float('inf')) < self.config.data['session_cooldown_ms'] / 1000:
            await self.store.mark_event(event['event_id'], 'cooldown')
            return
        if len(self.pending.get(event['session_id'], [])) >= self.config.data['session_max_pending']:
            await self.store.mark_event(event['event_id'], 'session_queue_full')
            return
        if self.pending_count + self.active >= self.config.data["max_pending"]:
            await self.reply(event, "消息有点多，等我回完再说一句吧。")
            await self.store.mark_event(event["event_id"], "queue_full")
            return
        event = {**event, "epoch": self.epochs[event["session_id"]], "received_mono": time.monotonic()}
        self.pending[event["session_id"]].append(event)
        self.last_queued[event['session_id']] = now
        self.pending_count += 1
        await self.store.mark_event(event["event_id"], "queued")
        sid = event["session_id"]
        if sid not in self.workers:
            self.workers[sid] = asyncio.create_task(self._session_worker(sid), name="conversation:" + sid)

    async def reset(self, sid):
        self.epochs[sid] += 1
        await self.store.reset_session(sid)
        for event in self.pending.pop(sid, []):
            self.pending_count -= 1
            await self.store.mark_event(event["event_id"], "reset_discarded")

    async def _session_worker(self, sid):
        try:
            while self.pending.get(sid) and not self.closing:
                await asyncio.sleep(self.config.data["merge_window_ms"] / 1000)
                batch = self.pending.pop(sid, [])
                self.pending_count -= len(batch)
                if not batch: continue
                self.active += 1
                try:
                    async with self.generation_gate:
                        await self._generate(batch)
                except asyncio.CancelledError:
                    for event in batch: await self.store.mark_event(event["event_id"], "shutdown_discarded")
                    raise
                except Exception:
                    self.metrics["failures"] += 1
                    for event in batch: await self.store.mark_event(event["event_id"], "internal_error", "会话处理失败")
                finally: self.active -= 1
        finally:
            self.workers.pop(sid, None)

    async def _generate(self, batch):
        event = batch[-1]
        sid, epoch = event["session_id"], event["epoch"]
        if self.epochs[sid] != epoch:
            for item in batch: await self.store.mark_event(item["event_id"], "reset_discarded")
            return
        for item in batch: await self.store.mark_event(item["event_id"], "generating")
        self.metrics["merged"] += len(batch) - 1
        # Snapshot excludes messages that arrived after this batch and avoids repeating it.
        history = await self.store.history(sid, limit=self.config.data["history_limit"] + len(batch) + 1,
                                          before_event_id=event["event_id"])
        incoming_ids = {item["event_id"] for item in batch}
        history = [row for row in history if row.get("event_id") not in incoming_ids]
        # New events in the pending queue are not part of the current prompt.
        future_ids = {item["event_id"] for item in self.pending.get(sid, [])}
        history = [row for row in history if row.get("event_id") not in future_ids][-self.config.data["history_limit"]:] if self.config.data["history_limit"] else []
        history = [row for row in history if row.get('user_id') not in self.config.data['user_blocklist']]
        current = "\n".join(f'[{index + 1}] {x.get("display_name") or x["user_id"]}: {x["text"]}' for index, x in enumerate(batch))
        memories = await self.store.recall(sid, limit=self.config.data['memory_limit']) if self.config.data['memory_enabled'] and self.config.data['memory_limit'] else []
        resources = await self.resources.lookup(event["text"], limit=self.config.data['resource_limit']) if self.config.data['resource_lookup_enabled'] and self.config.data['resource_limit'] else {}
        prompt = build_prompt(self.config.data, history, memories, resources, current,
                              owner_ids=getattr(self.config, 'owner_ids', [getattr(self.config, 'owner_id', '')]),
                              current_user_ids=[item['user_id'] for item in batch])
        began = time.monotonic()
        try:
            route = getattr(self.config, 'session_providers', {}).get(sid)
            if route: reply = await self.provider.complete(prompt, self.config.data["provider_timeout"], provider_id=route)
            else: reply = await self.provider.complete(prompt, self.config.data["provider_timeout"])
            self.metrics["last_model_ms"] = round((time.monotonic() - began) * 1000)
            if self.epochs[sid] != epoch:
                for item in batch: await self.store.mark_event(item["event_id"], "reset_discarded")
                return
            text, segments, attachments = await self._compose_model_reply(
                reply, allow_images=not any(_emoji_forbidden(item["text"]) for item in batch)
            )
            await self.reply(event, text, segments=segments, attachments=attachments, epoch=epoch)
            for item in batch[:-1]: await self.store.mark_event(item["event_id"], "merged")
            self.metrics["last_latency_ms"] = round((time.monotonic() - batch[0]["received_mono"]) * 1000)
        except ProviderError as exc:
            self.metrics["failures"] += 1
            if self.epochs[sid] == epoch: await self.reply(event, str(exc), epoch=epoch)
            for item in batch: await self.store.mark_event(item["event_id"], "provider_failed", str(exc))

    async def _emoji_attachment(self, query):
        """Resolve only the local catalogue and read a bounded, verified image."""
        try:
            emoji = await self.resources.sample_emoji(query)
            if not emoji:
                return None

            def read_local():
                roots = [Path(self.resources.root).resolve()]
                if self.resources.legacy_root is not None:
                    legacy = Path(self.resources.legacy_root).resolve()
                    media = (legacy / "data" / "emoji").resolve()
                    if media.is_relative_to(legacy):
                        roots.append(media)
                path = Path(emoji["path"]).resolve(strict=True)
                if not any(path.is_relative_to(root) for root in roots):
                    return None
                if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                    return None
                if not 0 < path.stat().st_size <= 5_000_000:
                    return None
                with path.open("rb") as stream:
                    payload = stream.read(5_000_001)
                if not payload or len(payload) > 5_000_000:
                    return None
                is_image = (
                    payload.startswith(b"\x89PNG\r\n\x1a\n") or payload.startswith(b"\xff\xd8\xff")
                    or payload.startswith((b"GIF87a", b"GIF89a"))
                    or (payload.startswith(b"RIFF") and payload[8:12] == b"WEBP")
                )
                return payload if is_image else None

            payload = await asyncio.to_thread(read_local)
            if payload is None or not emoji.get("id"):
                return None
            if emoji["id"] != "img_" + hashlib.sha256(payload).hexdigest()[:24]:
                return None
            segment = {"type": "image", "data": {"file": "base64://" + base64.b64encode(payload).decode("ascii")}}
            attachment = {"type": "image", "resource_id": str(emoji["id"]), "description": str(emoji.get("description", ""))[:500]}
            return segment, attachment
        except Exception:
            return None

    async def _send_emoji(self, event, query):
        if not self.config.data['emoji_enabled'] or not self.plugins.enabled("emoji"):
            return await self.reply(event, "表情功能现在关着，去管理面板就能打开。")
        image = await self._emoji_attachment(query)
        if image is None:
            self.metrics["image_unavailable"] += 1
            return await self.reply(event, "没找到对应的可用表情，换个关键词试试？" if query else "现在没有可用的表情图片。")
        segment, attachment = image
        return await self.reply(event, segments=[segment], attachments=[attachment])

    async def _compose_model_reply(self, reply, allow_images=True):
        reply = reply[:self.config.data["response_max_chars"] + 1024]
        markers = []
        for kind, pattern in enumerate(_EMOJI_MARKERS):
            markers.extend((match.start(), match.end(), kind, match) for match in pattern.finditer(reply))
        markers.sort(key=lambda item: (item[0], -item[1]))
        # Markdown alt text can itself resemble a legacy marker. Remove each
        # outer span once and attach no more than one actual catalogue image.
        filtered = []
        for marker in markers:
            if not filtered or marker[0] >= filtered[-1][1]:
                filtered.append(marker)
        if not filtered:
            text = reply[:self.config.data["response_max_chars"]]
            return text, text_segment(text), []
        cursor, pieces = 0, []
        for start, end, _, _ in filtered:
            pieces.append(reply[cursor:start])
            cursor = end
        pieces.append(reply[cursor:])
        text = "".join(pieces).strip()[:self.config.data["response_max_chars"]]
        _, _, kind, match = filtered[0]
        query = match[1]
        unsafe = False
        if kind == 1:
            target = match[2].strip()
            unsafe = bool(re.search(r"[:/\\]|\.\.", target))
            query = target if target.startswith("img_") else (match[1] or target)
        query = _emoji_query(query)
        unsafe = unsafe or bool(re.search(r"[:/\\]|\.\.", query))
        if not allow_images:
            text = "这次没有附图。"
            return text, text_segment(text), []
        if not self.config.data['emoji_enabled'] or not self.config.data['emoji_max_per_reply'] or not self.plugins.enabled("emoji"):
            text = "表情功能现在关着，这次没有附图。"
            return text, text_segment(text), []
        image = None if unsafe else await self._emoji_attachment(query)
        if image is None:
            self.metrics["image_unavailable"] += 1
            text = "没找到对应的可用表情，这次没有附图。"
            return text, text_segment(text), []
        segment, attachment = image
        segments = text_segment(text) if text else []
        images, attachments = [segment], [attachment]
        for _, _, extra_kind, extra_match in filtered[1:self.config.data['emoji_max_per_reply']]:
            extra_query = _emoji_query(extra_match[1])
            if extra_kind == 1 or re.search(r'[:/\\]|\.\.', extra_query): continue
            extra = await self._emoji_attachment(extra_query)
            if extra and extra[1]['resource_id'] not in {a['resource_id'] for a in attachments}:
                images.append(extra[0]); attachments.append(extra[1])
        return text, segments + images, attachments

    async def reply(self, event, text=None, segments=None, epoch=None, attachments=None):
        if epoch is not None and self.epochs[event["session_id"]] != epoch: return False
        segments = segments if segments is not None else text_segment(text or "")
        row = await self.store.outbox_prepare(event["event_id"], event["session_id"], segments)
        async with self.send_locks[event["session_id"]]:
            return await self._reply_locked(event, row, text, segments, epoch, attachments)

    async def _reply_locked(self, event, row, text=None, segments=None, epoch=None, attachments=None):
        if row["status"] != "prepared": return False
        if event.get("post_type") == "notice" and (self.closing or not getattr(self.config, "receive_enabled", True)):
            await self.store.outbox_mark(row["id"], "cancelled", error="通知接收已关闭")
            await self.store.mark_event(event["event_id"], "receive_disabled")
            return False
        if epoch is not None and self.epochs[event["session_id"]] != epoch:
            await self.store.outbox_mark(row["id"], "cancelled", error="会话已重置")
            await self.store.mark_event(event["event_id"], "reset_discarded")
            return False
        if epoch is not None and self.epochs[event["session_id"]] != epoch: return False
        if not await self.store.outbox_claim(row["id"]): return False
        if epoch is not None and self.epochs[event["session_id"]] != epoch:
            await self.store.outbox_mark(row["id"], "cancelled", error="会话已重置")
            await self.store.mark_event(event["event_id"], "reset_discarded")
            return False
        try:
            target = event["group_id"] if event["message_type"] == "group" else event["user_id"]
            data = await self.transport.send_message(event["message_type"], target, segments)
            message_id = str((data or {}).get("message_id", ""))
            await self.store.outbox_mark(row["id"], "sent", message_id)
            if epoch is None or self.epochs[event["session_id"]] == epoch:
                if event.get("post_type") == "notice":
                    await self.store.mark_event(event["event_id"], "notice_replied")
                else:
                    metadata = {"attachments": attachments} if attachments else {}
                    await self.store.add_message(event["session_id"], "assistant", text or "[图片]", user_id=self.config.self_id,
                                                 display_name=self.config.data["name"], message_id=message_id, **metadata)
                    await self.store.mark_event(event["event_id"], "replied")
            else:
                # A QQ send already in flight cannot be recalled; never seed the new context with it.
                await self.store.mark_event(event["event_id"], "sent_during_reset")
            self.metrics["replied"] += 1
            self.metrics["local_images"] += sum(item.get("type") == "image" for item in (attachments or []))
            return True
        except OneBotError as exc:
            state = "unknown" if exc.uncertain else "failed"
            await self.store.outbox_mark(row["id"], state, error="QQ发送状态不确定" if exc.uncertain else "QQ拒绝或未连接")
            await self.store.mark_event(event["event_id"], "send_" + state, "QQ发送未确认")
            self.metrics["failures"] += 1
            return False
        except asyncio.CancelledError:
            await self.store.outbox_mark(row["id"], "unknown", error="发送期间服务停止")
            raise
        except Exception:
            await self.store.outbox_mark(row["id"], "unknown", error="发送结果无法确认")
            await self.store.mark_event(event["event_id"], "send_unknown", "发送结果无法确认")
            self.metrics["failures"] += 1
            return False

    async def command(self, event, item, command):
        text = event["text"].strip()
        arg = text.partition(" ")[2].strip()
        sid = event["session_id"]
        group_owner = event["message_type"] != "group" or event["user_id"] in getattr(self.config, 'owner_ids', [self.config.owner_id])
        plugin_settings = self.plugins.settings(item['id'])
        if item['id'] == 'emoji_collector':
            try:
                result = await self.collector.handle(event)
                self.plugins.errors.pop(item['id'], None)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.plugins.errors[item['id']] = '表情收集暂时不可用，请检查收集记录与资源目录'
                result = '表情收集暂时不可用，请在插件面板检查设置。'
            await self.reply(event, result)
        elif item['id'] in NATIVE_IDS:
            try:
                result=await self.native.handle(event,item,command)
                self.plugins.errors.pop(item['id'],None)
            except Exception:
                self.plugins.errors[item['id']]='原生插件执行失败，请核对配置与数据目录'
                self.metrics['failures']+=1
                result='这个插件暂时不可用，其他功能仍可使用。'
            await self.reply(event,result)
        elif "worker" in item:
            prefix = self.config.data['command_prefix']
            if command != '@url' and prefix != '/' and text.startswith(prefix): text = '/' + text[len(prefix):]
            result = await self.plugins.execute(item, text, {"session_id": sid, "user_id": event["user_id"]})
            if result is None and item.get("silent_ack"):
                await self.store.mark_event(event["event_id"], "plugin_queued")
            else:
                await self.reply(event, result or "插件执行完成。")
        elif command == "/帮助":
            prefix = self.config.data['command_prefix']
            commands = [" ".join(prefix+c[1:] for c in i["commands"]) for i in self.plugins.list() if i["enabled"] and i["commands"]]
            await self.reply(event, "Yukino Core\n" + "\n".join(commands) + "\n" + plugin_settings['help_footer'])
        elif command == "/ping":
            await self.reply(event, "pong · 本地指令直接回复，不经过网页模型")
        elif command == "/状态":
            await self.reply(event, f'Yukino Core · 运行{int(time.monotonic()-self.started)}秒\n待处理{self.pending_count} · 生成中{self.active}\n上次模型{self.metrics["last_model_ms"]/1000:.1f}秒 · 累计回复{self.metrics["replied"]}')
        elif command == "/新对话":
            if not group_owner: return await self.reply(event, "群聊重置请让主人来，私聊可以直接重置。")
            await self.reset(sid)
            await self.reply(event, plugin_settings['reset_reply'])
        elif command == "/记住":
            if not group_owner: return await self.reply(event, "群里的长期记忆让主人来设置吧。")
            if not arg: return await self.reply(event, "用法：/记住 你想让我记住的事")
            mid = await self.store.remember(sid, arg[:plugin_settings['max_length']])
            await self.reply(event, f"记下了，编号{mid}。只在这个会话里使用。")
        elif command == "/记忆":
            rows = await self.store.recall(sid, limit=plugin_settings['list_limit'])
            await self.reply(event, "\n".join(f'{r["id"]}. {r["text"]}' for r in rows) or "这个会话还没有长期记忆。")
        elif command == "/忘记":
            if not group_owner: return await self.reply(event, "群里的长期记忆让主人来删除吧。")
            done = await self.store.forget(sid, int(arg)) if arg.isdigit() else False
            await self.reply(event, "已经忘掉了。" if done else "没找到这条记忆。用法：/忘记 编号")
        elif command == "/表情":
            await self._send_emoji(event, _emoji_query(arg))
        elif command in ("/资料", "/梗"):
            result = await self.resources.lookup(arg, limit=plugin_settings['result_limit'])
            lines = []
            for key in ("jargon", "knowledge"):
                for row in result.get(key, []):
                    value = row.get("text") or row.get("meaning") or str(row)
                    if key == "jargon" and row.get("meaning"): value += "：" + row["meaning"]
                    lines.append(value[:600])
            await self.reply(event, ("\n\n".join(lines)[:plugin_settings['response_chars']] if lines else "没有找到，试试：/梗 典") )
        elif command == "/骰子":
            match = re.fullmatch(r"(?:(\d{1,3})d)?(\d{1,7})", arg or "1d6", re.I)
            if not match: return await self.reply(event, "用法：/骰子 2d6（最多20颗，1—100000面）")
            count, faces = int(match[1] or 1), int(match[2])
            if not 1 <= count <= plugin_settings['max_count'] or not 1 <= faces <= plugin_settings['max_faces']: return await self.reply(event, "骰子数量或面数超出范围。")
            values = [random.SystemRandom().randint(1, faces) for _ in range(count)]
            await self.reply(event, f'{count}d{faces}: ' + ", ".join(map(str, values)) + f" = {sum(values)}")

    async def isolated_test(self, text):
        started = time.monotonic()
        prompt = build_prompt(self.config.data, [], [], {}, text)
        async with self.generation_gate:
            result = await self.provider.complete(prompt, self.config.data["provider_timeout"])
        return dict(text=result[:self.config.data["response_max_chars"]], latency_ms=round((time.monotonic()-started)*1000), model_calls=1, qq_sent=False)

    async def status(self):
        plugins = self.plugins.list()
        return dict(version=__version__, uptime=int(time.monotonic() - self.started),
                    transport={**self.transport.snapshot(), "qq": dict(self.qq_status)}, provider=self.provider.snapshot(),
                    queue=dict(pending=self.pending_count, active=self.active, max=self.config.data["max_pending"]),
                    metrics=dict(self.metrics), plugins=dict(total=len(plugins), enabled=sum(p["enabled"] for p in plugins)),
                    store=await self.store.stats(), resources=await self.resources.status(),
                    emoji_collection=self.collector.snapshot())

    async def close(self):
        self.closing = True
        await self.collector.close()
        await self.native.close()
        for task in list(self.workers.values()): task.cancel()
        for task in list(self.workers.values()):
            with contextlib.suppress(asyncio.CancelledError): await task
        for events in self.pending.values():
            for event in events: await self.store.mark_event(event["event_id"], "shutdown_discarded")
        self.pending.clear()
        self.pending_count = 0
        await self.plugins.close()
