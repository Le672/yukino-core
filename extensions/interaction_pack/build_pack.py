"""Build deterministic, independently installable original interaction workers."""
import argparse,hashlib,json,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT.parents[1]))
from yukino_core.plugin_packages import inspect_package
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def archive(path,files):
 with zipfile.ZipFile(path,'x',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
  for n,b in sorted(files.items()):
   e=zipfile.ZipInfo(n,(1980,1,1,0,0,0));e.compress_type=zipfile.ZIP_DEFLATED;e.external_attr=0o100644<<16;z.writestr(e,b)
def build(output):
 output.mkdir(parents=True,exist_ok=True);catalog=json.loads((ROOT/'catalog.json').read_text(encoding='utf-8'));entries=[];seen=set()
 assert len(catalog)==12 and len({p['id'] for p in catalog})==12
 for e in catalog:
  pid=e['id'];files={p.name:p.read_bytes() for p in (ROOT/'plugins'/pid).iterdir() if p.is_file()}
  for n in ('common.py','social.py','boards.py','engine.py','catalog.json','LICENSE'):files[n]=(ROOT/n).read_bytes()
  files['NOTICE']=(ROOT/'SOURCES.md').read_bytes();files['recommended_settings.json']=(json.dumps(e['settings'],ensure_ascii=False,indent=2)+'\n').encode()
  for n,b in files.items():
   b.decode('utf-8-sig')
   if n.endswith('.py'):compile(b,n,'exec')
  out=output/(pid+'-1.0.0.zip');archive(out,files);r,_=inspect_package(out.read_bytes())
  assert not seen.intersection(r['commands']);seen.update(r['commands'])
  row={**e,'archive':out.name,'sha256':digest(out)};entries.append(row)
  out.with_name(out.name+'.sha256').write_text(row['sha256']+'  '+out.name+'\n',encoding='ascii')
 files={}
 for p in ROOT.rglob('*'):
  rel=p.relative_to(ROOT)
  if p.is_file() and not p.is_symlink() and not any(n.startswith('.') or n=='__pycache__' for n in rel.parts) and (p.suffix in ('.py','.json','.md','.txt') or p.name=='LICENSE'):files['interaction_pack/'+rel.as_posix()]=p.read_bytes()
 source=output/'yukino-interaction-pack-1.0.0-source.zip';archive(source,files)
 source.with_name(source.name+'.sha256').write_text(digest(source)+'  '+source.name+'\n',encoding='ascii')
 manifest=dict(version='1.0.0',minimum_core_version='0.6.0',plugin_count=12,plugins=entries,source_archive=source.name,source_sha256=digest(source),upstream_code_included=False)
 (output/'pack-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'plugins':12,'output':str(output),'source_sha256':digest(source)}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);build(p.parse_args().output)
