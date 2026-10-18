"""ZIP transactions, real worker invocation and localhost authenticated API proof."""
import asyncio
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import warnings
import zipfile

from aiohttp import CookieJar, FormData
from aiohttp.test_utils import TestClient, TestServer

from yukino_core.app import create_app
from yukino_core.config import Config
from yukino_core.plugin_packages import inspect_package, install_package, reload_plugins, remove_package
from yukino_core.plugins import PluginManager


def package(pid='hello', extra=None, record=None, worker=None):
    data={'id':pid,'name':'问候插件','description':'本地测试','worker':'worker.py','commands':['/hello']}
    data.update(record or {})
    entries=[('plugin.json',json.dumps(data,ensure_ascii=False)),('worker.py',worker or 'def handle(text, context):\n    return "hello:" + text\n')]
    entries.extend(extra or [])
    output=io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore',UserWarning)
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for name,raw in entries:
                if isinstance(name,str):
                    # Windows' ZipInfo constructor normalizes backslashes; use
                    # the literal hostile archive name for cross-platform proof.
                    info=zipfile.ZipInfo('placeholder')
                    info.filename=info.orig_filename=name
                else: info=name
                archive.writestr(info,raw,compress_type=zipfile.ZIP_DEFLATED)
    return output.getvalue()


class PackageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.environment=patch.dict(os.environ,{'YUKINO_ADMIN_TOKEN':'test-package-admin-credential',
            'YUKINO_ONEBOT_TOKEN':'test-onebot','YUKINO_PROVIDER_TOKEN':'test-provider','YUKINO_SECRETS_FILE':''})
        self.environment.start()
        self.config=Config(self.root/'config.json')
        self.config.save()
        self.manager=PluginManager(self.config,self.root/'plugins')

    async def asyncTearDown(self):
        await self.manager.close()
        self.environment.stop()
        self.tmp.cleanup()

    def installed(self,pid='hello'):
        return next(item for item in self.manager.items if item['id']==pid)

    async def test_install_does_not_execute_then_enable_invokes_real_worker(self):
        marker=self.root/'worker-imported.txt'
        worker=f'from pathlib import Path\nPath({str(marker)!r}).write_text("imported")\ndef handle(text, context):\n    return "hello:" + text\n'
        result=await install_package(self.manager,package(worker=worker))
        self.assertEqual(result,{'installed':True,'id':'hello','enabled':False,'code_executed':False})
        self.assertFalse(marker.exists())
        self.assertFalse(self.manager.enabled('hello'))
        self.assertFalse(PluginManager(Config(self.config.path),self.manager.root).enabled('hello'))
        await reload_plugins(self.manager)
        self.assertFalse(marker.exists())
        await self.manager.toggle('hello',True)
        reply=await self.manager.execute(self.installed(),'/hello test',{},timeout=5)
        self.assertEqual(reply,'hello:/hello test')
        self.assertTrue(marker.exists())
        self.assertIsNone(self.manager.workers['hello'].returncode)

    async def test_rejects_traversal_absolute_backslash_drive_and_symlink(self):
        link=zipfile.ZipInfo('linked.py')
        link.create_system=3
        link.external_attr=(stat.S_IFLNK|0o777)<<16
        for name in ('../escape.py','/absolute.py','folder/../../escape.py','folder\\escape.py','C:/escape.py',
                     '.. /escape.py','directory./escape.py','CON.py',link):
            with self.subTest(name=str(name)),self.assertRaises(ValueError):
                await install_package(self.manager,package(extra=[(name,'outside')]))
            self.assertFalse(self.manager.mutating)
        self.assertFalse((self.manager.root/'manifest.json').exists())
        self.assertFalse((self.root/'escape.py').exists())

    async def test_rejects_duplicate_zip_paths_duplicate_ids_and_large_archives(self):
        for payload in (package(extra=[('worker.py','duplicate')]),package(extra=[('Worker.py','case collision')]),package(extra=[('blob.txt','x'*1_000_001)]),
                        package(extra=[(f'{i}.txt','x') for i in range(49)]),b'x'*2_000_001,b'not a zip'):
            with self.subTest(size=len(payload)),self.assertRaises(ValueError):
                await install_package(self.manager,payload)
            self.assertFalse(self.manager.mutating)
        await install_package(self.manager,package())
        before=(self.manager.root/'manifest.json').read_bytes()
        with self.assertRaises(ValueError): await install_package(self.manager,package())
        self.assertEqual((self.manager.root/'manifest.json').read_bytes(),before)
        self.assertEqual(len(list((self.manager.root/'packages').iterdir())),1)

    async def test_failed_save_restores_manifest_flags_config_and_only_own_directory(self):
        await install_package(self.manager,package('existing'))
        await self.manager.toggle('existing',True)
        previous_manifest=(self.manager.root/'manifest.json').read_bytes()
        previous_config=self.config.path.read_bytes()
        existing_dirs=set((self.manager.root/'packages').iterdir())
        sentinel=self.manager.root/'packages'/'unrelated'
        sentinel.mkdir(); (sentinel/'keep.txt').write_text('keep')
        original_save=self.config.save
        def failed_save():
            original_save()
            raise OSError('simulated write failure after replacement')
        with patch.object(self.config,'save',side_effect=failed_save),self.assertRaises(OSError):
            await install_package(self.manager,package())
        self.assertEqual((self.manager.root/'manifest.json').read_bytes(),previous_manifest)
        self.assertEqual(self.config.path.read_bytes(),previous_config)
        self.assertEqual(self.config.plugin_flags,{'existing':True})
        self.assertEqual(set((self.manager.root/'packages').iterdir()),existing_dirs|{sentinel})
        self.assertEqual((sentinel/'keep.txt').read_text(),'keep')
        self.assertFalse(self.manager.mutating)

    async def test_failed_validation_and_partial_file_write_clean_fresh_directory(self):
        with self.assertRaises(ValueError):
            await install_package(self.manager,package(record={'auto_url_hosts':['not a host']}))
        self.assertFalse((self.manager.root/'manifest.json').exists())
        self.assertEqual(list((self.manager.root/'packages').iterdir()),[])
        original_write=Path.write_bytes
        def failed_write(path,raw):
            if path.name=='worker.py': raise OSError('simulated source write failure')
            return original_write(path,raw)
        with patch.object(Path,'write_bytes',failed_write),self.assertRaises(OSError):
            await install_package(self.manager,package())
        self.assertEqual(list((self.manager.root/'packages').iterdir()),[])
        self.assertEqual(self.config.plugin_flags,{})
        self.assertFalse(self.manager.mutating)

    async def test_install_cancellation_releases_flag_without_source_changes(self):
        entered=asyncio.Event(); finish=asyncio.Event()
        async def delayed_inspect(*args):
            entered.set(); await finish.wait()
        with patch('yukino_core.plugin_packages.asyncio.to_thread',side_effect=delayed_inspect):
            task=asyncio.create_task(install_package(self.manager,package()))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertFalse(self.manager.mutating)
        self.assertFalse(self.manager.root.exists())

    async def test_concurrent_updates_and_execute_are_rejected_before_inspection_await(self):
        entered=asyncio.Event(); finish=asyncio.Event()
        async def delayed_inspect(function,payload):
            entered.set(); await finish.wait(); return function(payload)
        with patch('yukino_core.plugin_packages.asyncio.to_thread',side_effect=delayed_inspect):
            task=asyncio.create_task(install_package(self.manager,package()))
            await entered.wait()
            self.assertTrue(self.manager.mutating)
            for operation in (install_package(self.manager,package('other')),reload_plugins(self.manager),remove_package(self.manager,'hello')):
                with self.assertRaisesRegex(ValueError,'更新'): await operation
            reply=await self.manager.execute(self.installed('calculator'),'/计算 1+1',{})
            self.assertIn('更新',reply)
            self.assertEqual(self.manager.workers,{})
            finish.set(); await task
        self.assertFalse(self.manager.mutating)

    async def test_busy_plugin_rejects_every_mutation(self):
        await install_package(self.manager,package())
        lock=self.manager.locks.setdefault('hello',asyncio.Lock())
        await lock.acquire()
        try:
            for operation in (install_package(self.manager,package('other')),reload_plugins(self.manager),remove_package(self.manager,'hello')):
                with self.assertRaisesRegex(ValueError,'执行'): await operation
                self.assertFalse(self.manager.mutating)
        finally: lock.release()

    async def test_reload_holds_mutation_flag_until_close_completes(self):
        entered=asyncio.Event(); finish=asyncio.Event()
        async def delayed_close(): entered.set(); await finish.wait()
        with patch.object(self.manager,'close',side_effect=delayed_close):
            task=asyncio.create_task(reload_plugins(self.manager))
            await entered.wait()
            with self.assertRaises(ValueError): await reload_plugins(self.manager)
            self.assertIn('更新',await self.manager.execute(self.installed('calculator'),'x',{}))
            finish.set(); await task
        self.assertFalse(self.manager.mutating)

    async def test_remove_stops_worker_retains_source_and_manifest_backup(self):
        await install_package(self.manager,package())
        item=self.installed(); source=self.manager.root/item['worker']
        await self.manager.toggle('hello',True)
        self.assertEqual(await self.manager.execute(item,'live',{}),'hello:live')
        process=self.manager.workers['hello']
        result=await remove_package(self.manager,'hello')
        self.assertTrue(result['files_retained']); self.assertTrue(source.exists())
        self.assertIsNotNone(process.returncode)
        self.assertNotIn('hello',[i['id'] for i in self.manager.items])
        self.assertNotIn('hello',self.config.plugin_flags)
        backups=list((self.manager.root/'removed-manifests').glob('hello-*.json'))
        self.assertEqual(len(backups),1)
        self.assertEqual(json.loads(backups[0].read_text(encoding='utf-8'))[0]['id'],'hello')
        with self.assertRaises(ValueError): await remove_package(self.manager,'calculator')

    async def test_failed_remove_restores_manifest_flags_and_persisted_config(self):
        await install_package(self.manager,package())
        await self.manager.toggle('hello',True)
        before=(self.manager.root/'manifest.json').read_bytes(); config_before=self.config.path.read_bytes()
        with patch.object(self.manager,'_kill',new=AsyncMock(side_effect=OSError('kill failed'))),self.assertRaises(OSError):
            await remove_package(self.manager,'hello')
        self.assertEqual((self.manager.root/'manifest.json').read_bytes(),before)
        self.assertEqual(self.config.path.read_bytes(),config_before)
        self.assertTrue(self.manager.enabled('hello')); self.assertTrue(self.installed())
        self.assertFalse(self.manager.mutating)

    async def test_remove_holds_flag_while_awaiting_worker_exit(self):
        await install_package(self.manager,package())
        entered=asyncio.Event(); finish=asyncio.Event()
        async def delayed_kill(pid): entered.set(); await finish.wait()
        with patch.object(self.manager,'_kill',side_effect=delayed_kill):
            task=asyncio.create_task(remove_package(self.manager,'hello'))
            await entered.wait()
            with self.assertRaises(ValueError): await install_package(self.manager,package('other'))
            self.assertIn('更新',await self.manager.execute(self.installed(),'x',{}))
            finish.set(); await task
        self.assertFalse(self.manager.mutating)

    async def test_remove_save_failure_rolls_back_without_losing_other_settings(self):
        await install_package(self.manager,package())
        before=(self.manager.root/'manifest.json').read_bytes()
        original_save=self.config.save
        async def worker_exit(pid):
            # Represents another authenticated settings request completing while
            # worker shutdown is awaited.
            self.config.data['name']='Concurrent setting'
            original_save()
        def failed_save():
            original_save()
            raise OSError('simulated removal save failure')
        with patch.object(self.manager,'_kill',side_effect=worker_exit),patch.object(self.config,'save',side_effect=failed_save):
            with self.assertRaises(OSError): await remove_package(self.manager,'hello')
        self.assertEqual((self.manager.root/'manifest.json').read_bytes(),before)
        restored=Config(self.config.path)
        self.assertEqual(restored.data['name'],'Concurrent setting')
        self.assertEqual(restored.plugin_flags,{'hello':False})
        self.assertTrue(self.installed()); self.assertFalse(self.manager.mutating)

    async def test_cancelled_remove_restores_manifest_config_and_releases_flag(self):
        await install_package(self.manager,package())
        before=(self.manager.root/'manifest.json').read_bytes(); config_before=self.config.path.read_bytes()
        entered=asyncio.Event(); finish=asyncio.Event()
        async def delayed_kill(pid): entered.set(); await finish.wait()
        with patch.object(self.manager,'_kill',side_effect=delayed_kill):
            task=asyncio.create_task(remove_package(self.manager,'hello'))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual((self.manager.root/'manifest.json').read_bytes(),before)
        self.assertEqual(self.config.path.read_bytes(),config_before)
        self.assertFalse(self.manager.enabled('hello')); self.assertTrue(self.installed())
        self.assertFalse(self.manager.mutating)

    async def test_reload_validation_failure_keeps_existing_items(self):
        await install_package(self.manager,package())
        original_items=self.manager.items
        (self.manager.root/'manifest.json').write_text('{invalid json',encoding='utf-8')
        with self.assertRaises(ValueError): await reload_plugins(self.manager)
        self.assertIs(self.manager.items,original_items)
        self.assertFalse(self.manager.mutating)

    async def test_api_auth_csrf_upload_reload_enable_execute_and_remove(self):
        kernel=SimpleNamespace(config=self.config,plugins=self.manager)
        client=TestClient(TestServer(create_app(kernel)),cookie_jar=CookieJar(unsafe=True))
        await client.start_server()
        def upload():
            form=FormData(); form.add_field('file',package(),filename='hello.zip',content_type='application/zip')
            return form
        try:
            for method,url in (('post','/api/plugins/install'),('post','/api/plugins/reload'),('delete','/api/plugins/hello')):
                response=await getattr(client,method)(url)
                self.assertEqual(response.status,401)
            response=await client.post('/api/login',json={'token':self.config.admin_token})
            headers={'X-CSRF-Token':(await response.json())['csrf_token']}
            for method,url in (('post','/api/plugins/install'),('post','/api/plugins/reload'),('delete','/api/plugins/hello')):
                response=await getattr(client,method)(url)
                self.assertEqual(response.status,403)
            response=await client.post('/api/plugins/install',data=upload(),headers=headers)
            self.assertEqual(response.status,200)
            self.assertFalse((await response.json())['data']['enabled'])
            response=await client.post('/api/plugins/reload',json={},headers=headers)
            self.assertEqual(response.status,200)
            response=await client.post('/api/plugins/toggle',json={'id':'hello','enabled':True},headers=headers)
            self.assertEqual(response.status,200)
            self.assertEqual(await self.manager.execute(self.installed(),'api',{}),'hello:api')
            response=await client.delete('/api/plugins/hello',headers=headers)
            self.assertEqual(response.status,200)
            self.assertTrue((await response.json())['data']['files_retained'])
        finally: await client.close()


if __name__=='__main__': unittest.main()
