"""Portable Windows instance manager. Uses only the bundled runtime and local data."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid
from urllib.error import URLError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent.parent
EDITION = 'windows-portable-1'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    from yukino_core.config import atomic_json
    atomic_json(path, value)


@contextmanager
def lock(path):
    """OS releases this lock even if a launcher crashes. Never trusts a stale PID."""
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('Another launcher is starting or running this instance.') from None
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def settings():
    config = read_json(ROOT/'windows.json') if (ROOT/'windows.json').exists() else {}
    port = config.get('port', 18801)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('windows.json port must be an integer between 1024 and 65535.')
    return {'port': port}


def client():
    # Loopback management must not be sent through system HTTP proxies.
    return build_opener(ProxyHandler({}), HTTPCookieProcessor())


def request(opener, port, path, payload=None, csrf=None):
    headers = {'Content-Type': 'application/json'}
    if csrf:
        headers['X-CSRF-Token'] = csrf
    req = Request(f'http://127.0.0.1:{port}'+path,
                  data=json.dumps(payload).encode() if payload is not None else None,
                  headers=headers)
    with opener.open(req, timeout=3) as response:
        return json.load(response)


def running(data):
    try:
        state = read_json(data/'runtime.json')
        port, instance = state['port'], state['instance']
        if type(port) is not int or not 1024 <= port <= 65535 or not isinstance(instance,str):
            return None
        info = request(client(), port, '/windows/info')
        if info.get('instance') == instance and info.get('edition') == EDITION:
            return state
    except (OSError, ValueError, KeyError, URLError):
        pass
    return None


def initialize(data):
    private = [data/name for name in ('config.json','secrets.json','connections.json','providers.json')]
    if not any(p.exists() for p in private):
        script = ROOT/'app/scripts/init_instance.py'
        spec = importlib.util.spec_from_file_location('yukino_windows_init',script)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        module.initialize(data)
    elif not all(p.is_file() for p in private):
        raise RuntimeError('Incomplete instance configuration. Restore your backup; files were not overwritten.')
    # Never inherit a VPS token/provider/connection from the launching shell.
    for name in list(os.environ):
        if name.startswith('YUKINO_'):
            del os.environ[name]
    os.environ['YUKINO_SECRETS_FILE'] = str(data/'secrets.json')
    marker = data/'bundled-plugins.json'
    pending = data/'bundled-install-pending.json'
    pack = read_json(ROOT/'plugin-packages/manifest.json')
    previous = set(read_json(marker).get('ids',[])) if marker.exists() else set()
    if all(e['id'] in previous for e in pack['plugins']):
        return
    from yukino_core.config import Config
    from yukino_core.plugins import PluginManager
    from yukino_core.plugin_packages import install_package

    async def install():
        config = Config(data/'config.json')
        manager = PluginManager(config,data/'plugins')
        if manager.manifest_error:
            raise RuntimeError('Existing plugin manifest needs repair; no files were replaced.')
        try:
            for entry in pack['plugins']:
                if entry['id'] in previous:
                    continue
                exists = any(i['id']==entry['id'] for i in manager.items)
                own_pending = pending.exists() and read_json(pending).get('id')==entry['id']
                if exists and not own_pending:
                    previous.add(entry['id'])
                    write_json(marker,{'edition':EDITION,'ids':sorted(previous)})
                    continue
                path = ROOT/'plugin-packages'/entry['archive']
                if path.parent.resolve() != (ROOT/'plugin-packages').resolve():
                    raise ValueError('Invalid bundled package path')
                payload = path.read_bytes()
                if hashlib.sha256(payload).hexdigest()!=entry['sha256']:
                    raise ValueError('Bundled plugin checksum mismatch: '+entry['id'])
                if not exists:
                    write_json(pending,{'id':entry['id']})
                    await install_package(manager,payload)
                values = entry.get('settings',{})
                if values:
                    manager.update_settings(entry['id'],values)
                config.plugin_flags[entry['id']] = True
                config.save()
                previous.add(entry['id'])
                write_json(marker,{'edition':EDITION,'ids':sorted(previous)})
                pending.unlink(missing_ok=True)
        finally:
            await manager.close()
    asyncio.run(install())


def open_chrome(url):
    candidates = [shutil.which('chrome.exe')]
    for key in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA'):
        if os.environ.get(key):
            candidates.append(str(Path(os.environ[key])/'Google/Chrome/Application/chrome.exe'))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            subprocess.Popen([candidate,url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            return
    print('Chrome not found. Open this address in Chrome: '+url)


async def serve(data, instance, port):
    from aiohttp import web
    from yukino_core.app import make_runtime
    from yukino_core.config import Config
    stopped = asyncio.Event()
    app = await make_runtime(Config(data/'config.json'),str(data),str(data/'resources'),None)

    async def info(_):
        return web.json_response({'edition':EDITION,'instance':instance})

    async def stop(_):
        # /api/* already enforces admin login and CSRF in the core middleware.
        asyncio.get_running_loop().call_later(.2,stopped.set)
        return web.json_response({'ok':True,'data':{'stopping':True}})

    app.router.add_get('/windows/info',info)
    app.router.add_post('/api/windows/shutdown',stop)
    runner=web.AppRunner(app)
    try:
        await runner.setup()
        await web.TCPSite(runner,'127.0.0.1',port).start()
        write_json(data/'runtime.json',{'pid':os.getpid(),'instance':instance,'port':port})
        await stopped.wait()
    finally:
        await runner.cleanup()
        try:
            if read_json(data/'runtime.json').get('instance')==instance:
                (data/'runtime.json').unlink()
        except (OSError,ValueError):
            pass


def start(data, no_browser=False):
    with lock(data/'launcher.lock'):
        state=running(data)
        if not state:
            initialize(data)
            port=settings()['port']
            with socket.socket() as probe:
                try: probe.bind(('127.0.0.1',port))
                except OSError:
                    raise RuntimeError(f'Port {port} is occupied. Change windows.json; the existing process was not stopped.') from None
            instance=uuid.uuid4().hex
            logs=data/'logs';logs.mkdir(exist_ok=True)
            with (logs/'core.log').open('ab') as log:
                executable=Path(sys.executable).with_name('pythonw.exe')
                process=subprocess.Popen([str(executable),'-X','utf8',str(Path(__file__)),
                    '--data',str(data),'serve','--instance',instance,'--port',str(port)],
                    cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW)
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                state=running(data)
                if state and state['instance']==instance:
                    break
                if process.poll() is not None:
                    raise RuntimeError('Startup failed. See data/logs/core.log for details.')
                time.sleep(.2)
            else:
                raise RuntimeError('Startup is still pending. Check Status.cmd and data/logs/core.log.')
        url=f"http://localhost:{state['port']}/"
        print('Yukino Core is running: '+url)
        print('First login: use Show-Token.cmd. Stop gracefully with Stop.cmd.')
        if not no_browser:
            open_chrome(url)


def _stop(data):
    state=running(data)
    if not state:
        print('This local instance is not running.');return
    opener=client()
    token=read_json(data/'secrets.json')['admin_token']
    login=request(opener,state['port'],'/api/login',{'token':token})
    csrf=login['csrf_token']
    request(opener,state['port'],'/api/windows/shutdown',{},csrf)
    for _ in range(600):
        metadata=data/'runtime.json'
        same=metadata.exists() and read_json(metadata).get('instance')==state['instance']
        if not same:
            try:
                with lock(data/'server.lock'):
                    print('Stopped gracefully. Data and plugin state are preserved.');return
            except RuntimeError:
                pass
        time.sleep(.1)
    raise RuntimeError('Shutdown is still pending. No process was forcibly terminated.')


def stop(data):
    if not data.exists():
        print('This local instance is not running.');return
    with lock(data/'launcher.lock'):
        _stop(data)


def main():
    parser=argparse.ArgumentParser(description='Yukino Core Windows portable manager')
    parser.add_argument('--data',type=Path,default=ROOT/'data')
    sub=parser.add_subparsers(dest='command',required=True)
    start_parser=sub.add_parser('start');start_parser.add_argument('--no-browser',action='store_true')
    sub.add_parser('stop');sub.add_parser('status');sub.add_parser('token');sub.add_parser('open')
    server=sub.add_parser('serve');server.add_argument('--instance',required=True);server.add_argument('--port',type=int,required=True)
    args=parser.parse_args();data=args.data.expanduser().resolve()
    try:
        if os.name!='nt':
            raise RuntimeError('This launcher is for Windows. Use python -m yukino_core on other systems.')
        if args.command=='start':
            start(data,args.no_browser)
        elif args.command=='serve':
            with lock(data/'server.lock'):
                initialize(data)
                asyncio.run(serve(data,args.instance,args.port))
        elif args.command=='stop':
            stop(data)
        elif args.command=='status':
            state=running(data)
            print(json.dumps({'running':bool(state),'url':f"http://localhost:{state['port']}/" if state else None}))
        elif args.command=='open':
            state=running(data)
            if not state: raise RuntimeError('Use Start.cmd first.')
            open_chrome(f"http://localhost:{state['port']}/")
        else:
            if not (data/'secrets.json').is_file():raise RuntimeError('Use Start.cmd to initialize first.')
            print(read_json(data/'secrets.json')['admin_token'])
    except (OSError,ValueError,RuntimeError,KeyError) as exc:
        parser.exit(1,str(exc)+'\n')


if __name__=='__main__':
    main()
