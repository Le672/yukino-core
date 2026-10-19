import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from aiohttp.test_utils import TestClient,TestServer
from aiohttp import WSServerHandshakeError

from qq_desktop.config import validate
from qq_desktop.bridge import Bridge,Store,plain_message
from qq_desktop.atspi import UiError,UiUncertain
from qq_desktop.server import create_app

TOKEN='desktop-fixture-token-at-least-32-characters'

def config():
    b={'kind':'private','id':'10002','verified':True,'senders':{'Me':'10001','Peer':'10002'},'row_key':'description'}
    for key in ('contact','header','history','editor','send','body','sender'):b[key]={'name':key}
    return validate({'enabled':True,'self_id':'10001','identity':{'name':'10001'},'bindings':[b]})

class Driver:
    def __init__(self):self.online=True;self.rows=[];self.sent=[];self.failure=None
    def ready(self):return self.online
    def snapshot(self,binding):return copy.deepcopy(self.rows)
    def send(self,binding,text):
        self.sent.append((binding['id'],text))
        if self.failure:raise self.failure
        return 'outgoing-'+str(len(self.sent))


class ConfigTests(unittest.TestCase):
    def test_disabled_empty_config_and_strict_enabled_identity(self):
        self.assertFalse(validate({})['enabled'])
        self.assertTrue(config()['enabled'])
        for patch in ({'self_id':'nickname'},{'identity':None},{'bindings':[]},{'host':'0.0.0.0'},{'enabled':'yes'}):
            with self.subTest(patch=patch),self.assertRaises((ValueError,TypeError)):validate({**config(),**patch})
    def test_duplicate_unverified_and_invalid_paths(self):
        c=config();b=c['bindings'][0]
        for bindings in ([b,b],[{**b,'verified':False}],[{**b,'id':'not-number'}],[{**b,'contact':{'path':[-1]}}]):
            with self.subTest(bindings=bindings),self.assertRaises(ValueError):validate({**c,'bindings':bindings})
    def test_segments_never_silently_drop_media_or_parse_cq(self):
        self.assertEqual(plain_message([{'type':'text','data':{'text':'中文\ntext'}}]),'中文\ntext')
        for value in ('','x'*2001,'[CQ:image,file=x]','x\0y',[{'type':'image','data':{'file':'x'}}],None):
            with self.subTest(value=str(value)[:20]),self.assertRaises(ValueError):plain_message(value)


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.driver=Driver();self.bridge=Bridge(config(),self.driver,Path(self.tmp.name)/'state')
    async def asyncTearDown(self):await self.bridge.close();self.tmp.cleanup()
    async def test_disabled_and_offline_never_send_or_read_messages(self):
        self.bridge.config['enabled']=False
        self.assertFalse((await self.bridge.status())['online']);self.assertEqual(await self.bridge.poll(),[])
        with self.assertRaises(UiError):await self.bridge.send('private','10002','hello')
        self.assertEqual(self.driver.sent,[])
        self.bridge.config['enabled']=True;self.driver.online=False
        self.assertFalse((await self.bridge.status())['good'])
    async def test_unbound_conversation_rejected_and_unsupported_actions_explicit(self):
        with self.assertRaises(ValueError):await self.bridge.send('private','10003','hello')
        with self.assertRaises(NotImplementedError):await self.bridge.action('set_group_ban',{})
        self.assertFalse((await self.bridge.action('can_send_image',{}))['yes'])
        self.assertEqual(self.driver.sent,[])
    async def test_once_only_send_idempotency_and_different_content_rejected(self):
        a=await self.bridge.send('private','10002','hello','call-1')
        b=await self.bridge.send('private','10002','hello','call-1')
        self.assertEqual(a['message_id'],b['message_id']);self.assertEqual(len(self.driver.sent),1)
        with self.assertRaises(ValueError):await self.bridge.send('private','10002','different','call-1')
    async def test_uncertain_send_is_not_automatically_repeated_after_restart(self):
        self.driver.failure=UiUncertain('uncertain')
        with self.assertRaises(UiUncertain):await self.bridge.send('private','10002','hello','uncertain-1')
        await self.bridge.close();self.bridge=Bridge(config(),self.driver,Path(self.tmp.name)/'state')
        with self.assertRaises(UiUncertain):await self.bridge.send('private','10002','hello','uncertain-1')
        self.assertEqual(len(self.driver.sent),1)
    async def test_initial_visible_history_is_not_replayed_and_new_rows_are_deduped(self):
        self.driver.rows=[{'key':'old','user_id':'10002','text':'old'}]
        self.assertEqual(await self.bridge.poll(),[])
        self.driver.rows+=[{'key':'new','user_id':'10002','text':'你好'},{'key':'self','user_id':'10001','text':'me'}]
        e=await self.bridge.poll();self.assertEqual(len(e),1);self.assertEqual(e[0]['raw_message'],'你好')
        self.assertEqual(e[0]['user_id'],10002);self.assertTrue(e[0]['ui_observed']);self.assertEqual(await self.bridge.poll(),[])
    async def test_restart_seeds_visible_outage_history_without_spam(self):
        await self.bridge.poll();await self.bridge.close();self.bridge=Bridge(config(),self.driver,Path(self.tmp.name)/'state')
        self.driver.rows=[{'key':'outage','user_id':'10002','text':'not automatically replayed'}]
        self.assertEqual(await self.bridge.poll(),[])
    async def test_group_identity_is_explicit_and_lists_are_only_bindings(self):
        b={**self.bridge.config['bindings'][0],'kind':'group','id':'10003'}
        self.bridge.config['bindings']=[b];self.driver.rows=[];await self.bridge.poll()
        self.driver.rows=[{'key':'g1','user_id':'10002','text':'group message'}]
        self.assertEqual((await self.bridge.poll())[0]['group_id'],10003)
        self.assertEqual(await self.bridge.action('get_friend_list',{}),[])
    async def test_send_cooldown_does_not_send_twice(self):
        await self.bridge.send('private','10002','one')
        with self.assertRaises(ValueError):await self.bridge.send('private','10002','two')
        self.assertEqual(len(self.driver.sent),1)


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.driver=Driver();self.bridge=Bridge(config(),self.driver,Path(self.tmp.name)/'state')
        self.client=TestClient(TestServer(create_app(self.bridge,TOKEN)));await self.client.start_server()
    async def asyncTearDown(self):await self.client.close();self.tmp.cleanup()
    async def test_auth_browser_origin_and_echo(self):
        for headers,status in (({},401),({'Authorization':'Bearer wrong'},401),({'Authorization':'Bearer '+TOKEN,'Origin':'https://example.com'},403)):
            with self.subTest(headers=list(headers)),self.assertRaises(WSServerHandshakeError) as error:await self.client.ws_connect('/',headers=headers)
            self.assertEqual(error.exception.status,status)
        ws=await self.client.ws_connect('/api',headers={'Authorization':'Bearer '+TOKEN});self.assertEqual((await ws.receive_json())['meta_event_type'],'lifecycle')
        await ws.send_json({'action':'get_status','echo':{'request':3}});result=await ws.receive_json()
        self.assertEqual(result['retcode'],0);self.assertEqual(result['echo'],{'request':3});self.assertTrue(result['data']['online']);await ws.close()
    async def test_unsupported_media_and_uncertain_status(self):
        ws=await self.client.ws_connect('/api',headers={'Authorization':'Bearer '+TOKEN});await ws.receive_json()
        for action,params,code in [('delete_msg',{},1404),('send_private_msg',{'user_id':10002,'message':[{'type':'image','data':{'file':'x'}}]},1400)]:
            await ws.send_json({'action':action,'params':params});self.assertEqual((await ws.receive_json())['retcode'],code)
        self.driver.failure=UiUncertain('send uncertain')
        await ws.send_json({'action':'send_private_msg','params':{'user_id':10002,'message':'one'}});result=await ws.receive_json()
        self.assertEqual(result['retcode'],1500);self.assertTrue(result['ui_uncertain']);self.assertEqual(len(self.driver.sent),1);await ws.close()
    async def test_event_only_endpoint_rejects_actions(self):
        ws=await self.client.ws_connect('/event',headers={'Authorization':'Bearer '+TOKEN});await ws.receive_json()
        await ws.send_json({'action':'get_status'});self.assertEqual((await ws.receive_json())['retcode'],1400);await ws.close()
    async def test_health_is_redacted_and_available_while_disabled(self):
        self.bridge.config['enabled']=False;r=await self.client.get('/healthz');data=await r.json()
        self.assertFalse(data['enabled']);self.assertNotIn('self_id',data);self.assertNotIn(TOKEN,json.dumps(data))


if __name__=='__main__':unittest.main()
