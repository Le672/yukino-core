"""Optional host-configured local broker; no browser credential extraction."""
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request

def query(path,params):
    filename=os.environ.get('YUKINO_BILIBILI_BROKER_SETTINGS','')
    if not filename:return None
    kinds={'/x/web-interface/search/type':'search','/x/web-interface/popular':'popular'}
    if path not in kinds:return None
    config_path=Path(filename)
    if not config_path.is_absolute() or not 0<config_path.stat().st_size<=8192:raise ValueError('Invalid local broker configuration')
    cfg=json.loads(config_path.read_text());url=cfg['url'];parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='http' or parsed.hostname not in ('video-plugin','yukino-video-plugin','127.0.0.1','localhost') or parsed.port!=17831 or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ValueError('Local broker destination is not allowed')
    body={'kind':kinds[path]}
    if body['kind']=='search':body['keyword']=str((params or {}).get('keyword',''))[:80]
    request=urllib.request.Request(url.rstrip('/')+'/bilibili/query',data=json.dumps(body).encode(),
        headers={'Content-Type':'application/json','Authorization':'Bearer '+cfg['token']})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect()).open(request,timeout=12) as response:
        raw=response.read(2_000_001)
    if len(raw)>2_000_000:raise ValueError('Bilibili query response too large')
    result=json.loads(raw)
    if not isinstance(result,dict):raise ValueError('Invalid Bilibili response')
    return result

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('Local broker redirects are not allowed')
