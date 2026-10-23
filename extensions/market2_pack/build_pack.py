"""Build original Yukino workers inspired by publicly listed market features."""
import argparse,hashlib,json,sys,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parents[1]))
from yukino_core.plugin_packages import inspect_package

NETWORK={'m_rss','m_bangumi','m_abbr','m_webtext','m_bing'}
EXPECTED=NETWORK|{'m_ics','m_mahjong','m_uno'}
HELPERS={'m_ics':'ics_engine.py','m_mahjong':'mahjong_engine.py','m_uno':'uno_engine.py'}

def archive(path,files):
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as result:
        for name,payload in sorted(files.items()):
            item=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));item.compress_type=zipfile.ZIP_DEFLATED
            item.external_attr=0o100644<<16;result.writestr(item,payload)

def build(output):
    output.mkdir(parents=True,exist_ok=True)
    catalog=[]
    for name in ('network_catalog.json','local_catalog.json','uno_catalog.json'):
        data=json.loads((ROOT/name).read_text(encoding='utf-8'))
        catalog.extend(data if isinstance(data,list) else data['plugins'])
    assert {p['id'] for p in catalog}==EXPECTED and len(catalog)==8
    commands=set();entries=[]
    for item in sorted(catalog,key=lambda x:x['id']):
        pid=item['id'];folder=ROOT/'plugins'/pid;files={}
        for p in folder.iterdir():
            if p.is_file() and p.suffix in ('.py','.json','.md','.txt'):files[p.name]=p.read_bytes()
        if pid in NETWORK:
            files['_network.py']=(ROOT/'network_engine.py').read_bytes()
            files['_safe_net.py']=(ROOT/'safe_net.py').read_bytes()
        else:files['_engine.py']=(ROOT/HELPERS[pid]).read_bytes()
        files['LICENSE']=(ROOT/'LICENSE').read_bytes()
        files['NOTICE']=(ROOT/'SOURCES.md').read_bytes()
        settings=item.get('settings',{'timeout':20 if pid in NETWORK else 6,'input_max_chars':4000,'output_max_chars':6000})
        files['recommended_settings.json']=(json.dumps(settings,ensure_ascii=False,indent=2)+'\n').encode()
        for name,payload in files.items():
            payload.decode('utf-8-sig')
            if name.endswith('.py'):compile(payload,pid+'/'+name,'exec')
        dest=output/(pid+'-'+json.loads(files['plugin.json'])['version']+'.zip');archive(dest,files)
        record,_=inspect_package(dest.read_bytes());assert record['id']==pid
        assert not commands.intersection(record['commands']),'Duplicate command'
        commands.update(record['commands'])
        entry={**item,'archive':dest.name,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest(),'settings':settings}
        entries.append(entry)
        (dest.with_name(dest.name+'.sha256')).write_text(entry['sha256']+'  '+dest.name+'\n',encoding='ascii')
    # The source archive has its own allowlist; no state, credentials or network downloads.
    files={}
    for path in ROOT.rglob('*'):
        rel=path.relative_to(ROOT)
        if not path.is_file() or path.is_symlink() or any(p.startswith('.') or p=='__pycache__' for p in rel.parts):continue
        if path.suffix not in ('.py','.json','.md','.txt') and path.name not in ('LICENSE','NOTICE'):continue
        if path.stat().st_size>500_000:raise ValueError('Unexpected source file')
        files['market2_pack/'+rel.as_posix()]=path.read_bytes()
    source=output/'yukino-market2-pack-1.3.0-source.zip';archive(source,files)
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    (source.with_name(source.name+'.sha256')).write_text(source_hash+'  '+source.name+'\n',encoding='ascii')
    manifest={'version':'1.3.0','minimum_core_version':'0.10.0','plugin_count':8,'source_archive':source.name,
              'source_sha256':source_hash,'plugins':entries,'upstream_code_included':False}
    (output/'pack-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'plugins':8,'source_sha256':source_hash,'output':str(output)}))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path)
    build(parser.parse_args().output)
