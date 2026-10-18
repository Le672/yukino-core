"""Settings are exercised through real HTTP, SQLite and the actual model registry."""
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from yukino_core.app import create_app
from yukino_core.config import Config, DEFAULTS, settings_schema
from yukino_core.core import Kernel
from yukino_core.models import ProviderManager
from yukino_core.plugins import PluginManager
from yukino_core.resources import ResourceLibrary
from yukino_core.store import Store
import test_core as fixtures


class SettingsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ, {'YUKINO_ADMIN_TOKEN':'private-admin-credential-123', 'YUKINO_SECRETS_FILE':'',
            'YUKINO_ONEBOT_TOKEN':'private-onebot-credential', 'YUKINO_PROVIDER_TOKEN':'private-provider-credential'})
        self.env.start()
        self.config=Config(self.root/'config.json')
        self.store=Store(self.root/'core.db'); await self.store.open()
        self.resources=ResourceLibrary(self.root/'resources'); await self.resources.open()
        self.provider=ProviderManager(self.config,self.root/'providers.json'); await self.provider.start()
        self.plugins=PluginManager(self.config,self.root/'plugins')
        self.kernel=Kernel(self.config,self.store,self.resources,self.provider,self.plugins)
        self.kernel.transport=fixtures.FakeTransport()
        self.client=TestClient(TestServer(create_app(self.kernel)),cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        r=await self.client.post('/api/login',json={'token':self.config.admin_token})
        self.headers={'X-CSRF-Token':(await r.json())['csrf_token']}

    async def asyncTearDown(self):
        await self.client.close(); await self.kernel.close(); await self.provider.close(); await self.store.close(); self.tmp.cleanup(); self.env.stop()

    async def test_schema_covers_every_mutable_field_and_roundtrips(self):
        r=await self.client.get('/api/settings/schema')
        fields=(await r.json())['data']['fields']
        self.assertEqual({f['key'] for f in fields},set(DEFAULTS))
        updated={'history_char_limit':9000,'memory_enabled':False,'trigger_names':['NewName'],'command_prefix':'!'}
        r=await self.client.put('/api/config',json=updated,headers=self.headers)
        self.assertEqual(r.status,200)
        restored=Config(self.config.path)
        for k,v in updated.items(): self.assertEqual(restored.data[k],v)
        r=await self.client.put('/api/config',json={'memory_limit':-1,'name':'should-not-save'},headers=self.headers)
        self.assertEqual(r.status,400); self.assertNotEqual(self.config.data['name'],'should-not-save')

    async def test_registry_routes_credential_redaction_and_default_selection(self):
        profile=dict(id='local-api',name='Local',type='openai_compatible',base_url='http://127.0.0.1:9999/v1',model='example',api_key='NEVER-RETURN-THIS-KEY')
        self.assertEqual((await self.client.post('/api/providers',json=profile)).status,403)
        r=await self.client.post('/api/providers',json=profile,headers=self.headers); self.assertEqual(r.status,200)
        r=await self.client.post('/api/providers/local-api/default',json={},headers=self.headers); self.assertEqual(r.status,200)
        r=await self.client.put('/api/sessions/p:42/provider',json={'provider_id':'local-api'},headers=self.headers)
        self.assertEqual(r.status,200); self.assertEqual(self.config.session_providers['p:42'],'local-api')
        for route in ('/api/providers','/api/config/export','/api/system/diagnostics'):
            r=await self.client.get(route); text=await r.text(); self.assertEqual(r.status,200)
            for key in ('NEVER-RETURN-THIS-KEY','private-onebot-credential','private-provider-credential','private-admin-credential-123'):
                self.assertNotIn(key,text)
        r=await self.client.get('/api/providers'); self.assertEqual((await r.json())['data']['default_id'],'local-api')
        self.assertTrue((self.root/'providers.json').exists())

    async def test_import_validates_all_fields_before_mutating(self):
        r=await self.client.post('/api/config/import',json={'config':{'name':'changed'},'plugin_settings':{'dice':{'max_count':1000}}},headers=self.headers)
        self.assertEqual(r.status,400); self.assertNotEqual(self.config.data['name'],'changed')
        r=await self.client.post('/api/config/import',json={'config':{'name':'imported'},'plugin_settings':{'dice':{'max_count':3}},'connections':{'receive_enabled':False}},headers=self.headers)
        self.assertEqual(r.status,200); self.assertEqual(self.config.data['name'],'imported')
        self.assertTrue(self.config.receive_enabled); self.assertEqual(self.plugins.settings('dice')['max_count'],3)

    async def test_plugin_settings_save_validate_and_influence_real_commands(self):
        r=await self.client.put('/api/plugins/dice/config',json={'values':{'max_count':2}},headers=self.headers)
        self.assertEqual(r.status,200)
        event=fixtures.KernelTests.message
        msg={'event_id':'e1','session_id':'p:42','message_id':'1','user_id':'42','group_id':'','message_type':'private','text':'/骰子 3d6','display_name':'test'}
        await self.store.ingest_event(msg)
        item,command=self.plugins.match(msg['text']); await self.kernel.command(msg,item,command)
        self.assertIn('超出范围',self.kernel.transport.sent[-1][2][0]['data']['text'])
        r=await self.client.put('/api/plugins/dice/config',json={'values':{'unknown':2}},headers=self.headers); self.assertEqual(r.status,400)

    async def test_connection_validation_and_private_persistence(self):
        for url in ('http://host','ws://user:secret@host','ws://host?token=hidden'):
            with self.assertRaises(ValueError): self.config.update_connections({'onebot_url':url})
        self.config.update_connections({'owner_ids':['42','43'],'self_id':'900','onebot_token':'NEW-PRIVATE-TOKEN','receive_enabled':False})
        restored=Config(self.config.path)
        self.assertEqual(restored.owner_ids,['42','43']); self.assertEqual(restored.onebot_token,'NEW-PRIVATE-TOKEN')
        self.assertNotIn('NEW-PRIVATE-TOKEN',json.dumps(restored.connections()))
        with self.assertRaises(ValueError): self.config.update_connections({'reconnect_min':20,'reconnect_max':2})

    async def test_failed_save_leaves_runtime_values_unchanged(self):
        before=copy.deepcopy(self.config.data)
        with patch.object(self.config,'save',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError): self.config.update({'name':'not-saved'})
            with self.assertRaises(OSError): self.plugins.update_settings('dice',{'max_count':1})
            with self.assertRaises(OSError): await self.plugins.toggle('dice',False)
        self.assertEqual(self.config.data,before)
        self.assertEqual(self.plugins.settings('dice')['max_count'],20)
        self.assertTrue(self.plugins.enabled('dice'))

    async def test_documented_persona_limit_accepts_multibyte_chinese(self):
        persona='中文设定' * 6000
        response=await self.client.put('/api/config',json={'persona':persona},headers=self.headers)
        self.assertEqual(response.status,200)
        self.assertEqual((await response.json())['data']['persona'],persona)


class RuntimeSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f=fixtures.KernelTests(); await self.f.asyncSetUp()
    async def asyncTearDown(self): await self.f.asyncTearDown()

    async def test_access_trigger_overrides_and_blacklist_priority(self):
        f=self.f; cfg=f.config.data
        cfg['private_allowlist']=['42']
        self.assertFalse(await f.kernel.triggered(f.message(user='43')))
        cfg['group_reply_all']=True; cfg['group_blocklist']=['77']
        self.assertFalse(await f.kernel.triggered(f.message(group='77',mentioned=True)))
        cfg['group_blocklist']=[]; cfg['user_blocklist']=['42']
        self.assertFalse(await f.kernel.triggered(f.message(group='77',mentioned=True)))
        cfg['user_blocklist']=[]; cfg['group_reply_all']=False; cfg['mention_enabled']=False
        self.assertFalse(await f.kernel.triggered(f.message(group='77',mentioned=True)))
        cfg['group_reply_all']=True
        self.assertTrue(await f.kernel.triggered(f.message(group='77')))

    async def test_memory_resource_disable_and_session_specific_provider_reach_generation(self):
        f=self.f; cfg=f.config.data
        cfg['memory_enabled']=False; cfg['resource_lookup_enabled']=False
        await f.store.remember('p:42','SECRET-MEMORY')
        f.config.session_providers={'p:42':'alternate'}
        calls=[]
        async def complete(prompt,timeout,provider_id=None): calls.append((prompt,provider_id)); return 'ok'
        f.provider.complete=complete
        await f.kernel.on_event(f.message('你好'))
        await fixtures.until(lambda:bool(f.transport.sent))
        data=fixtures.prompt_data(calls[0][0]); self.assertEqual(data['memories'],[]);self.assertEqual(data['reference'],{})
        self.assertEqual(calls[0][1],'alternate')

    async def test_custom_command_prefix_and_cooldown_take_effect(self):
        f=self.f; f.config.data['command_prefix']='!'; f.config.data['session_cooldown_ms']=60000
        self.assertIsNone(f.plugins.match('/ping')[0]); self.assertEqual(f.plugins.match('!ping')[1],'/ping')
        await f.kernel.on_event(f.message('!ping')); self.assertEqual(len(f.transport.sent),1)
        await f.kernel.on_event(f.message('first'))
        msg=f.message('second'); await f.kernel.on_event(msg)
        self.assertEqual(await f.store.event_state(msg['event_id']),'cooldown')


if __name__=='__main__': unittest.main()
