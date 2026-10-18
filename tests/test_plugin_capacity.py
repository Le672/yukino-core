import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import zipfile

from yukino_core.plugins import PluginManager, MAX_EXTERNAL_PLUGINS, MAX_MANIFEST_BYTES, BUILTINS
from yukino_core.plugin_packages import install_package


def records(count):
    return [dict(id=f'capacity_{i}',name=f'Capacity {i}',description='x'*450,
        commands=[f'/capacity_{i}'],worker='placeholder.py') for i in range(count)]


def payload():
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as package:
        package.writestr('plugin.json',json.dumps(dict(id='capacity_new',name='New',description='New plugin',
            commands=['/capacity_new'],worker='worker.py')))
        package.writestr('worker.py','def handle(text,context):\n    return "ok"\n')
    return output.getvalue()


class PluginCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.config=SimpleNamespace(data={},plugin_flags={},plugin_settings={},save=lambda:None)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def manager(self,count):
        (self.root/'manifest.json').write_text(json.dumps(records(count)),encoding='utf-8')
        return PluginManager(self.config,self.root)

    async def test_loads_more_than_64kb_and_50_plugins(self):
        manager=self.manager(MAX_EXTERNAL_PLUGINS)
        self.assertGreater((self.root/'manifest.json').stat().st_size,65536)
        self.assertFalse(manager.manifest_error)
        self.assertEqual(len(manager.items),len(BUILTINS)+MAX_EXTERNAL_PLUGINS)

    async def test_over_capacity_rejected_without_hiding_builtins(self):
        manager=self.manager(MAX_EXTERNAL_PLUGINS+1)
        self.assertTrue(manager.manifest_error)
        self.assertEqual(len(manager.items),len(BUILTINS))

    async def test_oversized_manifest_rejected(self):
        (self.root/'manifest.json').write_bytes(b'[]'+b' '*MAX_MANIFEST_BYTES)
        manager=PluginManager(self.config,self.root)
        self.assertTrue(manager.manifest_error)
        self.assertEqual(len(manager.items),len(BUILTINS))

    async def test_last_slot_installs_disabled_and_survives_reload(self):
        manager=self.manager(MAX_EXTERNAL_PLUGINS-1)
        result=await install_package(manager,payload())
        self.assertFalse(result['enabled'])
        self.assertEqual(len(manager.items),len(BUILTINS)+MAX_EXTERNAL_PLUGINS)
        again=PluginManager(self.config,self.root)
        self.assertFalse(again.manifest_error)
        self.assertEqual(len(again.items),len(manager.items))

    async def test_install_at_capacity_does_not_mutate_manifest(self):
        manager=self.manager(MAX_EXTERNAL_PLUGINS);before=(self.root/'manifest.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'128个外部'):
            await install_package(manager,payload())
        self.assertEqual((self.root/'manifest.json').read_bytes(),before)
        self.assertFalse(manager.mutating)


if __name__=='__main__':unittest.main()
