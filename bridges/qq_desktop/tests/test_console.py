import copy
import json
from pathlib import Path
import tempfile
import unittest
from aiohttp.test_utils import TestClient,TestServer
from qq_desktop.atspi import UiError
from qq_desktop.bridge import Bridge
from qq_desktop.server import create_app
from test_bridge import config,Driver,TOKEN
from test_media import PNG


class ConsoleDriver(Driver):
    def __init__(self):super().__init__();self.inspections=0;self.probes=0
    def inspect(self):self.inspections+=1;return [{'name':'private-visible-value','path':[0],'text':'<script>not markup</script>','role':'label'}]
    def probe(self,cfg,binding=None):
        self.probes+=1
        if not self.online or cfg.get('self_id')!='10001':raise UiError('QQ identity mismatch')
        return {'identity_verified':True,'visible_rows':0,'numeric_senders_verified':binding is not None}


class ConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.driver=ConsoleDriver();self.path=Path(self.tmp.name)/'config.json';self.path.write_text(json.dumps(config()))
        self.bridge=Bridge(config(),self.driver,Path(self.tmp.name)/'state')
        self.client=TestClient(TestServer(create_app(self.bridge,TOKEN,self.path)));await self.client.start_server()
        self.headers={'Authorization':'Bearer '+TOKEN,'X-Desktop-Console':'1'}
    async def asyncTearDown(self):await self.client.close();self.tmp.cleanup()
    async def current(self):
        r=await self.client.get('/console/api/config',headers=self.headers);d=await r.json();self.headers['X-Config-Revision']=d['revision'];return d
    async def test_private_inspection_requires_auth_and_never_navigates(self):
        r=await self.client.get('/console/api/inspect');self.assertEqual(r.status,401);self.assertEqual(self.driver.inspections,0)
        r=await self.client.get('/console/api/inspect',headers=self.headers);d=await r.json();self.assertTrue(d['no_qq_message_sent']);self.assertEqual(self.driver.sent,[])
        self.assertEqual(d['nodes'][0]['name'],'private-visible-value')
    async def test_cross_origin_and_missing_console_header_are_rejected(self):
        for headers in ({'Authorization':'Bearer '+TOKEN},{**self.headers,'Origin':'https://evil.example'}):
            r=await self.client.put('/console/api/config',headers=headers,json=config());self.assertEqual(r.status,403)
    async def test_save_pauses_and_resets_verification_preserves_binding_address(self):
        d=await self.current();cfg=d['config'];cfg['host']='0.0.0.0';cfg['port']=18088;cfg['poll_seconds']=3
        r=await self.client.put('/console/api/config',headers=self.headers,json=cfg);self.assertEqual(r.status,200);saved=await r.json()
        self.assertFalse(saved['config']['enabled']);self.assertFalse(saved['config']['bindings'][0]['verified'])
        self.assertEqual(saved['config']['host'],'127.0.0.1');self.assertEqual(saved['config']['port'],3002)
        self.assertTrue(self.path.with_name('config.json.previous').exists());self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['poll_seconds'],3)
    async def test_stale_revision_cannot_overwrite_new_settings(self):
        d=await self.current();r=await self.client.put('/console/api/config',headers=self.headers,json=d['config']);self.assertEqual(r.status,200)
        r=await self.client.post('/console/api/state',headers=self.headers,json={'enabled':False});self.assertEqual(r.status,409)
    async def test_verify_and_enable_require_live_genuine_identity_probe(self):
        d=await self.current();await self.client.put('/console/api/config',headers=self.headers,json=d['config']);await self.current()
        r=await self.client.post('/console/api/state',headers=self.headers,json={'enabled':True});self.assertEqual(r.status,400)
        self.driver.online=False;r=await self.client.post('/console/api/verify',headers=self.headers,json={'index':0});self.assertEqual(r.status,409)
        self.driver.online=True;r=await self.client.post('/console/api/verify',headers=self.headers,json={'index':0});self.assertEqual(r.status,200)
        await self.current();r=await self.client.post('/console/api/state',headers=self.headers,json={'enabled':True});self.assertEqual(r.status,200)
        self.assertTrue(self.bridge.config['enabled']);self.assertEqual(self.driver.sent,[])
    async def test_public_page_contains_no_token_or_private_tree_and_has_csp(self):
        r=await self.client.get('/console');text=await r.text();self.assertEqual(r.status,200)
        self.assertNotIn(TOKEN,text);self.assertNotIn('private-visible-value',text);self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
        r=await self.client.get('/console/static/console.js');self.assertNotIn('innerHTML',await r.text())
    async def test_upload_download_auth_integrity_and_upload_does_not_send(self):
        r=await self.client.post('/console/api/upload?kind=image&name=x.png',headers={**self.headers,'Content-Type':'application/octet-stream'},data=PNG)
        self.assertEqual(r.status,200);d=await r.json();self.assertNotIn('path',d);self.assertEqual(self.driver.sent,[])
        r=await self.client.get('/console/api/media/'+d['id']);self.assertEqual(r.status,401)
        r=await self.client.get('/console/api/media/'+d['id'],headers=self.headers);self.assertEqual(await r.read(),PNG)
        self.assertIn('attachment',r.headers['Content-Disposition'])
    async def test_event_subscriber_receives_onebot_heartbeat_with_offline_status(self):
        self.bridge.config['enabled']=False;ws=await self.client.ws_connect('/event',headers=self.headers)
        self.assertEqual((await ws.receive_json())['meta_event_type'],'lifecycle')
        d=await ws.receive_json(timeout=4);self.assertEqual(d['meta_event_type'],'heartbeat');self.assertFalse(d['status']['online']);self.assertEqual(d['interval'],15000);await ws.close()


if __name__=='__main__':unittest.main()
