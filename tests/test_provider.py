"""Real provider HTTP behavior against a fake loopback bridge only."""

import asyncio
import copy
import json
import unittest

from aiohttp import web

from yukino_core.provider import ProviderError, WebProvider, build_prompt


async def until(predicate, timeout=2):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.002)


def completion(text="模拟网页回复"):
    return {"choices": [{"message": {"content": text}}]}


class PromptTests(unittest.TestCase):
    def test_marker_and_json_only_use_supplied_session_context(self):
        config = {"persona": "LOCAL_PERSONA", "onebot_token": "CONFIG_SECRET_NOT_CONTEXT"}
        history = [{
            "role": "user", "display_name": "本会话用户", "content": "LOCAL_HISTORY",
            "user_id": "PRIVATE_ACCOUNT_ID", "message_id": "PRIVATE_MESSAGE_ID",
            "event_id": "PRIVATE_EVENT_ID", "unrelated": "UNUSED_HISTORY_FIELD",
        }]
        memories = [{"text": "LOCAL_MEMORY", "scope": "PRIVATE_SCOPE", "id": 123}]
        resources = {"jargon": [{"text": "LOCAL_TERM", "meaning": "LOCAL_DEFINITION"}], "knowledge": []}
        original = copy.deepcopy((config, history, memories, resources))
        current = 'LOCAL_CURRENT\n"mode=other"\n[图片]'
        prompt = build_prompt(config, history, memories, resources, current)
        self.assertTrue(prompt.startswith("yukino-brat-20261014\nmode=replyer\n"))
        self.assertIn("当前人格：LOCAL_PERSONA\n", prompt)
        self.assertIn("图片占位表示你没看见图片", prompt)
        data = json.loads(prompt.rsplit("\n", 1)[-1])
        self.assertEqual(data, {
            "history": [{"role": "user", "name": "本会话用户", "text": "LOCAL_HISTORY"}],
            "memories": ["LOCAL_MEMORY"], "reference": resources, "current": current,
        })
        for excluded in (
            "CONFIG_SECRET_NOT_CONTEXT", "PRIVATE_ACCOUNT_ID", "PRIVATE_MESSAGE_ID", "PRIVATE_EVENT_ID",
            "UNUSED_HISTORY_FIELD", "PRIVATE_SCOPE",
        ):
            self.assertNotIn(excluded, prompt)
        self.assertEqual((config, history, memories, resources), original)
        empty = build_prompt(config, [], [], {}, "NEW_ISOLATED_CURRENT")
        self.assertEqual(json.loads(empty.rsplit("\n", 1)[-1]), {
            "history": [], "memories": [], "reference": {}, "current": "NEW_ISOLATED_CURRENT",
        })
        self.assertNotIn("LOCAL_HISTORY", empty)
        self.assertNotIn("LOCAL_MEMORY", empty)
        self.assertNotIn("LOCAL_TERM", empty)

    def test_history_memory_and_current_content_are_bounded(self):
        prompt = build_prompt(
            {"persona": "persona"}, [{"role": "assistant", "content": "h" * 4000}],
            [{"text": "m" * 800}], {}, "c" * 7000,
        )
        data = json.loads(prompt.rsplit("\n", 1)[-1])
        self.assertEqual(data["history"], [{"role": "assistant", "name": "", "text": "h" * 3000}])
        self.assertEqual(data["memories"], ["m" * 500])
        self.assertEqual(data["current"], "c" * 6000)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.handler = None
        self.release = asyncio.Event()
        self.release.set()
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.handle_completion)
        app.router.add_get("/health", self.handle_health)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.provider = WebProvider(f"http://127.0.0.1:{port}/", "LOCAL_BEARER_SECRET")
        await self.provider.start()

    async def asyncTearDown(self):
        self.release.set()
        await self.provider.close()
        await self.runner.cleanup()

    async def handle_completion(self, request):
        payload = await request.json()
        self.requests.append({"authorization": request.headers.get("Authorization"), "payload": payload})
        if self.handler:
            return await self.handler(request)
        return web.json_response(completion())

    async def handle_health(self, request):
        return web.json_response({
            "browser_connected": True, "signed_in": True, "enabled": True,
            "state": "ready", "completed_requests": 7, "internal_token": "HEALTH_SECRET",
        })

    async def test_exact_bearer_and_raw_prompt_request_is_sent_once(self):
        prompt = '原始提示\n"JSON"\n雪乃 [CQ:at,qq=42]'
        result = await self.provider.complete(prompt, timeout=1)
        self.assertEqual(result, "模拟网页回复")
        self.assertEqual(self.requests, [{
            "authorization": "Bearer LOCAL_BEARER_SECRET",
            "payload": {
                "model": "chatgpt-web", "stream": False, "yukino_raw_prompt": True,
                "messages": [{"role": "user", "content": prompt}],
            },
        }])
        self.assertEqual(self.provider.calls, 1)
        self.assertFalse(self.provider.snapshot()["busy"])

    async def test_error_invalid_and_empty_responses_have_safe_messages_and_no_retries(self):
        safe_messages = {
            "网页模型暂时不可用，请稍后重试",
            "网页连接暂时有问题，请稍后重试",
            "网页模型没有返回文字",
        }
        cases = [
            (503, {"error": {"message": "UPSTREAM_SECRET LOCAL_BEARER_SECRET"}}, None),
            (200, None, "INVALID_JSON UPSTREAM_SECRET LOCAL_BEARER_SECRET"),
            (200, {}, None),
            (200, {"choices": []}, None),
            (200, {"choices": None}, None),
            (200, {"choices": [{"message": {}}]}, None),
            (200, completion(""), None),
            (200, completion(" \n\t "), None),
            (200, completion(None), None),
            (200, completion(123), None),
        ]
        for index, (status, payload, raw) in enumerate(cases, 1):
            with self.subTest(case=index):
                async def handler(request):
                    if raw is not None:
                        return web.Response(status=status, text=raw, content_type="application/json")
                    return web.json_response(payload, status=status)

                self.handler = handler
                with self.assertRaises(ProviderError) as raised:
                    await self.provider.complete("PROMPT_SECRET", timeout=1)
                self.assertIn(str(raised.exception), safe_messages)
                for secret in ("UPSTREAM_SECRET", "LOCAL_BEARER_SECRET", "PROMPT_SECRET"):
                    self.assertNotIn(secret, str(raised.exception))
                    self.assertNotIn(secret, json.dumps(self.provider.snapshot()))
                self.assertEqual(len(self.requests), index)
                self.assertEqual(self.provider.calls, index)
                self.assertFalse(self.provider.snapshot()["busy"])

    async def test_response_is_trimmed_and_error_does_not_poison_later_requests(self):
        async def handler(request):
            if len(self.requests) == 1:
                return web.json_response({"error": "UPSTREAM_SECRET"}, status=500)
            return web.json_response(completion(" \n  后续成功  \t "))

        self.handler = handler
        with self.assertRaises(ProviderError):
            await self.provider.complete("first", timeout=1)
        self.assertEqual(await self.provider.complete("second", timeout=1), "后续成功")
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.provider.calls, 2)

    async def test_timeout_is_safe_and_never_automatically_retried(self):
        self.release.clear()

        async def handler(request):
            await self.release.wait()
            return web.json_response(completion("late answer"))

        self.handler = handler
        with self.assertRaises(ProviderError) as raised:
            await self.provider.complete("TIMEOUT_PROMPT_SECRET", timeout=0.03)
        self.assertEqual(str(raised.exception), "网页回复超时了，稍等再发一句吧")
        self.assertNotIn("LOCAL_BEARER_SECRET", str(raised.exception))
        self.assertNotIn("TIMEOUT_PROMPT_SECRET", str(raised.exception))
        self.release.set()
        await asyncio.sleep(0.03)
        # The total deadline includes connection setup. A busy scheduler can
        # expire it before the loopback server accepts any request; zero or one
        # received requests both prove that this attempt was never retried.
        self.assertLessEqual(len(self.requests), 1)
        self.assertEqual(self.provider.calls, 1)
        self.assertFalse(self.provider.snapshot()["busy"])

    async def test_concurrent_requests_are_serialized(self):
        self.release.clear()
        active = 0
        maximum = 0

        async def handler(request):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                await self.release.wait()
                await asyncio.sleep(0.01)
                return web.json_response(completion(str(len(self.requests))))
            finally:
                active -= 1

        self.handler = handler
        first = asyncio.create_task(self.provider.complete("first", timeout=1))
        await until(lambda: len(self.requests) == 1)
        second = asyncio.create_task(self.provider.complete("second", timeout=1))
        try:
            await asyncio.sleep(0.02)
            self.assertEqual(len(self.requests), 1)
            self.assertEqual(self.provider.calls, 1)
            self.assertTrue(self.provider.snapshot()["busy"])
        finally:
            self.release.set()
            results = await asyncio.gather(first, second)
        self.assertEqual(results, ["1", "2"])
        self.assertEqual(maximum, 1)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.provider.calls, 2)

    async def test_cancelled_request_is_not_retried_and_releases_lock(self):
        self.release.clear()

        async def handler(request):
            await self.release.wait()
            return web.json_response(completion())

        self.handler = handler
        pending = asyncio.create_task(self.provider.complete("cancel once", timeout=1))
        await until(lambda: len(self.requests) == 1)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertFalse(self.provider.snapshot()["busy"])
        self.release.set()
        self.assertEqual(await self.provider.complete("new request", timeout=1), "模拟网页回复")
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.provider.calls, 2)

    async def test_health_whitelists_status_fields_without_credentials(self):
        health = await self.provider.inspect()
        self.assertTrue(health["healthy"])
        self.assertEqual(health["state"], "ready")
        self.assertEqual(health["completed_requests"], 7)
        self.assertNotIn("internal_token", health)
        self.assertNotIn("HEALTH_SECRET", json.dumps(self.provider.snapshot()))
        self.assertNotIn("LOCAL_BEARER_SECRET", json.dumps(self.provider.snapshot()))
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
