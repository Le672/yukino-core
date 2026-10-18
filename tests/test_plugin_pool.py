"""The worker pool bounds persistent processes without losing plugin state."""
import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from yukino_core.plugins import PluginManager

CODE='''import time
from pathlib import Path
def handle(text,context):
    root=Path(context['data_dir']);marker=root/'count.txt'
    count=int(marker.read_text())+1 if marker.exists() else 1
    marker.write_text(str(count))
    if text.endswith('slow'):time.sleep(.3)
    return str(count)
'''

class PoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.cfg=SimpleNamespace(data={'plugin_worker_limit':1},plugin_flags={},plugin_settings={},save=lambda:None)
        self.manager=PluginManager(self.cfg,self.root)
        (self.root/'worker.py').write_text(CODE,encoding='utf-8')
        self.items={}
        for name in ('one','two','three'):
            item={'id':name,'name':name,'description':'test','commands':['/'+name],'worker':'worker.py'}
            self.manager.items.append(item);self.items[name]=item
    async def asyncTearDown(self):await self.manager.close();self.tmp.cleanup()
    async def call(self,pid,text='run'):return await self.manager.execute(self.items[pid],text,{},timeout=2)
    async def test_idle_worker_evicted_and_durable_state_restored(self):
        self.assertEqual(await self.call('one'),'1');old=self.manager.workers['one']
        self.assertEqual(await self.call('two'),'1');self.assertIsNotNone(old.returncode)
        self.assertEqual(set(self.manager.workers),{'two'})
        self.assertEqual(await self.call('one'),'2');self.assertEqual(len(self.manager.workers),1)
    async def test_busy_worker_is_not_killed_or_replayed_to_make_room(self):
        pending=asyncio.create_task(self.call('one','slow'))
        async with asyncio.timeout(2):
            while not (self.root/'state/one/count.txt').exists():await asyncio.sleep(.005)
        self.assertIn('都在处理',await self.call('two'))
        self.assertEqual(await pending,'1');self.assertNotIn('two',self.manager.workers)
        self.assertNotIn('two',self.manager.errors)
    async def test_simultaneous_spawns_never_exceed_limit(self):
        await asyncio.gather(self.call('one'),self.call('two'),self.call('three'))
        self.assertLessEqual(len(self.manager.workers),1)
    async def test_lowering_limit_reclaims_idle_workers_on_next_call(self):
        self.cfg.data['plugin_worker_limit']=3
        for pid in self.items:await self.call(pid)
        self.assertEqual(len(self.manager.workers),3)
        self.cfg.data['plugin_worker_limit']=1
        await self.call('three');self.assertEqual(set(self.manager.workers),{'three'})

if __name__=='__main__':unittest.main()
