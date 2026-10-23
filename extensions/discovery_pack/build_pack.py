"""Build seven independently installable, text-only source plugins."""
import argparse,hashlib,json,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT.parents[1]))
from yukino_core.plugin_packages import inspect_package

MODULES={'d_images':('common.py','images.py'),'d_memes':('common.py','graphics.py'),'d_qrcode':('common.py','graphics.py','vendor/qrcodegen.py','vendor/provenance.json'),
         **{p:('common.py','network.py') for p in ('d_epic','d_projects','d_dictionary','d_air')}}

def archive(path,files):
    with zipfile.ZipFile(path,'x',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,payload in sorted(files.items()):
            entry=zipfile.ZipInfo(name,(1980,1,1,0,0,0));entry.compress_type=zipfile.ZIP_DEFLATED;entry.external_attr=0o100644<<16;z.writestr(entry,payload)

def build(output):
    output.mkdir(parents=True,exist_ok=True);entries=[];commands=set()
    for pid,modules in sorted(MODULES.items()):
        folder=ROOT/'plugins'/pid;files={n:(folder/n).read_bytes().replace(b'\r\n',b'\n') for n in ('plugin.json','worker.py','README.md')}
        for name in modules:files[Path(name).name]=(ROOT/name).read_bytes().replace(b'\r\n',b'\n')
        files.update(LICENSE=(ROOT/'LICENSE').read_bytes(),NOTICE=(ROOT/'SOURCES.md').read_bytes())
        for name,raw in files.items():
            raw.decode('utf-8-sig')
            if name.endswith('.py'):compile(raw,name,'exec')
        meta=json.loads(files['plugin.json']);path=output/(pid+'-'+meta['version']+'.zip');archive(path,files);payload=path.read_bytes();record,checked=inspect_package(payload)
        assert not commands.intersection(record['commands']);commands.update(record['commands'])
        recommended={'timeout':30,'output_max_chars':8000,'online_enabled':True,'network_seconds':15,'cache_seconds':300}
        if pid=='d_images':recommended.pop('cache_seconds')
        if pid in ('d_memes','d_qrcode'):recommended={'timeout':12,'output_max_chars':4000}
        entries.append({k:record[k] for k in ('id','name','commands')}|dict(archive=path.name,sha256=hashlib.sha256(payload).hexdigest(),bytes=len(payload),settings=recommended))
        path.with_name(path.name+'.sha256').write_text(entries[-1]['sha256']+'  '+path.name+'\n',encoding='ascii')
    manifest=dict(version='0.1.0',minimum_core_version='0.11.0',plugin_count=len(entries),plugins=entries,credentials_included=False)
    (output/'pack-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    archive(output/'yukino-discovery-7-0.1.0.zip',{'manifest.json':json.dumps(manifest,ensure_ascii=False,indent=2).encode(),**{'plugins/'+r['archive']:(output/r['archive']).read_bytes() for r in entries}})
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);m=build(p.parse_args().output);print(json.dumps({'plugins':m['plugin_count'],'version':m['version']}))
