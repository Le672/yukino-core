"""Exercise model adapters over real loopback HTTP, without external credentials."""
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aiohttp import web

from yukino_core.models import ProviderManager
from yukino_core.provider import ProviderError, build_prompt


class ModelManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yukino-models-test-")
        self.path = Path(self.temp.name) / "private" / "providers.json"
        self.requests = []
        self.handler = None
        self.release = asyncio.Event()
        self.release.set()
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.handle)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])
        self.config = SimpleNamespace(provider_url=self.url, provider_token="PRIVATE_WEB_KEY")
        self.manager = ProviderManager(self.config, self.path)
        await self.manager.start()

    async def asyncTearDown(self):
        self.release.set()
        await self.manager.close()
        await self.runner.cleanup()
        self.temp.cleanup()

    async def handle(self, request):
        body = await request.json() if request.method == "POST" else None
        self.requests.append({"method": request.method, "path": request.path, "query": dict(request.query),
                              "headers": dict(request.headers), "body": body})
        if self.handler:
            return await self.handler(request)
        if request.path == "/health":
            return web.json_response({"browser_connected": True, "signed_in": True, "enabled": True,
                                      "state": "PRIVATE_WEB_KEY", "completed_requests": 2,
                                      "internal_token": "INTERNAL_SECRET"})
        if request.path.endswith("/models"):
            return web.json_response({"data": [{"id": "model-a"}, {"id": "model-b"}]})
        return web.json_response({"choices": [{"message": {"content": " OK "}}]})

    async def profile(self, kind="openai_compatible", profile_id="api-a", **kwargs):
        data = {"id": profile_id, "type": kind, "name": profile_id, "base_url": self.url + "/proxy/v1",
                "model": "model-a", "api_key": "PRIVATE_API_KEY"}
        data.update(kwargs)
        return await self.manager.upsert(data)

    async def wait_requests(self, count):
        async with asyncio.timeout(2):
            while len(self.requests) < count:
                await asyncio.sleep(0.002)

    async def test_optional_model_listing_does_not_erase_generation_proof(self):
        await self.profile()
        await self.manager.set_default('api-a')
        async def handler(request):
            if request.method == 'GET': return web.json_response({'error':'listing-not-supported'},status=404)
            return web.json_response({'choices':[{'message':{'content':'OK'}}]})
        self.handler=handler
        health=await self.manager.inspect()
        self.assertEqual(health['state'],'models_unavailable')
        self.assertFalse(health['generation_verified'])
        self.assertEqual(await self.manager.complete('test'),'OK')
        health=await self.manager.inspect()
        self.assertTrue(health['healthy']); self.assertTrue(health['generation_verified'])
        self.assertFalse(health['models_available'])

    async def test_default_migrates_web_provider_without_switching(self):
        public = self.manager.list_profiles()
        self.assertEqual(public["default_id"], "chatgpt-web")
        self.assertEqual(len(public["types"]), 6)
        self.assertTrue(public["profiles"][0]["has_api_key"])
        self.assertNotIn("api_key", public["profiles"][0])
        self.assertEqual(await self.manager.complete("raw prompt"), "OK")
        self.assertEqual(self.requests[0]["path"], "/v1/chat/completions")
        self.assertEqual(self.requests[0]["headers"]["Authorization"], "Bearer PRIVATE_WEB_KEY")
        self.assertEqual(self.requests[0]["body"], {
            "model": "chatgpt-web", "stream": False, "yukino_raw_prompt": True,
            "messages": [{"role": "user", "content": "raw prompt"}],
        })
        self.assertEqual(self.manager.calls, 1)
        self.assertFalse(self.manager.snapshot()["busy"])
        await self.profile()
        self.assertEqual(self.manager.default_id, "chatgpt-web")

    async def test_web_health_and_errors_only_expose_safe_fields(self):
        health = await self.manager.inspect()
        self.assertEqual(health, {"browser_connected": True, "signed_in": True, "enabled": True,
                                  "healthy": True, "state": "ready", "completed_requests": 2})
        self.assertNotIn("PRIVATE_WEB_KEY", json.dumps(self.manager.snapshot()))
        self.assertEqual((await self.manager.list_models("chatgpt-web"))["source"], "configured")

        async def login_error(request):
            return web.json_response({"error": {"code": "chatgpt_login_required", "message": "PRIVATE_WEB_KEY"}}, status=401)
        self.handler = login_error
        with self.assertRaisesRegex(ProviderError, "网页需要重新登录"):
            await self.manager.complete("hello")

    async def test_openai_compatible_wire_and_same_type_multiple_profiles(self):
        await self.profile(temperature=0.4, top_p=0.7, max_tokens=128, extra_body={"stop": ["END"]})
        await self.profile(profile_id="api-b", model="model-b", api_key="SECOND_KEY")
        await self.manager.set_default("api-a")
        self.assertEqual(await self.manager.complete("hello"), "OK")
        first = self.requests[-1]
        self.assertEqual(first["path"], "/proxy/v1/chat/completions")
        self.assertEqual(first["headers"]["Authorization"], "Bearer PRIVATE_API_KEY")
        self.assertEqual(first["body"], {"model": "model-a", "messages": [{"role": "user", "content": "hello"}],
                                           "stream": False, "temperature": 0.4, "top_p": 0.7,
                                           "max_tokens": 128, "stop": ["END"]})
        self.assertNotIn("yukino_raw_prompt", first["body"])
        await self.manager.complete("selected", provider_id="api-b")
        self.assertEqual(self.requests[-1]["body"]["model"], "model-b")
        self.assertEqual(self.requests[-1]["headers"]["Authorization"], "Bearer SECOND_KEY")
        self.assertEqual(self.manager.default_id, "api-a")
        models = await self.manager.list_models("api-b")
        self.assertEqual(models["models"], [{"id": "model-a", "name": "model-a"}, {"id": "model-b", "name": "model-b"}])

    async def test_openai_responses_native_output_and_parameters(self):
        await self.profile("openai_responses", max_tokens=64, temperature=0.5, extra_body={"store": False})
        async def responses(request):
            return web.json_response({"status": "completed", "output": [
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "not an answer"}]},
                {"type": "message", "content": [{"type": "output_text", "text": "Hi"}, {"type": "output_text", "text": " there"}]},
            ]})
        self.handler = responses
        self.assertEqual(await self.manager.complete("hello", provider_id="api-a"), "Hi there")
        self.assertEqual(self.requests[0]["path"], "/proxy/v1/responses")
        self.assertEqual(self.requests[0]["body"], {"store": False, "model": "model-a", "stream": False,
                                                   "input": [{"role": "user", "content": "hello"}],
                                                   "max_output_tokens": 64, "temperature": 0.5})

    async def test_anthropic_native_headers_blocks_and_required_token_limit(self):
        await self.profile("anthropic", base_url=self.url + "/anthropic/v1", top_p=0.9)
        async def messages(request):
            return web.json_response({"content": [{"type": "thinking", "thinking": "private reasoning"},
                                                   {"type": "text", "text": "Hello "}, {"type": "text", "text": "Claude"}]})
        self.handler = messages
        self.assertEqual(await self.manager.complete("hi", provider_id="api-a"), "Hello Claude")
        self.assertEqual(self.requests[0]["path"], "/anthropic/v1/messages")
        self.assertEqual(self.requests[0]["headers"]["x-api-key"], "PRIVATE_API_KEY")
        self.assertEqual(self.requests[0]["headers"]["anthropic-version"], "2023-06-01")
        self.assertNotIn("Authorization", self.requests[0]["headers"])
        self.assertEqual(self.requests[0]["body"], {"model": "model-a", "messages": [{"role": "user", "content": "hi"}],
                                                   "stream": False, "max_tokens": 1024, "top_p": 0.9})

    async def test_gemini_native_header_content_and_thought_filter(self):
        await self.profile("gemini", base_url=self.url + "/google/v1beta", model="models/gemini-local",
                           temperature=0.6, top_p=0.8, max_tokens=256, extra_body={"generationConfig": {"topK": 20}})
        async def generate(request):
            return web.json_response({"candidates": [{"content": {"parts": [{"text": "thinking", "thought": True},
                                                                            {"text": "Gemini answer"}]}}]})
        self.handler = generate
        self.assertEqual(await self.manager.complete("hi", provider_id="api-a"), "Gemini answer")
        request = self.requests[0]
        self.assertEqual(request["path"], "/google/v1beta/models/gemini-local:generateContent")
        self.assertEqual(request["headers"]["x-goog-api-key"], "PRIVATE_API_KEY")
        self.assertEqual(request["query"], {})
        self.assertEqual(request["body"], {"contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                                           "generationConfig": {"temperature": 0.6, "topP": 0.8, "maxOutputTokens": 256, "topK": 20}})

    async def test_ollama_native_wire_and_tags(self):
        await self.profile("ollama", base_url=self.url + "/ollama/api", model="llama:local", api_key="",
                           temperature=0.3, top_p=0.6, max_tokens=50, extra_body={"options": {"num_ctx": 2048}, "keep_alive": "5m"})
        async def ollama(request):
            if request.method == "GET":
                return web.json_response({"models": [{"name": "llama:local"}, {"name": "qwen:local"}]})
            return web.json_response({"message": {"role": "assistant", "content": "local answer"}, "done": True})
        self.handler = ollama
        self.assertEqual(await self.manager.complete("hello", provider_id="api-a"), "local answer")
        self.assertEqual(self.requests[0]["path"], "/ollama/api/chat")
        self.assertNotIn("Authorization", self.requests[0]["headers"])
        self.assertEqual(self.requests[0]["body"], {"model": "llama:local", "stream": False,
                                                   "messages": [{"role": "user", "content": "hello"}],
                                                   "options": {"num_ctx": 2048, "temperature": 0.3, "top_p": 0.6, "num_predict": 50}, "keep_alive": "5m"})
        self.assertEqual([row["id"] for row in (await self.manager.list_models("api-a"))["models"]], ["llama:local", "qwen:local"])
        self.assertEqual(self.requests[-1]["path"], "/ollama/api/tags")

    async def test_model_lists_paginate_anthropic_and_gemini_and_filter_non_generation(self):
        await self.profile("anthropic")
        async def anthropic(request):
            second = bool(request.query.get("after_id"))
            return web.json_response({"data": [{"id": "claude-two" if second else "claude-one", "display_name": "Claude"}],
                                      "has_more": not second, "last_id": "claude-one"})
        self.handler = anthropic
        self.assertEqual([row["id"] for row in (await self.manager.list_models("api-a"))["models"]], ["claude-one", "claude-two"])
        self.assertEqual(self.requests[-1]["query"], {"after_id": "claude-one"})
        await self.profile("gemini", profile_id="google")
        async def gemini(request):
            if request.query.get("pageToken"):
                return web.json_response({"models": [{"name": "models/gemini-two", "supportedGenerationMethods": ["generateContent"]}]})
            return web.json_response({"models": [{"name": "models/gemini-one", "displayName": "Gemini One", "supportedGenerationMethods": ["generateContent"]},
                                                  {"name": "models/embed-only", "supportedGenerationMethods": ["embedContent"]},
                                                  {"name": "models/invalid", "supportedGenerationMethods": None}], "nextPageToken": "page-two"})
        self.handler = gemini
        listing = await self.manager.list_models("google")
        self.assertEqual(listing["models"], [{"id": "gemini-one", "name": "Gemini One"}, {"id": "gemini-two", "name": "gemini-two"}])
        self.assertEqual(self.requests[-1]["query"], {"pageToken": "page-two"})

    async def test_full_endpoint_urls_and_custom_prefixes_are_preserved(self):
        for kind, suffix, expected in (("openai_compatible", "/chat/completions", "/custom/v1/chat/completions"),
                                       ("openai_responses", "/responses", "/custom/v1/responses"),
                                       ("anthropic", "/messages", "/custom/v1/messages")):
            with self.subTest(kind=kind):
                await self.profile(kind, base_url=self.url + "/custom/v1" + suffix)
                async def handler(request):
                    if kind == "openai_responses":
                        return web.json_response({"output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]})
                    if kind == "anthropic":
                        return web.json_response({"content": [{"type": "text", "text": "OK"}]})
                    return web.json_response({"choices": [{"message": {"content": "OK"}}]})
                self.handler = handler
                self.assertEqual(await self.manager.complete("hello", provider_id="api-a"), "OK")
                self.assertEqual(self.requests[-1]["path"], expected)

    async def test_root_urls_add_the_protocol_prefix(self):
        for kind, expected in (("openai_compatible", "/v1/models"), ("openai_responses", "/v1/models"),
                               ("anthropic", "/v1/models"), ("gemini", "/v1beta/models"), ("ollama", "/api/tags")):
            with self.subTest(kind=kind):
                await self.profile(kind, base_url=self.url)
                async def listing(request):
                    return web.json_response({"models": []} if kind in ("ollama", "gemini") else {"data": []})
                self.handler = listing
                self.assertEqual((await self.manager.list_models("api-a"))["models"], [])
                self.assertEqual(self.requests[-1]["path"], expected)

    async def test_credentials_are_write_only_blank_preserves_and_clear_removes(self):
        original = {"id": "api-a", "name": "API", "type": "openai_compatible", "base_url": self.url + "/proxy/v1",
                    "model": "model-a", "api_key": "PRIVATE_API_KEY", "extra_body": {"stop": ["END"]}}
        before = copy.deepcopy(original)
        public = await self.manager.upsert(original)
        self.assertEqual(original, before)
        self.assertTrue(public["has_api_key"])
        self.assertNotIn("api_key", public)
        for output in (public, self.manager.list_profiles(), self.manager.export_public(), self.manager.snapshot()):
            self.assertNotIn("PRIVATE_API_KEY", json.dumps(output))
            self.assertNotIn("PRIVATE_WEB_KEY", json.dumps(output))
        self.assertIn("PRIVATE_API_KEY", self.path.read_text(encoding="utf-8"))
        await self.manager.upsert({"id": "api-a", "api_key": "", "name": "renamed"})
        await self.manager.complete("hello", provider_id="api-a")
        self.assertEqual(self.requests[-1]["headers"]["Authorization"], "Bearer PRIVATE_API_KEY")
        await self.manager.upsert({"id": "api-a", "clear_api_key": True})
        await self.manager.complete("hello", provider_id="api-a")
        self.assertNotIn("Authorization", self.requests[-1]["headers"])
        self.assertNotIn("PRIVATE_API_KEY", self.path.read_text(encoding="utf-8"))

    async def test_persistence_restores_multiple_profiles_default_and_private_file(self):
        await self.profile()
        await self.profile("gemini", profile_id="google", model="gemini-local")
        await self.manager.set_default("google")
        restored = ProviderManager(SimpleNamespace(provider_url="https://unused.invalid", provider_token="SHOULD_NOT_MIGRATE"), self.path)
        self.assertEqual(restored.list_profiles(), self.manager.list_profiles())
        self.assertEqual(restored.default_id, "google")
        self.assertEqual(len(list(self.path.parent.iterdir())), 1)
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    async def test_release_empty_config_does_not_force_web_provider(self):
        empty_path = Path(self.temp.name) / "empty.json"
        empty_path.write_text('{"profiles":[],"default_id":""}', encoding="utf-8")
        empty = ProviderManager(self.config, empty_path)
        self.assertEqual(empty.list_profiles()["profiles"], [])
        self.assertEqual(empty.snapshot()["state"], "not_configured")
        with self.assertRaisesRegex(ProviderError, "默认模型"):
            await empty.complete("hello")
        self.assertEqual(await empty.inspect(), {"healthy": False, "state": "not_configured"})

    async def test_remove_default_clears_default_without_silent_switch(self):
        await self.profile()
        await self.manager.remove("chatgpt-web")
        self.assertIsNone(self.manager.default_id)
        self.assertEqual(self.manager.snapshot()["state"], "not_configured")
        with self.assertRaises(ProviderError):
            await self.manager.complete("no accidental paid fallback")
        await self.manager.set_default("api-a")
        await self.manager.upsert({"id": "api-a", "enabled": False})
        self.assertEqual(self.manager.snapshot()["state"], "disabled")
        self.assertEqual((await self.manager.inspect())["state"], "disabled")
        with self.assertRaisesRegex(ProviderError, "停用"):
            await self.manager.complete("disabled")
        with self.assertRaises(ValueError):
            await self.manager.set_default("api-a")
        self.assertEqual(self.requests, [])

    async def test_atomic_save_failure_preserves_file_and_memory(self):
        await self.profile()
        public_before = self.manager.list_profiles()
        disk_before = self.path.read_bytes()
        with patch("yukino_core.models.os.replace", side_effect=PermissionError("PRIVATE_API_KEY")):
            with self.assertRaisesRegex(ValueError, "保存失败") as raised:
                await self.manager.upsert({"id": "api-a", "name": "should not be applied", "api_key": "NEW_SECRET"})
        self.assertNotIn("PRIVATE_API_KEY", str(raised.exception))
        self.assertEqual(self.manager.list_profiles(), public_before)
        self.assertEqual(self.path.read_bytes(), disk_before)
        self.assertEqual(len(list(self.path.parent.iterdir())), 1)

    async def test_corrupt_private_file_is_preserved_and_error_never_echoes_content(self):
        original = b'{"PRIVATE_API_KEY": "SECRET", invalid JSON}'
        self.path.write_bytes(original)
        with self.assertRaisesRegex(ValueError, "原文件未被覆盖") as raised:
            ProviderManager(self.config, self.path)
        self.assertNotIn("PRIVATE_API_KEY", str(raised.exception))
        self.assertEqual(self.path.read_bytes(), original)

    async def test_profile_validation_rejects_unsafe_urls_and_invalid_values(self):
        for address in ("file:///etc/passwd", "ftp://host", "https://user:PRIVATE_API_KEY@host/v1", "https://host?key=PRIVATE_API_KEY",
                        "https://host/#SECRET", "https://host/?", "https://host:bad/v1", "https://host/\nheader", "https://host/ space"):
            with self.subTest(address=address), self.assertRaises(ValueError) as raised:
                await self.profile(base_url=address)
            self.assertNotIn("PRIVATE_API_KEY", str(raised.exception))
        for change in ({"type": []}, {"id": "../escape"}, {"temperature": float("nan")}, {"temperature": 3},
                       {"top_p": -1}, {"max_tokens": 0}, {"max_tokens": True}, {"timeout": 4}, {"timeout": 601},
                       {"enabled": "true"}, {"api_key": "key\nheader"}, {"extra_body": []}, {"unknown": "SECRET"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                await self.profile(**change)

    async def test_extra_body_cannot_override_credentials_model_input_or_stream(self):
        for extra in ({"model": "override"}, {"stream": True}, {"messages": []}, {"input": "replace"}, {"contents": []},
                      {"headers": {"Authorization": "SECRET"}}, {"api_key": "SECRET"}, {"nested": {"access_token": "SECRET"}},
                      {"options": {"model": "replace"}}, {"temperature": float("nan")}, {"yukino_raw_prompt": True}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                await self.profile(extra_body=extra)

    async def test_model_discovery_works_before_model_id_is_selected(self):
        await self.profile(model="")
        self.assertTrue((await self.manager.list_models("api-a"))["models"])
        with self.assertRaisesRegex(ProviderError, "模型 ID"):
            await self.manager.complete("hello", provider_id="api-a")
        with self.assertRaises(ValueError):
            await self.manager.set_default("api-a")
        self.assertTrue(all(request["method"] == "GET" for request in self.requests))

    async def test_null_sampling_is_omitted_and_reasoning_token_limit_can_use_extra_body(self):
        await self.profile(extra_body={"max_completion_tokens": 512, "reasoning_effort": "low"})
        await self.manager.complete("hello", provider_id="api-a")
        body = self.requests[-1]["body"]
        for field in ("temperature", "top_p", "max_tokens"):
            self.assertNotIn(field, body)
        self.assertEqual(body["max_completion_tokens"], 512)
        with self.assertRaises(ValueError):
            await self.profile(max_tokens=512, extra_body={"max_completion_tokens": 1024})

    async def test_errors_json_failures_and_redirects_are_safe_and_never_retried(self):
        await self.profile()
        cases = [(401, {"error": {"message": "PRIVATE_API_KEY PRIVATE_PROMPT"}}),
                 (403, {"error": "PRIVATE_API_KEY"}), (404, {}), (429, {}), (503, {}),
                 (302, {}), (200, []), (200, None), (200, {}), (200, {"choices": []}),
                 (200, {"choices": [{"message": {"content": " "}}]})]
        for status, raw in cases:
            with self.subTest(status=status, raw=raw):
                async def error_response(request):
                    if raw is None:
                        return web.Response(text="INVALID_JSON PRIVATE_API_KEY", content_type="application/json", status=status)
                    return web.json_response(raw, status=status, headers={"Location": self.url + "/redirected"} if status == 302 else {})
                self.handler = error_response
                before = len(self.requests)
                with self.assertRaises(ProviderError) as raised:
                    await self.manager.complete("PRIVATE_PROMPT", provider_id="api-a")
                self.assertEqual(len(self.requests), before + 1)
                for secret in ("PRIVATE_API_KEY", "PRIVATE_PROMPT"):
                    self.assertNotIn(secret, str(raised.exception))
                    self.assertNotIn(secret, json.dumps(self.manager.list_profiles()))
                    self.assertNotIn(secret, json.dumps(self.manager.snapshot()))

    async def test_timeout_and_cancellation_release_lock_without_retry(self):
        await self.profile()
        self.release.clear()
        async def slow(request):
            await self.release.wait()
            return web.json_response({"choices": [{"message": {"content": "late"}}]})
        self.handler = slow
        with self.assertRaisesRegex(ProviderError, "超时"):
            await self.manager.complete("PRIVATE_PROMPT", timeout=0.04, provider_id="api-a")
        self.assertLessEqual(len(self.requests), 1)
        self.assertFalse(self.manager.snapshot()["busy"])
        count = len(self.requests)
        pending = asyncio.create_task(self.manager.complete("cancel", provider_id="api-a"))
        await self.wait_requests(count + 1)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertFalse(self.manager.snapshot()["busy"])
        self.release.set()

    async def test_generation_is_serialized_and_configuration_mutation_rejects_busy(self):
        await self.profile()
        self.release.clear()
        async def slow(request):
            await self.release.wait()
            return web.json_response({"choices": [{"message": {"content": "OK"}}]})
        self.handler = slow
        first = asyncio.create_task(self.manager.complete("first", provider_id="api-a"))
        await self.wait_requests(1)
        second = asyncio.create_task(self.manager.complete("second", provider_id="api-a"))
        await asyncio.sleep(0.02)
        self.assertEqual(len(self.requests), 1)
        for mutation in (lambda: self.manager.upsert({"id": "api-a", "name": "busy"}),
                         lambda: self.manager.remove("api-a"), lambda: self.manager.set_default("api-a")):
            with self.assertRaisesRegex(ValueError, "模型正在回复"):
                await mutation()
        self.release.set()
        self.assertEqual(await asyncio.gather(first, second), ["OK", "OK"])
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.manager.calls, 2)

    async def test_model_test_has_real_response_and_latency_without_changing_default(self):
        await self.profile()
        result = await self.manager.test("api-a")
        self.assertEqual(result["provider_id"], "api-a")
        self.assertEqual(result["text"], "OK")
        self.assertTrue(result["ok"])
        self.assertIsInstance(result["latency_ms"], int)
        self.assertEqual(self.requests[0]["body"]["messages"][0]["content"], "只回复OK")
        self.assertEqual(self.manager.default_id, "chatgpt-web")

    async def test_response_text_key_echo_is_redacted(self):
        await self.profile()
        async def echo(request):
            return web.json_response({"choices": [{"message": {"content": "key=PRIVATE_API_KEY"}}]})
        self.handler = echo
        answer = await self.manager.complete("hi", provider_id="api-a")
        self.assertEqual(answer, "key=[已隐藏密钥]")

    async def test_oversized_response_is_bounded(self):
        await self.profile()
        async def large(request):
            return web.json_response({"response": "x" * 1000})
        self.handler = large
        with patch("yukino_core.models._MAX_RESPONSE_BYTES", 64):
            with self.assertRaisesRegex(ProviderError, "数据过大"):
                await self.manager.complete("hi", provider_id="api-a")

    async def test_incomplete_responses_are_not_silently_sent_as_complete_answers(self):
        await self.profile("openai_responses")
        async def incomplete(request):
            return web.json_response({"status": "incomplete", "output": [{"type": "message", "content": [{"type": "output_text", "text": "partial"}]}]})
        self.handler = incomplete
        with self.assertRaisesRegex(ProviderError, "响应未完成"):
            await self.manager.complete("hello", provider_id="api-a")

    async def test_malformed_nested_extra_options_are_rejected_before_a_network_call(self):
        for kind, key in (("gemini", "generationConfig"), ("ollama", "options")):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                await self.profile(kind, extra_body={key: []})
        self.assertEqual(self.requests, [])

    async def test_models_pagination_loop_is_rejected_without_unbounded_requests(self):
        await self.profile("gemini")
        async def listing(request):
            return web.json_response({"models": [], "nextPageToken": "repeated"})
        self.handler = listing
        with self.assertRaisesRegex(ProviderError, "分页"):
            await self.manager.list_models("api-a")
        self.assertEqual(len(self.requests), 2)

    async def test_missing_or_disabled_or_incomplete_profile_never_calls_network(self):
        for invalid in ("missing", [], None):
            if invalid is None:
                continue
            with self.subTest(invalid=invalid), self.assertRaises(ProviderError):
                await self.manager.complete("hello", provider_id=invalid)
        await self.profile(enabled=False)
        with self.assertRaises(ProviderError):
            await self.manager.list_models("api-a")
        await self.manager.upsert({"id": "api-a", "enabled": True, "model": ""})
        with self.assertRaises(ProviderError):
            await self.manager.complete("hello", provider_id="api-a")
        self.assertEqual(self.requests, [])


class ConfigurablePromptTests(unittest.TestCase):
    def test_limits_marker_and_emoji_setting_are_honored(self):
        prompt = build_prompt({"persona": "persona", "web_prompt_marker": "", "history_char_limit": 7,
                               "current_message_max_chars": 5, "emoji_enabled": False},
                              [{"role": "user", "content": "old"}, {"role": "assistant", "content": "newest"}], [], {}, "123456789")
        self.assertTrue(prompt.startswith("mode=replyer\n"))
        self.assertNotIn("yukino-brat", prompt)
        self.assertNotIn("[[emoji:", prompt)
        self.assertIn("已禁用图片表情", prompt)
        data = json.loads(prompt.rsplit("\n", 1)[-1])
        self.assertEqual(data["current"], "12345")
        self.assertEqual(data["history"], [{"role": "assistant", "name": "", "text": "newest"}])
        prompt = build_prompt({"persona": "persona", "web_prompt_marker": "custom-marker", "emoji_max_per_reply": 0}, [], [], {}, "hi")
        self.assertTrue(prompt.startswith("custom-marker\n"))
        self.assertNotIn("[[emoji:", prompt)


if __name__ == "__main__":
    unittest.main()
