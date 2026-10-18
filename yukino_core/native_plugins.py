"""Native extensions that need trusted host capabilities, never passed to user workers."""
from __future__ import annotations
import asyncio
import contextlib
from datetime import datetime,timedelta,timezone
import json
import re
import sqlite3
import time
import uuid
from .group_admin import GroupAdmin

NATIVE_IDS={'group_admin','reminders','keyword_reply'}

class NativeExtensions:
    def __init__(self,kernel):
        self.k=kernel;self.admin=GroupAdmin(kernel);self.task=None;self.keyword_last={};self.recover_pending=True

    def _db(self):
        directory=self.k.plugins._state_directory('reminders')
        path=directory/'reminders.sqlite3'
        db=sqlite3.connect(path,timeout=2,isolation_level=None)
        path.chmod(0o600)
        db.execute('CREATE TABLE IF NOT EXISTS reminders(id TEXT PRIMARY KEY,scope TEXT,user TEXT,body TEXT,due REAL,state TEXT,created REAL,note TEXT)')
        db.execute('CREATE INDEX IF NOT EXISTS reminder_due ON reminders(state,due)')
        return db

    def settings(self,pid):return self.k.plugins.settings(pid)

    def owners(self):return set(getattr(self.k.config,'owner_ids',[getattr(self.k.config,'owner_id','')]))

    def _rules(self):
        if not self.k.plugins.enabled('keyword_reply'):return []
        try:
            rules=json.loads(self.settings('keyword_reply').get('rules_json','[]'))
            if not isinstance(rules,list) or len(rules)>50:return []
            return [r for r in rules if isinstance(r,dict) and isinstance(r.get('key'),str) and 1<=len(r['key'])<=80
                    and isinstance(r.get('reply'),str) and 1<=len(r['reply'])<=800 and r.get('mode','exact') in ('exact','contains')]
        except (ValueError,TypeError):return []

    def keyword_match(self,event):
        text=event.get('text','').strip()
        cfg=self.settings('keyword_reply')
        if event.get('message_type')=='group' and not cfg.get('in_groups',True):return None
        if event.get('message_type')=='private' and not cfg.get('in_private',True):return None
        for rule in self._rules():
            if (text==rule['key'] if rule.get('mode','exact')=='exact' else rule['key'] in text):return rule['reply']
        return None

    def keyword_reply(self,event):
        text=self.keyword_match(event)
        if text is None:return False,None
        now=time.monotonic();sid=event['session_id']
        cooldown=self.settings('keyword_reply').get('cooldown_seconds',10)
        if now-self.keyword_last.get(sid,-float('inf'))<cooldown:return True,None
        if len(self.keyword_last)>2000:self.keyword_last.clear()
        self.keyword_last[sid]=now
        return True,text

    async def handle(self,event,item,command):
        if item['id']=='group_admin':return await self.admin.handle(event,command)
        if item['id']=='keyword_reply':return self.keyword_command(event)
        return self.reminder_command(event)

    def keyword_command(self,event):
        parts=event['text'].strip().split(maxsplit=2)
        action=parts[1] if len(parts)>1 else '列表';rest=parts[2] if len(parts)>2 else ''
        if action=='列表':
            rows=self._rules()
            return '关键词规则：\n'+('\n'.join(f"{i}. {r['key']}（{r.get('mode','exact')}）" for i,r in enumerate(rows,1)) or '尚未设置。主人可用 /关键词 添加 关键词|回复，也可在面板编辑。')
        if str(event['user_id']) not in self.owners():return '关键词规则仅允许机器人主人修改。'
        rows=self._rules()
        if action in ('添加','包含'):
            key,sep,reply=rest.partition('|')
            key,reply=key.strip(),reply.strip()
            if not sep or not 1<=len(key)<=80 or not 1<=len(reply)<=800 or any(ord(x)<32 for x in key):return '用法：/关键词 添加 关键词|回复（精确匹配）；/关键词 包含 关键词|回复。'
            if len(rows)>=50:return '最多50条规则，请先移除不需要的规则。'
            rows.append({'key':key,'reply':reply,'mode':'contains' if action=='包含' else 'exact'})
        elif action=='删除':
            if not rest.isdigit() or not 1<=int(rest)<=len(rows):return '请从 /关键词 列表 查找有效编号。'
            rows.pop(int(rest)-1)
        else:return '用法：/关键词 列表、添加 关键词|回复、包含 关键词|回复、删除 编号。'
        self.k.plugins.update_settings('keyword_reply',{'rules_json':json.dumps(rows,ensure_ascii=False)})
        return '关键词规则已更新，后续消息生效。'

    def reminder_command(self,event):
        parts=event['text'].strip().split(maxsplit=1)
        arg=parts[1] if len(parts)>1 else '列表'
        scope,user=event['session_id'],str(event['user_id']);cfg=self.settings('reminders')
        if not re.fullmatch(r'[pg]:[0-9]{1,20}',scope):return '提醒目标必须是当前QQ私聊或群聊。'
        zone=timezone(timedelta(hours=cfg.get('utc_offset_hours',8)));now=time.time()
        with contextlib.closing(self._db()) as db:
            if arg in ('列表','我的','list'):
                rows=db.execute('SELECT id,body,due,state,note FROM reminders WHERE scope=? AND user=? ORDER BY created DESC LIMIT 12',(scope,user)).fetchall()
                return '本会话我的提醒：\n'+('\n'.join(f"{i}｜{datetime.fromtimestamp(due,zone):%m-%d %H:%M}｜{state}｜{body[:100]}"+(f'（{note}）' if note else '') for i,body,due,state,note in rows) or '暂无。用法：/提醒 30m 拿快递 或 /提醒 2026-10-18 09:00 开会')
            if arg.startswith('取消 '):
                rid=arg[3:].strip()
                result=db.execute("UPDATE reminders SET state='cancelled',note='用户取消' WHERE id=? AND scope=? AND user=? AND state='pending'",(rid,scope,user))
                return '提醒已取消。' if result.rowcount else '没有找到你在本会话中待执行的提醒。'
            when,sep,body=arg.partition(' ')
            relative=re.fullmatch(r'([0-9]{1,6})(s|m|h|d|秒|分钟|小时|天)(?:后)?',when,re.I)
            if relative:
                scale={'s':1,'m':60,'h':3600,'d':86400,'秒':1,'分钟':60,'小时':3600,'天':86400}[relative[2].lower()]
                due=now+int(relative[1])*scale
            elif re.fullmatch(r'\d{4}-\d{2}-\d{2}',when):
                clock,_,body=body.partition(' ')
                try:due=datetime.strptime(when+' '+clock,'%Y-%m-%d %H:%M').replace(tzinfo=zone).timestamp()
                except ValueError:return '日期时间格式：/提醒 YYYY-MM-DD HH:MM 内容（按插件配置时区）。'
            else:return '用法：/提醒 30m 拿快递（支持s/m/h/d或秒/分钟/小时/天）；/提醒 YYYY-MM-DD HH:MM 内容；/提醒 列表；/提醒 取消 编号。'
            body=body.strip()
            if not sep or not 1<=len(body)<=300:return '提醒内容须为1—300字。'
            if not 5<=due-now<=cfg.get('max_days',365)*86400:return '时间至少在5秒后，并且不超过插件允许的天数。'
            db.execute('BEGIN IMMEDIATE')
            count=db.execute("SELECT count(*) FROM reminders WHERE scope=? AND user=? AND state='pending'",(scope,user)).fetchone()[0]
            if count>=cfg.get('per_user_limit',10):db.rollback();return '待处理提醒已达上限，请先取消旧提醒。'
            rid=uuid.uuid4().hex[:12]
            db.execute('INSERT INTO reminders VALUES(?,?,?,?,?,?,?,?)',(rid,scope,user,body,due,'pending',now,''));db.commit()
            return f'已设提醒 {rid}：{datetime.fromtimestamp(due,zone):%Y-%m-%d %H:%M:%S %z}\n{body}\n只发回当前会话。'

    async def start(self):
        if self.task is not None:return
        # Recovery records are never replayed after an uncertain send.
        try:
            with contextlib.closing(self._db()) as db:
                db.execute("UPDATE reminders SET state='unknown',note='上次发送期间服务中断，未自动重发' WHERE state='dispatching'")
                self.recover_pending=False
        except (OSError,ValueError,sqlite3.Error):self.k.plugins.errors['reminders']='提醒数据目录不可用'
        self.task=asyncio.create_task(self._loop())

    async def _loop(self):
        while True:
            try:await self.tick()
            except asyncio.CancelledError:raise
            except Exception:self.k.plugins.errors['reminders']='提醒处理失败，请核对数据目录'
            await asyncio.sleep(2)

    def _allowed(self,scope,user):
        cfg=self.k.config.data
        if user in cfg['user_blocklist']:return False
        target=scope[2:]
        if scope.startswith('g:'):
            return cfg['group_enabled'] and target not in cfg['group_blocklist'] and (not cfg['group_allowlist'] or target in cfg['group_allowlist'])
        return cfg['private_enabled'] and (not cfg['private_allowlist'] or user in cfg['private_allowlist'])

    async def tick(self,now=None):
        if (not self.k.plugins.enabled('reminders') or not getattr(self.k.config,'receive_enabled',True)
                or not self.k.transport.snapshot().get('connected')):return
        now=time.time() if now is None else now
        with contextlib.closing(self._db()) as db:
            self.k.plugins.errors.pop('reminders',None)
            if self.recover_pending:
                db.execute("UPDATE reminders SET state='unknown',note='上次发送期间服务中断，未自动重发' WHERE state='dispatching'")
                self.recover_pending=False
            rows=db.execute("SELECT id,scope,user,body,due FROM reminders WHERE state='pending' AND due<=? ORDER BY due LIMIT 5",(now,)).fetchall()
            for rid,scope,user,body,due in rows:
                if not re.fullmatch(r'[pg]:[0-9]{1,20}',scope) or not self._allowed(scope,user):
                    db.execute("UPDATE reminders SET state='paused',note='当前会话或用户不允许机器人发言' WHERE id=?",(rid,));continue
                if now-due>self.settings('reminders').get('overdue_grace_minutes',60)*60:
                    db.execute("UPDATE reminders SET state='expired',note='已超过补发宽限期' WHERE id=?",(rid,));continue
                claimed=db.execute("UPDATE reminders SET state='dispatching' WHERE id=? AND state='pending'",(rid,)).rowcount
                if not claimed:continue
                event={'event_id':'reminder:'+rid,'session_id':scope,'user_id':user,'group_id':scope[2:] if scope.startswith('g:') else '',
                       'message_type':'group' if scope.startswith('g:') else 'private','message_id':'','display_name':user,'text':'[到时提醒]',
                       'mentioned':False,'reply_to':'','time':int(now),'self_id':self.k.config.self_id,'segments':[],'is_bot':False}
                try:
                    await self.k.store.ingest_event(event)
                    sent=await self.k.reply(event,'⏰ 提醒 '+user+'：'+body)
                    db.execute('UPDATE reminders SET state=?,note=? WHERE id=?',('sent' if sent else 'unknown','' if sent else 'QQ结果未确认，未自动重试',rid))
                except asyncio.CancelledError:
                    db.execute("UPDATE reminders SET state='unknown',note='发送期间服务停止，未自动重发' WHERE id=?",(rid,));raise
                except Exception:db.execute("UPDATE reminders SET state='unknown',note='发送结果未确认，未自动重发' WHERE id=?",(rid,))

    async def close(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):await self.task
            self.task=None
