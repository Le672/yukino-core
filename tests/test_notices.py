"""Real SQLite/outbox and fake OneBot acceptance of notification routing."""
import asyncio,copy,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import test_core as core_fixtures
import test_transport as transport_fixtures
from test_transport import until
from yukino_core.plugins import BUILTINS,PluginManager
from yukino_core.transport import normalize_event,OneBotError
from yukino_core.store import Store

def notice(user=42,group=None,kind='notify',sub='poke',**kwargs):
    value=dict(post_type='notice',notice_type=kind,sub_type=sub,user_id=user,self_id=900,time=100,target_id=900)
    if group:value['group_id']=group
    return {**value,**kwargs}

class NoticeNormalization(unittest.TestCase):
    def test_private_napcat_sender_field_and_group_scope(self):
        p=normalize_event(notice(user=900,sender_id=42));g=normalize_event(notice(group=77))
        self.assertEqual((p['user_id'],p['session_id']),('42','p:42'))
        self.assertEqual((g['user_id'],g['session_id']),('42','g:77'))
        self.assertEqual(p['post_type'],'notice')
        self.assertEqual(normalize_event(dict(reversed(list(notice().items()))))['event_id'],normalize_event(notice())['event_id'])
    def test_wrong_target_self_echo_malformed_and_unrelated_rejected(self):
        cases=[notice(user=900),notice(target_id=43),notice(self_id=901),notice(time=0),notice(time=True),notice(time='bad'),notice(user_id='../42'),notice(group_id=-1),notice(kind='group_upload'),notice(sub='honor')]
        for raw in cases:
            with self.subTest(raw=raw):self.assertIsNone(normalize_event(raw,'900'))
    def test_six_supported_event_types_and_bounded_text(self):
        for kind,sub in [('notify','poke'),('notify','title'),('group_increase','approve'),('group_decrease','leave'),('group_admin','set'),('group_card','')]:
            with self.subTest(kind=kind,sub=sub):
                e=normalize_event(notice(group=77,kind=kind,sub=sub,title='x'*500,card_old='old',card_new='new'))
                self.assertIsNotNone(e);self.assertEqual(e['session_id'],'g:77');self.assertLessEqual(len(e['title']),200)

class NoticeKernelTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=core_fixtures.KernelTests.asyncSetUp
    asyncTearDown=core_fixtures.KernelTests.asyncTearDown
    add_test_image=core_fixtures.KernelTests.add_test_image
    message=core_fixtures.KernelTests.message
    def event(self,**kw):return normalize_event(notice(**kw),'900')
    async def test_private_and_group_poke_immediate_without_model_or_history(self):
        for group in (None,77):
            e=self.event(group=group);await self.kernel.on_event(e)
            self.assertEqual(await self.store.event_state(e['event_id']),'notice_replied')
            self.assertEqual(await self.store.history(e['session_id']),[])
        self.assertEqual(len(self.transport.sent),2);self.assertEqual(self.provider.prompts,[])
        self.assertEqual(self.kernel.metrics['notice_replied'],2)
    async def test_dedup_cooldown_survive_store_reopen_and_other_user(self):
        e=self.event(group=77);await self.kernel.on_event(e);await self.kernel.on_event(e)
        self.assertEqual(self.kernel.metrics['duplicates'],1)
        e2=self.event(group=77,time=101);await self.kernel.on_event(e2)
        self.assertEqual(await self.store.event_state(e2['event_id']),'notice_cooldown')
        path=self.store.path;await self.store.close();self.store=Store(path);await self.store.open();self.kernel.store=self.store
        e3=self.event(group=77,time=102,user=43);await self.kernel.on_event(e3)
        self.assertEqual(await self.store.event_state(e3['event_id']),'notice_cooldown')
        self.assertEqual(len(self.transport.sent),1)
    async def test_access_lists_and_platform_and_plugin_switches(self):
        settings=[('user_blocklist',['42']),('private_allowlist',['43']),('private_enabled',False)]
        for n,v in settings:
            old=self.config.data[n];self.config.data[n]=v;e=self.event(time=200+len(self.transport.sent)+len(n));await self.kernel.on_event(e);self.config.data[n]=old
            self.assertEqual(await self.store.event_state(e['event_id']),'notice_ignored')
        self.config.receive_enabled=False;e=self.event(time=302);await self.kernel.on_event(e)
        self.assertEqual(await self.store.event_state(e['event_id']),'receive_disabled')
        self.config.receive_enabled=True;self.config.plugin_flags['poke']=False;e=self.event(time=303);await self.kernel.on_event(e)
        self.assertEqual(await self.store.event_state(e['event_id']),'notice_disabled');self.assertEqual(self.transport.sent,[])
    async def test_group_notice_default_off_and_templates_honoured_when_enabled(self):
        for kind,sub,pid in [('group_increase','approve','group_welcome'),('group_decrease','kick','group_farewell'),('group_admin','set','group_admin_notice'),('notify','title','group_title_notice'),('group_card','','group_card_notice')]:
            e=self.event(group=77,kind=kind,sub=sub);await self.kernel.on_event(e)
            self.assertEqual(await self.store.event_state(e['event_id']),'notice_disabled')
            self.config.plugin_flags[pid]=True
            self.plugins.update_settings(pid,{'templates':'{user} {action} {title} {old_card} {new_card}'})
            e=self.event(group=77,kind=kind,sub=sub,time=101,title='头衔',card_old='旧名',card_new='新名');await self.kernel.on_event(e)
            self.assertEqual(await self.store.event_state(e['event_id']),'notice_replied')
        self.assertEqual(len(self.transport.sent),5);self.assertEqual(self.provider.prompts,[])
        self.assertIn('头衔',str(self.transport.sent[-1]));self.assertEqual(await self.store.history('g:77'),[])
    async def test_group_own_allowlist_and_probability_and_private_settings(self):
        self.plugins.update_settings('poke',{'group_allowlist':'88','in_private':False})
        for group in (None,77):
            e=self.event(group=group);await self.kernel.on_event(e);self.assertEqual(await self.store.event_state(e['event_id']),'notice_ignored')
        self.plugins.update_settings('poke',{'probability':0});e=self.event(group=88);await self.kernel.on_event(e)
        self.assertEqual(await self.store.event_state(e['event_id']),'notice_probability');self.assertEqual(self.transport.sent,[])
    async def test_global_group_filters_override_poke_direct_trigger(self):
        for index,(name,value) in enumerate([('group_enabled',False),('group_allowlist',['88']),('group_blocklist',['77'])]):
            old=self.config.data[name];self.config.data[name]=value;e=self.event(group=77,time=100+index);await self.kernel.on_event(e);self.config.data[name]=old
            self.assertEqual(await self.store.event_state(e['event_id']),'notice_ignored')
        self.assertEqual(self.transport.sent,[])
    async def test_local_image_and_backpoke_only_after_confirmed_send(self):
        await self.add_test_image();calls=[]
        async def call(action,params,**kwargs):calls.append((action,params))
        self.transport.call=call;self.plugins.update_settings('poke',{'attach_emoji':True,'poke_back':True})
        e=self.event();await self.kernel.on_event(e)
        self.assertEqual(await self.store.event_state(e['event_id']),'notice_replied_poked')
        self.assertEqual(calls,[('send_poke',{'user_id':'42'})])
        self.assertEqual(sum(s['type']=='image' for s in self.transport.sent[0][2]),1)
        self.assertEqual(await self.store.history('p:42'),[])
    async def test_uncertain_send_never_backpokes_or_retries(self):
        calls=[]
        async def call(*a,**kw):calls.append(a)
        self.transport.call=call;self.transport.error=OneBotError('timeout',uncertain=True)
        self.plugins.update_settings('poke',{'poke_back':True});e=self.event();await self.kernel.on_event(e);await self.kernel.on_event(e)
        self.assertEqual(await self.store.event_state(e['event_id']),'send_unknown');self.assertEqual(calls,[]);self.assertEqual(len(self.transport.sent),1)
    async def test_backpoke_timeout_is_partial_success_and_no_retry(self):
        async def call(*a,**kw):raise OneBotError('timeout',uncertain=True)
        self.transport.call=call;self.plugins.update_settings('poke',{'poke_back':True});e=self.event();await self.kernel.on_event(e)
        self.assertEqual(await self.store.event_state(e['event_id']),'notice_backpoke_unknown')
        self.assertEqual(len(self.transport.sent),1)
    async def test_pause_while_waiting_send_lock_cancels_notice(self):
        e=self.event();lock=self.kernel.send_locks[e['session_id']];await lock.acquire()
        task=asyncio.create_task(self.kernel.on_event(e))
        await until(lambda:self.kernel.metrics['notices']==1)
        await asyncio.sleep(.03);self.config.receive_enabled=False;lock.release();await task
        self.assertEqual(self.transport.sent,[]);self.assertEqual(await self.store.event_state(e['event_id']),'receive_disabled')
    async def test_backpoke_pending_restart_not_replayed(self):
        e=self.event();await self.store.ingest_event(e);await self.store.mark_event(e['event_id'],'notice_backpoke_sending')
        path=self.store.path;await self.store.close();self.store=Store(path);await self.store.open();self.kernel.store=self.store
        self.assertEqual(await self.store.event_state(e['event_id']),'notice_backpoke_unknown')
        await self.kernel.on_event(e);self.assertEqual(self.transport.sent,[])
    async def test_invalid_template_and_range_are_rejected_without_save(self):
        old=copy.deepcopy(self.plugins.settings('poke'))
        for values in ({'templates':'{user.__class__}'},{'templates':'{user:100000}'},{'templates':''},{'group_allowlist':'../bad'},{'probability':2}):
            with self.assertRaises(ValueError):self.plugins.update_settings('poke',values)
        self.assertEqual(old,self.plugins.settings('poke'))

class NoticeTransportTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=transport_fixtures.TransportTests.asyncSetUp
    asyncTearDown=transport_fixtures.TransportTests.asyncTearDown
    handle_ws=transport_fixtures.TransportTests.handle_ws
    client=transport_fixtures.TransportTests.client
    async def test_real_websocket_notice_dispatch_and_correlated_reply(self):
        events=[]
        client=await self.client(callback=lambda e:events.append(e))
        await self.sockets[-1].send_json(notice(group=77))
        await until(lambda:len(events)==1)
        self.assertEqual(events[0]['post_type'],'notice')
        result=await client.send_message('group','77',[{'type':'text','data':{'text':'我在呢'}}])
        self.assertEqual(result['message_id'],123)
        self.assertEqual(self.actions[-1]['action'],'send_group_msg')
        await self.sockets[-1].send_json(notice(user=900,group=77));await asyncio.sleep(.03)
        self.assertEqual(len(events),1)

if __name__=='__main__':unittest.main()
