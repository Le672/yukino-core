"""Create a disabled private instance; refuse to replace existing configuration."""
import argparse
import json
import os
from pathlib import Path
import secrets

ROOT=Path(__file__).resolve().parents[1]


def initialize(directory):
    root=Path(directory).absolute();data=root/'data'
    if any(p.is_symlink() for p in (root,*root.parents)):raise ValueError('Linked instance directory')
    if any((root/name).exists() for name in ('private.env','client.env','data')):raise FileExistsError('Instance already exists; it was not modified')
    if os.name=='posix' and os.geteuid()!=0:raise PermissionError('Run initialization with sudo on Linux to set container UID 10001')
    lock=json.loads((ROOT/'client-lock.example.json').read_text())
    cfg=json.loads((ROOT/'config.example.json').read_text());cfg.update(host='0.0.0.0',allow_network_bind=True)
    root.mkdir(parents=True,exist_ok=True);data.mkdir(mode=0o700)
    files={'private.env':'QQ_DESKTOP_TOKEN='+secrets.token_urlsafe(32)+'\n',
           'client.env':''.join(key+'='+lock[key]+'\n' for key in ('BASE_IMAGE','QQ_DEB_URL','QQ_DEB_SHA256')),
           'data/config.json':json.dumps(cfg,indent=2)+'\n'}
    for name,value in files.items():
        path=root/name;fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as output:output.write(value)
    if os.name=='posix':os.chown(data,10001,10001);os.chown(data/'config.json',10001,10001)
    return {'initialized':True,'enabled':False,'private_token_printed':False,'directory':str(root)}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--directory',type=Path,default=Path('.'));args=p.parse_args();print(json.dumps(initialize(args.directory)))
