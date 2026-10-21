"""Package only the experimental bridge source; never include a QQ client or data."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
VERSION='0.1.0a5'
FILES=('README.md','pyproject.toml','Dockerfile','Dockerfile.fixture','compose.example.yaml',
       'config.example.json','qqnt-private.example.json','binding.example.json','client-lock.example.json','.gitignore','.dockerignore')


def build(root,output):
    root=Path(root).resolve();output=Path(output).resolve();files={}
    paths=[root/name for name in FILES]
    for folder in ('qq_desktop','scripts','tests'):
        paths.extend(p for p in (root/folder).rglob('*') if (p.suffix in ('.py','.sh') or
            (p.parent==root/'qq_desktop/static' and p.suffix in ('.html','.js','.css'))) and '__pycache__' not in p.parts)
    for path in paths:
        if (not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root)
                or any(p.is_symlink() for p in path.parents if p!=root.parent) or path.stat().st_size>2_000_000):
            raise ValueError('Invalid release source path')
        files[path.relative_to(root).as_posix()]=path.read_text(encoding='utf-8-sig').replace('\r\n','\n').encode()
    license=root/'LICENSE'
    if not license.exists():license=root.parents[1]/'LICENSE'
    files['LICENSE']=license.read_bytes()
    manifest={'version':VERSION,'status':'experimental_requires_ui_calibration','qq_client_included':False,
              'credentials_included':False,'real_qq_roundtrip_verified':False,
              'files':[{'path':n,'sha256':hashlib.sha256(v).hexdigest(),'bytes':len(v)} for n,v in sorted(files.items())]}
    files['source-manifest.json']=(json.dumps(manifest,indent=2)+'\n').encode()
    output.mkdir(parents=True,exist_ok=True);archive=output/('YukinoQQDesktop-'+VERSION+'-source.zip')
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,content in sorted(files.items()):
            info=zipfile.ZipInfo('YukinoQQDesktop-'+VERSION+'/'+name,(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16;z.writestr(info,content)
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name+'.sha256').write_text(digest+'  '+archive.name+'\n',encoding='ascii')
    return {'archive':str(archive),'sha256':digest,'source_files':len(files),'qq_client_included':False}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args();print(json.dumps(build(ROOT,args.output)))
