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
from .media import MediaStore,image_extension
from .config import numeric


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
        self.media=MediaStore(Path(directory)/'media')
        self.lock=asyncio.Lock()
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='qq-desktop-ui')
        self.seeded=set(); self.next_binding=0; self.last_send=0
        self.last_error='';self.started=time.time()
        self.metrics={'polls':0,'events':0,'poke_events':0,'sends_confirmed':0,'sends_uncertain':0}
        self.connections={'api':0,'events':0};self.last_poll_at=None;self.last_event_at=None

    async def ui(self,method,*args):
        return await asyncio.get_running_loop().run_in_executor(self.executor,getattr(self.driver,method),*args)

    async def available(self):
        # Some native clients only expose account IDs in a transient profile.
        # Their operation performs an active numeric check; status stays read-only.
        return await self.ui('available' if hasattr(self.driver,'available') else 'ready')

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
        media=None
        if isinstance(message,list) and len(message)==1 and isinstance(message[0],dict) and message[0].get('type') in ('image','file','video'):
            media=message[0];data=media.get('data')
            if not isinstance(data,dict) or not isinstance(data.get('file'),str) or not set(data)<={'file','name'}:raise ValueError('Invalid attachment data')
            text=None
        else:text=plain_message(message)
        binding=self.binding(kind,target)
        if key is not None and (not isinstance(key,str) or not 1<=len(key)<=128): raise ValueError('Invalid idempotency key')
        key=key or str(uuid.uuid4())
        async with self.lock:
            if not self.config['enabled'] or not await self.available(): raise UiError('QQ is offline or not calibrated')
            if media:
                m=binding.get('attachments',{})
                if not m.get('verified') or media['type'] not in m.get('kinds',[]):raise UiError('Attachment flow has not been calibrated for this conversation')
                record=await asyncio.to_thread(self.media.prepare,media['data']['file'],media['type'],media['data'].get('name'))
                content=[media['type'],record['id']]
            else:content=text
            digest=hashlib.sha256(json.dumps([kind,str(target),content],ensure_ascii=False).encode()).hexdigest()
            old=self.store.lookup(key,digest)
            if old is not None:return {'message_id':old,'ui_confirmed':True,'reused':True}
            if time.monotonic()-self.last_send<self.config['send_interval_seconds']:
                raise ValueError('Desktop send cooldown is active')
            self.store.reserve(key,digest)
            self.last_send=time.monotonic()
            try:rowkey=await self.ui('send_attachment',binding,record) if media else await self.ui('send',binding,text)
            except UiUncertain:
                self.metrics['sends_uncertain']+=1
                raise
            mid,_=self.store.observe(kind+':'+str(target),'out:'+rowkey)
            self.store.complete(key,mid)
            self.metrics['sends_confirmed']+=1
            return {'message_id':mid,'ui_confirmed':True,'recipient_receipt_verified':False}

    async def poll(self):
        if not self.config['enabled'] or not self.config['bindings']:return []
        async with self.lock:
            if not await self.available():return []
            binding=self.config['bindings'][self.next_binding%len(self.config['bindings'])]
            self.next_binding+=1
            rows=await self.ui('snapshot',binding)
            self.metrics['polls']+=1;self.last_poll_at=int(time.time())
            scope=binding['kind']+':'+binding['id']; first=scope not in self.seeded; result=[]
            for row in rows:
                mid,new=self.store.observe(scope,row['key'])
                if first or not new or row['user_id']==self.config['self_id']:continue
                if row.get('notice'):
                    if row['notice']!='poke' or not binding.get('notices',{}).get('verified'):
                        raise UiError('System notice flow has not been calibrated')
                    actor=numeric(row['user_id']);target=numeric(row['target_id'])
                    if binding['kind']=='private' and any(v not in (binding['id'],self.config['self_id']) for v in (actor,target)):
                        raise UiError('Poke identities do not belong to the verified private conversation')
                    event={'time':int(time.time()),'self_id':int(self.config['self_id']),'post_type':'notice',
                           'notice_type':'notify','sub_type':'poke','user_id':int(actor),'target_id':int(target),'ui_observed':True}
                    if binding['kind']=='group':event['group_id']=int(binding['id'])
                    result.append(event);self.metrics['poke_events']+=1
                    continue
                segments=[{'type':'text','data':{'text':row['text']}}] if row['text'] else []
                attachment=row.get('attachment');attachment_state=None
                if attachment:
                    attachment_state={'kind':attachment['kind'],'name':attachment['name'],'download_available':False}
                    try:
                        record=await asyncio.to_thread(self.media.observed,attachment.get('path'),attachment['kind'])
                        segments.append({'type':attachment['kind'],'data':{'file':record['id'],'name':record['name'],'size':record['size'],'ui_local_media':True}})
                        attachment_state.update(download_available=True,media_id=record['id'])
                    except (ValueError,OSError):
                        label={'image':'图片','file':'文件','video':'视频'}.get(attachment['kind'],'附件')
                        segments.append({'type':'text','data':{'text':'['+label+'：'+attachment['name']+'，未读取到已下载文件]'}})
                e={'time':int(time.time()),'self_id':int(self.config['self_id']),'post_type':'message',
                   'message_type':binding['kind'],'sub_type':'friend' if binding['kind']=='private' else 'normal',
                   'message_id':mid,'user_id':int(row['user_id']),'message':segments,
                   'raw_message':row['text'],'font':0,'sender':{'user_id':int(row['user_id'])},'ui_observed':True}
                if binding['kind']=='group':e['group_id']=int(binding['id'])
                if attachment_state:e['ui_attachment']=attachment_state
                result.append(e)
            self.seeded.add(scope)
            self.metrics['events']+=len(result)
            if result:self.last_event_at=int(time.time())
            return result

    async def capabilities(self):
        status=await self.status();online=status['online']
        native=self.config.get('backend')=='qqnt_private'
        flows=[b for b in self.config['bindings'] if b['verified'] and b.get('attachments',{}).get('verified')]
        return {'experimental':True,'version':__version__,'backend':self.config.get('backend','generic'),'status':status,
            'text':{'implemented':True,'ready':online},
            'image':{'implemented':not native,'ready':online and any('image' in b['attachments']['kinds'] for b in flows)},
            'file':{'implemented':not native,'ready':online and any('file' in b['attachments']['kinds'] for b in flows)},
            'video':{'implemented':not native,'ready':online and any('video' in b['attachments']['kinds'] for b in flows),'formats':['mp4'],'max_bytes':8*1024*1024},
            'poke_receive':{'implemented':True,'ready':online and any(b['verified'] and b.get('notices',{}).get('verified') for b in self.config['bindings'])},
            'receive_media':{'implemented':not native,'requires':'genuine row fields and downloaded file under media/inbox'},
            'unsupported':['record','forward','recall','poke_send','group_admin']+(['group','image','file','video'] if native else []),
            'real_qq_roundtrip_verified':False,'uptime_seconds':int(time.time()-self.started),'metrics':dict(self.metrics)}

    async def diagnostics(self):
        async with self.lock:desktop=await self.ui('diagnose')
        bindings=self.config['bindings'];verified=sum(b['verified'] for b in bindings)
        checks=[{'code':'enabled','ok':self.config['enabled'],'message':'已启用自动收发' if self.config['enabled'] else '自动收发已暂停',
                 'next_step':'' if self.config['enabled'] else '先完成本人身份及会话核验，再启用。'},
                {'code':'bindings','ok':bool(bindings) and verified==len(bindings),
                 'message':f'已核验会话 {verified} / {len(bindings)}',
                 'next_step':'' if bindings and verified==len(bindings) else '在目标会话中校准独立的数字身份字段。'},
                {'code':'event_subscribers','ok':self.connections['events']>0,
                 'message':f'OneBot 事件连接 {self.connections["events"]} 个',
                 'next_step':'' if self.connections['events'] else '机器人内核需连接事件端点，接口连接本身不会订阅消息。'}]
        return {**desktop,'checks':checks+desktop['checks'],'connections':dict(self.connections),
                'last_poll_at':self.last_poll_at,'last_event_at':self.last_event_at,
                'last_error':self.last_error,'no_navigation':True,'no_qq_message_sent':True,
                'real_qq_roundtrip_verified':False}

    async def action(self,name,params):
        if name=='get_status':return await self.status()
        if name=='get_version_info':return {'app_name':'yukino-qq-desktop','app_version':__version__,'protocol_version':'v11','experimental':True}
        if name=='get_capabilities':return await self.capabilities()
        if name=='get_desktop_diagnostics':return await self.diagnostics()
        if name=='can_send_image':return {'yes':(await self.capabilities())['image']['ready']}
        if name=='can_send_record':return {'yes':False}
        if name=='get_image':
            record=await asyncio.to_thread(self.media.get,params.get('file'))
            image_extension(Path(record['path']).read_bytes())
            return {'file':record['path'],'size':record['size'],'filename':record['name'],
                    'media_id':record['id'],'download_endpoint':'/console/api/media/'+record['id'],'requires_bearer_auth':True}
        if name in ('upload_private_file','upload_group_file'):
            if not set(params)<={'file','name','user_id','group_id','idempotency_key'}:raise NotImplementedError('File folders and extra upload options are not supported')
            kind='private' if name=='upload_private_file' else 'group'
            return await self.send(kind,params.get('user_id' if kind=='private' else 'group_id'),
                [{'type':'file','data':{k:params[k] for k in ('file','name') if k in params}}],params.get('idempotency_key'))
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
