import argparse
import json
from pathlib import Path
from .config import load,token_from_environment


def main():
    p=argparse.ArgumentParser(description='Experimental official-Linux-QQ desktop bridge')
    p.add_argument('command',choices=('serve','inspect','applications','check-config'))
    p.add_argument('--config',type=Path,default=Path('config.json'))
    p.add_argument('--data',type=Path,default=Path('data'))
    p.add_argument('--output',type=Path)
    args=p.parse_args();cfg=load(args.config)
    if args.command=='check-config':print(json.dumps({'valid':True,'enabled':cfg['enabled'],'bindings':len(cfg['bindings'])}));return
    from .atspi import AtspiDesktop
    if args.command=='applications':
        import gi
        gi.require_version('Atspi','2.0')
        from gi.repository import Atspi
        Atspi.init();desktop=Atspi.get_desktop(0)
        print(json.dumps([desktop.get_child_at_index(i).get_name() for i in range(min(desktop.get_child_count(),200))],ensure_ascii=False));return
    driver=AtspiDesktop(cfg)
    if args.command=='inspect':
        if not args.output:p.error('inspect requires a private --output file; it may contain visible chat text')
        value=driver.inspect();args.output.parent.mkdir(parents=True,exist_ok=True)
        # Exclusive create avoids overwriting another artifact or following links.
        import os
        fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)
        print(json.dumps({'nodes':len(value),'private_output':str(args.output)}));return
    from aiohttp import web
    from .bridge import Bridge
    from .server import create_app
    web.run_app(create_app(Bridge(cfg,driver,args.data),token_from_environment(),args.config),host=cfg['host'],port=cfg['port'],access_log=None,print=None)


if __name__=='__main__':main()
