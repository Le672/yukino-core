"""Local fake OneBot integration tests; no NapCat or QQ account is contacted."""

import asyncio
import json
import unittest

from aiohttp import web

from yukino_core.transport import OneBotClient, OneBotError, normalize_event


def event(message_id=1, *, user_id=42, message=None, message_type="private", group_id=77):
    result = {
        "post_type": "message", "message_type": message_type, "self_id": 900,
        "user_id": user_id, "message_id": message_id, "time": 100,
        "sender": {"user_id": user_id, "nickname": "测试", "card": "群名片"},
        "message": message if message is not None else [{"type": "text", "data": {"text": "你好"}}],
    }
    if message_type == "group":
        result["group_id"] = group_id
    return result


async def until(predicate, timeout=2):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


class NormalizationTests(unittest.TestCase):
    def test_segments_mentions_reply_images_and_ids(self):
        normalized = normalize_event(event(message_type="group", message=[
            {"type": "reply", "data": {"id": 12}},
            {"type": "at", "data": {"qq": "900"}},
            {"type": "text", "data": {"text": " 看看 "}},
            {"type": "image", "data": {"file": "secret.jpg", "url": "https://example.invalid/image"}},
        ]))
        self.assertEqual(normalized["session_id"], "g:77")
        self.assertEqual(normalized["user_id"], "42")
        self.assertEqual(normalized["self_id"], "900")
        self.assertEqual(normalized["reply_to"], "12")
        self.assertEqual(normalized["text"], "看看 [图片]")
        self.assertTrue(normalized["mentioned"])
        self.assertEqual(normalized["display_name"], "群名片")
        self.assertEqual(normalized["segments"][3]["data"]["file"], "secret.jpg")

    def test_cq_string_escaping_and_nonself_mentions(self):
        raw = event(message="[CQ:reply,id=21][CQ:at,qq=900] hi &#91;ok&#93;&amp; [CQ:at,qq=43][CQ:image,file=a&#44;b.jpg]")
        normalized = normalize_event(raw)
        self.assertEqual(normalized["text"], "hi [ok]& @43[图片]")
        self.assertEqual(normalized["reply_to"], "21")
        self.assertEqual(normalized["segments"][-1]["data"]["file"], "a,b.jpg")
        self.assertTrue(normalized["mentioned"])

    def test_session_isolation_and_stable_fallback_id(self):
        private = normalize_event(event(4))
        group = normalize_event(event(4, message_type="group"))
        self.assertNotEqual(private["event_id"], group["event_id"])
        self.assertEqual(private["session_id"], "p:42")
        raw = event()
        del raw["message_id"]
        self.assertEqual(normalize_event(raw)["event_id"], normalize_event(dict(reversed(list(raw.items()))))["event_id"])

    def test_ignored_events_and_configured_self_id(self):
        self.assertIsNone(normalize_event(event(user_id=900)))
        self.assertIsNone(normalize_event(event(user_id=42), self_id="42"))
        self.assertIsNone(normalize_event({"post_type": "meta_event", "meta_event_type": "heartbeat"}))
        self.assertIsNone(normalize_event({"post_type": "message_sent", "message_type": "private"}))
        self.assertIsNone(normalize_event([]))


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clients = []
        self.sockets = []
        self.actions = []
        self.auth_headers = []
        self.connects = 0
        self.on_connect = None
        self.on_action = None
        app = web.Application()
        app.router.add_get("/onebot", self.handle_ws)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/onebot".replace("http:", "ws:")

    async def asyncTearDown(self):
        for client in self.clients:
            await client.stop()
        for socket in self.sockets:
            await socket.close()
        await self.runner.cleanup()

    async def handle_ws(self, request):
        self.auth_headers.append(request.headers.get("Authorization"))
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.append(ws)
        self.connects += 1
        if self.on_connect:
            await self.on_connect(ws, self.connects)
        async for message in ws:
            if message.type == web.WSMsgType.TEXT:
                action = json.loads(message.data)
                self.actions.append(action)
                if self.on_action:
                    await self.on_action(ws, action)
                else:
                    await ws.send_json({"status": "ok", "retcode": 0, "data": {"message_id": 123}, "echo": action["echo"]})
        return ws

    async def client(self, callback=None, **kwargs):
        client = OneBotClient(
            self.url, "test-secret", callback or (lambda item: None), self_id="900",
            reconnect_min=0.01, reconnect_max=0.04, **kwargs,
        )
        self.clients.append(client)
        await client.start()
        await until(lambda: client.snapshot()["connected"])
        return client

    async def test_echo_correlation_authorization_and_out_of_order_responses(self):
        buffered = []

        async def action(ws, value):
            buffered.append(value)
            if len(buffered) == 2:
                for item in reversed(buffered):
                    await ws.send_json({"status": "ok", "retcode": 0, "data": item["action"], "echo": item["echo"]})

        self.on_action = action
        client = await self.client()
        results = await asyncio.gather(client.call("first"), client.call("second"))
        self.assertEqual(results, ["first", "second"])
        self.assertEqual(self.auth_headers, ["Bearer test-secret"])
        self.assertEqual(client.snapshot()["pending_actions"], 0)

    async def test_event_callback_can_await_a_reply_without_reader_deadlock(self):
        finished = asyncio.Event()
        events = []
        client = None

        async def callback(value):
            events.append(value)
            result = await client.send_message("private", value["user_id"], [{"type": "text", "data": {"text": "[CQ:at,qq=42] literal"}}])
            self.assertEqual(result["message_id"], 123)
            finished.set()

        client = await self.client(callback)
        await self.sockets[-1].send_json(event())
        await asyncio.wait_for(finished.wait(), 1)
        self.assertEqual(events[0]["text"], "你好")
        self.assertEqual(self.actions[0]["action"], "send_private_msg")
        self.assertEqual(self.actions[0]["params"]["user_id"], "42")
        self.assertIsInstance(self.actions[0]["params"]["message"], list)
        self.assertEqual(self.actions[0]["params"]["message"][0]["data"]["text"], "[CQ:at,qq=42] literal")

    async def test_definite_retcode_rejection(self):
        async def action(ws, value):
            await ws.send_json({"status": "failed", "retcode": 1404, "wording": "No target", "echo": value["echo"]})

        self.on_action = action
        client = await self.client()
        with self.assertRaises(OneBotError) as raised:
            await client.call("bad_action")
        self.assertEqual(raised.exception.code, 1404)
        self.assertFalse(raised.exception.uncertain)
        self.assertEqual(str(raised.exception), "No target")

    async def test_send_timeout_is_uncertain_and_never_replayed(self):
        async def action(ws, value):
            pass

        self.on_action = action
        client = await self.client(action_timeout=0.04)
        with self.assertRaises(OneBotError) as raised:
            await client.send_message("group", "77", [{"type": "text", "data": {"text": "once"}}])
        self.assertEqual(raised.exception.code, "action_timeout")
        self.assertTrue(raised.exception.uncertain)
        self.assertEqual(client.snapshot()["pending_actions"], 0)
        await self.sockets[-1].close()
        await until(lambda: self.connects >= 2 and client.snapshot()["connected"])
        await asyncio.sleep(0.03)
        self.assertEqual(len(self.actions), 1)

    async def test_disconnect_fails_pending_and_new_connection_can_send(self):
        async def action(ws, value):
            if value["action"] == "send_private_msg":
                await ws.close()
            else:
                await ws.send_json({"status": "ok", "retcode": 0, "data": "ready", "echo": value["echo"]})

        self.on_action = action
        client = await self.client()
        with self.assertRaises(OneBotError) as raised:
            await client.send_message("private", "42", [{"type": "text", "data": {"text": "once"}}])
        self.assertEqual(raised.exception.code, "connection_lost")
        self.assertTrue(raised.exception.uncertain)
        await until(lambda: self.connects >= 2 and client.snapshot()["connected"])
        self.assertEqual(await client.call("get_status"), "ready")
        self.assertEqual(sum(item["action"] == "send_private_msg" for item in self.actions), 1)
        self.assertGreaterEqual(client.snapshot()["reconnects"], 1)

    async def test_self_message_and_heartbeat_cannot_create_reply_loops(self):
        received = []
        client = await self.client(received.append)
        socket = self.sockets[-1]
        await socket.send_json(event(user_id=900))
        await socket.send_json({"post_type": "meta_event", "meta_event_type": "heartbeat", "self_id": 900})
        await socket.send_str("invalid json")
        await socket.send_json(event(2))
        await until(lambda: len(received) == 1)
        self.assertEqual(received[0]["message_id"], "2")
        self.assertEqual(client.snapshot()["events_received"], 1)

    async def test_bounded_queue_does_not_block_rpc_reader(self):
        blocked = asyncio.Event()
        release = asyncio.Event()

        async def callback(value):
            blocked.set()
            await release.wait()

        client = await self.client(callback, event_queue_size=1)
        socket = self.sockets[-1]
        await socket.send_json(event())
        await asyncio.wait_for(blocked.wait(), 1)
        for message_id in range(2, 10):
            await socket.send_json(event(message_id))
        self.assertEqual(await client.call("get_status", timeout=0.5), {"message_id": 123})
        self.assertEqual(client.snapshot()["event_queue"], 1)
        self.assertGreater(client.snapshot()["events_dropped"], 0)
        release.set()

    async def test_stop_fails_pending_and_disconnected_call_is_definite(self):
        async def action(ws, value):
            pass

        self.on_action = action
        client = await self.client()
        pending = asyncio.create_task(client.call("get_status"))
        await until(lambda: len(self.actions) == 1)
        await client.stop()
        with self.assertRaises(OneBotError) as raised:
            await pending
        self.assertEqual(raised.exception.code, "stopped")
        self.assertTrue(raised.exception.uncertain)
        self.assertEqual(client.snapshot()["state"], "stopped")
        with self.assertRaises(OneBotError) as disconnected:
            await client.call("get_status")
        self.assertFalse(disconnected.exception.uncertain)

    async def test_snapshot_redacts_url_and_callback_error_credentials(self):
        client = await self.client()
        client.url = "ws://user:password@localhost:3001/onebot?access_token=test-secret"
        client._last_error = client._safe_error(f"Failed {client.url} Bearer test-secret")
        snapshot = json.dumps(client.snapshot())
        self.assertNotIn("password", snapshot)
        self.assertNotIn("test-secret", snapshot)
        self.assertNotIn("access_token", snapshot)


if __name__ == "__main__":
    unittest.main()
