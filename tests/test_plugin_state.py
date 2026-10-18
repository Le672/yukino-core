"""Plugin state paths stay scoped, trusted and stable across package updates."""
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
import zipfile

from yukino_core.plugin_packages import install_package, remove_package
from yukino_core.plugins import PluginManager


WORKER = '''import json
from pathlib import Path
def handle(text, context):
    directory = Path(context["data_dir"])
    counter = directory / "counter.txt"
    value = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(value))
    return json.dumps({"data_dir": str(directory), "count": value,
                       "settings": context["settings"], "self_id": context["self_id"],
                       "owner_ids": context["owner_ids"]})
'''


def package():
    output = io.BytesIO()
    record = dict(id='durable', name='State test', description='Test state',
                  worker='worker.py', commands=['/durable'])
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('plugin.json', json.dumps(record))
        archive.writestr('worker.py', WORKER)
    return output.getvalue()


class PluginStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = SimpleNamespace(plugin_flags={}, plugin_settings={},
            self_id='bot', owner_ids=['owner'], save=lambda: None)
        self.manager = PluginManager(self.config, self.root / 'plugins')

    async def asyncTearDown(self):
        await self.manager.close()
        self.tmp.cleanup()

    def item(self):
        return next(item for item in self.manager.items if item['id'] == 'durable')

    async def invoke(self, context=None):
        return await self.manager.execute(self.item(), '/durable', context or {}, timeout=2)

    async def test_worker_receives_trusted_absolute_path_and_protected_context(self):
        await install_package(self.manager, package())
        result = json.loads(await self.invoke({'data_dir': str(self.root / 'outside'),
            'settings': {'injected': True}, 'self_id': 'fake', 'owner_ids': ['fake']}))
        expected = (self.root / 'plugins' / 'state' / 'durable').resolve()
        self.assertEqual(result['data_dir'], str(expected))
        self.assertTrue(expected.is_dir())
        self.assertFalse((self.root / 'outside').exists())
        self.assertNotIn('injected', result['settings'])
        self.assertEqual(result['self_id'], 'bot')
        self.assertEqual(result['owner_ids'], ['owner'])
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(expected.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(expected.parent.stat().st_mode), 0o700)

    async def test_state_survives_worker_restart_and_reinstall_with_new_package_uuid(self):
        await install_package(self.manager, package())
        first_worker = self.item()['worker']
        first = json.loads(await self.invoke())
        await self.manager._kill('durable')
        second = json.loads(await self.invoke())
        await remove_package(self.manager, 'durable')
        await install_package(self.manager, package())
        self.assertNotEqual(first_worker, self.item()['worker'])
        third = json.loads(await self.invoke())
        self.assertEqual([first['count'], second['count'], third['count']], [1, 2, 3])
        self.assertEqual(first['data_dir'], second['data_dir'])
        self.assertEqual(first['data_dir'], third['data_dir'])

    async def test_legacy_manifest_ids_cannot_create_paths_or_start_workers(self):
        self.manager.root.mkdir()
        for name in ('../outside', '/outside', 'C:\\outside', 'Upper', '', 'a' * 41, 'a/b', 'a..b'):
            with self.subTest(name=name):
                item = dict(id=name, name='Invalid', description='Test', worker='worker.py', commands=['/invalid'])
                self.manager.items.append(item)
                reply = await self.manager.execute(item, '/invalid', {}, timeout=1)
                self.assertIn('数据目录', reply)
                self.assertNotIn(name, self.manager.workers)
        self.assertFalse((self.manager.root / 'state').exists())
        self.assertFalse((self.root / 'outside').exists())

    async def test_listing_settings_and_native_configuration_do_not_create_state(self):
        self.manager.list()
        self.manager.get_settings('dice')
        self.manager.update_settings('dice', {'max_count': 5})
        await self.manager.toggle('dice', False)
        self.assertFalse(self.manager.root.exists())
        await install_package(self.manager, package())
        self.manager.list()
        self.manager.get_settings('durable')
        self.assertFalse((self.manager.root / 'state').exists())

    async def test_symlinks_at_plugin_root_state_root_or_plugin_directory_are_rejected(self):
        if not hasattr(os, 'symlink'): self.skipTest('symbolic links unsupported')
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'keep.txt').write_text('untouched')
        try:
            probe = self.root / 'probe'
            probe.symlink_to(outside, target_is_directory=True)
            probe.unlink()
        except (OSError, NotImplementedError):
            self.skipTest('creating symbolic links is not permitted')
        for level in ('plugins', 'state', 'durable'):
            with self.subTest(level=level):
                base = self.root / ('case-' + level)
                root = base / 'plugins'
                base.mkdir()
                if level == 'plugins':
                    root.symlink_to(outside, target_is_directory=True)
                else:
                    root.mkdir()
                    state = root / 'state'
                    if level == 'state': state.symlink_to(outside, target_is_directory=True)
                    else:
                        state.mkdir()
                        (state / 'durable').symlink_to(outside, target_is_directory=True)
                manager = PluginManager(self.config, root)
                item = dict(id='durable', name='State', description='Test', worker='worker.py', commands=['/durable'])
                manager.items.append(item)
                reply = await manager.execute(item, '/durable', {}, timeout=1)
                self.assertIn('数据目录', reply)
                self.assertFalse(manager.workers)
                self.assertEqual(list(outside.iterdir()), [outside / 'keep.txt'])
                self.assertEqual((outside / 'keep.txt').read_text(), 'untouched')
                await manager.close()


if __name__ == '__main__':
    unittest.main()
