"""Contextual group participation with persistent budgets and no replay on boot."""
from __future__ import annotations
import asyncio
import contextlib
from datetime import datetime,timedelta,timezone
import random
import re
import secrets
import time


class ProactiveChat:
    def __init__(self,kernel):
        self.kernel=kernel;self.groups={};self.task=None;self.random=random.SystemRandom()
        self.metrics={'joined':0,'idle_topics':0,'sent':0,'skipped':0,'budget_limited':0}
        self.mute_cache={}

    def allowed(self,event):
        k=self.kernel;c=k.config.data;group=event.get('group_id','');user=event.get('user_id','')
        return bool(c['proactive_enabled'] and getattr(k.config,'receive_enabled',True)
            and not k.closing and event.get('message_type')=='group' and group
            and c['group_enabled'] and group not in c['group_blocklist'] and user not in c['user_blocklist']
            and (not c['group_allowlist'] or group in c['group_allowlist'])
            and (not c['proactive_group_allowlist'] or group in c['proactive_group_allowlist'])
            and user!=str(k.config.self_id) and not (c['ignore_bots'] and event.get('is_bot')))

    def quiet(self):
        c=self.kernel.config.data
        hour=datetime.now(timezone(timedelta(hours=c['proactive_utc_offset_hours']))).hour
        start,end=c['proactive_quiet_start_hour'],c['proactive_quiet_end_hour']
        return start<=hour<end if start<end else (hour>=start or hour<end) if start>end else False

    def observe(self,event):
        if not self.allowed(event):return
        stamp=event.get('time',0)
        if stamp and abs(time.time()-stamp)>120:return
        # Only meaningful human text keeps a group active; stickers and bot output do not.
        plain=''.join(str(s.get('data',{}).get('text','')) for s in event.get('segments',[]) if s.get('type')=='text').strip()
        if not plain or len(plain)<self.kernel.config.data['proactive_min_message_chars']:return
        if len(self.groups)>=200 and event['session_id'] not in self.groups:
            self.groups.pop(min(self.groups,key=lambda sid:self.groups[sid]['human_at']))
        # A monotonic clock has an arbitrary origin, including shortly after
        # boot. An absent bot reply must not count as a reply at clock zero.
        state=self.groups.setdefault(event['session_id'],{'bot_at':-float('inf'),'idle_done':''})
        state.update(human_at=time.monotonic(),event={key:event.get(key) for key in
            ('event_id','session_id','message_type','group_id','user_id','display_name','time','self_id','is_bot')})
        state['event'].update(text=plain[:1000],segments=[],message_id='',mentioned=False,reply_to='')

    def available(self,event):
        k=self.kernel
        if not self.allowed(event) or self.quiet():return False
        if not k.transport or not k.transport.snapshot().get('connected'):return False
        if k.provider.snapshot().get('healthy') is False:return False
        if event.get('proactive_kind')=='idle':
            state=self.groups.get(event['session_id'])
            if not state or state['event']['event_id']!=event.get('history_anchor_event_id'):return False
        return True

    async def can_speak(self,event):
        """Check the actual QQ mute field before an unsolicited reply."""
        k=self.kernel;group=event['group_id'];now=time.monotonic()
        cached=self.mute_cache.get(group)
        if cached and cached[0]>now:return cached[1]
        if not hasattr(k.transport,'call'):return True
        try:
            member=await k.transport.call('get_group_member_info',{'group_id':group,'user_id':k.config.self_id,'no_cache':True},timeout=4)
            allowed=float(member.get('shut_up_timestamp') or 0)<=time.time()
        except Exception:allowed=False
        self.mute_cache[group]=(now+30,allowed)
        return allowed

    async def reserve(self,event):
        k=self.kernel;c=k.config.data
        if not self.available(event) or k.active or k.pending_count or k.generation_gate.locked():return False
        state=self.groups.get(event['session_id'])
        if state and time.monotonic()-state['bot_at']<c['proactive_min_interval_seconds']:return False
        if not await self.can_speak(event):return False
        result=await k.store.claim_proactive(event['session_id'],c['proactive_min_interval_seconds'],c['proactive_max_per_hour'],c['proactive_global_max_per_hour'])
        if not result:self.metrics['budget_limited']+=1
        return result

    async def consider(self,event):
        c=self.kernel.config.data
        if not self.allowed(event) or self.quiet():return False
        text=event.get('text','').strip()
        if len(text)<c['proactive_min_message_chars'] or text.startswith(c['command_prefix']) or re.search(r'https?://',text):return False
        if event.get('mentioned') or event.get('reply_to'):return False
        if any(s.get('type')=='at' for s in event.get('segments',[])):return False
        if event.get('time') and abs(time.time()-event['time'])>120:return False
        if not any(s.get('type')=='text' and s.get('data',{}).get('text','').strip() for s in event.get('segments',[])):return False
        if self.random.randrange(100)>=c['proactive_chance_percent']:return False
        if not await self.reserve(event):return False
        self.metrics['joined']+=1;return True

    def sent(self,event):
        state=self.groups.get(event['session_id'])
        if state:state['bot_at']=time.monotonic()
        if event.get('proactive_kind'):self.metrics['sent']+=1

    def reset(self,sid):
        self.groups.pop(sid,None)

    async def tick(self):
        k=self.kernel;c=k.config.data
        if not c['proactive_idle_enabled'] or not c['proactive_enabled'] or self.quiet():return
        now=time.monotonic()
        for sid,state in list(self.groups.items()):
            if now-state['human_at']>c['proactive_recent_activity_minutes']*60:
                self.groups.pop(sid,None);continue
            if now-max(state['human_at'],state['bot_at'])<c['proactive_idle_minutes']*60:continue
            original=state['event']
            if state['idle_done']==original['event_id']:continue
            event={**original,'event_id':'proactive:'+secrets.token_hex(16),'post_type':'proactive',
                   'proactive_kind':'idle','history_anchor_event_id':original['event_id'],
                   'text':'群聊暂时安静了。结合同一群的近期聊天自然接续一个话题；没有适合接续的内容则保持安静。'}
            if not await self.reserve(event):continue
            state['idle_done']=original['event_id'];self.metrics['idle_topics']+=1
            if await k.store.ingest_event(event):await k.enqueue(event)
            break

    async def start(self):
        if self.task:return
        async def watch():
            while not self.kernel.closing:
                await asyncio.sleep(15)
                try:await self.tick()
                except asyncio.CancelledError:raise
                except Exception:self.metrics['skipped']+=1
        self.task=asyncio.create_task(watch(),name='group-proactive-chat')

    async def close(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):await self.task
            self.task=None

    def snapshot(self):
        return {'enabled':self.kernel.config.data['proactive_enabled'],'idle_enabled':self.kernel.config.data['proactive_idle_enabled'],
                'recent_groups':len(self.groups),'quiet_now':self.quiet(),**self.metrics}
