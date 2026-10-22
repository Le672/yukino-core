import asyncio
import time
import unittest
from unittest.mock import AsyncMock,patch
import test_core as fixtures
from yukino_core.provider import ProviderError
from yukino_core.store import Store

class ProactiveTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.KernelTests.asyncSetUp
    asyncTearDown=fixtures.KernelTests.asyncTearDown
    message=fixtures.KernelTests.message
    idle=fixtures.KernelTests.idle

    def enable(self):
        self.config.data.update(proactive_enabled=True,proactive_chance_percent=100,
            proactive_quiet_start_hour=0,proactive_quiet_end_hour=0)

    def group_message(self,text='这个游戏的剧情真不错',group='777',**kwargs):
        event=self.message(text,group=group,**kwargs);event['time']=int(time.time());return event

    async def test_ordinary_group_message_gets_contextual_reply_without_mention(self):
        self.enable();event=self.group_message()
        await self.kernel.on_event(event);await self.idle()
        self.assertEqual(len(self.transport.sent),1)
        self.assertIn('主动参与',self.provider.prompts[0])
        self.assertEqual(self.kernel.proactive.snapshot()['sent'],1)
        self.assertEqual(await self.store.event_state(event['event_id']),'replied')

    async def test_disabled_blocked_bot_private_and_other_group_remain_quiet(self):
        await self.kernel.on_event(self.group_message());self.assertFalse(self.provider.prompts)
        self.enable();self.config.data['proactive_group_allowlist']=['888']
        await self.kernel.on_event(self.group_message())
        self.config.data['proactive_group_allowlist']=[];self.config.data['user_blocklist']=['42']
        await self.kernel.on_event(self.group_message())
        self.config.data['user_blocklist']=[];event=self.group_message();event['is_bot']=True
        await self.kernel.on_event(event)
        self.assertFalse(self.provider.prompts)

    async def test_probability_quiet_hours_and_old_messages_do_not_join(self):
        self.enable();self.config.data['proactive_chance_percent']=0
        await self.kernel.on_event(self.group_message())
        self.config.data['proactive_chance_percent']=100
        with patch.object(self.kernel.proactive,'quiet',return_value=True):await self.kernel.on_event(self.group_message())
        event=self.group_message();event['time']=int(time.time())-1000
        await self.kernel.on_event(event);self.assertFalse(self.provider.prompts)

    async def test_direct_mention_still_works_during_proactive_cooldown(self):
        self.enable();await self.kernel.on_event(self.group_message());await self.idle()
        await self.kernel.on_event(self.group_message('普通的第二句话'));await self.idle()
        self.assertEqual(len(self.transport.sent),1)
        await self.kernel.on_event(self.group_message('请解释这个剧情',mentioned=True));await self.idle()
        self.assertEqual(len(self.transport.sent),2)

    async def test_idle_once_per_human_activity_and_no_fabricated_user_history(self):
        self.enable();self.config.data.update(proactive_chance_percent=0,proactive_idle_enabled=True)
        event=self.group_message();await self.kernel.on_event(event)
        state=self.kernel.proactive.groups['g:777'];state['human_at']=time.monotonic()-1900
        await self.kernel.proactive.tick();await self.idle()
        self.assertEqual(len(self.transport.sent),1)
        prompt=fixtures.prompt_data(self.provider.prompts[0])
        self.assertIn(event['text'],str(prompt['history']))
        self.assertEqual(prompt['current_senders'],[])
        rows=await self.store.history('g:777')
        self.assertEqual(len([r for r in rows if r['role']=='user']),1)
        state['bot_at']=0;await self.kernel.proactive.tick();await self.idle()
        self.assertEqual(len(self.transport.sent),1)

    async def test_disable_while_generating_cancels_unsolicited_send(self):
        self.enable();self.provider.release.clear()
        event=self.group_message();await self.kernel.on_event(event);await self.provider.started.wait()
        self.config.data['proactive_enabled']=False;self.provider.release.set();await self.idle()
        self.assertFalse(self.transport.sent)
        self.assertEqual(await self.store.event_state(event['event_id']),'proactive_skipped')

    async def test_actual_mute_and_query_failure_skip_without_group_errors(self):
        self.enable();self.transport.call=AsyncMock(return_value={'shut_up_timestamp':time.time()+600})
        await self.kernel.on_event(self.group_message());self.assertFalse(self.provider.prompts)
        self.kernel.proactive.mute_cache.clear();self.transport.call.side_effect=OSError('offline')
        await self.kernel.on_event(self.group_message());self.assertFalse(self.transport.sent)

    async def test_provider_errors_and_silent_decisions_are_not_posted(self):
        self.enable();self.provider.error=ProviderError('private backend failure')
        await self.kernel.on_event(self.group_message());await self.idle();self.assertFalse(self.transport.sent)
        self.provider.error=None;self.provider.response='[[silent]]'
        await self.kernel.on_event(self.group_message(group='888'));await self.idle();self.assertFalse(self.transport.sent)

    async def test_context_does_not_cross_groups_or_recent_reset(self):
        self.enable();self.config.data['proactive_chance_percent']=0
        await self.kernel.on_event(self.group_message('另一个群的暗号 SECRET-OTHER-GROUP',group='888'))
        self.config.data['proactive_chance_percent']=100
        await self.kernel.on_event(self.group_message());await self.idle()
        self.assertNotIn('SECRET-OTHER-GROUP',self.provider.prompts[-1])
        await self.kernel.reset('g:777');self.assertNotIn('g:777',self.kernel.proactive.groups)

    async def test_rolling_budgets_persist_and_are_atomic(self):
        with patch('yukino_core.store.time.time',return_value=10000):
            self.assertTrue(await self.store.claim_proactive('g:777',15,2,3))
            other=Store(self.store.path);await other.open()
            try:self.assertFalse(await other.claim_proactive('g:777',15,2,3))
            finally:await other.close()
        with patch('yukino_core.store.time.time',return_value=10020):
            self.assertTrue(await self.store.claim_proactive('g:777',15,2,3))
        with patch('yukino_core.store.time.time',return_value=10040):
            self.assertFalse(await self.store.claim_proactive('g:777',15,2,3))
            self.assertTrue(await self.store.claim_proactive('g:888',15,2,3))
            self.assertFalse(await self.store.claim_proactive('g:999',15,2,3))

    async def test_mentions_of_other_people_are_not_hijacked(self):
        self.enable();event=self.group_message();event['segments'].insert(0,{'type':'at','data':{'qq':'123'}})
        await self.kernel.on_event(event);self.assertFalse(self.transport.sent)
