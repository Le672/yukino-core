"""Authenticated calibration UI. Inspection never navigates or sends messages."""
import asyncio
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import time
from aiohttp import web
from .config import validate
from .atspi import UiError
from .media import LIMIT

STATIC=Path(__file__).with_name('static')


def revision(config):
    return hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()


class Calibration:
    def __init__(self,bridge,path=None):self.bridge=bridge;self.path=Path(path) if path else None

    def persist(self,cfg):
        if self.path is None:raise ValueError('No writable config path was configured for this instance')
        p=self.path
        if p.is_symlink() or any(q.is_symlink() for q in p.parents) or (p.exists() and p.stat().st_nlink!=1):raise ValueError('Linked config path')
        raw=(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n').encode()
        if len(raw)>131072:raise ValueError('Configuration exceeds 128 KiB')
        # Keep a private recoverable previous copy, without a public download route.
        if p.exists():
            old=p.read_bytes()
            backup=p.with_name(p.name+'.previous')
            if backup.is_symlink() or (backup.exists() and backup.stat().st_nlink!=1):raise ValueError('Linked config backup')
            fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(fd,'wb') as f:f.write(old)
        fd,name=tempfile.mkstemp(prefix='.config-',dir=p.parent)
        try:
            with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            os.replace(name,p)
        finally:
            if os.path.exists(name):os.unlink(name)

    def install(self,cfg):
        self.persist(cfg);self.bridge.config=cfg;self.bridge.driver.config=cfg
        self.bridge.seeded.clear();self.bridge.next_binding=0;self.bridge.last_error=''

    def match(self,expected):
        if not isinstance(expected,str) or not hmac.compare_digest(revision(self.bridge.config),expected):
            raise web.HTTPConflict(text='Configuration changed; reload before saving')

    async def draft(self,raw,expected):
        async with self.bridge.lock:
            self.match(expected)
            if not isinstance(raw,dict):raise ValueError('Expected config object')
            cfg=copy.deepcopy(raw);cfg['enabled']=False
            if cfg.get('backend','generic')!=self.bridge.config.get('backend','generic'):
                raise ValueError('Changing the desktop backend requires a service restart')
            for k in ('host','port','allow_network_bind'):
                if k in self.bridge.config:cfg[k]=self.bridge.config[k]
                else:cfg.pop(k,None)
            for b in cfg.get('bindings',[]):
                b['verified']=False
                if 'attachments' in b:b['attachments']['verified']=False
                if 'notices' in b:b['notices']['verified']=False
            cfg=validate(cfg);self.install(cfg)
            return {'config':cfg,'revision':revision(cfg),'saved_disabled':True}

    async def verify(self,index,expected,attachments_reviewed=False,notices_reviewed=False):
        async with self.bridge.lock:
            self.match(expected);cfg=copy.deepcopy(self.bridge.config)
            if type(index) is not int or not 0<=index<len(cfg['bindings']):raise ValueError('Select a valid conversation index')
            b=cfg['bindings'][index]
            if 'notices' in b:b['notices']['verified']=notices_reviewed is True
            result=await self.bridge.ui('probe',cfg,b)
            b['verified']=True
            if 'attachments' in b:b['attachments']['verified']=attachments_reviewed is True
            cfg=validate(cfg);self.install(cfg)
            return {'checks':result,'config':cfg,'revision':revision(cfg),'no_qq_message_sent':True}

    async def state(self,enabled,expected):
        if type(enabled) is not bool:raise ValueError('enabled must be boolean')
        async with self.bridge.lock:
            self.match(expected);cfg=validate({**copy.deepcopy(self.bridge.config),'enabled':enabled})
            if enabled:await self.bridge.ui('probe',cfg)
            self.install(cfg)
            return {'config':cfg,'revision':revision(cfg)}


def install_console(app,bridge,token,config_path=None):
    controller=Calibration(bridge,config_path)
    def auth(request):
        if not hmac.compare_digest(request.headers.get('Authorization','').encode(),('Bearer '+token).encode()):raise web.HTTPUnauthorized()
        origin=request.headers.get('Origin')
        if origin and origin!=request.scheme+'://'+request.host:raise web.HTTPForbidden(text='Foreign browser origin')
        if request.method not in ('GET','HEAD') and request.headers.get('X-Desktop-Console')!='1':raise web.HTTPForbidden(text='Missing console request header')

    async def api(request):
        auth(request);name=request.match_info['action']
        try:
            if request.method=='GET':
                if name=='status':return web.json_response({'capabilities':await bridge.capabilities(),'last_error':bridge.last_error,'media':await asyncio.to_thread(bridge.media.stats)})
                if name=='diagnostics':return web.json_response(await bridge.diagnostics())
                if name=='config':return web.json_response({'config':bridge.config,'revision':revision(bridge.config),'writable':config_path is not None})
                if name=='inspect':
                    async with bridge.lock:tree=await bridge.ui('inspect')
                    return web.json_response({'nodes':tree,'private_visible_ui':True,'no_qq_message_sent':True})
            else:
                value=await request.json()
                if not isinstance(value,dict):raise ValueError('Expected object')
                rev=request.headers.get('X-Config-Revision')
                if name=='config':data=await controller.draft(value,rev)
                elif name=='verify':data=await controller.verify(value.get('index'),rev,value.get('attachments_reviewed',False),value.get('notices_reviewed',False))
                elif name=='state':data=await controller.state(value.get('enabled'),rev)
                else:raise web.HTTPNotFound()
                return web.json_response(data)
            raise web.HTTPNotFound()
        except (ValueError,KeyError,TypeError,json.JSONDecodeError) as e:raise web.HTTPBadRequest(text=str(e)[:200]) from None
        except UiError as e:raise web.HTTPConflict(text=str(e)[:200]) from None

    async def upload(request):
        auth(request)
        if request.content_type!='application/octet-stream':raise web.HTTPUnsupportedMediaType()
        chunks=[];total=0
        async for chunk in request.content.iter_chunked(65536):
            total+=len(chunk)
            if total>LIMIT:raise web.HTTPRequestEntityTooLarge(max_size=LIMIT,actual_size=total)
            chunks.append(chunk)
        try:record=await asyncio.to_thread(bridge.media.ingest,b''.join(chunks),request.query.get('kind','file'),request.query.get('name'))
        except ValueError as e:raise web.HTTPBadRequest(text=str(e)[:200]) from None
        return web.json_response({k:v for k,v in record.items() if k!='path'})

    async def download(request):
        auth(request)
        try:record=await asyncio.to_thread(bridge.media.get,request.match_info['key'])
        except ValueError:raise web.HTTPNotFound() from None
        return web.FileResponse(record['path'],headers={'Content-Disposition':"attachment; filename*=UTF-8''"+__import__('urllib.parse',fromlist=['quote']).quote(record['name']),
            'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'"})

    async def page(request):
        name=request.match_info.get('name') or 'index.html'
        if name not in ('index.html','console.js','console.css'):raise web.HTTPNotFound()
        return web.FileResponse(STATIC/name,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"})

    app.router.add_get('/console',page)
    app.router.add_get('/console/',page)
    app.router.add_get('/console/static/{name}',page)
    app.router.add_get('/console/api/media/{key:.+}',download)
    app.router.add_post('/console/api/upload',upload)
    app.router.add_route('GET','/console/api/{action}',api)
    app.router.add_route('POST','/console/api/{action}',api)
    app.router.add_route('PUT','/console/api/{action}',api)
