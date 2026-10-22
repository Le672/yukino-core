"""Build 63 independent optional plugins; no runtime data or credentials."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parents[1]))
from yukino_core.plugin_packages import inspect_package
from yukino_core.plugins import BUILTINS

GROUPS={'dev_tools':'dev_engine.py','life_tools':'life_engine.py','games':'games_engine.py'}
VERSION='1.2.0'
NOTICE=b'Yukino Expansion Pack 1.2.0\nIndependent original implementations for Yukino Core.\nNo runtime databases, credentials, third-party code or media included.\nDistributed under the accompanying MIT LICENSE.\n'


def archive_bytes(files):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as package:
        for name,payload in sorted(files.items()):
            if Path(name).is_absolute() or '..' in Path(name).parts or '\\' in name:raise ValueError('Invalid archive path')
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
            package.writestr(info,payload)
    return output.getvalue()


def catalogs():
    result=[]
    for group in GROUPS:
        data=json.loads((ROOT/group/'catalog.json').read_text(encoding='utf-8'))
        data=data if isinstance(data,list) else data['plugins']
        if len(data)!=21:raise ValueError(group+' must provide 21 actual plugins')
        for item in data:result.append({**item,'group':group})
    if len({p['id'] for p in result})!=63:raise ValueError('Expected 63 distinct plugin IDs')
    return result


def build(output):
    license_path=ROOT/'LICENSE'
    license_data=(license_path if license_path.is_file() else ROOT.parents[1]/'LICENSE').read_bytes()
    known_commands={command for item in BUILTINS for command in item['commands']}
    known_commands.update(['/help','/reset'])
    for existing in ('community_pack','maibot_pack'):
        for path in (ROOT.parent/existing/'plugins').glob('*/plugin.json'):
            known_commands.update(json.loads(path.read_text(encoding='utf-8'))['commands'])
    entries=[];artifacts={};all_catalogs=catalogs()
    for item in all_catalogs:
        folder=ROOT/item['group']/'plugins'/item['id']
        manifest=json.loads((folder/'plugin.json').read_text(encoding='utf-8'))
        if manifest['id']!=item['id'] or manifest['commands']!=item['commands']:raise ValueError('Catalog differs from manifest: '+item['id'])
        for command in manifest['commands']:
            if command in known_commands:raise ValueError('Duplicate command: '+command)
            known_commands.add(command)
        files={}
        for path in folder.iterdir():
            if path.is_file() and path.suffix in ('.py','.json','.md','.txt'):
                if path.is_symlink() or path.stat().st_size>200_000:raise ValueError('Not a bounded ordinary file: '+str(path))
                data=path.read_bytes();data.decode('utf-8-sig')
                if path.suffix=='.py':compile(data,str(path),'exec')
                files[path.name]=data
        helper=ROOT/item['group']/GROUPS[item['group']]
        helper_data=helper.read_bytes();compile(helper_data,str(helper),'exec');files['_engine.py']=helper_data
        # Extra fixed helpers, if any, are copied by explicit filename from the group root.
        for path in (ROOT/item['group']).glob('helper_*.py'):
            data=path.read_bytes();compile(data,str(path),'exec');files[path.name]=data
        settings={'timeout':30 if item['id'] in ('x_ip','x_url','x_http') else 5,'input_max_chars':4000,'output_max_chars':6000}
        files.update({'LICENSE':license_data,'NOTICE':NOTICE,
            'recommended_settings.json':(json.dumps(settings,indent=2)+'\n').encode()})
        payload=archive_bytes(files);record,checked=inspect_package(payload)
        name=record['id']+'-'+VERSION+'.zip';artifacts[name]=payload
        entries.append({**item,'name':record['name'],'description':record['description'],'archive':name,
            'sha256':hashlib.sha256(payload).hexdigest(),'bytes':len(payload),'files':len(checked),
            'settings':settings,'implementation':'original_yukino_worker'})
    sources={}
    for group in GROUPS:
        for path in sorted((ROOT/group).rglob('*')):
            if path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts and path.suffix in ('.py','.json','.md','.txt'):
                sources['expansion_pack/'+path.relative_to(ROOT).as_posix()]=path.read_bytes()
    for name in ('build_pack.py','verify_pack.py','README.md','SOURCES.md'):
        sources['expansion_pack/'+name]=(ROOT/name).read_bytes()
    sources['expansion_pack/LICENSE']=license_data;sources['expansion_pack/NOTICE']=NOTICE
    source_name='yukino-expansion-pack-'+VERSION+'-source.zip';source_payload=archive_bytes(sources);artifacts[source_name]=source_payload
    manifest={'version':VERSION,'minimum_core_version':'0.8.0','plugin_count':63,'groups':{name:21 for name in GROUPS},
        'plugins':entries,'source_archive':source_name,'source_sha256':hashlib.sha256(source_payload).hexdigest(),
        'credentials_included':False,'runtime_data_included':False}
    output.mkdir(parents=True,exist_ok=True)
    if any((output/name).exists() for name in [*artifacts,'pack-manifest.json']):raise FileExistsError('Choose a new output directory')
    for name,payload in artifacts.items():
        (output/name).write_bytes(payload)
        (output/(name+'.sha256')).write_text(hashlib.sha256(payload).hexdigest()+'  '+name+'\n',encoding='ascii')
    (output/'pack-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=build(args.output);print(json.dumps({'plugins':result['plugin_count'],'source_sha256':result['source_sha256'],'output':str(args.output)}))
