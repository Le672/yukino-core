import asyncio
import base64
import copy
from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PIL import Image
from aiohttp.test_utils import TestClient,TestServer
from yukino_core.app import create_app
from yukino_core.config import DEFAULTS
from yukino_core.core import Kernel
from yukino_core.plugin_results import validate_result,music_intent
from yukino_core.plugins import PluginManager
from yukino_core.resources import ResourceLibrary
from yukino_core.rich_output import AssetStore,audio_format,safe_name
from yukino_core.store import Store
from yukino_core.transport import normalize_event,OneBotError
from yukino_core import public_net


class SchemaTests(unittest.TestCase):
    def test_backwards_text_and_structured_media(self):
        self.assertEqual(validate_result('abcd',2),'ab')
        self.assertIsNone(validate_result(None))
        self.assertEqual(validate_result({'version':1,'media':[{'type':'file','name':'a.json','text':'{}'}]})['media'][0]['name'],'a.json')
    def test_reject_arbitrary_paths_actions_and_oversized_payload(self):
        bad=[{'version':1,'media':[{'type':'image','url':'file:///etc/passwd'}]},
             {'version':1,'media':[{'type':'image','url':'https://example.com','path':'/tmp/x'}]},
             {'version':1,'action':'set_group_ban','text':'x'},
             {'version':1,'media':[{'type':'audio','url':'https://example.com','duration':float('nan')}]},
             {'version':1,'media':[{'type':'file','name':'a.txt','text':'x'*40001}]}]
        for value in bad:
            with self.subTest(value=str(value)[:90]),self.assertRaises(ValueError):validate_result(value)
    def test_explicit_music_intent_does_not_capture_discussion(self):
        self.assertEqual(music_intent('我想听 起风了'),'/点歌 起风了')
        self.assertIsNone(music_intent('怎么播放音乐'))
        self.assertIsNone(music_intent('不要播放音乐'))
    def test_media_network_rejects_local_paths_and_private_hosts(self):
        for url in ('http://example.com','https://127.0.0.1/','https://192.168.1.1/','https://localhost/','https://u:p@example.com/'):
            with self.subTest(url=url),self.assertRaises(ValueError):public_net.canonical_url(url)
    def test_media_names_and_audio_body_validation(self):
        self.assertNotIn('/',safe_name('../../secret.mp3'))
        self.assertEqual(safe_name('CON'),'result.txt')
        with self.assertRaises(ValueError):audio_format(b'<html>not audio</html>')


class Peer:
    def __init__(self):self.sent=[];self.actions=[];self.uncertain=False
    async def send_message(self,kind,target,segments,timeout=None):
        self.sent.append((kind,target,segments));return {'message_id':str(len(self.sent))}
    async def call(self,action,params,timeout=None):
        self.actions.append((action,params))
        if self.uncertain:raise OneBotError('timeout',uncertain=True)
        return {'file_id':'file-1'}
    def snapshot(self):return {'connected':True}


class RichTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        cfg=SimpleNamespace(data=copy.deepcopy(DEFAULTS),plugin_flags={},plugin_settings={},self_id='900',owner_id='42',owner_ids=['42'],save=lambda:None,admin_token='test-only-token',session_providers={})
        self.store=Store(root/'test.db');await self.store.open()
        self.resources=ResourceLibrary(root/'resources');await self.resources.open()
        self.plugins=PluginManager(cfg,root/'plugins')
        provider=SimpleNamespace(lock=asyncio.Lock(),snapshot=lambda:{'healthy':True})
        self.k=Kernel(cfg,self.store,self.resources,provider,self.plugins);self.peer=Peer();self.k.transport=self.peer
        self.n=0
    async def asyncTearDown(self):
        await self.k.close();await self.store.close();self.tmp.cleanup()
    async def event(self):
        self.n+=1
        e=normalize_event({'post_type':'message','self_id':900,'message_id':self.n,'user_id':42,'message_type':'private','message':[{'type':'text','data':{'text':'/样例'}}],'time':100})
        await self.store.ingest_event(e);return e
    def item(self,pid='demo'):
        item={'id':pid,'name':'实际结果','description':'test','commands':['/样例'],'worker':'worker.py'}
        if not any(x['id']==pid for x in self.plugins.items):self.plugins.items.append(item)
        return next(x for x in self.plugins.items if x['id']==pid)
    async def test_real_document_uses_file_upload_not_a_text_link(self):
        e=await self.event();item=self.item()
        result={'version':1,'text':'已生成JSON','media':[{'type':'file','name':'result.json','text':'{"answer":42}'}]}
        self.assertTrue(await self.k.rich.send(e,item,result))
        self.assertEqual(self.peer.actions[0][0],'upload_private_file')
        self.assertEqual(base64.b64decode(self.peer.actions[0][1]['file'][9:]),b'{"answer":42}')
        rows=await self.store.history(e['session_id']);self.assertEqual(rows[-1]['attachments'][0]['type'],'file')
    async def test_actual_worker_protocol_reaches_media_dispatcher(self):
        item=self.item();self.plugins.root.mkdir(parents=True,exist_ok=True)
        (self.plugins.root/'worker.py').write_text("def handle(text,context):\n assert context['rich_result_version']==1\n return {'version':1,'text':'文件结果','media':[{'type':'file','name':'output.txt','text':'actual worker output'}]}\n",encoding='utf-8')
        event=normalize_event({'post_type':'message','message_type':'private','self_id':900,'user_id':42,'message_id':999,'time':100,'message':[{'type':'text','data':{'text':'/样例'}}]})
        await self.k.on_event(event)
        self.assertEqual(self.peer.actions[0][0],'upload_private_file');self.assertFalse(self.plugins.errors)
    async def test_image_is_downloaded_validated_and_sent_as_image(self):
        stream=BytesIO();Image.new('RGB',(16,16),'green').save(stream,'PNG')
        e=await self.event();item=self.item()
        with patch.object(public_net,'request',return_value=SimpleNamespace(body=stream.getvalue())):
            self.assertTrue(await self.k.rich.send(e,item,{'version':1,'text':'图','media':[{'type':'image','url':'https://example.com/image.png'}]}))
        self.assertEqual(self.peer.sent[0][2][-1]['type'],'image')
    async def test_short_audio_has_playable_record_and_full_audio_file(self):
        e=await self.event();item=self.item();payload=b'ID3'+b'\0'*400
        with patch.object(public_net,'request',return_value=SimpleNamespace(body=payload)):
            self.assertTrue(await self.k.rich.send(e,item,{'version':1,'text':'歌曲','media':[{'type':'audio','url':'https://example.com/a.mp3','name':'song.mp3','duration':30}]}))
        self.assertEqual(self.peer.sent[1][2][0]['type'],'record')
        self.assertEqual(self.peer.actions[0][0],'upload_private_file')
    async def test_broken_image_reports_failure_without_fake_attachment(self):
        e=await self.event()
        with patch.object(public_net,'request',return_value=SimpleNamespace(body=b'<html>blocked</html>')):
            await self.k.rich.send(e,self.item(),{'version':1,'text':'结果','media':[{'type':'image','url':'https://example.com/x'}]})
        self.assertEqual([x['type'] for x in self.peer.sent[0][2]],['text'])
        self.assertIn('未能获取',self.peer.sent[0][2][0]['data']['text'])
    async def test_uncertain_file_send_stops_without_automatic_retry(self):
        e=await self.event();item=self.item();self.peer.uncertain=True
        value={'version':1,'media':[{'type':'file','name':'one.txt','text':'1'},{'type':'file','name':'two.txt','text':'2'}]}
        self.assertFalse(await self.k.rich.send(e,item,value));self.assertEqual(len(self.peer.actions),1)
        self.assertFalse(await self.k.rich.send(e,item,value));self.assertEqual(len(self.peer.actions),1)
    async def test_hot_trends_preserve_titles_only_even_in_card_mode(self):
        item=self.item('hot_trends');self.plugins.update_settings('hot_trends',{'output_mode':'card'})
        steps=await self.k.rich.prepare(item,'1. 实际标题\n2. 另一个标题')
        self.assertEqual([s['type'] for s in steps[0]['segments']],['text'])
        self.assertEqual(steps[0]['text'],'1. 实际标题\n2. 另一个标题')
    async def test_board_has_real_image_and_text_mode_can_disable_layout(self):
        item=self.item('x_2048');text='2048\n2 4 8 16\n0 0 0 0'
        steps=await self.k.rich.prepare(item,text);self.assertIn('image',[s['type'] for s in steps[0]['segments']])
        self.plugins.update_settings(item['id'],{'output_mode':'text'})
        steps=await self.k.rich.prepare(item,text);self.assertEqual([s['type'] for s in steps[0]['segments']],['text'])
    async def test_asset_preview_requires_login_and_supports_audio_range(self):
        asset=self.k.rich.assets.save(b'ID3'+b'\0'*100,'audio','a.mp3','audio/mpeg')
        client=TestClient(TestServer(create_app(self.k)));await client.start_server()
        try:
            path='/api/plugin-media/'+asset['resource_id']
            self.assertEqual((await client.get(path)).status,401)
            response=await client.post('/api/login',json={'token':'test-only-token'});self.assertEqual(response.status,200)
            response=await client.get(path,headers={'Range':'bytes=0-2'});self.assertEqual(response.status,206)
            self.assertEqual(await response.read(),b'ID3');self.assertEqual(response.headers['Content-Type'],'audio/mpeg')
        finally:await client.close()


if __name__=='__main__':unittest.main()
