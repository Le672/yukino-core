"""Build independent optional plugins; never includes downloaded upstream archives."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1]))
from yukino_core.plugin_packages import inspect_package

EXPECTED = {'f1_info','game_news','daily_luck','tarot','answer_book','decision','wordle','guess_number','rps','todo_notes','polls','time_tools','text_tools','quotes','daily_greetings'}
NETWORK = {'f1_info','game_news'}
LICENSE = ((ROOT / 'LICENSE') if (ROOT / 'LICENSE').is_file() else (ROOT.parents[1] / 'LICENSE')).read_bytes()
NOTICE = '''Yukino Mai Function Extension Pack 1.0.0

Function discovery reference: https://github.com/Mai-with-u/plugin-repo
Implementation: independent Python workers for Yukino Core.
No upstream implementation, images, runtime data or author account IDs are included.
Individual upstream licenses remain their authors' property. This is independent code, not a MaiBot SDK compatibility layer.
This package does not imply upstream authorship, endorsement, or full feature compatibility.
Yukino implementation is distributed under the accompanying MIT LICENSE.
Public data remains subject to the respective service's terms and availability.
'''.encode()


def archive(path: Path, files: dict[str, bytes]):
    if path.exists():
        raise FileExistsError('Choose a new output directory: '+str(path))
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as output:
        for name,payload in sorted(files.items()):
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100644<<16
            output.writestr(info,payload)


def build(destination: Path):
    destination.mkdir(parents=True,exist_ok=True)
    sources={}
    entries=[]
    directories={p.name:p for p in (ROOT/'plugins').iterdir() if p.is_dir() and (p/'plugin.json').is_file()}
    if set(directories)!=EXPECTED:
        raise ValueError('Incomplete plugin source set: '+str(sorted(EXPECTED-set(directories))))
    for pid,folder in sorted(directories.items()):
        files={}
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.suffix not in {'.py','.json','.md','.txt'}:
                continue
            if path.is_symlink() or path.stat().st_size>200_000:
                raise ValueError('Not a bounded ordinary source file')
            payload=path.read_bytes()
            payload.decode('utf-8-sig')
            if path.suffix=='.py': compile(payload,str(path),'exec')
            files[path.name]=payload
        if pid not in NETWORK:
            files['_local.py']=(ROOT/'local_engine.py').read_bytes()
        files['LICENSE']=LICENSE
        files['NOTICE']=NOTICE
        config={'timeout':18 if pid in NETWORK else 5,'output_max_chars':5000}
        files['recommended_settings.json']=(json.dumps(config,indent=2)+'\n').encode()
        result_path=destination/(pid+'-1.0.0.zip')
        archive(result_path,files)
        record,checked=inspect_package(result_path.read_bytes())
        if record['id']!=pid: raise ValueError('Plugin directory and ID mismatch')
        entries.append({'id':pid,'name':record['name'],'commands':record['commands'],
                        'archive':result_path.name,'sha256':hashlib.sha256(result_path.read_bytes()).hexdigest(),
                        'bytes':result_path.stat().st_size,'files':len(checked),'settings':config,
                        'source_reference':'https://github.com/Mai-with-u/plugin-repo',
                        'implementation':'independent_yukino_worker','upstream_code_included':False})
        sources.update({'maibot_pack/plugins/'+pid+'/'+name:payload for name,payload in files.items()})
    for name in ('build_pack.py','README.md','SOURCES.md','local_engine.py'):
        sources['maibot_pack/'+name]=(ROOT/name).read_bytes()
    for path in sorted((ROOT/'tests').glob('test_*.py')):
        sources['maibot_pack/tests/'+path.name]=path.read_bytes()
    sources['maibot_pack/LICENSE']=LICENSE
    sources['maibot_pack/NOTICE']=NOTICE
    source_path=destination/'yukino-mai-pack-1.0.0-source.zip'
    archive(source_path,sources)
    manifest={'version':'1.0.0','minimum_core_version':'0.4.0','native_plugins':['group_admin','reminders','keyword_reply'],'plugins':entries,
              'source_archive':source_path.name,'source_sha256':hashlib.sha256(source_path.read_bytes()).hexdigest(),
              'upstream_code_included':False,'credentials_included':False}
    (destination/'pack-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for item in [*entries,{'archive':source_path.name,'sha256':manifest['source_sha256']}]:
        (destination/(item['archive']+'.sha256')).write_text(item['sha256']+'  '+item['archive']+'\n',encoding='ascii')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=build(args.output)
    print(json.dumps({'plugins':len(result['plugins']),'output':str(args.output),'source_sha256':result['source_sha256']}))
