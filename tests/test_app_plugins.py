"""Real localhost HTTP and subprocess tests; no provider/QQ network traffic."""

import asyncio
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from yukino_core.app import create_app
from yukino_core.config import Config
from yukino_core.core import Kernel
from yukino_core.plugins import PluginManager
from yukino_core.resources import ResourceLibrary
from yukino_core.store import Store


ADMIN_SECRET = "test-admin-secret-keep-private"
ONEBOT_SECRET = "test-onebot-secret-keep-private"
PROVIDER_SECRET = "test-provider-secret-keep-private"


class FakeProvider:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.calls = []

    async def complete(self, prompt, timeout):
        self.calls.append((prompt, timeout))
        return "这是独立模型测试回复"

    def snapshot(self):
        return {"ready": True, "state": "ready", "busy": self.lock.locked()}


class FakeTransport:
    def __init__(self):
        self.sent = []

    def snapshot(self):
        return {"connected": False, "state": "test"}

    async def send_message(self, message_type, target, segments):
        self.sent.append((message_type, target, segments))
        raise AssertionError("HTTP isolated tests must never send QQ messages")


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        with patch.dict(os.environ, {"YUKINO_ADMIN_TOKEN": ADMIN_SECRET, "YUKINO_ONEBOT_TOKEN": ONEBOT_SECRET,
                                     "YUKINO_PROVIDER_TOKEN": PROVIDER_SECRET, "YUKINO_SECRETS_FILE": ""}):
            self.config = Config(base / "config.json")
        self.store = Store(base / "store.db")
        await self.store.open()
        self.resources = ResourceLibrary(base / "resources")
        await self.resources.open()
        self.plugins = PluginManager(self.config)
        self.provider, self.transport = FakeProvider(), FakeTransport()
        self.kernel = Kernel(self.config, self.store, self.resources, self.provider, self.plugins)
        self.kernel.transport = self.transport
        self.client = TestClient(TestServer(create_app(self.kernel)), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        self.csrf = None

    async def asyncTearDown(self):
        await self.client.close()
        await self.kernel.close()
        await self.store.close()
        self.tmp.cleanup()

    async def login(self):
        response = await self.client.post("/api/login", json={"token": ADMIN_SECRET})
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.csrf = data["csrf_token"]
        return response

    def write_headers(self):
        return {"X-CSRF-Token": self.csrf}

    async def test_video_session_is_authenticated_masked_and_clearable(self):
        self.plugins.root = Path(self.tmp.name) / 'private-plugins'
        route = '/api/plugins/video/session'
        self.assertEqual((await self.client.get(route)).status, 401)
        await self.login()
        self.assertEqual((await self.client.post(route, json={'cookie': 'sessionid=private-test'})).status, 403)
        response = await self.client.post(route, json={'cookie': 'sessionid=private-test; other=value'}, headers=self.write_headers())
        self.assertEqual(response.status, 200)
        self.assertTrue((await response.json())['data']['configured'])
        response = await self.client.get(route)
        self.assertNotIn('private-test', await response.text())
        self.assertTrue(json.loads((self.plugins.root / 'douyin-session.json').read_text())['cookies']['sessionid'] == 'private-test')
        for bad in ('Cookie: sessionid=bad', 'a=b\r\nX-Test: injected', 'missing-equals', 'a=' + 'x' * 17000):
            response = await self.client.post(route, json={'cookie': bad}, headers=self.write_headers())
            self.assertEqual(response.status, 400)
        response = await self.client.post(route, json={'cookie': ''}, headers=self.write_headers())
        self.assertFalse((await response.json())['data']['configured'])
        self.assertFalse((self.plugins.root / 'douyin-session.json').exists())

    async def test_login_cookie_csrf_and_logout_invalidate_session(self):
        response = await self.client.get("/api/config")
        self.assertEqual(response.status, 401)
        response = await self.client.post("/api/login", json={"token": "wrong"})
        self.assertEqual(response.status, 401)
        response = await self.login()
        cookie = response.cookies["yukino_session"]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Strict")
        self.assertEqual(cookie["path"], "/")
        sid = cookie.value
        response = await self.client.get("/api/me")
        self.assertEqual((await response.json())["data"]["csrf_token"], self.csrf)
        response = await self.client.put("/api/config", json={"name": "测试名"})
        self.assertEqual(response.status, 403)
        self.assertEqual((await response.json())["error"]["code"], "csrf")
        response = await self.client.put("/api/config", json={"name": "测试名"}, headers=self.write_headers())
        self.assertEqual(response.status, 200)
        response = await self.client.post("/api/logout", headers=self.write_headers())
        self.assertEqual(response.status, 200)
        self.client.session.cookie_jar.update_cookies({"yukino_session": sid})
        response = await self.client.get("/api/me")
        self.assertEqual(response.status, 401)

    async def test_origin_is_checked_for_login_and_authenticated_writes(self):
        response = await self.client.post("/api/login", json={"token": ADMIN_SECRET}, headers={"Origin": "https://evil.invalid"})
        self.assertEqual(response.status, 403)
        await self.login()
        headers = {**self.write_headers(), "Origin": "https://evil.invalid"}
        response = await self.client.put("/api/config", json={"name": "不得修改"}, headers=headers)
        self.assertEqual(response.status, 403)
        self.assertNotEqual(self.config.data["name"], "不得修改")
        response = await self.client.put("/api/config", json={"name": "同源"}, headers={
            **self.write_headers(), "Origin": str(self.client.make_url("/")).rstrip("/")})
        self.assertEqual(response.status, 200)

    async def test_security_headers_cover_unauthorized_and_rejected_writes(self):
        response = await self.client.get("/api/status")
        self.assertEqual(response.status, 401)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        await self.login()
        response = await self.client.put("/api/config", json={"name": "x"})
        self.assertEqual(response.status, 403)
        self.assertIn("frame-ancestors 'none'", response.headers.get("Content-Security-Policy", ""))

    async def test_secrets_are_not_returned_or_mutable_via_api(self):
        await self.login()
        for route in ("/api/config", "/api/status", "/api/plugins", "/api/resources", "/api/events", "/api/me"):
            response = await self.client.get(route)
            self.assertEqual(response.status, 200, route)
            text = await response.text()
            for secret in (ADMIN_SECRET, ONEBOT_SECRET, PROVIDER_SECRET):
                self.assertNotIn(secret, text, route)
        response = await self.client.put("/api/config", json={"admin_token": "injected"}, headers=self.write_headers())
        self.assertEqual(response.status, 400)
        self.assertEqual(self.config.admin_token, ADMIN_SECRET)
        persisted = Path(self.config.path)
        if persisted.exists():
            self.assertNotIn(ADMIN_SECRET, persisted.read_text(encoding="utf-8"))

    async def test_api_memory_scopes_and_reset_preserve_audit_history(self):
        await self.login()
        response = await self.client.post("/api/memories", json={"scope": "p:100", "text": "私聊记忆"}, headers=self.write_headers())
        self.assertEqual(response.status, 200)
        mid = (await response.json())["data"]["id"]
        await self.client.post("/api/memories", json={"scope": "g:100", "text": "群聊记忆"}, headers=self.write_headers())
        response = await self.client.get("/api/memories?scope=p:100")
        self.assertEqual([row["text"] for row in (await response.json())["data"]], ["私聊记忆"])
        response = await self.client.delete(f"/api/memories/{mid}?scope=g:100", headers=self.write_headers())
        self.assertFalse((await response.json())["data"]["deleted"])
        response = await self.client.delete(f"/api/memories/{mid}?scope=p:100", headers=self.write_headers())
        self.assertTrue((await response.json())["data"]["deleted"])
        response = await self.client.get("/api/memories?scope=global")
        self.assertEqual(response.status, 400)
        await self.store.add_message("p:100", "user", "原始历史")
        await self.store.add_message("g:100", "user", "群上下文")
        response = await self.client.post("/api/sessions/p:100/reset", headers=self.write_headers())
        self.assertEqual(response.status, 200)
        self.assertEqual(await self.store.history("p:100"), [])
        self.assertEqual(len(await self.store.history("g:100")), 1)
        response = await self.client.get("/api/sessions/p:100/messages")
        self.assertEqual((await response.json())["data"][0]["content"], "原始历史")

    async def test_isolated_api_model_test_uses_one_call_and_never_sends_qq(self):
        await self.login()
        response = await self.client.post("/api/test", json={"text": "你好"}, headers=self.write_headers())
        self.assertEqual(response.status, 200)
        data = (await response.json())["data"]
        self.assertEqual(data["model_calls"], 1)
        self.assertFalse(data["qq_sent"])
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.transport.sent, [])
        self.assertEqual((await self.store.stats())["messages"], 0)
        await self.provider.lock.acquire()
        try:
            response = await self.client.post("/api/test", json={"text": "忙碌测试"}, headers=self.write_headers())
            self.assertEqual(response.status, 409)
        finally:
            self.provider.lock.release()

    async def test_malformed_json_shapes_are_client_errors(self):
        response = await self.client.post("/api/login", json=[])
        self.assertEqual(response.status, 400)
        await self.login()
        for route in ("/api/memories", "/api/plugins/toggle", "/api/plugins/batch", "/api/test"):
            response = await self.client.post(route, json=None, headers=self.write_headers())
            self.assertEqual(response.status, 400, route)


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def test_external_video_rules_match_only_exact_hosts_and_video_paths(self):
        item = dict(id="video", name="视频", description="自动视频", worker="video.py", commands=["/视频解析"],
                    auto_url_hosts=["www.bilibili.com", "v.douyin.com"],
                    auto_url_paths={"www.bilibili.com": "/video/", "v.douyin.com": "/"}, silent_ack=True)
        (self.root / "manifest.json").write_text(json.dumps([item]), encoding="utf-8")
        manager = PluginManager(self.config, self.root)
        for text in ("来看 https://www.bilibili.com/video/av123/", "复制链接 https://v.douyin.com/ABC/ 观看"):
            self.assertEqual(manager.match(text)[0]["id"], "video")
        for text in ("https://www.bilibili.com.evil.test/video/av123/", "https://www.bilibili.com/user/123/",
                     "https://user@www.bilibili.com/video/av123/", "https://www.bilibili.com:1234/video/av123/"):
            self.assertIsNone(manager.match(text)[0], text)
        self.config.plugin_flags["video"] = False
        self.assertIsNone(manager.match("https://www.bilibili.com/video/av123/")[0])
        self.assertEqual(manager.match("/视频解析 https://www.bilibili.com/video/av123/")[0]["id"], "video")

    async def test_invalid_auto_url_metadata_cannot_break_other_plugins(self):
        for patch in ({"auto_url_hosts": "bilibili.com"}, {"auto_url_hosts": [".*"]},
                      {"auto_url_hosts": ["bilibili.com"], "auto_url_paths": {"evil.test": "/"}}, {"silent_ack": "yes"}):
            record = dict(id="video", name="视频", description="test", worker="video.py", commands=["/视频"])
            record.update(patch)
            (self.root / "manifest.json").write_text(json.dumps([record]), encoding="utf-8")
            manager = PluginManager(self.config, self.root)
            self.assertIsNone(manager.match("/视频")[0])
            self.assertEqual(manager.match("/ping")[0]["id"], "utilities")

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = SimpleNamespace(plugin_flags={}, save=lambda: None)
        source = Path(__file__).resolve().parents[1] / "yukino_core" / "plugins" / "calculator.py"
        shutil.copy2(source, self.root / "calculator.py")
        self.manager = PluginManager(self.config, self.root)

    async def asyncTearDown(self):
        await self.manager.close()
        self.tmp.cleanup()

    def worker(self, name, source):
        (self.root / f"{name}.py").write_text(source, encoding="utf-8")
        item = {"id": name, "name": name, "description": "test worker", "commands": ["/" + name], "worker": name + ".py"}
        self.manager.items.append(item)
        return item

    async def test_calculator_runs_in_worker_and_rejects_code_execution(self):
        item, _ = self.manager.match("/计算 (12+3)*4/2")
        self.assertEqual(await self.manager.execute(item, "/计算 (12+3)*4/2", {}), "(12+3)*4/2 = 30")
        self.assertIn("算不了", await self.manager.execute(item, "/计算 __import__('os').getcwd()", {}))
        self.assertIn("算不了", await self.manager.execute(item, "/计算 9**99999999", {}))
        self.assertIn("算不了", await self.manager.execute(item, "/计算 1/0", {}))
        self.assertEqual(self.manager.workers["calculator"].returncode, None)
        proc = self.manager.workers["calculator"]
        await self.manager.toggle("calculator", False)
        self.assertFalse(self.manager.enabled("calculator"))
        self.assertIsNotNone(proc.returncode)

    async def test_crash_and_hang_are_isolated_and_leave_healthy_plugin_available(self):
        crash = self.worker("crash", "import os\ndef handle(text, context):\n    os._exit(7)\n")
        hang = self.worker("hang", "import time\ndef handle(text, context):\n    time.sleep(30)\n")
        calculator, _ = self.manager.match("/计算 2+2")
        started = time.monotonic()
        bad, slow, good = await asyncio.gather(
            self.manager.execute(crash, "/crash", {}, timeout=.25),
            self.manager.execute(hang, "/hang", {}, timeout=.25),
            self.manager.execute(calculator, "/计算 2+2", {}, timeout=1.0))
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertIn("其他功能仍可使用", bad)
        self.assertIn("其他功能仍可使用", slow)
        self.assertEqual(good, "2+2 = 4")
        self.assertNotIn("crash", self.manager.workers)
        self.assertNotIn("hang", self.manager.workers)
        self.assertEqual({row["id"]: row["state"] for row in self.manager.list()}["hang"], "failed")
        self.assertEqual(await self.manager.execute(calculator, "/计算 8/2", {}), "8/2 = 4")

    async def test_import_hang_oversized_output_and_plugin_prints(self):
        import_hang = self.worker("import_hang", "import time\ntime.sleep(30)\ndef handle(text,context): return 'late'\n")
        oversized = self.worker("oversized", "def handle(text,context): return 'x'*100000\n")
        noisy = self.worker("noisy", "print('import noise')\ndef handle(text,context):\n    print('handler noise')\n    return '中文正常回复'\n")
        self.assertIn("暂时没响应", await self.manager.execute(import_hang, "/x", {}, timeout=.2))
        self.assertIn("暂时没响应", await self.manager.execute(oversized, "/x", {}, timeout=.5))
        self.assertEqual(await self.manager.execute(noisy, "/x", {}), "中文正常回复")

    async def test_one_deadline_covers_startup_handler_and_waiting_for_worker_lock(self):
        delayed = self.worker("delayed", "import time\ntime.sleep(.25)\ndef handle(text,context):\n    time.sleep(.25)\n    return 'too late'\n")
        started = time.monotonic()
        result = await self.manager.execute(delayed, "/x", {}, timeout=.4)
        self.assertIn("暂时没响应", result)
        self.assertLess(time.monotonic() - started, .8)
        calculator, _ = self.manager.match("/计算 2+2")
        lock = asyncio.Lock()
        await lock.acquire()
        self.manager.locks["calculator"] = lock
        try:
            started = time.monotonic()
            self.assertIn("暂时没响应", await self.manager.execute(calculator, "/计算 2+2", {}, timeout=.05))
            self.assertLess(time.monotonic() - started, .3)
        finally:
            lock.release()

    async def test_bad_external_manifest_cannot_abort_builtin_plugins(self):
        (self.root / "manifest.json").write_text("{invalid", encoding="utf-8")
        manager = PluginManager(self.config, self.root)
        self.assertIsNotNone(manager.match("/计算 1+1")[0])
        await manager.close()
        (self.root / "manifest.json").write_text(json.dumps([None, {}, {"id": "escape", "worker": "../outside.py"}]), encoding="utf-8")
        manager = PluginManager(self.config, self.root)
        self.assertIsNotNone(manager.match("/计算 1+1")[0])
        self.assertNotIn("escape", [row["id"] for row in manager.list()])
        await manager.close()


if __name__ == "__main__":
    unittest.main()
