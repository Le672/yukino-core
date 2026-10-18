"""Deterministic group operations. Permission is checked against OneBot, never an LLM."""
from __future__ import annotations
import asyncio
import json
import re
import time
from collections import defaultdict
from .transport import OneBotError

HELP = ('群管：/群信息 /成员 QQ号 /管理员\n'
        '/禁言 QQ号 10m /解禁 QQ号 /全员禁言 /解除全禁\n'
        '/踢出 QQ号 /群名片 QQ号 新名片 /群名称 新名称 /撤回 消息ID\n'
        '也可用 /群管 禁言 QQ号 10m。需要机器人和操作者具备相应权限。\n'
        '机器人主人：/管理 状态、插件、启用 插件ID、停用 插件ID、授权 QQ号、撤销 QQ号。')

def ids(value):
    if not isinstance(value,str): return set()
    return {s for s in re.split(r'[\s,，]+',value.strip()) if re.fullmatch(r'[1-9][0-9]{0,19}',s)}

def qid(value):
    value=value.lstrip('@')
    if not re.fullmatch(r'[1-9][0-9]{0,19}',value): raise ValueError('QQ号须为正整数。')
    return value

def duration(value, maximum):
    m=re.fullmatch(r'([0-9]{1,7})(s|m|h|d|秒|分钟|小时|天)?',value,re.I)
    if not m: raise ValueError('时长用 30s、10m、1h、1d；不带单位按秒。')
    result=int(m[1])*{'s':1,'m':60,'h':3600,'d':86400,'秒':1,'分钟':60,'小时':3600,'天':86400,None:1}[m[2].lower() if m[2] else None]
    if not 1<=result<=maximum: raise ValueError(f'禁言时长需为1—{maximum}秒。')
    return result

def safe(value, limit=100):
    return re.sub(r'[\x00-\x1f\x7f]', ' ',str(value))[:limit]

