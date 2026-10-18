from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parent.parent


def script(name):
    spec = importlib.util.spec_from_file_location("release_" + name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = script("build_release")
initializer = script("init_instance")


class ReleaseTests(unittest.TestCase):
    def fixture(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        for name in builder.ROOT_FILES:
            shutil.copy2(ROOT / name, root / name)
        for name in builder.RELEASE_SCRIPTS:
            destination = root / "scripts" / name
            destination.parent.mkdir(exist_ok=True)
            shutil.copy2(ROOT / "scripts" / name, destination)
        for folder in ("yukino_core/static", "tests", "docs"):
            (root / folder).mkdir(parents=True, exist_ok=True)
        (root / "yukino_core/__init__.py").write_text('__version__="0.3.0"\n', encoding="utf-8")
        (root / "yukino_core/static/index.html").write_text(
            '<link rel="icon" type="image/png" href="/static/brand-logo.png">', encoding="utf-8")
        (root / "docs/QUICKSTART.md").write_text("# Empty instance quickstart\n", encoding="utf-8")

    def test_archive_excludes_private_runtime_and_replaces_logo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.fixture(root)
            for name in ("data/secrets.json", "credentials/id_ed25519", "resources/images/private.png",
                         "yukino_core/__pycache__/config.pyc", "integrations/account.json",
                         "yukino_core/static/brand-logo.png", ".env"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"PRIVATE_NOT_FOR_RELEASE")
            result = builder.build(root, Path(tmp) / "dist")
            archive = Path(result["archive"])
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), result["sha256"])
            with zipfile.ZipFile(archive) as package:
                payloads = {name.split("/", 1)[1]: package.read(name) for name in package.namelist()}
            self.assertFalse(any(b"PRIVATE_NOT_FOR_RELEASE" in data for data in payloads.values()))
            self.assertIn("yukino_core/static/brand-logo.svg", payloads)
            self.assertNotIn("yukino_core/static/brand-logo.png", payloads)
            self.assertIn(b"brand-logo.svg", payloads["yukino_core/static/index.html"])
            self.assertEqual(json.loads(payloads["resources/curated.json"])["images"], [])
            manifest = json.loads(payloads["release-manifest.json"])
            self.assertFalse(manifest["contains_credentials"])
            self.assertFalse(manifest["optional_integrations_included"])
            for row in manifest["entries"]:
                self.assertEqual(hashlib.sha256(payloads[row["path"]]).hexdigest(), row["sha256"])
            with self.assertRaises(FileExistsError):
                builder.build(root, Path(tmp) / "dist")
            repeated = builder.build(root, Path(tmp) / "dist-second")
            self.assertEqual(result["sha256"], repeated["sha256"])

    def test_known_private_account_in_source_blocks_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            marker = "EXAMPLE_PRIVATE_DEPLOYMENT_SENTINEL"
            (root / "yukino_core/leaked.py").write_text("ACCOUNT=" + repr(marker), encoding="utf-8")
            with patch.dict("os.environ", {"YUKINO_RELEASE_FORBIDDEN": marker}):
                with self.assertRaisesRegex(ValueError, "私人部署标记"):
                    builder.collect_files(root)

    def test_named_tooling_and_plugin_sources_are_included_without_generated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            allowed = ("windows/README.md", "windows/launcher.py", "extensions/interaction_pack/catalog.json",
                       "examples/plugins/hello_counter/worker.py", ".github/workflows/ci.yml")
            excluded = ("windows/__pycache__/launcher.pyc", "windows/data/secrets.json",
                        "extensions/unknown_pack/worker.py", "extensions/interaction_pack/dist/artifact.json")
            for name in (*allowed, *excluded):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")
            files, _ = builder.collect_files(root)
            for name in allowed:
                self.assertIn(name, files)
            for name in excluded:
                self.assertNotIn(name, files)

    def test_generic_scan_blocks_account_assignments_and_keys(self):
        example_id = "123456789"
        self.assertTrue(builder.contains_private_data('self_id = ' + json.dumps(example_id)))
        self.assertTrue(builder.contains_private_data(json.dumps({"owner_ids": [example_id]})))
        self.assertTrue(builder.contains_private_data("-----BEGIN " + "OPENSSH PRIVATE KEY-----"))
        self.assertTrue(builder.contains_private_data("sk-" + "x" * 32))
        self.assertFalse(builder.contains_private_data("http://127.0.0.1:18001/health"))
        self.assertFalse(builder.contains_private_data("ws://192.0.2.1:3001"))

    def test_source_link_is_rejected_instead_of_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.fixture(root)
            external = Path(tmp) / "external.py"
            external.write_text("PRIVATE", encoding="utf-8")
            try:
                (root / "yukino_core/link.py").symlink_to(external)
            except OSError:
                self.skipTest("Windows account lacks symlink privileges")
            with self.assertRaisesRegex(ValueError, "越界路径"):
                builder.collect_files(root)

    def test_initialization_has_random_tokens_empty_models_and_no_receive(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "first", Path(tmp) / "second"
            initializer.initialize(first)
            initializer.initialize(second, docker=True)
            tokens = json.loads((first / "secrets.json").read_text(encoding="utf-8"))
            self.assertEqual(set(tokens), {"admin_token", "onebot_token", "provider_token"})
            self.assertTrue(all(len(token) >= 32 for token in tokens.values()))
            self.assertNotEqual(tokens, json.loads((second / "secrets.json").read_text(encoding="utf-8")))
            connection = json.loads((first / "connections.json").read_text(encoding="utf-8"))
            self.assertFalse(connection["receive_enabled"])
            self.assertEqual(connection["self_id"], "")
            self.assertEqual(connection["owner_ids"], [])
            self.assertEqual(connection["onebot_token"], tokens["onebot_token"])
            self.assertEqual(json.loads((first / "providers.json").read_text(encoding="utf-8")),
                             {"profiles": [], "default_id": ""})
            before = (first / "secrets.json").read_bytes()
            with self.assertRaises(FileExistsError):
                initializer.initialize(first)
            self.assertEqual((first / "secrets.json").read_bytes(), before)

    def test_package_metadata_has_installable_core_and_console_entry(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8-sig"))
        self.assertEqual(metadata["project"]["name"], "yukino-core")
        self.assertEqual(metadata["project"]["scripts"]["yukino-core"], "yukino_core.__main__:main")
        self.assertIn("setuptools.build_meta", metadata["build-system"]["build-backend"])
        self.assertTrue((ROOT / "LICENSE").is_file())
        self.assertTrue((ROOT / "yukino_core/__main__.py").is_file())
        dependencies = metadata["project"]["dependencies"]
        self.assertTrue(any(item.startswith("aiohttp") for item in dependencies))
        self.assertFalse(any("file:" in item or "D:" in item for item in dependencies))

    def test_public_config_example_covers_runtime_fields(self):
        from yukino_core.config import Config, DEFAULTS
        sample = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8-sig"))
        self.assertTrue(set(DEFAULTS).issubset(sample))
        validated = Config.validate({key: sample[key] for key in DEFAULTS})
        self.assertEqual(set(validated), set(DEFAULTS))
        self.assertEqual(sample["web_prompt_marker"], "")


if __name__ == "__main__":
    unittest.main()
