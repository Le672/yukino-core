"""Permission checks, deterministic native triggers and durable reminder delivery."""
import asyncio
import contextlib
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from yukino_core.config import Config
from yukino_core.core import Kernel
from yukino_core.plugins import PluginManager
from yukino_core.resources import ResourceLibrary
from yukino_core.store import Store
from yukino_core.transport import OneBotError,normalize_event

class Provider:
    def __init__(self):self.prompts=[]
    async def complete(self,prompt,timeout):self.prompts.append(prompt);return 'model'

class Peer:
    def __init__(self):
        self.calls=[];self.sent=[];self.roles={'42':'member','43':'member','44':'member','900':'admin'}
        self.connected=True;self.fail_action=None;self.fail_send=False;self.message_group='777'
    def snapshot(self):return {'connected':self.connected}
    async def call(self,action,params=None,timeout=None):
        self.calls.append((action,copy.deepcopy(params)))
        if action==self.fail_action:raise OneBotError('test rejected/uncertain',uncertain=True)
        if action=='get_group_member_info':return {'group_id':params['group_id'],'user_id':params['user_id'],'role':self.roles.get(params['user_id'],'member'),'nickname':'member'}
        if action=='get_group_info':return {'group_name':'Test group','member_count':3,'max_member_count':200}
        if action=='get_msg':return {'group_id':self.message_group,'message_type':'group','sender':{'user_id':'44'}}
        return {}
    async def send_message(self,typ,target,segments):
        self.sent.append((typ,target,copy.deepcopy(segments)))
        if self.fail_send:raise OneBotError('test send unknown',uncertain=True)
        return {'message_id':str(1000+len(self.sent))}

class NativeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'YUKINO_ADMIN_TOKEN':'test-native-admin-credential','YUKINO_SECRETS_FILE':'','YUKINO_SELF_ID':'900','YUKINO_OWNER_ID':'42','YUKINO_RECEIVE_ENABLED':'true'});self.env.start()
        self.cfg=Config(self.root/'config.json');self.cfg.data['merge_window_ms']=0
        self.store=Store(self.root/'core.db');await self.store.open()
        self.resources=ResourceLibrary(self.root/'resources');await self.resources.open()
        self.plugins=PluginManager(self.cfg,self.root/'plugins');self.peer=Peer();self.provider=Provider()
        self.k=Kernel(self.cfg,self.store,self.resources,self.provider,self.plugins);self.k.transport=self.peer;self.n=0
    async def asyncTearDown(self):
        await self.k.close();await self.store.close();self.env.stop();self.tmp.cleanup()
    def event(self,text,user='43',group='777'):
        self.n+=1
        raw={'post_type':'message','message_type':'group' if group else 'private','message_id':str(self.n),'self_id':'900','user_id':user,'message':text,'sender':{'user_id':user,'role':'owner','nickname':'主人'}}
        if group:raw['group_id']=group
        return normalize_event(raw,'900')
    async def invoke(self,text,user='43',group='777'):
        await self.k.on_event(self.event(text,user,group));return self.peer.sent[-1][2][0]['data']['text'] if self.peer.sent else ''
    def mutations(self):return [(a,p) for a,p in self.peer.calls if a.startswith('set_') or a=='delete_msg']
    def rows(self):
        with contextlib.closing(self.k.native._db()) as db:return db.execute('SELECT id,scope,user,body,due,state,note FROM reminders ORDER BY created').fetchall()

    async def test_nickname_and_claimed_event_role_cannot_grant_admin(self):
        reply=await self.invoke('/禁言 44 10m')
        self.assertIn('没有本群管理权限',reply);self.assertEqual(self.mutations(),[]);self.assertEqual(self.provider.prompts,[])
    async def test_qq_admin_can_ban_current_group_after_live_role_check(self):
        self.peer.roles['43']='admin';reply=await self.invoke('/禁言 @44 10m')
        self.assertIn('已禁言600秒',reply);self.assertEqual(self.mutations(),[('set_group_ban',{'group_id':'777','user_id':'44','duration':600})])
    async def test_owner_does_not_bypass_bot_qq_permissions(self):
        self.peer.roles['900']='member';reply=await self.invoke('/全员禁言',user='42')
        self.assertIn('尚未成为',reply);self.assertFalse(self.mutations())
    async def test_group_admin_switch_revokes_group_role_access(self):
        self.peer.roles['43']='admin';self.plugins.update_settings('group_admin',{'allow_group_admins':False})
        self.assertIn('没有本群',await self.invoke('/全员禁言'));self.assertFalse(self.mutations())
    async def test_owner_grants_operator_but_operator_cannot_delegate_again(self):
        self.assertIn('已添加',await self.invoke('/管理 授权 43',user='42',group=None))
        self.assertIn('已开启',await self.invoke('/全员禁言'))
        self.assertIn('仅允许',await self.invoke('/管理 授权 44',group=None))
        self.assertEqual(self.plugins.settings('group_admin')['operators'],'43')
    async def test_operator_cannot_modify_protected_owner_or_admin(self):
        self.peer.roles['43']='admin';self.peer.roles['44']='admin'
        self.assertIn('群主或管理员',await self.invoke('/踢出 44'))
        self.assertIn('主人',await self.invoke('/禁言 42 10m'));self.assertFalse(self.mutations())
    async def test_ban_limits_and_kick_switch_are_enforced(self):
        self.peer.roles['43']='admin'
        self.assertIn('时长',await self.invoke('/禁言 44 -1m'))
        self.assertIn('需为',await self.invoke('/禁言 44 30d'))
        self.plugins.update_settings('group_admin',{'allow_kick':False})
        self.assertIn('关闭',await self.invoke('/踢出 44'));self.assertFalse(self.mutations())
    async def test_qq_admin_change_off_by_default(self):
        self.peer.roles['900']='owner'
        self.assertIn('默认关闭',await self.invoke('/设管理员 44',user='42'));self.assertFalse(self.mutations())
        self.plugins.update_settings('group_admin',{'allow_admin_changes':True})
        self.assertIn('成功',await self.invoke('/设管理员 44',user='42'))
        self.assertEqual(self.mutations()[-1][0],'set_group_admin')
    async def test_recall_requires_message_in_current_group(self):
        self.peer.roles['43']='admin';self.peer.message_group='888'
        self.assertIn('当前群',await self.invoke('/撤回 789'));self.assertFalse(self.mutations())
        self.peer.message_group='777';self.assertIn('已撤回',await self.invoke('/撤回 789'))
        self.assertEqual(self.mutations()[-1],('delete_msg',{'message_id':'789'}))
    async def test_uncertain_mutation_is_never_retried_or_claimed_success(self):
        self.peer.roles['43']='admin';self.peer.fail_action='set_group_ban'
        event=self.event('/禁言 44 1m');await self.k.on_event(event);await self.k.on_event(event)
        self.assertEqual(len(self.mutations()),1);self.assertIn('未能确认',self.peer.sent[-1][2][0]['data']['text'])
    async def test_private_mutations_and_blocked_groups_do_not_act(self):
        self.assertIn('目标群',await self.invoke('/全员禁言',user='42',group=None))
        self.cfg.data['group_blocklist']=['777'];before=len(self.peer.calls)
        await self.k.on_event(self.event('/全员禁言',user='42'));self.assertEqual(len(self.peer.calls),before)
    async def test_ordinary_member_can_read_current_group_only(self):
        self.assertIn('Test group',await self.invoke('/群信息'))
        self.assertIn('QQ：44',await self.invoke('/成员 44'))
        self.assertNotIn('42',await self.invoke('/管理员'))
    async def test_keyword_requires_owner_and_bypasses_model_without_at(self):
        self.assertIn('主人',await self.invoke('/关键词 添加 在吗|在这里'))
        self.assertIn('更新',await self.invoke('/关键词 添加 在吗|在这里',user='42'))
        self.assertEqual(await self.invoke('在吗'),'在这里');self.assertEqual(self.provider.prompts,[])
    async def test_keyword_cooldown_does_not_fall_through_to_model(self):
        self.plugins.update_settings('keyword_reply',{'rules_json':'[{"key":"hi","reply":"hello"}]','cooldown_seconds':100})
        await self.k.on_event(self.event('hi'));before=len(self.peer.sent)
        await self.k.on_event(self.event('hi'));self.assertEqual(len(self.peer.sent),before);self.assertFalse(self.provider.prompts)
    async def test_keyword_settings_still_obey_group_access_and_disable(self):
        self.plugins.update_settings('keyword_reply',{'rules_json':'[{"key":"hi","reply":"hello"}]'})
        self.cfg.data['group_allowlist']=['888'];await self.k.on_event(self.event('hi'))
        self.cfg.data['group_allowlist']=[];await self.plugins.toggle('keyword_reply',False);await self.k.on_event(self.event('hi'))
        self.assertFalse(self.peer.sent);self.assertFalse(self.provider.prompts)
    async def test_keyword_configuration_survives_reload_and_invalid_json_is_safe(self):
        await self.invoke('/关键词 包含 你好|欢迎',user='42')
        cfg=Config(self.cfg.path);self.assertIn('欢迎',cfg.plugin_settings['keyword_reply']['rules_json'])
        with self.assertRaises(ValueError):self.plugins.update_settings('keyword_reply',{'rules_json':'not json'})
        self.cfg.plugin_settings['keyword_reply']['rules_json']='not json' # Existing malformed file still fails closed.
        self.assertIsNone(self.k.native.keyword_match(self.event('你好')))
    async def test_reminder_persists_and_dispatches_once_to_same_chat(self):
        self.assertIn('已设提醒',await self.invoke('/提醒 30s 拿快递'))
        row=self.rows()[0];before=len(self.peer.sent)
        await self.k.native.tick(row[4]+1);await self.k.native.tick(row[4]+2)
        self.assertEqual(len(self.peer.sent),before+1);self.assertEqual(self.peer.sent[-1][:2],('group','777'));self.assertEqual(self.rows()[0][5],'sent')
        self.assertEqual(self.provider.prompts,[])
    async def test_private_reminder_uses_original_requester(self):
        await self.invoke('/提醒 10s 私聊提醒',group=None)
        row=self.rows()[0];await self.k.native.tick(row[4]+1)
        self.assertEqual(self.peer.sent[-1][:2],('private','43'))
    async def test_reminder_cancel_is_scoped_to_requester(self):
        await self.invoke('/提醒 30s 内容');rid=self.rows()[0][0]
        self.assertIn('没有找到',await self.invoke('/提醒 取消 '+rid,user='44'))
        self.assertIn('已取消',await self.invoke('/提醒 取消 '+rid))
        before=len(self.peer.sent);await self.k.native.tick(time.time()+60);self.assertEqual(len(self.peer.sent),before)
    async def test_reminder_unknown_send_not_replayed_after_restart(self):
        await self.invoke('/提醒 30s 内容');row=self.rows()[0];self.peer.fail_send=True
        await self.k.native.tick(row[4]+1);before=len(self.peer.sent)
        self.assertEqual(self.rows()[0][5],'unknown');self.peer.fail_send=False
        await self.k.native.start();await self.k.native.close();await self.k.native.tick(row[4]+2)
        self.assertEqual(len(self.peer.sent),before)
    async def test_reminder_startup_recovers_interrupted_dispatch_as_unknown(self):
        await self.invoke('/提醒 30s 内容');rid=self.rows()[0][0]
        with contextlib.closing(self.k.native._db()) as db:db.execute("UPDATE reminders SET state='dispatching' WHERE id=?",(rid,))
        await self.k.native.start();await self.k.native.close();self.assertEqual(self.rows()[0][5],'unknown')
    async def test_reminder_offline_waits_then_expired_or_blocked_jobs_not_sent(self):
        await self.invoke('/提醒 10s 内容');row=self.rows()[0];self.peer.connected=False
        await self.k.native.tick(row[4]+1);self.assertEqual(self.rows()[0][5],'pending')
        self.peer.connected=True;self.cfg.data['user_blocklist']=['43'];await self.k.native.tick(row[4]+1)
        self.assertEqual(self.rows()[0][5],'paused')
        self.cfg.data['user_blocklist']=[];await self.invoke('/提醒 10s 旧提醒');row=self.rows()[-1]
        await self.k.native.tick(row[4]+7200);self.assertEqual(self.rows()[-1][5],'expired')
    async def test_reminder_limits_and_bad_dates(self):
        self.plugins.update_settings('reminders',{'per_user_limit':1})
        self.assertIn('已设',await self.invoke('/提醒 10m 内容'))
        self.assertIn('上限',await self.invoke('/提醒 20m 内容'))
        self.assertIn('至少',await self.invoke('/提醒 0s 内容'))
        self.assertIn('格式',await self.invoke('/提醒 2026-02-30 09:00 内容'))
    async def test_whole_ban_rejects_ambiguous_extra_target_or_duration(self):
        self.assertIn('不接受',await self.invoke('/全员禁言 10m',user='42'))
        self.assertIn('不接受',await self.invoke('/解除全禁 888',user='42'));self.assertFalse(self.mutations())
        self.assertIn('已修改',await self.invoke('/群名片 900 测试机器人',user='42'))
        self.assertEqual(self.mutations()[-1],('set_group_card',{'group_id':'777','user_id':'900','card':'测试机器人'}))
    async def test_plugin_data_failure_does_not_crash_other_features_or_expose_path(self):
        with patch.object(self.k.native,'_db',side_effect=OSError('private path')):
            reply=await self.invoke('/提醒 10s 内容');self.assertIn('暂时不可用',reply);self.assertNotIn('private',reply)
            await self.k.native.start();await self.k.native.close()
        self.assertIn('Yukino Core',await self.invoke('/状态'));self.assertFalse(self.provider.prompts)

if __name__=='__main__':unittest.main()
