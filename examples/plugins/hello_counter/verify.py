"""Exercise the real package installer and worker without a QQ/model connection."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
CORE = ROOT.parents[2]
sys.path.insert(0, str(CORE))
from yukino_core.config import Config
from yukino_core.plugin_packages import install_package, reload_plugins, remove_package
from yukino_core.plugins import PluginManager


async def verify(package):
    payload = package.read_bytes()
    # This is a separate CLI process; do not use an existing instance's secrets.
    os.environ["YUKINO_ADMIN_TOKEN"] = secrets.token_urlsafe(32)
    os.environ["YUKINO_SECRETS_FILE"] = ""
    with tempfile.TemporaryDirectory(prefix="yukino-plugin-guide-") as temporary:
        root = Path(temporary)
        config = Config(root / "config.json")
        config.save()
        manager = PluginManager(config, root / "plugins")
        try:
            installed = await install_package(manager, payload)
            assert installed["id"] == "hello_counter", "This verifier is for the bundled example"
            assert installed["enabled"] is False and installed["code_executed"] is False
            await manager.toggle("hello_counter", True)
            manager.update_settings("hello_counter", {"timeout": 5, "greeting": "早上好", "style": "plain"})

            async def call(text, session="g:example", user="example-a"):
                item, _ = manager.match(text)
                assert item is not None and item["id"] == "hello_counter"
                result = await manager.execute(item, text, {"session_id": session, "user_id": user})
                assert "hello_counter" not in manager.errors, manager.errors
                return result

            first = await call("/hello 小明")
            assert first == "早上好，小明。\n这是你在本会话的第 1 次问候。", first
            assert "1 次" in await call("/hello_count")
            assert "0 次" in await call("/hello_count", user="example-b")
            assert "0 次" in await call("/hello_count", session="g:another")
            manager.update_settings("hello_counter", {"show_count": False})
            assert await call("/hello 小明") == "早上好，小明。"
            manager.update_settings("hello_counter", {"show_count": True})
            await reload_plugins(manager)
            assert "2 次" in await call("/hello_count")
            await remove_package(manager, "hello_counter")
            installed_again = await install_package(manager, payload)
            assert not installed_again["enabled"]
            await manager.toggle("hello_counter", True)
            assert "2 次" in await call("/hello_count")
        finally:
            await manager.close()
    return {"passed": True, "checks": ["package_install", "disabled_by_default", "real_worker",
        "settings_applied", "user_isolation", "session_isolation", "reload_persistence",
        "reinstall_persistence"], "qq_connected": False, "model_called": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(verify(args.package)), ensure_ascii=False))
