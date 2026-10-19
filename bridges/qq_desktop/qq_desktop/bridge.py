import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .atspi import UiError, UiUncertain
from . import __version__


class Store:
    """Hashes and local IDs only; no plaintext chat archive or QQ credentials."""
    def __init__(self, directory):
        root = Path(directory).absolute()
        if any(p.is_symlink() for p in (root, *root.parents)): raise ValueError('Linked state directory')
        root.mkdir(parents=True, exist_ok=True)
        path = root / 'bridge.sqlite3'
        for p in (path, Path(str(path)+'-wal'), Path(str(path)+'-shm')):
            if p.is_symlink() or (p.exists() and (p.stat().st_nlink > 1 or p.stat().st_size > 16*1024*1024)):
                raise ValueError('Invalid or oversized bridge state')
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA max_page_count=4096')
        self.db.execute('CREATE TABLE IF NOT EXISTS seen(scope TEXT,key TEXT,id INTEGER PRIMARY KEY AUTOINCREMENT,UNIQUE(scope,key))')
        self.db.execute('CREATE TABLE IF NOT EXISTS sends(key TEXT PRIMARY KEY,digest TEXT,status TEXT,id INTEGER,at REAL)')
        self.db.commit()

    def observe(self, scope, key):
        old = self.db.execute('SELECT id FROM seen WHERE scope=? AND key=?', (scope,key)).fetchone()
        if old: return old[0], False
        with self.db:
            row = self.db.execute('INSERT INTO seen(scope,key) VALUES(?,?)', (scope,key))
            self.db.execute('DELETE FROM seen WHERE id IN (SELECT id FROM seen ORDER BY id DESC LIMIT -1 OFFSET 12000)')
        return row.lastrowid, True

    def lookup(self, key, digest):
        row = self.db.execute('SELECT digest,status,id FROM sends WHERE key=?', (key,)).fetchone()
        if row:
            if row[0] != digest: raise ValueError('Idempotency key already used for different content')
            if row[1] == 'confirmed': return row[2]
            raise UiUncertain('Previous attempt did not have a confirmed result; it was not resent')
        return None

    def reserve(self, key, digest):
        old=self.lookup(key,digest)
        if old is not None:return old
        with self.db:
            self.db.execute('DELETE FROM sends WHERE at<?', (time.time()-7*86400,))
            if self.db.execute('SELECT count(*) FROM sends').fetchone()[0] >= 2000:
                raise ValueError('Send journal is full')
            self.db.execute('INSERT INTO sends VALUES(?,?,?,NULL,?)', (key,digest,'pending',time.time()))
        return None

    def complete(self, key, mid):
        with self.db: self.db.execute("UPDATE sends SET status='confirmed',id=? WHERE key=?", (mid,key))

    def close(self): self.db.close()


def plain_message(value):
    if isinstance(value, list):
        if not value or len(value)>30: raise ValueError('Expected text segments')
        parts=[]
        for segment in value:
            if not isinstance(segment,dict) or segment.get('type')!='text' or not isinstance(segment.get('data'),dict) or not isinstance(segment['data'].get('text'),str):
                raise ValueError('This experimental bridge supports text only')
            parts.append(segment['data']['text'])
        value=''.join(parts)
    if not isinstance(value,str) or not value.strip() or len(value)>2000 or '[CQ:' in value or any(ord(c)<32 and c not in '\n\t' for c in value):
        raise ValueError('Expected 1..2000 plain-text characters')
    return value


