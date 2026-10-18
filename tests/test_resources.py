import asyncio
import hashlib
from contextlib import closing
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

from yukino_core.resources import ResourceLibrary


class ResourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "resources"
        self.root.mkdir()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def manifest(self, **data):
        (self.root / "curated.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def image(self, name="cat.png"):
        path = self.root / name
        path.write_bytes(b"\x89PNG\r\n\x1a\ncurated-test-image")
        return path

    def upload_bytes(self, image_format="PNG", color="red"):
        output = BytesIO()
        Image.new("RGB", (2, 2), color).save(output, format=image_format)
        return output.getvalue()

    async def test_portable_resource_bundle_works_without_old_workspace(self):
        # Generate an independently distributable fixture; a public release must
        # not need the private deployment's downloaded pictures or catalogue.
        (self.root / 'generated.jpg').write_bytes(self.upload_bytes('JPEG'))
        self.manifest(jargons=[{'text':'测试术语','meaning':'示例释义'}],
            knowledge=[{'title':'测试知识','text':'公开测试内容'}],
            expressions=[{'situation':'问候','style':'简短','examples':['你好']}],
            images=[{'path':'generated.jpg','description':'思考测试图片','tags':['思考']}])
        lib = ResourceLibrary(self.root)
        await lib.open()
        status = await lib.status()
        self.assertEqual((status["jargon"], status["knowledge"], status["expressions"], status["images"]), (1, 1, 1, 1))
        self.assertEqual(status["errors"], [])
        result = await lib.lookup("测试术语是什么意思")
        self.assertEqual(result["jargon"][0]["text"], "测试术语")
        image = await lib.sample_emoji("思考")
        self.assertIsNotNone(image)
        self.assertTrue(Path(image["path"]).is_relative_to(lib.root))

    async def test_malformed_files_and_rows_are_reported_without_crashing(self):
        self.manifest(jargons=[None, {"text": "空释义"}, {"content": "绷不住", "meaning": "想笑"}],
                      knowledge=[None, {"text": ["invalid"]}], expressions="not a list", images=[None])
        (self.root / "knowledge.json").write_text("{broken", encoding="utf-8")
        lib = ResourceLibrary(self.root)
        await lib.open()
        status = await lib.status()
        self.assertTrue(status["ready"])
        self.assertEqual(status["jargon"], 1)
        self.assertGreaterEqual(status["rejected"], 5)
        self.assertEqual(len(status["errors"]), 1)
        self.assertEqual((await lib.lookup("绷不住"))["jargon"][0]["meaning"], "想笑")

    async def test_keyword_lookup_is_bounded_and_does_not_return_unrelated_resources(self):
        self.manifest(jargons=[{"text": "测试" + str(i), "meaning": "测试释义" + "字" * 2000} for i in range(30)],
                      knowledge=[{"title": "测试资料", "text": "字" * 3000}])
        lib = ResourceLibrary(self.root)
        await lib.open()
        result = await lib.lookup("测试", 999)
        self.assertEqual(len(result["jargon"]), 12)
        self.assertLessEqual(len(result["jargon"][0]["meaning"]), 600)
        self.assertLessEqual(len(result["knowledge"][0]["text"]), 1200)
        self.assertEqual(await lib.lookup(""), {"jargon": [], "knowledge": [], "expressions": []})
        self.assertEqual((await lib.lookup("xylophone"))["jargon"], [])

    async def test_image_traversal_absolute_uri_hash_and_removed_files(self):
        valid = self.image()
        outside = Path(self.tmp.name) / "secret.png"
        outside.write_bytes(b"private contents")
        self.manifest(images=[
            {"id": "good", "path": "cat.png", "description": "思考猫猫", "tags": ["思考"]},
            {"id": "traversal", "path": "../secret.png", "description": "外部"},
            {"id": "windows-traversal", "path": "..\\secret.png", "description": "外部"},
            {"id": "absolute", "path": str(outside), "description": "外部"},
            {"id": "uri", "path": outside.as_uri(), "description": "外部"},
            {"id": "bad-hash", "path": "cat.png", "description": "损坏", "sha256": "0" * 64},
        ])
        lib = ResourceLibrary(self.root)
        await lib.open()
        self.assertEqual((await lib.status())["images"], 1)
        self.assertEqual((await lib.sample_emoji("思考"))["id"], "img_" + hashlib.sha256(valid.read_bytes()).hexdigest()[:24])
        self.assertIsNone(await lib.sample_emoji("完全无关"))
        valid.unlink()
        self.assertIsNone(await lib.sample_emoji())

    async def test_manifest_and_image_symlinks_cannot_escape_root(self):
        outside = Path(self.tmp.name) / "outside.png"
        outside.write_bytes(b"private")
        try:
            (self.root / "link.png").symlink_to(outside)
        except OSError:
            self.skipTest("host does not grant symlink creation")
        self.manifest(images=[{"path": "link.png", "description": "猫猫"}])
        lib = ResourceLibrary(self.root)
        await lib.open()
        self.assertEqual((await lib.status())["images"], 0)
        self.assertIsNone(await lib.sample_emoji())

    async def test_replacing_image_after_open_cannot_bypass_manifest_hash(self):
        path = self.image()
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        self.manifest(images=[{"path": "cat.png", "description": "已校验的图片", "sha256": expected}])
        lib = ResourceLibrary(self.root)
        await lib.open()
        self.assertIsNotNone(await lib.sample_emoji())
        path.write_bytes(b"modified image that is not the curated content")
        self.assertIsNone(await lib.sample_emoji())

    async def test_same_verified_content_at_two_paths_counts_once(self):
        original = self.image("original.png")
        migrated = self.image("canonical-hash.png")
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        self.manifest(images=[{"id": "bundled", "path": original.name, "description": "原始资源", "sha256": digest}])
        (self.root / "images.json").write_text(json.dumps({"images": [
            {"id": "migrated", "path": migrated.name, "description": "迁移资源", "sha256": digest.upper()},
            {"id": "same-path-without-hash", "path": original.name, "description": "相同路径"},
        ]}), encoding="utf-8")
        lib = ResourceLibrary(self.root)
        await lib.open()
        self.assertEqual((await lib.status())["images"], 1)
        self.assertEqual((await lib.sample_emoji())["id"], "img_" + digest[:24])

    async def test_replacing_legacy_directory_after_open_cannot_escape_root(self):
        legacy = Path(self.tmp.name) / "legacy"
        media = legacy / "data" / "emoji"
        media.mkdir(parents=True)
        (media / "known.png").write_bytes(b"curated")
        outside = Path(self.tmp.name) / "outside-media"
        outside.mkdir()
        (outside / "known.png").write_bytes(b"private image with same name")
        db_path = legacy / "data" / "MaiBot.db"
        with closing(sqlite3.connect(db_path)) as db, db:
            db.execute("CREATE TABLE Images(full_path TEXT,description TEXT,is_registered INTEGER,is_banned INTEGER)")
            db.execute("INSERT INTO Images VALUES('data/emoji/known.png','known curated image',1,0)")
        self.manifest(media_catalogue=[{"description": "known curated image"}])
        lib = ResourceLibrary(self.root, legacy)
        await lib.open()
        self.assertIsNotNone(await lib.sample_emoji())
        preserved = media.with_name("emoji-original")
        self.assertTrue(media.resolve().is_relative_to(Path(self.tmp.name).resolve()))
        self.assertTrue(preserved.resolve().is_relative_to(Path(self.tmp.name).resolve()))
        media.rename(preserved)
        try:
            media.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("host does not grant directory symlink creation")
        self.assertIsNone(await lib.sample_emoji())

    async def test_legacy_is_readonly_curated_only_and_confined_to_emoji_directory(self):
        legacy = Path(self.tmp.name) / "legacy"
        media = legacy / "data" / "emoji"
        media.mkdir(parents=True)
        (media / "good.png").write_bytes(b"curated")
        (media / "private.png").write_bytes(b"private")
        (legacy / "data" / "private.png").write_bytes(b"private outside emoji directory")
        db_path = legacy / "data" / "MaiBot.db"
        with closing(sqlite3.connect(db_path)) as db, db:
            db.execute("CREATE TABLE Images(id INTEGER,full_path TEXT,description TEXT,is_registered INTEGER,is_banned INTEGER)")
            db.executemany("INSERT INTO Images VALUES(?,?,?,?,?)", [
                (1, "/opt/yukino-bot/maibot/data/emoji/good.png", "已知手工资源", 1, 0),
                (2, "data/emoji/private.png", "私人照片说明", 1, 0),
                (3, "data/emoji/../private.png", "已知手工资源", 1, 0),
                (4, "data/private.png", "已知手工资源", 1, 0),
                (5, "data/emoji/good.png", "已知手工资源", 1, 1),
                (6, "data/emoji/good.png", "已知手工资源", 0, 0),
            ])
            db.execute("CREATE TABLE messages(content TEXT)")
            db.execute("INSERT INTO messages VALUES('严禁自动导入的私聊')")
            db.execute("CREATE TABLE jargon(content TEXT,meaning TEXT,is_global INTEGER)")
            db.execute("INSERT INTO jargon VALUES('个人昵称','严禁导入',1)")
        before = hashlib.sha256(db_path.read_bytes()).hexdigest()
        self.manifest(media_catalogue=[{"id": "known", "description": "已知手工资源", "tags": ["测试"]}])
        lib = ResourceLibrary(self.root, legacy)
        await lib.open()
        status = await lib.status()
        self.assertEqual(status["legacy"], "read_only")
        self.assertEqual(status["legacy_images"], 1)
        self.assertEqual(status["images"], 1)
        self.assertEqual(status["jargon"], 0)
        self.assertEqual(status["knowledge"], 0)
        image = await lib.sample_emoji("测试")
        self.assertEqual(Path(image["path"]), (media / "good.png").resolve())
        self.assertEqual(image["source"], "legacy_curated")
        self.assertEqual(before, hashlib.sha256(db_path.read_bytes()).hexdigest())
        self.assertEqual((await lib.lookup("严禁"))["knowledge"], [])

    async def test_missing_and_corrupt_legacy_db_do_not_break_bundled_data(self):
        self.manifest(jargons=[{"text": "整活", "meaning": "做有节目效果的事情"}])
        legacy = Path(self.tmp.name) / "missing"
        lib = ResourceLibrary(self.root, legacy)
        await lib.open()
        self.assertEqual((await lib.status())["legacy"], "not_found")
        (legacy / "data").mkdir(parents=True)
        (legacy / "data" / "MaiBot.db").write_text("not sqlite", encoding="utf-8")
        await lib.open()
        self.assertEqual((await lib.status())["legacy"], "unreadable")
        self.assertEqual((await lib.lookup("整活"))["jargon"][0]["text"], "整活")

    async def test_seed_edit_soft_delete_restore_persist_without_touching_source(self):
        self.manifest(jargons=[{"text": "整活", "meaning": "原始释义"}],
                      knowledge=[{"title": "通用知识", "text": "原始知识"}],
                      expressions=[{"situation": "夸奖", "style": "先认领", "examples": ["眼光不错"]}])
        source = (self.root / "curated.json").read_bytes()
        lib = ResourceLibrary(self.root)
        await lib.open()
        item = (await lib.list_items("jargon"))["items"][0]
        item_id = item["id"]
        self.assertTrue(item_id.startswith("jargon_"))
        with patch("yukino_core.resources.hashlib.file_digest", side_effect=AssertionError("edits must not reread media")):
            updated = await lib.update_item("jargon", item_id, {"meaning": "更新后的释义"})
            self.assertEqual(updated["id"], item_id)
            self.assertEqual((await lib.lookup("整活"))["jargon"][0]["meaning"], "更新后的释义")
            self.assertTrue(await lib.delete_item("jargon", item_id))
        self.assertEqual((await lib.list_items("jargon"))["total"], 0)
        self.assertEqual((await lib.lookup("整活"))["jargon"], [])
        self.assertFalse(await lib.delete_item("jargon", item_id))
        self.assertFalse((await lib.get_item("jargon", item_id))["enabled"])
        self.assertEqual((await lib.status())["disabled"]["jargon"], 1)
        restarted = ResourceLibrary(self.root)
        await restarted.open()
        self.assertFalse((await restarted.get_item("jargon", item_id))["enabled"])
        restored = await restarted.update_item("jargon", item_id, {"enabled": True})
        self.assertTrue(restored["enabled"])
        self.assertEqual(restored["meaning"], "更新后的释义")
        self.assertEqual((self.root / "curated.json").read_bytes(), source)
        self.assertEqual(list(self.root.glob(".admin-resources-*.tmp")), [])

    async def test_create_all_text_kinds_stable_uuid_search_pagination_and_kind_isolation(self):
        lib = ResourceLibrary(self.root)
        await lib.open()
        rows = []
        for number in range(5):
            rows.append(await lib.create_item("jargon", {"text": "新词" + str(number), "meaning": "新释义"}))
        knowledge = await lib.create_item("knowledge", {"title": "新知识", "text": "只在知识中"})
        expression = await lib.create_item("expressions", {"situation": "胜利", "style": "得意", "examples": ["赢了"]})
        self.assertEqual(len({row["id"] for row in rows}), 5)
        self.assertTrue(all(row["id"].startswith("usr_") for row in rows))
        page = await lib.list_items("jargon", "新", offset=1, limit=2)
        self.assertEqual((page["total"], page["offset"], page["limit"]), (5, 1, 2))
        self.assertEqual([row["id"] for row in page["items"]], [rows[1]["id"], rows[2]["id"]])
        with self.assertRaises(KeyError):
            await lib.get_item("knowledge", rows[0]["id"])
        self.assertFalse(await lib.delete_item("images", knowledge["id"]))
        await lib.update_item("expressions", expression["id"], {"examples": ["更新的例句"]})
        restarted = ResourceLibrary(self.root)
        await restarted.open()
        self.assertEqual((await restarted.get_item("knowledge", knowledge["id"]))["text"], "只在知识中")
        self.assertEqual((await restarted.get_item("expressions", expression["id"]))["examples"], ["更新的例句"])

    async def test_concurrent_creates_and_disjoint_updates_do_not_lose_changes(self):
        lib = ResourceLibrary(self.root)
        await lib.open()
        created = await asyncio.gather(*(lib.create_item("knowledge", {"title": str(i), "text": "知识" + str(i)}) for i in range(20)))
        self.assertEqual((await lib.list_items("knowledge"))["total"], 20)
        item_id = created[0]["id"]
        await asyncio.gather(lib.update_item("knowledge", item_id, {"title": "改标题"}),
                             lib.update_item("knowledge", item_id, {"text": "改正文"}))
        restarted = ResourceLibrary(self.root)
        await restarted.open()
        self.assertEqual((await restarted.list_items("knowledge"))["total"], 20)
        item = await restarted.get_item("knowledge", item_id)
        self.assertEqual((item["title"], item["text"]), ("改标题", "改正文"))

    async def test_cancelled_request_cannot_release_lock_before_disk_commit_finishes(self):
        lib = ResourceLibrary(self.root)
        await lib.open()
        entered, release = threading.Event(), threading.Event()
        persist = lib._persist_overlay

        def delayed(overrides):
            entered.set()
            release.wait(2)
            persist(overrides)

        with patch.object(lib, "_persist_overlay", side_effect=delayed):
            first = asyncio.create_task(lib.create_item("knowledge", {"text": "第一个写入"}))
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            first.cancel()
            second = asyncio.create_task(lib.create_item("knowledge", {"text": "第二个写入"}))
            await asyncio.sleep(.02)
            self.assertFalse(second.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await first
            await second
        restarted = ResourceLibrary(self.root)
        await restarted.open()
        self.assertEqual((await restarted.list_items("knowledge"))["total"], 2)

    async def test_upload_real_formats_deduplicates_preserves_bytes_and_hides_paths(self):
        lib = ResourceLibrary(self.root)
        await lib.open()
        for image_format in ("PNG", "JPEG", "GIF", "WEBP"):
            content = self.upload_bytes(image_format)
            row = await lib.upload_image("中文文件名.bin", content, "开心图片" + image_format, ["开心", image_format])
            self.assertEqual(row["id"], "img_" + hashlib.sha256(content).hexdigest()[:24])
            self.assertEqual(set(row), {"id", "kind", "enabled", "description", "tags"})
            path = await lib.image_file(row["id"])
            self.assertTrue(path.is_relative_to(self.root))
            self.assertEqual(path.read_bytes(), content)
            duplicate = await lib.upload_image("same.png", content, "不能创建重复图片", ["重复"])
            self.assertEqual(duplicate["id"], row["id"])
            self.assertEqual(duplicate["description"], row["description"])
            self.assertEqual((await lib.sample_emoji(row["id"]))["id"], row["id"])
        self.assertEqual((await lib.list_items("images"))["total"], 4)
        restarted = ResourceLibrary(self.root)
        await restarted.open()
        self.assertEqual((await restarted.status())["images"], 4)
        public = json.dumps(await restarted.list_items("images"), ensure_ascii=False)
        self.assertNotIn(str(self.root), public)
        self.assertNotIn("uploads/", public)
        self.assertIsNone(await restarted.image_file("not-an-image-id"))

    async def test_disabled_images_preview_but_never_sample_and_restore(self):
        lib = ResourceLibrary(self.root)
        await lib.open()
        row = await lib.upload_image("cat.png", self.upload_bytes(), "思考猫猫", ["思考"])
        await lib.update_item("images", row["id"], {"description": "改描述", "tags": ["得意"]})
        self.assertIsNone(await lib.sample_emoji("思考"))
        self.assertEqual((await lib.sample_emoji("得意"))["id"], row["id"])
        self.assertTrue(await lib.delete_item("images", row["id"]))
        self.assertEqual((await lib.list_items("images"))["total"], 0)
        self.assertFalse((await lib.list_items("images", include_disabled=True))["items"][0]["enabled"])
        self.assertIsNotNone(await lib.image_file(row["id"]))
        self.assertIsNone(await lib.sample_emoji(row["id"]))
        self.assertIsNone(await lib.sample_emoji("得意"))
        await lib.update_item("images", row["id"], {"enabled": True})
        self.assertIsNotNone(await lib.sample_emoji(row["id"]))

    async def test_upload_rejects_bad_magic_truncation_size_pixels_and_unsafe_filename(self):
        lib = ResourceLibrary(self.root)
        await lib.open()
        good = self.upload_bytes()
        bad_inputs = [("cat.svg", b"<svg></svg>"), ("cat.png", b"\x89PNG\r\n\x1a\ntruncated"),
                      ("cat.jpg", b"\xff\xd8\xffbroken"), ("cat.png", b"x" * 5_000_001),
                      ("../secret.png", good), ("..\\secret.png", good), ("http://remote/x.png", good)]
        for filename, content in bad_inputs:
            with self.subTest(filename=filename, size=len(content)):
                with self.assertRaises(ValueError):
                    await lib.upload_image(filename, content)
        for image_format in ("PNG", "JPEG", "GIF", "WEBP"):
            with self.subTest(truncated_real_format=image_format):
                with self.assertRaises(ValueError):
                    await lib.upload_image("truncated.bin", self.upload_bytes(image_format)[:-1])
        large = BytesIO()
        Image.new("1", (5000, 4001)).save(large, format="PNG")
        with self.assertRaises(ValueError):
            await lib.upload_image("large.png", large.getvalue())
        self.assertEqual((await lib.status())["images"], 0)

    async def test_animated_upload_keeps_original_bytes_and_frame_count(self):
        output = BytesIO()
        Image.new("RGB", (2, 2), "red").save(output, format="GIF", save_all=True,
            append_images=[Image.new("RGB", (2, 2), "blue")], duration=100, loop=0)
        content = output.getvalue()
        lib = ResourceLibrary(self.root)
        await lib.open()
        row = await lib.upload_image("动画.gif", content, "双色动画")
        path = await lib.image_file(row["id"])
        self.assertEqual(path.read_bytes(), content)
        with Image.open(path) as image:
            self.assertEqual(image.n_frames, 2)
        path.write_bytes(b"replaced file")
        self.assertIsNone(await lib.image_file(row["id"]))

    async def test_jpeg_trailing_bytes_are_preserved_but_missing_eoi_is_rejected(self):
        lib = ResourceLibrary(self.root)
        await lib.open()
        original = self.upload_bytes("JPEG")
        trailing = original + b"\x1fV\x80W\x00\x00\x00\x004\x12\x1f\x02\xd1\x8c\xa4\xdc\xf5\xc2\xfe\xef\xd8WM\xff"
        alternate = self.upload_bytes('JPEG', color='blue') + b'public-test-trailing-data'
        for filename, content in (("trailing.jpeg", trailing), ('generated-blue.jpg', alternate)):
            with self.subTest(filename=filename):
                self.assertFalse(content.endswith(b"\xff\xd9"))
                row = await lib.upload_image(filename, content, "含尾随数据的正常JPEG")
                path = await lib.image_file(row["id"])
                self.assertEqual(path.suffix, ".jpg")
                self.assertEqual(path.read_bytes(), content)
                with Image.open(path) as image:
                    image.load()
                    self.assertEqual(image.format, "JPEG")
        with self.assertRaises(ValueError):
            await lib.upload_image("missing-eoi.jpeg", original[:-2])
        self.assertEqual((await lib.list_items("images"))["total"], 2)

    async def test_overlay_and_upload_links_cannot_escape_root(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        try:
            (self.root / "uploads").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("host does not grant directory symlink creation")
        lib = ResourceLibrary(self.root)
        await lib.open()
        with self.assertRaises(ValueError):
            await lib.upload_image("cat.png", self.upload_bytes())
        self.assertEqual(list(outside.iterdir()), [])
        (self.root / "uploads").unlink()
        secret = outside / "secret.json"
        secret.write_text("private", encoding="utf-8")
        (self.root / "admin-resources.json").symlink_to(secret)
        await lib.open()
        with self.assertRaises(ValueError):
            await lib.create_item("knowledge", {"text": "不得写到外面"})
        self.assertEqual(secret.read_text(encoding="utf-8"), "private")

    async def test_invalid_metadata_is_rejected_without_changing_overlay(self):
        lib = ResourceLibrary(self.root)
        await lib.open()
        row = await lib.create_item("jargon", {"text": "测试词", "meaning": "有效"})
        before = (self.root / "admin-resources.json").read_bytes()
        for payload in ({"path": "../secret.png"}, {"enabled": 1}, {"text": ""}, {"meaning": "x" * 601}):
            with self.assertRaises(ValueError):
                await lib.update_item("jargon", row["id"], payload)
        with self.assertRaises(ValueError):
            await lib.create_item("images", {"description": "不允许路径创建"})
        with self.assertRaises(ValueError):
            await lib.list_items("private-history")
        self.assertEqual((self.root / "admin-resources.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
