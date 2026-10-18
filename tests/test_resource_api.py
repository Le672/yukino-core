import asyncio
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from yukino_core.app import create_app
from yukino_core.config import Config
from yukino_core.core import Kernel
from yukino_core.plugins import PluginManager
from yukino_core.resources import ResourceLibrary
from yukino_core.store import Store
from test_app_plugins import ADMIN_SECRET, FakeProvider, FakeTransport


def png_bytes():
    out = BytesIO()
    Image.new("RGB", (3, 2), (40, 100, 80)).save(out, format="PNG")
    return out.getvalue()


class ResourceApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        with patch.dict(os.environ, {"YUKINO_ADMIN_TOKEN": ADMIN_SECRET, "YUKINO_SECRETS_FILE": ""}):
            self.config = Config(self.root / "config.json")
        self.store = Store(self.root / "db.sqlite")
        await self.store.open()
        self.resources = ResourceLibrary(self.root / "resources")
        await self.resources.open()
        self.kernel = Kernel(self.config, self.store, self.resources, FakeProvider(), PluginManager(self.config))
        self.kernel.transport = FakeTransport()
        self.client = TestClient(TestServer(create_app(self.kernel)), cookie_jar=aiohttp.CookieJar(unsafe=True))
        await self.client.start_server()
        response = await self.client.post("/api/login", json={"token": ADMIN_SECRET})
        self.csrf = (await response.json())["csrf_token"]

    async def asyncTearDown(self):
        await self.client.close()
        await self.kernel.close()
        await self.store.close()
        self.tmp.cleanup()

    def headers(self): return {"X-CSRF-Token": self.csrf}

    async def upload(self, payload=None, filename="cat.png", description="一只开心猫"):
        form = aiohttp.FormData()
        form.add_field("file", png_bytes() if payload is None else payload, filename=filename, content_type="image/png")
        form.add_field("description", description)
        form.add_field("tags", json.dumps(["开心", "猫"], ensure_ascii=False))
        return await self.client.post("/api/resources/images/upload", data=form, headers=self.headers())

    async def test_text_resource_crud_search_soft_delete_restore_and_restart(self):
        cases = {"jargon": {"text": "测试梗", "meaning": "这是一条解释"},
                 "expressions": {"situation": "得意时", "style": "嘴硬地夸一下", "examples": ["算你有眼光"]},
                 "knowledge": {"title": "资源管理", "text": "修改后立即使用"}}
        for kind, payload in cases.items():
            with self.subTest(kind=kind):
                response = await self.client.post("/api/resources/"+kind, json=payload, headers=self.headers())
                self.assertEqual(response.status, 200, await response.text())
                item = (await response.json())["data"]
                self.assertTrue(item["enabled"])
                response = await self.client.get(f"/api/resources/{kind}?offset=0&limit=1")
                self.assertEqual((await response.json())["data"]["total"], 1)
                patch_data = {"meaning": "改过的解释"} if kind == "jargon" else ({"style": "新风格"} if kind == "expressions" else {"text": "新知识"})
                response = await self.client.patch(f'/api/resources/{kind}/{item["id"]}', json=patch_data, headers=self.headers())
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())["data"][next(iter(patch_data))], next(iter(patch_data.values())))
                response = await self.client.delete(f'/api/resources/{kind}/{item["id"]}', headers=self.headers())
                self.assertTrue((await response.json())["data"]["deleted"])
                response = await self.client.get(f"/api/resources/{kind}")
                self.assertEqual((await response.json())["data"]["total"], 0)
                response = await self.client.get(f"/api/resources/{kind}?include_disabled=true")
                self.assertFalse((await response.json())["data"]["items"][0]["enabled"])
                response = await self.client.patch(f'/api/resources/{kind}/{item["id"]}', json={"enabled": True}, headers=self.headers())
                self.assertTrue((await response.json())["data"]["enabled"])
        reopened = ResourceLibrary(self.resources.root)
        await reopened.open()
        self.assertEqual((await reopened.list_items("jargon", q="改过"))["total"], 1)
        self.assertEqual((await reopened.lookup("测试梗"))["jargon"][0]["meaning"], "改过的解释")

    async def test_image_upload_preview_metadata_edit_and_safe_disabled_preview(self):
        response = await self.upload(filename="private.html")
        self.assertEqual(response.status, 200, await response.text())
        item = (await response.json())["data"]
        self.assertEqual(item["id"], "img_" + hashlib.sha256(png_bytes()).hexdigest()[:24])
        self.assertNotIn("path", item)
        self.assertNotIn("private", json.dumps(item))
        preview = item["preview_url"]
        response = await self.client.get(preview)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Type"].split(";")[0], "image/png")
        self.assertEqual(await response.read(), png_bytes())
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        async with aiohttp.ClientSession() as outsider:
            response = await outsider.get(self.client.make_url(preview))
            self.assertEqual(response.status, 401)
        response = await self.client.patch('/api/resources/images/'+item["id"], json={"description": "编辑后的小猫", "tags": ["小猫", "得意"]}, headers=self.headers())
        self.assertEqual((await response.json())["data"]["description"], "编辑后的小猫")
        self.assertIsNotNone(await self.resources.sample_emoji("得意"))
        await self.client.delete('/api/resources/images/'+item["id"], headers=self.headers())
        self.assertIsNone(await self.resources.sample_emoji("得意"))
        self.assertEqual((await self.client.get(preview)).status, 200)
        self.assertEqual((await self.resources.status())["images"], 0)
        await self.client.patch('/api/resources/images/'+item["id"], json={"enabled": True}, headers=self.headers())
        self.assertIsNotNone(await self.resources.sample_emoji(item["id"]))

    async def test_upload_rejects_broken_images_oversize_and_path_metadata(self):
        for payload in (b"<svg onload=alert(1)>", b"\x89PNG\r\n\x1a\nBROKEN", b"x"*5_000_001):
            response = await self.upload(payload)
            self.assertIn(response.status, (400, 413))
        self.assertEqual((await self.resources.status())["images"], 0)
        response = await self.upload()
        rid = (await response.json())["data"]["id"]
        response = await self.client.patch('/api/resources/images/'+rid, json={"path": "../../private.json"}, headers=self.headers())
        self.assertEqual(response.status, 400)
        response = await self.client.get('/api/resources/images/not-found/file')
        self.assertEqual(response.status, 404)

    async def test_mutation_csrf_scope_validation_and_json_size_limits(self):
        response = await self.client.post('/api/resources/jargon', json={"text": "x", "meaning": "y"})
        self.assertEqual(response.status, 403)
        response = await self.client.post('/api/resources/jargon', json={"text": "x"}, headers=self.headers())
        self.assertEqual(response.status, 400)
        response = await self.client.post('/api/resources/jargon', data=b'{' + b'x'*70000, headers=self.headers())
        self.assertEqual(response.status, 413)
        response = await self.client.get('/api/resources/invalid')
        self.assertEqual(response.status, 404)
        response = await self.client.get('/api/resources/jargon?limit=1000')
        self.assertEqual(response.status, 400)
        response = await self.client.patch('/api/resources/jargon/missing', json={"meaning": "x"}, headers=self.headers())
        self.assertEqual(response.status, 404)

    async def test_scoped_memory_edit_and_image_history_use_authenticated_previews(self):
        response = await self.upload()
        image = (await response.json())["data"]
        mid = await self.store.remember("p:100", "旧记忆")
        response = await self.client.patch(f'/api/memories/{mid}?scope=g:100', json={"text": "越界修改"}, headers=self.headers())
        self.assertFalse((await response.json())["data"]["updated"])
        response = await self.client.patch(f'/api/memories/{mid}?scope=p:100', json={"text": "改好的记忆"}, headers=self.headers())
        self.assertTrue((await response.json())["data"]["updated"])
        self.assertEqual((await self.store.recall("p:100"))[0]["text"], "改好的记忆")
        await self.store.add_message("p:100", "assistant", "给你一只猫", message_id="qq1",
            attachments=[{"type": "image", "resource_id": image["id"], "description": image["description"]}])
        response = await self.client.get('/api/sessions/p:100/messages')
        row = (await response.json())["data"][0]
        self.assertEqual(row["attachments"][0]["preview_url"], image["preview_url"])
        self.assertNotIn("base64", json.dumps(row))
        self.assertNotIn(str(self.root), json.dumps(row))