class GroupAdmin:
    def __init__(self,kernel):
        self.k=kernel
        self.locks=defaultdict(asyncio.Lock)

    def owners(self):
        return set(getattr(self.k.config,'owner_ids',[getattr(self.k.config,'owner_id','')]))

    def settings(self): return self.k.plugins.settings('group_admin')

    async def _member(self,group,user):
        row=await self.k.transport.call('get_group_member_info',{'group_id':group,'user_id':user,'no_cache':True},timeout=6)
        if not isinstance(row,dict) or str(row.get('user_id',''))!=str(user) or str(row.get('group_id',''))!=str(group):
            raise ValueError('无法核实群成员身份，本次没有执行管理动作。')
        return row

    async def handle(self,event,command):
        arg=event['text'].strip().split(maxsplit=1)
        arg=arg[1] if len(arg)>1 else ''
        actor=str(event['user_id'])
        cfg=self.settings()
        if command=='/管理': return await self._bot_admin(actor,arg)
        if command=='/群管':
            pair=arg.split(maxsplit=1)
            if not pair or pair[0] in ('帮助','help'):return HELP
            command='/'+pair[0];arg=pair[1] if len(pair)>1 else ''
        if command=='/管理员' and not arg:
            if actor not in self.owners():
                return '机器人群管授权：'+('你已获授权。' if actor in ids(cfg.get('operators','')) else '你未在额外授权名单中。')+'\n本群QQ管理员是否可用由插件设置和QQ实际角色共同判断。'
            return '机器人主人：'+('、'.join(sorted(self.owners())) or '未配置')+'\n授权群管：'+('、'.join(sorted(ids(cfg.get('operators','')))) or '无')+'\n群内权限以QQ实际角色为准。'
        if event.get('message_type')!='group' or not re.fullmatch(r'[0-9]{1,20}',str(event.get('group_id',''))):
            return '群管理指令请在目标群里使用；私聊可用 /管理 状态。'
        group=str(event['group_id'])
        async with self.locks[group]:
            try:
                return await self._group(event,command,arg,cfg)
            except ValueError as exc:return str(exc)
            except OneBotError as exc:
                return 'QQ未能确认操作结果，请先核对群内实际状态；本次不会自动重试。' if exc.uncertain else 'QQ拒绝了操作或连接不可用，请核对机器人群权限和目标。'
            except Exception:return '群管查询或操作失败，本次没有自动重试。'

    async def _bot_admin(self,actor,arg):
        if actor not in self.owners():return '这条机器人管理指令仅允许已配置的主人使用。'
        parts=arg.split(maxsplit=1);action=parts[0] if parts else '帮助';rest=parts[1].strip() if len(parts)>1 else ''
        if action=='状态':
            snap=self.k.transport.snapshot()
            return f"Yukino Core\nQQ连接：{'正常' if snap.get('connected') else '断开'}\n运行{int(time.monotonic()-self.k.started)}秒\n插件{sum(x['enabled'] for x in self.k.plugins.list())}/{len(self.k.plugins.items)}\n待处理{self.k.pending_count}，生成中{self.k.active}"
        if action=='插件':return '\n'.join(x['id']+'｜'+x['name']+'｜'+('开' if x['enabled'] else '关') for x in self.k.plugins.list())[:3800]
        if action in ('启用','停用'):
            if not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}',rest):return '用法：/管理 启用 插件ID 或 /管理 停用 插件ID'
            try:await self.k.plugins.toggle(rest,action=='启用')
            except ValueError as exc:return str(exc)
            return f"插件 {rest} 已{'启用' if action=='启用' else '停用'}。"
        if action in ('授权','撤销'):
            try:target=qid(rest)
            except ValueError:return '用法：/管理 授权 QQ号 或 /管理 撤销 QQ号'
            current=ids(self.settings().get('operators',''))
            if action=='授权':current.add(target)
            else:current.discard(target)
            if len(current)>100:return '授权群管最多100人。'
            self.k.plugins.update_settings('group_admin',{'operators':'\n'.join(sorted(current))})
            return f"已{'添加' if action=='授权' else '撤销'} {target} 的机器人群管授权。没有改变QQ群管理员身份。"
        return HELP

    async def _group(self,event,command,arg,cfg):
        group,actor=str(event['group_id']),str(event['user_id'])
        if command in ('/群信息','/成员'):
            if not cfg.get('public_queries',True) and actor not in self.owners() and actor not in ids(cfg.get('operators','')):
                row=await self._member(group,actor)
                if row.get('role') not in ('admin','owner'):return '该群查询仅向管理员开放。'
            if command=='/群信息':
                row=await self.k.transport.call('get_group_info',{'group_id':group,'no_cache':True},timeout=6)
                return f"群：{safe(row.get('group_name','未知'))}\n群号：{group}\n人数：{row.get('member_count','未知')}/{row.get('max_member_count','未知')}"
            target=qid(arg.strip() or actor);row=await self._member(group,target)
            return f"QQ：{target}\n群名片：{safe(row.get('card') or row.get('nickname') or target)}\n角色：{dict(owner='群主',admin='管理员',member='群员').get(row.get('role'),'未知')}"
        supported={'/禁言','/解禁','/全员禁言','/解除全禁','/踢出','/群名片','/群名称','/撤回','/设管理员','/撤管理员'}
        if command not in supported:return HELP
        if command in ('/全员禁言','/解除全禁') and arg.strip():return '该指令不接受目标群或时长，只操作当前群；解除时使用 /解除全禁。'
        actor_row=await self._member(group,actor)
        authorized=actor in self.owners() or actor in ids(cfg.get('operators','')) or (cfg.get('allow_group_admins',True) and actor_row.get('role') in ('owner','admin'))
        if not authorized:return '你没有本群管理权限；QQ昵称或消息里的“主人”字样不构成授权。'
        bot=str(self.k.config.self_id);bot_row=await self._member(group,bot)
        if bot_row.get('role') not in ('owner','admin'):return '机器人尚未成为本群管理员，无法执行此操作。'
        params={'group_id':group};target=None
        if command in ('/禁言','/解禁','/踢出','/群名片','/设管理员','/撤管理员'):
            parts=arg.split(maxsplit=1)
            if not parts:raise ValueError('请填写目标QQ号。')
            target=qid(parts[0]);rest=parts[1] if len(parts)>1 else ''
            if command in ('/解禁','/踢出','/设管理员','/撤管理员') and rest.strip():return '该指令只接受一个目标QQ号，不接受其他参数。'
            target_row=await self._member(group,target)
            if command!='/群名片' and (target==bot or target in self.owners()):return '不能对机器人自身或已配置的机器人主人执行这项操作。'
            if command not in ('/群名片','/设管理员','/撤管理员') and target_row.get('role') in ('owner','admin'):
                return '该目标是群主或管理员，本插件不会对其禁言或踢出。'
            params['user_id']=target
        if command in ('/禁言','/解禁'):
            seconds=duration(rest,int(cfg.get('max_ban_seconds',86400))) if command=='/禁言' else 0
            params['duration']=seconds;action='set_group_ban';summary=f'{target} 已'+(f'禁言{seconds}秒。' if seconds else '解除禁言。')
        elif command in ('/全员禁言','/解除全禁'):
            params['enable']=command=='/全员禁言';action='set_group_whole_ban';summary='已'+('开启' if params['enable'] else '关闭')+'全员禁言。'
        elif command=='/踢出':
            if not cfg.get('allow_kick',True):return '踢人功能已在插件设置中关闭。'
            params['reject_add_request']=False;action='set_group_kick';summary=f'已将 {target} 移出本群。'
        elif command=='/群名片':
            if not rest or len(rest)>60 or any(ord(c)<32 for c in rest):return '名片须为1—60字的单行文字。'
            if target_row.get('role')=='owner' and target!=actor:return '不修改其他群主的名片。'
            params['card']=rest;action='set_group_card';summary=f'已修改 {target} 的群名片。'
        elif command=='/群名称':
            if not arg or len(arg)>60 or any(ord(c)<32 for c in arg):return '群名称须为1—60字的单行文字。'
            params['group_name']=arg;action='set_group_name';summary='已修改本群名称。'
        elif command in ('/设管理员','/撤管理员'):
            if not cfg.get('allow_admin_changes',False):return 'QQ管理员变更默认关闭，可由机器人主人在控制台明确开启。'
            if bot_row.get('role')!='owner' or actor not in self.owners():return 'QQ管理员变更要求机器人实际为群主，并由已配置的机器人主人执行。'
            params['enable']=command=='/设管理员';action='set_group_admin';summary='QQ管理员变更已提交成功。'
        else:
            mid=arg.strip() or str(event.get('reply_to',''))
            if not re.fullmatch(r'-?[0-9]{1,24}',mid):return '用法：/撤回 消息ID，或引用本群消息后发送 /撤回。'
            message=await self.k.transport.call('get_msg',{'message_id':mid},timeout=6)
            if str(message.get('group_id',''))!=group or message.get('message_type')!='group':return '只能撤回经过核实属于当前群的消息。'
            action='delete_msg';params={'message_id':mid};summary='已撤回指定消息。'
        await self.k.transport.call(action,params,timeout=8)
        return summary
