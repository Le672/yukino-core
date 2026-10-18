"""Kernel acceptance tests with real SQLite/resources and fake external peers."""

import asyncio
import base64
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from yukino_core.config import DEFAULTS
from yukino_core.core import Kernel
from yukino_core.plugins import PluginManager
from yukino_core.resources import ResourceLibrary
from yukino_core.store import Store
from yukino_core.transport import OneBotError, normalize_event


async def until(predicate, timeout=3):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.002)


def prompt_data(prompt):
    return json.loads(prompt.rsplit("\n", 1)[-1])


class FakeProvider:
    def __init__(self):
        self.prompts = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.error = None
        self.lock = asyncio.Lock()
        self.response = "测试模型回复"

    async def complete(self, prompt, timeout):
        async with self.lock:
            self.prompts.append(prompt)
            self.started.set()
            await self.release.wait()
            if self.error:
                raise self.error
            return self.response

    def snapshot(self):
        return {"calls": len(self.prompts), "healthy": True}


class FakeTransport:
    def __init__(self):
        self.sent = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.error = None

    async def send_message(self, message_type, target_id, segments):
        self.sent.append((message_type, target_id, copy.deepcopy(segments)))
        self.started.set()
        await self.release.wait()
        if self.error:
            raise self.error
        return {"message_id": str(1000 + len(self.sent))}

    def snapshot(self):
        return {"connected": True, "state": "fake"}


class KernelTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_link_in_group_runs_plugin_without_mention_or_model_reply(self):
        self.plugins.root.mkdir(exist_ok=True)
        (self.plugins.root / "video.py").write_text("def handle(text,context): return None\n", encoding="utf-8")
        item = dict(id="video", name="video", description="video", commands=["/视频解析"], worker="video.py",
                    auto_url_hosts=["www.bilibili.com"], auto_url_paths={"www.bilibili.com": "/video/"}, silent_ack=True)
        self.plugins.items.append(item)
        event = self.message("来看 https://www.bilibili.com/video/av123/", group="777")
        await self.kernel.on_event(event)
        self.assertEqual(self.provider.prompts, [])
        self.assertEqual(self.transport.sent, [])
        self.assertEqual(self.kernel.metrics["local_commands"], 1)

    async def test_automatic_video_link_still_obeys_group_allowlist_and_disable(self):
        item = dict(id="video", name="video", description="video", commands=["/视频解析"], worker="missing.py",
                    auto_url_hosts=["www.bilibili.com"], silent_ack=True)
        self.plugins.items.append(item)
        self.config.data["group_allowlist"] = ["888"]
        await self.kernel.on_event(self.message("https://www.bilibili.com/video/av123/", group="777"))
        self.config.data["group_allowlist"] = []
        self.config.plugin_flags["video"] = False
        await self.kernel.on_event(self.message("https://www.bilibili.com/video/av123/", group="777"))
        self.assertEqual(self.provider.prompts, [])
        self.assertEqual(self.transport.sent, [])
        self.assertEqual(self.kernel.metrics["ignored"], 2)

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yukino-core-test-")
        root = Path(self.temp.name)
        self.config = SimpleNamespace(
            data=copy.deepcopy(DEFAULTS), plugin_flags={}, self_id="900", owner_id="42", save=lambda: None
        )
        self.config.data["merge_window_ms"] = 0
        # This suite exercises the existing configured persona, independently of
        # the neutral defaults supplied to a fresh public installation.
        self.config.data['trigger_names'] = ['雪乃', 'Yukino']
        self.store = Store(root / "state.sqlite3")
        await self.store.open()
        resources = root / "resources"
        resources.mkdir()
        (resources / "curated.json").write_text(
            json.dumps({"jargon": [{"text": "典", "meaning": "典型的说法"}]}, ensure_ascii=False), encoding="utf-8"
        )
        self.resources = ResourceLibrary(resources)
        await self.resources.open()
        self.provider = FakeProvider()
        self.transport = FakeTransport()
        self.plugins = PluginManager(self.config, root / "plugins")
        self.kernel = Kernel(self.config, self.store, self.resources, self.provider, self.plugins)
        self.kernel.transport = self.transport
        self.counter = 0

    async def asyncTearDown(self):
        await self.kernel.close()
        await self.store.close()
        self.temp.cleanup()

    def message(self, text="你好", *, user="42", group=None, mentioned=False, reply_to=""):
        self.counter += 1
        segments = [{"type": "text", "data": {"text": text}}]
        if mentioned:
            segments.insert(0, {"type": "at", "data": {"qq": "900"}})
        if reply_to:
            segments.insert(0, {"type": "reply", "data": {"id": reply_to}})
        raw = dict(
            post_type="message", message_type="group" if group else "private", self_id="900",
            user_id=user, message_id=str(self.counter), message=segments, time=100,
            sender={"user_id": user, "nickname": "用户" + user},
        )
        if group:
            raw["group_id"] = group
        return normalize_event(raw)

    async def idle(self):
        await until(lambda: not self.kernel.workers)

    async def add_test_image(self):
        self.image_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+iGZ0AAAAASUVORK5CYII="
        )
        (self.resources.root / "cat.png").write_bytes(self.image_bytes)
        (self.resources.root / "curated.json").write_text(json.dumps({
            "images": [{"id": "img_test_cat", "path": "cat.png", "description": "得意猫哼哼",
                        "tags": ["猫", "得意", "开心", "哼哼"]}],
        }, ensure_ascii=False), encoding="utf-8")
        await self.resources.open()
        return await self.resources.sample_emoji("猫")

    def image_segments(self, sent):
        return [segment for segment in sent[2] if segment["type"] == "image"]

    def sent_text(self, sent):
        return "".join(segment["data"].get("text", "") for segment in sent[2] if segment["type"] == "text")

    async def test_explicit_natural_emoji_requests_send_real_bytes_without_model(self):
        resource = await self.add_test_image()
        for text in ("发点表情包", "来个猫猫表情包", "给我发张开心的表情", "雪乃，来张带猫的表情包吧"):
            await self.kernel.on_event(self.message(text))
        self.assertEqual(self.provider.prompts, [])
        self.assertEqual(len(self.transport.sent), 4)
        for sent in self.transport.sent:
            images = self.image_segments(sent)
            self.assertEqual(len(images), 1)
            self.assertTrue(images[0]["data"]["file"].startswith("base64://"))
            self.assertEqual(base64.b64decode(images[0]["data"]["file"][9:]), self.image_bytes)
        assistants = [row for row in await self.store.history("p:42") if row["role"] == "assistant"]
        self.assertEqual(len(assistants), 4)
        for row in assistants:
            self.assertEqual(row["content"], "[图片]")
            self.assertEqual(row["attachments"], [{"type": "image", "resource_id": resource["id"], "description": "得意猫哼哼"}])
            self.assertNotIn("base64://", json.dumps(row))
            self.assertNotIn(str(self.resources.root), json.dumps(row))
        self.assertEqual(self.kernel.metrics["local_images"], 4)

    async def test_natural_emoji_requests_still_require_group_invocation(self):
        await self.add_test_image()
        ambient = self.message("发点表情包", group="77")
        await self.kernel.on_event(ambient)
        self.assertEqual(await self.store.event_state(ambient["event_id"]), "ignored")
        self.assertEqual(self.transport.sent, [])
        await self.kernel.on_event(self.message("发点表情包", group="77", mentioned=True))
        await self.kernel.on_event(self.message("雪乃，发个猫猫表情包", group="77"))
        self.assertEqual(len(self.transport.sent), 2)
        self.assertTrue(all(self.image_segments(item) for item in self.transport.sent))
        self.assertEqual(self.provider.prompts, [])

    async def test_negated_emoji_requests_and_discussion_are_not_intercepted(self):
        await self.add_test_image()
        texts = ("不要发表情包", "别给我发猫猫表情包", "表情包是什么", "我今天发了一张表情包", "你喜欢猫猫表情包吗")
        for text in texts:
            await self.kernel.on_event(self.message(text))
            await self.idle()
        self.assertEqual(len(self.provider.prompts), len(texts))
        self.assertFalse(any(self.image_segments(item) for item in self.transport.sent))
        self.assertEqual(self.kernel.metrics["local_images"], 0)

    async def test_negative_user_intent_overrides_model_image_marker(self):
        await self.add_test_image()
        self.provider.response = "给你发一张。[[emoji:猫]]"
        await self.kernel.on_event(self.message("不要再给我发表情包"))
        await self.idle()
        self.assertEqual(len(self.provider.prompts), 1)
        self.assertEqual(self.image_segments(self.transport.sent[-1]), [])
        self.assertIn("没有附图", self.sent_text(self.transport.sent[-1]))
        self.assertNotIn("给你发", self.sent_text(self.transport.sent[-1]))

    async def test_specific_missing_emoji_theme_does_not_fall_back_to_random_image(self):
        await self.add_test_image()
        await self.kernel.on_event(self.message("发张宇航员表情包"))
        self.assertEqual(self.provider.prompts, [])
        self.assertEqual(len(self.transport.sent), 1)
        self.assertEqual(self.image_segments(self.transport.sent[0]), [])
        self.assertIn("没找到", self.sent_text(self.transport.sent[0]))
        await self.kernel.on_event(self.message("来个表情包"))
        self.assertEqual(len(self.image_segments(self.transport.sent[-1])), 1)

    async def test_disabled_emoji_plugin_blocks_explicit_requests_and_model_markers(self):
        await self.add_test_image()
        self.config.plugin_flags["emoji"] = False
        await self.kernel.on_event(self.message("发个猫猫表情包"))
        self.assertEqual(self.provider.prompts, [])
        self.provider.response = "给你发张猫图。[[emoji:猫]]"
        await self.kernel.on_event(self.message("你好"))
        await self.idle()
        self.assertEqual(len(self.provider.prompts), 1)
        self.assertFalse(any(self.image_segments(item) for item in self.transport.sent))
        for sent in self.transport.sent:
            self.assertIn("关着", self.sent_text(sent))
            self.assertNotIn("给你发", self.sent_text(sent))
            self.assertNotIn("[[emoji:", self.sent_text(sent))
        self.assertEqual(self.kernel.metrics["local_images"], 0)

    async def test_reserved_legacy_and_catalogue_markdown_markers_attach_one_local_image(self):
        resource = await self.add_test_image()
        responses = (
            "哼，给你。[[emoji:猫]][[emoji:开心]]",
            "[得意猫：哼哼.jpg] 别得意。",
            "这张。![得意猫](cat.png)",
            "按编号。[[emoji:" + resource["id"] + "]]",
        )
        for index, response in enumerate(responses, 1):
            self.provider.response = response
            await self.kernel.on_event(self.message("普通聊天"))
            await self.idle()
            self.assertEqual(len(self.transport.sent), index)
            sent = self.transport.sent[-1]
            self.assertEqual(len(self.image_segments(sent)), 1)
            self.assertEqual(base64.b64decode(self.image_segments(sent)[0]["data"]["file"][9:]), self.image_bytes)
            self.assertNotIn("[[emoji:", self.sent_text(sent))
            self.assertNotIn(".jpg", self.sent_text(sent))
            self.assertNotIn("![", self.sent_text(sent))
        self.assertEqual(len(self.provider.prompts), len(responses))
        self.assertEqual(len(await self.store.list_outbox()), len(responses))
        history = await self.store.history("p:42")
        self.assertTrue(all(len(row["attachments"]) == 1 for row in history if row["role"] == "assistant"))

    async def test_unmatched_model_marker_has_honest_text_and_no_fake_filename_claim(self):
        await self.add_test_image()
        self.provider.response = "给你发张宇航员图。[宇航员.png]"
        await self.kernel.on_event(self.message("你好"))
        await self.idle()
        sent = self.transport.sent[-1]
        self.assertEqual(self.image_segments(sent), [])
        self.assertIn("没有附图", self.sent_text(sent))
        self.assertNotIn("给你发", self.sent_text(sent))
        self.assertNotIn(".png", self.sent_text(sent))
        self.assertEqual((await self.store.history("p:42"))[-1]["attachments"], [])

    async def test_external_markdown_urls_are_never_resolved_or_sent(self):
        await self.add_test_image()
        sampled = []
        original = self.resources.sample_emoji

        async def sample(query=""):
            sampled.append(query)
            return await original(query)

        self.resources.sample_emoji = sample
        self.provider.response = "发你。![猫](https://example.invalid/private.png)"
        await self.kernel.on_event(self.message("你好"))
        await self.idle()
        self.assertEqual(sampled, [])
        self.assertEqual(self.image_segments(self.transport.sent[-1]), [])
        self.assertNotIn("example.invalid", self.sent_text(self.transport.sent[-1]))
        self.assertIn("没有附图", self.sent_text(self.transport.sent[-1]))

    async def test_untrusted_paths_oversize_bad_magic_and_resource_errors_send_no_images(self):
        await self.add_test_image()
        outside = Path(self.temp.name) / "outside.png"
        outside.write_bytes(self.image_bytes)
        oversized = self.resources.root / "oversized.png"
        with oversized.open("wb") as stream:
            stream.truncate(5_000_001)
        disguised = self.resources.root / "fake.jpg"
        disguised.write_text("not an image", encoding="utf-8")
        paths = [outside, oversized, disguised, self.resources.root / "missing.png"]
        for path in paths:
            with self.subTest(path=path.name):
                async def sample(query=""):
                    return {"id": "img_unsafe", "path": str(path), "description": "unsafe", "tags": ["猫"]}

                self.resources.sample_emoji = sample
                await self.kernel.on_event(self.message("发个猫猫表情包"))
                self.assertEqual(self.image_segments(self.transport.sent[-1]), [])
                self.assertIn("没找到", self.sent_text(self.transport.sent[-1]))
                self.assertNotIn(str(path), self.sent_text(self.transport.sent[-1]))

        async def broken(query=""):
            raise RuntimeError("private resource path/secret")

        self.resources.sample_emoji = broken
        await self.kernel.on_event(self.message("发点表情包"))
        self.assertEqual(self.image_segments(self.transport.sent[-1]), [])
        self.assertNotIn("secret", self.sent_text(self.transport.sent[-1]))
        self.assertEqual(self.provider.prompts, [])

    async def test_concurrent_duplicate_events_generate_and_send_once(self):
        message = self.message()
        await asyncio.gather(self.kernel.on_event(message), self.kernel.on_event(copy.deepcopy(message)))
        await self.idle()
        self.assertEqual(len(self.provider.prompts), 1)
        self.assertEqual(len(self.transport.sent), 1)
        self.assertEqual(self.kernel.metrics["duplicates"], 1)
        history = await self.store.history(message["session_id"])
        self.assertEqual([row["role"] for row in history], ["user", "assistant"])
        self.assertEqual(await self.store.event_state(message["event_id"]), "replied")

    async def test_private_group_triggers_and_context_are_isolated(self):
        private = self.message("PRIVATE_SECRET")
        await self.kernel.on_event(private)
        await self.idle()
        ambient = self.message("群里随便说一句", group="77")
        await self.kernel.on_event(ambient)
        self.assertEqual(await self.store.event_state(ambient["event_id"]), "ignored")
        self.assertEqual(len(self.provider.prompts), 1)
        group = self.message("GROUP_ONLY", group="77", mentioned=True)
        await self.kernel.on_event(group)
        await self.idle()
        group_data = prompt_data(self.provider.prompts[-1])
        self.assertNotIn("PRIVATE_SECRET", json.dumps(group_data, ensure_ascii=False))
        self.assertIn("GROUP_ONLY", group_data["current"])
        group_reply_id = str(1000 + len(self.transport.sent))
        self.assertTrue(await self.kernel.triggered(self.message("继续", group="77", reply_to=group_reply_id)))
        self.assertFalse(await self.kernel.triggered(self.message("继续", group="88", reply_to=group_reply_id)))
        self.assertTrue(await self.kernel.triggered(self.message("雪乃，今天如何", group="77")))
        self.assertFalse(await self.kernel.triggered(self.message("雪乃这个词在句子中间", group="77")))

    async def test_disable_flags_and_group_allowlist_apply_to_commands_too(self):
        self.config.data["private_enabled"] = False
        self.config.data["group_allowlist"] = ["77"]
        for message in [self.message("/ping"), self.message("/ping", group="88")]:
            await self.kernel.on_event(message)
            self.assertEqual(await self.store.event_state(message["event_id"]), "ignored")
        await self.kernel.on_event(self.message("/ping", group="77"))
        self.assertEqual(len(self.transport.sent), 1)
        self.config.data["group_enabled"] = False
        await self.kernel.on_event(self.message("/ping", group="77"))
        self.assertEqual(len(self.transport.sent), 1)
        self.assertEqual(self.provider.prompts, [])

    async def test_merge_window_uses_one_model_call_without_repeating_batch_in_history(self):
        self.config.data["merge_window_ms"] = 40
        first = self.message("第一句")
        second = self.message("第二句")
        await self.kernel.on_event(first)
        await self.kernel.on_event(second)
        await self.idle()
        self.assertEqual(len(self.provider.prompts), 1)
        self.assertEqual(len(self.transport.sent), 1)
        data = prompt_data(self.provider.prompts[0])
        self.assertEqual(data["history"], [])
        self.assertIn("第一句", data["current"])
        self.assertIn("第二句", data["current"])
        self.assertEqual(await self.store.event_state(first["event_id"]), "merged")
        self.assertEqual(await self.store.event_state(second["event_id"]), "replied")
        self.assertEqual(self.kernel.pending_count, 0)

    async def test_reset_discards_late_model_response_and_excludes_old_context(self):
        self.provider.release.clear()
        old = self.message("RESET_OLD_CONTEXT")
        await self.kernel.on_event(old)
        await asyncio.wait_for(self.provider.started.wait(), 1)
        await asyncio.wait_for(self.kernel.reset(old["session_id"]), 1)
        self.provider.release.set()
        await self.idle()
        self.assertEqual(self.transport.sent, [])
        self.assertEqual(await self.store.event_state(old["event_id"]), "reset_discarded")
        await self.kernel.on_event(self.message("new context"))
        await self.idle()
        data = prompt_data(self.provider.prompts[-1])
        self.assertNotIn("RESET_OLD_CONTEXT", json.dumps(data))
        self.assertEqual(len(self.transport.sent), 1)

    async def test_uncertain_send_stays_unknown_and_cannot_be_replayed(self):
        self.transport.error = OneBotError("timed out", code="action_timeout", uncertain=True)
        message = self.message()
        await self.kernel.on_event(message)
        await self.idle()
        self.assertEqual(await self.store.event_state(message["event_id"]), "send_unknown")
        self.assertEqual((await self.store.list_outbox())[0]["status"], "unknown")
        await self.kernel.on_event(copy.deepcopy(message))
        self.assertFalse(await self.kernel.reply(message, "retry"))
        self.assertEqual(len(self.transport.sent), 1)
        self.assertEqual(len(self.provider.prompts), 1)

    async def test_local_commands_bypass_provider_and_memories_stay_scoped(self):
        for text in ("/ping", "/记住 私聊限定记忆", "/记忆", "/梗 典", "/骰子 1d6"):
            await self.kernel.on_event(self.message(text))
        await self.kernel.on_event(self.message("/记忆", group="77"))
        self.assertEqual(self.provider.prompts, [])
        self.assertEqual(len(self.transport.sent), 6)
        self.assertIn("pong", self.transport.sent[0][2][0]["data"]["text"])
        self.assertIn("私聊限定记忆", self.transport.sent[2][2][0]["data"]["text"])
        self.assertNotIn("私聊限定记忆", self.transport.sent[-1][2][0]["data"]["text"])
        self.assertEqual(len(await self.store.recall("p:42")), 1)
        self.assertEqual(await self.store.recall("g:77"), [])

    async def test_lone_slash_is_local_help_in_private_and_group_messages(self):
        for message in (self.message("/"), self.message("/", group="77")):
            await self.kernel.on_event(message)
            self.assertEqual(await self.store.event_state(message["event_id"]), "replied")
        self.assertEqual(self.provider.prompts, [])
        self.assertEqual(len(self.transport.sent), 2)
        self.assertEqual([item[:2] for item in self.transport.sent], [("private", "42"), ("group", "77")])
        for _, _, segments in self.transport.sent:
            help_text = segments[0]["data"]["text"]
            self.assertIn("/帮助", help_text)
            self.assertIn("/计算", help_text)

    async def test_model_queue_is_bounded_and_overflow_is_recorded(self):
        self.config.data.update(max_pending=2, merge_window_ms=30)
        self.provider.release.clear()
        await self.kernel.on_event(self.message("first", user="41"))
        await asyncio.wait_for(self.provider.started.wait(), 1)
        await self.kernel.on_event(self.message("second", user="42"))
        overflow = self.message("overflow", user="43")
        await self.kernel.on_event(overflow)
        self.assertLessEqual(self.kernel.pending_count + self.kernel.active, 2)
        self.assertEqual(await self.store.event_state(overflow["event_id"]), "queue_full")
        self.assertEqual(len(self.provider.prompts), 1)
        self.provider.release.set()
        await self.idle()
        self.assertEqual(len(self.provider.prompts), 2)

    async def test_busy_session_cannot_jump_ahead_of_another_waiting_session(self):
        self.provider.release.clear()
        await self.kernel.on_event(self.message("A_FIRST", user="41"))
        await asyncio.wait_for(self.provider.started.wait(), 1)
        await self.kernel.on_event(self.message("A_SECOND", user="41"))
        await self.kernel.on_event(self.message("B_WAITING", user="42"))
        await until(lambda: self.kernel.active == 2)
        self.provider.release.set()
        await self.idle()
        currents = [prompt_data(item)["current"] for item in self.provider.prompts]
        self.assertEqual(len(currents), 3)
        self.assertIn("A_FIRST", currents[0])
        self.assertIn("B_WAITING", currents[1])
        self.assertIn("A_SECOND", currents[2])

    async def test_reset_of_session_waiting_for_model_skips_obsolete_model_call(self):
        self.provider.release.clear()
        await self.kernel.on_event(self.message("A_ACTIVE", user="41"))
        await asyncio.wait_for(self.provider.started.wait(), 1)
        waiting = self.message("B_OBSOLETE", user="42")
        await self.kernel.on_event(waiting)
        await until(lambda: self.kernel.active == 2)
        await asyncio.wait_for(self.kernel.reset(waiting["session_id"]), 1)
        self.provider.release.set()
        await self.idle()
        self.assertEqual(len(self.provider.prompts), 1)
        self.assertEqual(await self.store.event_state(waiting["event_id"]), "reset_discarded")

    async def test_shutdown_marks_active_and_pending_work_and_clears_queue(self):
        self.provider.release.clear()
        active = self.message("active")
        pending = self.message("pending")
        await self.kernel.on_event(active)
        await asyncio.wait_for(self.provider.started.wait(), 1)
        await self.kernel.on_event(pending)
        await asyncio.wait_for(self.kernel.close(), 1)
        self.assertEqual(await self.store.event_state(active["event_id"]), "shutdown_discarded")
        self.assertEqual(await self.store.event_state(pending["event_id"]), "shutdown_discarded")
        self.assertEqual(self.kernel.pending_count, 0)
        self.assertEqual(self.kernel.active, 0)
        self.assertEqual(self.transport.sent, [])

    async def test_reset_during_outbox_preparation_does_not_send_stale_reply(self):
        prepared = asyncio.Event()
        release = asyncio.Event()
        original = self.store.outbox_prepare

        async def delayed_prepare(*args, **kwargs):
            result = await original(*args, **kwargs)
            prepared.set()
            await release.wait()
            return result

        self.store.outbox_prepare = delayed_prepare
        message = self.message("stale reply")
        await self.kernel.on_event(message)
        await asyncio.wait_for(prepared.wait(), 1)
        await asyncio.wait_for(self.kernel.reset(message["session_id"]), 1)
        release.set()
        await self.idle()
        self.assertEqual(self.transport.sent, [])
        self.assertEqual(await self.store.event_state(message["event_id"]), "reset_discarded")

    async def test_unexpected_error_after_send_attempt_is_not_left_replayable(self):
        self.transport.error = RuntimeError("write outcome unknown")
        message = self.message()
        await self.kernel.on_event(message)
        await self.idle()
        self.assertEqual(len(self.transport.sent), 1)
        self.assertEqual((await self.store.list_outbox())[0]["status"], "unknown")

    async def test_concurrent_outbox_reply_claim_sends_only_once(self):
        message = self.message()
        await self.store.ingest_event(message)
        await asyncio.gather(self.kernel.reply(message, "one"), self.kernel.reply(message, "two"))
        self.assertEqual(len(self.transport.sent), 1)
        self.assertEqual((await self.store.list_outbox())[0]["status"], "sent")

    async def test_current_prompt_excludes_ingested_future_event_not_yet_queued(self):
        history_started = asyncio.Event()
        allow_history = asyncio.Event()
        future_ingested = asyncio.Event()
        allow_future_queue = asyncio.Event()
        original_history = self.store.history
        original_trigger = self.kernel.triggered
        old = self.message("current batch")
        future = self.message("FUTURE_NOT_CURRENT")

        async def delayed_history(*args, **kwargs):
            if not history_started.is_set():
                history_started.set()
                await allow_history.wait()
            return await original_history(*args, **kwargs)

        async def delayed_trigger(message):
            if message["event_id"] == future["event_id"]:
                future_ingested.set()
                await allow_future_queue.wait()
            return await original_trigger(message)

        self.store.history = delayed_history
        self.kernel.triggered = delayed_trigger
        await self.kernel.on_event(old)
        await asyncio.wait_for(history_started.wait(), 1)
        future_task = asyncio.create_task(self.kernel.on_event(future))
        try:
            await asyncio.wait_for(future_ingested.wait(), 1)
            allow_history.set()
            await asyncio.wait_for(self.provider.started.wait(), 1)
            data = prompt_data(self.provider.prompts[0])
            self.assertNotIn("FUTURE_NOT_CURRENT", json.dumps(data))
        finally:
            allow_history.set()
            allow_future_queue.set()
            await future_task
            await self.idle()


if __name__ == "__main__":
    unittest.main()
