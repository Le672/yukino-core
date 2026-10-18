"""Build an offline portable Windows edition from audited source and plugin ZIPs."""
import argparse
import ast
import hashlib
import json
import re
from pathlib import Path,PurePosixPath
import shutil
import subprocess
import sys
import zipfile

SOURCE=Path(__file__).resolve().parent

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def catalog_literal(node):
    """Read a declarative catalog without importing or executing release code."""
    if isinstance(node,ast.List):return [catalog_literal(item) for item in node.elts]
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='dict' and not node.args:
        if any(item.arg is None for item in node.keywords):raise ValueError('Nonliteral catalog expansion')
        return {item.arg:ast.literal_eval(item.value) for item in node.keywords}
    return ast.literal_eval(node)

def unpack(path,dest,strip=0):
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            parts=PurePosixPath(entry.filename).parts[strip:]
            if not parts or entry.is_dir():continue
            if any(p in ('.','..') or ':' in p or '\\' in p for p in parts):raise ValueError('Unsafe archive path')
            target=dest.joinpath(*parts)
            if not target.resolve().is_relative_to(dest.resolve()):raise ValueError('Unsafe archive path')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(entry))

def build(args):
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    if args.build_number<1:raise ValueError('Invalid Windows build number')
    # Read the frozen release, never a separately maintained version/count.
    with zipfile.ZipFile(args.core_source) as source:
        manifest_names=[n for n in source.namelist() if n.count('/')==1 and n.endswith('/release-manifest.json')]
        if len(manifest_names)!=1:raise ValueError('Ambiguous source manifest')
        source_manifest=json.loads(source.read(manifest_names[0]))
        version=source_manifest['version']
        if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',version):raise ValueError('Invalid release version')
        prefix=manifest_names[0].split('/')[0]
        tree=ast.parse(source.read(prefix+'/yukino_core/plugins.py'))
        builtin_assignments=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='BUILTINS' for t in n.targets)]
        if len(builtin_assignments)!=1:raise ValueError('Ambiguous builtin catalog')
        builtins=catalog_literal(builtin_assignments[0].value)
        notice_tree=ast.parse(source.read(prefix+'/yukino_core/notice_plugins.py')) if prefix+'/yukino_core/notice_plugins.py' in source.namelist() else None
        if notice_tree is not None:
            notices=[n for n in notice_tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='NOTICE_PLUGINS' for t in n.targets)]
            if len(notices)!=1:raise ValueError('Ambiguous notice catalog')
            builtins+=catalog_literal(notices[0].value)
        builtin_count=len(builtins)
        if len({p['id'] for p in builtins})!=builtin_count:raise ValueError('Duplicate builtin ID')
    suffix='' if args.build_number==1 else '-build'+str(args.build_number)
    root=output/('YukinoCore-'+version+'-Windows-x64'+suffix)
    if root.exists():raise FileExistsError('Choose a new output folder')
    root.mkdir()
    cache=args.cache.resolve();lock=json.loads((cache/'dependency-lock.json').read_text(encoding='utf-8'))
    for item in [lock['python'],*lock['wheels']]:
        if Path(item['file']).name!=item['file'] or digest(cache/item['file'])!=item['sha256']:
            raise ValueError('Dependency checksum mismatch')
    runtime=root/'runtime';runtime.mkdir()
    unpack(cache/lock['python']['file'],runtime)
    site=runtime/'Lib/site-packages'
    subprocess.run([sys.executable,'-m','pip','install','--no-index','--no-deps','--no-compile','--disable-pip-version-check',
                    '--target',str(site),*[str(cache/w['file']) for w in lock['wheels']]],check=True,stdout=subprocess.DEVNULL)
    (runtime/'python314._pth').write_text('python314.zip\n.\nLib/site-packages\n../app\n../windows\nimport site\n',encoding='ascii')
    app=root/'app';app.mkdir();unpack(args.core_source.resolve(),app,strip=1)
    metadata=json.loads((app/'release-manifest.json').read_text(encoding='utf-8'))
    if metadata['version']!=version:raise ValueError('Unexpected core version')
    windows=root/'windows';windows.mkdir()
    for name in ('launcher.py','build_windows.py','fetch_runtime.py','README.md','requirements.txt'):
        shutil.copy2(SOURCE/name,windows/name)
    if (SOURCE/'tests').is_dir():
        for path in (SOURCE/'tests').glob('test_*.py'):
            target=windows/'tests'/path.name;target.parent.mkdir(exist_ok=True);shutil.copy2(path,target)
    shutil.copy2(SOURCE/'README.md',root/'使用说明.md')
    shutil.copy2(cache/'dependency-lock.json',root/'dependency-lock.json')
    licenses=root/'licenses';licenses.mkdir()
    shutil.copy2(cache/(lock['python']['file']+'.spdx.json'),licenses/'CPython.spdx.json')
    if (runtime/'LICENSE.txt').exists():shutil.copy2(runtime/'LICENSE.txt',licenses/'CPython-LICENSE.txt')
    for folder in sorted(site.glob('*.dist-info')):
        dest=licenses/folder.name;dest.mkdir()
        shutil.copy2(folder/'METADATA',dest/'METADATA')
        for file in folder.rglob('*'):
            if file.is_file() and ('license' in str(file.relative_to(folder)).lower() or file.name.lower() in ('copying','notice','authors')):
                target=dest/file.relative_to(folder);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,target)
    packages=root/'plugin-packages';packages.mkdir();entries=[];seen=set()
    help_root=root/'plugin-help';help_root.mkdir()
    for directory in args.plugins:
        manifest=json.loads((directory/'pack-manifest.json').read_text(encoding='utf-8'))
        for entry in manifest['plugins']:
            if entry['id'] in seen:raise ValueError('Duplicate plugin ID')
            seen.add(entry['id']);path=directory/entry['archive']
            if path.parent.resolve()!=directory.resolve() or digest(path)!=entry['sha256']:raise ValueError('Plugin checksum mismatch')
            shutil.copy2(path,packages/path.name)
            with zipfile.ZipFile(path) as plugin:
                if 'README.md' in plugin.namelist():
                    (help_root/(entry['id']+'.md')).write_bytes(plugin.read('README.md'))
            entries.append({k:entry[k] for k in ('id','name','archive','sha256','settings')})
    (packages/'manifest.json').write_text(json.dumps({'plugins':entries},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (root/'windows.json').write_text('{"port":18801}\n',encoding='ascii')
    for name,command,pause in [('Start','start',False),('Stop','stop',True),('Status','status',True),('Open-Panel','open',False),('Show-Token','token',True)]:
        content='@echo off\r\ncd /d "%~dp0"\r\n"%~dp0runtime\\python.exe" -X utf8 "%~dp0windows\\launcher.py" '+command+'\r\n'
        content+='pause\r\n' if pause else 'if errorlevel 1 pause\r\n'
        (root/(name+'.cmd')).write_bytes(content.encode('ascii'))
    (root/'LICENSE').write_bytes((app/'LICENSE').read_bytes())
    (root/'THIRD_PARTY_NOTICES.md').write_text((app/'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')+'\n\nWindows runtime/wheel licenses and upstream metadata: see licenses/ and dependency-lock.json.\n',encoding='utf-8')
    files=[]
    for path in sorted(root.rglob('*')):
        if path.is_file():files.append({'path':path.relative_to(root).as_posix(),'sha256':digest(path),'bytes':path.stat().st_size})
    manifest={'format':1,'version':version,'windows_build':args.build_number,'architecture':'x64','python':lock['python']['version'],
              'source_archive_sha256':digest(args.core_source),'builtin_plugins':builtin_count,'bundled_external_plugins':len(entries),'initial_total_plugins':builtin_count+len(entries),
              'credentials_included':False,'runtime_data_included':False,'entries':files}
    (root/'windows-release-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    archive=output/(root.name+'-portable.zip')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as package:
        for path in sorted(root.rglob('*')):
            if not path.is_file():continue
            entry=zipfile.ZipInfo(root.name+'/'+path.relative_to(root).as_posix(),date_time=(1980,1,1,0,0,0))
            entry.compress_type=zipfile.ZIP_DEFLATED;entry.external_attr=0o100644<<16
            package.writestr(entry,path.read_bytes())
    (archive.with_name(archive.name+'.sha256')).write_text(digest(archive)+'  '+archive.name+'\n',encoding='ascii')
    print(json.dumps({'root':str(root),'archive':str(archive),'sha256':digest(archive),'bytes':archive.stat().st_size,'plugins':builtin_count+len(entries)}))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--core-source',required=True,type=Path)
    parser.add_argument('--cache',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--build-number',type=int,default=1)
    parser.add_argument('--plugins',required=True,type=Path,action='append')
    build(parser.parse_args())
