import unittest
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from yukino_core.image_inputs import command_images,image_command_event
from yukino_core.core import Kernel
from yukino_core.plugins import PluginManager
from yukino_core.transport import normalize_event

class Store:
    def __init__(self,messages=None):self.messages=messages or {};self.calls=[]
    async def message_event(self,sid,message):self.calls.append((sid,message));return self.messages.get((sid,message))

def image(url):return dict(type='image',data={'url':url})

def manager(prefix='/'):
    plugins=PluginManager.__new__(PluginManager)
    plugins.config=SimpleNamespace(data={'command_prefix':prefix},plugin_flags={'d_images':True},plugin_settings={})
    plugins.items=[{'id':'d_images','name':'图片工坊','commands':['/摸头','/转圈','/动图加速'],'worker':'worker.py'}]
    plugins.execute=AsyncMock(return_value='done')
    return plugins

class ImageCommandTextTests(unittest.TestCase):
    def test_only_real_image_workshop_commands_are_normalized(self):
        plugins=manager()
        for text,segments in [('/摸头[图片]',[{'type':'text','data':{'text':'/摸头[图片]'}}]),
                              ('/ping[图片]',[{'type':'text','data':{'text':'/ping'}},image('https://example.com/x.png')]),
                              ('[图片]这是什么',[image('https://example.com/x.png'),{'type':'text','data':{'text':'这是什么'}}])]:
            event={'text':text,'segments':segments}
            self.assertIs(image_command_event(event,plugins),event)

class ImageInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_message_images_invoke_worker_before_or_after_text_without_at(self):
        for prefix,command,first in [('/','摸头',False),('/','转圈',True),('!','摸头',False),('!','动图加速 2',True)]:
            with self.subTest(prefix=prefix,command=command,image_first=first):
                url='https://example.com/source.gif';photo=image(url);words={'type':'text','data':{'text':prefix+command}}
                event=normalize_event({'post_type':'message','message_type':'group','group_id':77,'user_id':11,'self_id':22,'message_id':3,
                                       'message':[photo,words] if first else [words,photo],'sender':{'nickname':'test'}},'22')
                original=event['text'];plugins=manager(prefix);kernel=Kernel.__new__(Kernel)
                kernel.plugins=plugins;kernel.config=SimpleNamespace(receive_enabled=True,owner_id='11',owner_ids=['11'],data={
                    'command_prefix':prefix,'user_blocklist':[],'ignore_bots':True,'group_enabled':True,'group_blocklist':[],
                    'group_allowlist':[],'group_reply_all':False,'mention_enabled':True,'reply_trigger_enabled':False,
                    'trigger_prefix_enabled':False,'trigger_names':[]})
                kernel.closing=False;kernel.metrics=defaultdict(int);kernel.store=SimpleNamespace(ingest_event=AsyncMock(return_value=True))
                kernel.collector=SimpleNamespace(observe=Mock(return_value=False));kernel.proactive=SimpleNamespace(observe=Mock())
                kernel.games=SimpleNamespace(observe=AsyncMock(return_value=False),accept=AsyncMock(return_value=False))
                kernel.native=SimpleNamespace(keyword_match=Mock(return_value=None));kernel.reply=AsyncMock()
                await kernel.on_event(event)
                plugins.execute.assert_awaited_once();_,sent,context=plugins.execute.await_args.args
                self.assertEqual(sent,'/'+command);self.assertEqual(context['image_urls'],[url]);self.assertEqual(context['session_id'],'g:77')
                self.assertEqual(event['text'],original);kernel.store.ingest_event.assert_awaited_once_with(event)
                kernel.reply.assert_awaited_once();self.assertEqual(kernel.metrics['local_commands'],1)

    async def test_inline_images_are_bounded_and_qq_cdn_upgrades_to_https(self):
        store=Store();event=dict(session_id='g:7',segments=[image('http://multimedia.nt.qq.com.cn/download?fileid=example')]*5,reply_to='88')
        urls=await command_images(store,event);self.assertEqual(urls,['https://multimedia.nt.qq.com.cn/download?fileid=example']);self.assertEqual(store.calls,[])
    async def test_quoted_images_are_only_looked_up_in_this_session(self):
        store=Store({('p:22','88'):{'segments':[image('https://example.com/private.png')]}})
        event=dict(session_id='p:11',segments=[],reply_to='88');self.assertEqual(await command_images(store,event),[]);self.assertEqual(store.calls,[('p:11','88')])
        event['session_id']='p:22';self.assertEqual(await command_images(store,event),['https://example.com/private.png'])
    async def test_local_private_ip_avatar_and_malformed_inputs_are_not_forwarded(self):
        event=dict(session_id='g:7',segments=[image(u) for u in ('file:///secret.png','https://127.0.0.1/private','https://q1.qlogo.cn/g?nk=11')],reply_to='')
        self.assertEqual(await command_images(Store(),event),[])

if __name__=='__main__':unittest.main()
