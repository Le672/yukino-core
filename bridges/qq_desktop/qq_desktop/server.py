import asyncio
import contextlib
import hmac
import json
import time
from aiohttp import web,WSMsgType
from .atspi import UiError,UiUncertain


def create_app(bridge,token):
    sockets={}; app=web.Application(client_max_size=65536)
    def authorized(request):
        return hmac.compare_digest(request.headers.get('Authorization','').encode(),('Bearer '+token).encode())

    async def health(request):
        return web.json_response({'service':'yukino-qq-desktop','experimental':True,'enabled':bridge.config['enabled']})

    async def ws(request):
        if not authorized(request):raise web.HTTPUnauthorized()
        if request.headers.get('Origin'):raise web.HTTPForbidden(text='Browser origins are not accepted')
        if len(sockets)>=4:raise web.HTTPTooManyRequests()
        socket=web.WebSocketResponse(heartbeat=30,max_msg_size=65536);await socket.prepare(request)
        sockets[socket]=request.path!='/api'
        if sockets[socket]:
            await socket.send_json({'time':int(time.time()),'self_id':int(bridge.config.get('self_id','0')),'post_type':'meta_event','meta_event_type':'lifecycle','sub_type':'connect'})
        try:
            async for message in socket:
                if message.type!=WSMsgType.TEXT:continue
                echo=None
                try:
                    value=json.loads(message.data)
                    if not isinstance(value,dict):raise ValueError('Expected action object')
                    echo=value.get('echo')
                    if len(json.dumps(echo))>2048:raise ValueError('Echo is too large')
                    name=value.get('action');params=value.get('params',{})
                    if request.path=='/event':raise ValueError('Event-only endpoint')
                    if not isinstance(name,str) or len(name)>80 or not isinstance(params,dict):raise ValueError('Invalid action or params')
                    data=await bridge.action(name,params)
                    response={'status':'ok','retcode':0,'data':data,'echo':echo}
                except UiUncertain as error:response={'status':'failed','retcode':1500,'data':None,'echo':echo,'wording':str(error),'ui_uncertain':True}
                except NotImplementedError:response={'status':'failed','retcode':1404,'data':None,'echo':echo,'wording':'Unsupported action; see desktop capability list'}
                except (ValueError,TypeError,KeyError) as error:response={'status':'failed','retcode':1400,'data':None,'echo':echo,'wording':str(error)[:200]}
                except UiError as error:response={'status':'failed','retcode':1503,'data':None,'echo':echo,'wording':str(error)[:200]}
                except Exception:response={'status':'failed','retcode':1500,'data':None,'echo':echo,'wording':'Desktop adapter failed; no automatic retry'}
                await socket.send_json(response)
        finally:sockets.pop(socket,None)
        return socket

    async def poller():
        while True:
            if any(sockets.values()):
                try:
                    events=await bridge.poll()
                    for event in events:
                        for socket,wants_events in list(sockets.items()):
                            if wants_events and not socket.closed:
                                try:await socket.send_json(event)
                                except (ConnectionError,RuntimeError):pass
                    bridge.last_error=''
                except Exception as error:bridge.last_error=type(error).__name__
            await asyncio.sleep(bridge.config['poll_seconds'])

    async def start(app):app['poll_task']=asyncio.create_task(poller())
    async def stop(app):
        app['poll_task'].cancel()
        with contextlib.suppress(asyncio.CancelledError):await app['poll_task']
        for socket in list(sockets):await socket.close()
        await bridge.close()
    app.router.add_get('/healthz',health)
    for path in ('/','/api','/event'):app.router.add_get(path,ws)
    app.on_startup.append(start);app.on_cleanup.append(stop)
    return app