class Bridge:
    def __init__(self,config,driver,directory):
        self.config,self.driver=config,driver
        self.store=Store(directory)
        self.lock=asyncio.Lock()
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='qq-desktop-ui')
        self.seeded=set(); self.next_binding=0; self.last_send=0
        self.last_error=''; self.events=[]

    async def ui(self,method,*args):
        return await asyncio.get_running_loop().run_in_executor(self.executor,getattr(self.driver,method),*args)

    async def status(self):
        if not self.config['enabled']: return {'online':False,'good':False,'reason':'disabled_until_calibrated'}
        async with self.lock:
            online=bool(await self.ui('ready'))
        return {'online':online,'good':online,'reason':'ready' if online else 'login_or_calibration_required'}

    def binding(self,kind,target):
        target=str(target)
        found=[b for b in self.config['bindings'] if b['kind']==kind and b['id']==target and b['verified']]
        if len(found)!=1: raise ValueError('Conversation has no unique verified desktop binding')
        return found[0]

    async def send(self,kind,target,message,key=None):
        text=plain_message(message)
        binding=self.binding(kind,target)
        if key is not None and (not isinstance(key,str) or not 1<=len(key)<=128): raise ValueError('Invalid idempotency key')
        key=key or str(uuid.uuid4())
        digest=hashlib.sha256(json.dumps([kind,str(target),text],ensure_ascii=False).encode()).hexdigest()
        async with self.lock:
            if not self.config['enabled'] or not await self.ui('ready'): raise UiError('QQ is offline or not calibrated')
            old=self.store.lookup(key,digest)
            if old is not None:return {'message_id':old,'ui_confirmed':True,'reused':True}
            if time.monotonic()-self.last_send<self.config['send_interval_seconds']:
                raise ValueError('Desktop send cooldown is active')
            self.store.reserve(key,digest)
            self.last_send=time.monotonic()
            rowkey=await self.ui('send',binding,text)
            mid,_=self.store.observe(kind+':'+str(target),'out:'+rowkey)
            self.store.complete(key,mid)
            return {'message_id':mid,'ui_confirmed':True,'recipient_receipt_verified':False}

    async def poll(self):
        if not self.config['enabled'] or not self.config['bindings']:return []
        async with self.lock:
            if not await self.ui('ready'):return []
            binding=self.config['bindings'][self.next_binding%len(self.config['bindings'])]
            self.next_binding+=1
            rows=await self.ui('snapshot',binding)
            scope=binding['kind']+':'+binding['id']; first=scope not in self.seeded; result=[]
            for row in rows:
                mid,new=self.store.observe(scope,row['key'])
                if first or not new or row['user_id']==self.config['self_id']:continue
                e={'time':int(time.time()),'self_id':int(self.config['self_id']),'post_type':'message',
                   'message_type':binding['kind'],'sub_type':'friend' if binding['kind']=='private' else 'normal',
                   'message_id':mid,'user_id':int(row['user_id']),'message':[{'type':'text','data':{'text':row['text']}}],
                   'raw_message':row['text'],'font':0,'sender':{'user_id':int(row['user_id'])},'ui_observed':True}
                if binding['kind']=='group':e['group_id']=int(binding['id'])
                result.append(e)
            self.seeded.add(scope)
            return result

    async def action(self,name,params):
        if name=='get_status':return await self.status()
        if name=='get_version_info':return {'app_name':'yukino-qq-desktop','app_version':__version__,'protocol_version':'v11','experimental':True}
        if name in ('can_send_image','can_send_record'):return {'yes':False}
        if name=='get_login_info':
            if not (await self.status())['online']:raise UiError('QQ login/account identity has not been verified')
            return {'user_id':int(self.config['self_id']),'nickname':self.config.get('nickname','')}
        if name=='get_friend_list':return [{'user_id':int(b['id']),'nickname':b.get('label',''),'remark':b.get('label','')} for b in self.config['bindings'] if b['kind']=='private' and b['verified']]
        if name=='get_group_list':return [{'group_id':int(b['id']),'group_name':b.get('label','')} for b in self.config['bindings'] if b['kind']=='group' and b['verified']]
        if name=='send_msg':
            kind=params.get('message_type') or ('group' if 'group_id' in params else 'private')
            if kind not in ('private','group'):raise ValueError('Invalid message_type')
        elif name in ('send_private_msg','send_group_msg'):kind='private' if name=='send_private_msg' else 'group'
        else:raise NotImplementedError('Action is not supported by the desktop bridge')
        return await self.send(kind,params.get('user_id' if kind=='private' else 'group_id'),params.get('message'),params.get('idempotency_key'))

    async def close(self):
        self.executor.shutdown(wait=True,cancel_futures=True); self.store.close()
