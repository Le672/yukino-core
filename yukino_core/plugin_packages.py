"""Install local source packages without importing or running their code."""
import asyncio
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import uuid
import zipfile

from .config import atomic_json
from . import __version__
from .plugins import BUILTINS, MAX_EXTERNAL_PLUGINS, MAX_MANIFEST_BYTES

BUILTIN_IDS = {item['id'] for item in BUILTINS}


@contextmanager
def _mutation(manager):
    # Acquire before the first await, including archive inspection. This guards
    # execute() as well as competing administrative requests on this event loop.
    if manager.mutating: raise ValueError('插件配置正在更新，请稍后重试')
    if any(lock.locked() for lock in manager.locks.values()): raise ValueError('插件正在执行，请稍后更新')
    manager.mutating=True
    try: yield
    finally: manager.mutating=False


def _restore_file(path, previous):
    if previous is None:
        path.unlink(missing_ok=True)
        return
    descriptor, temporary=tempfile.mkstemp(prefix='.'+path.name+'.rollback-',dir=path.parent)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            os.chmod(temporary,0o600)
            stream.write(previous); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def _snapshot_config(manager):
    path=getattr(manager.config,'path',None)
    path=Path(path) if path is not None else None
    return dict(manager.config.plugin_flags),path,path.read_bytes() if path and path.exists() else None


def _rollback_config(manager,snapshot):
    flags,path,previous=snapshot
    manager.config.plugin_flags=flags
    if path is not None: _restore_file(path,previous)


def _cleanup_created_package(manager,target,created_name):
    # Never delete an existing package, the parent directory, or a symlink's
    # destination. Only this operation's fresh id-UUID directory is eligible.
    packages=manager.root/'packages'
    root=manager.root.resolve()
    if (target.name!=created_name or target.is_symlink() or packages.is_symlink()
            or packages.resolve()!=root/'packages'
            or target.resolve().parent!=packages.resolve()):
        raise ValueError('插件临时目录边界变化，已保留文件供管理员检查')
    shutil.rmtree(target)


async def reload_plugins(manager):
    with _mutation(manager):
        candidate=type(manager)(manager.config,manager.root)
        if candidate.manifest_error: raise ValueError(candidate.manifest_error)
        await manager.close()
        manager.items=candidate.items
        manager.manifest_error=''
        manager.errors.clear()
        return manager.list()


def inspect_package(payload):
    if not isinstance(payload,bytes) or not 0<len(payload)<=2_000_000: raise ValueError('ZIP文件须小于2MB')
    try:
        archive=zipfile.ZipFile(io.BytesIO(payload))
        entries=archive.infolist()
        if len(entries)>50 or sum(x.file_size for x in entries)>1_000_000: raise ValueError('插件最多50个文件、解压后最多1MB')
        files={}
        portable_paths=set()
        for item in entries:
            original_name=getattr(item,'orig_filename',item.filename)
            path=PurePosixPath(item.filename)
            mode=item.external_attr>>16
            if (path.is_absolute() or '..' in path.parts or '\\' in original_name or ':' in original_name or '\0' in original_name
                    or stat.S_ISLNK(mode) or item.flag_bits & 1): raise ValueError('ZIP中含越界、符号链接或加密条目')
            if (any(part.rstrip(' .')!=part or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',part) for part in path.parts)
                    or any(ord(char)<32 or char in '<>"|?*' for char in original_name)):
                raise ValueError('ZIP包含不兼容Windows的路径')
            if str(path).casefold() in portable_paths or len(path.parts)>5: raise ValueError('ZIP路径重复或层级过深')
            portable_paths.add(str(path).casefold())
            if item.is_dir(): continue
            if path.suffix not in ('.py','.json','.md','.txt') and path.name not in ('LICENSE','NOTICE','COPYING'):
                raise ValueError('插件包仅允许Python源码、JSON和说明文本')
            raw=archive.read(item)
            raw.decode('utf-8-sig')
            files[str(path)]=raw
        record=json.loads(files['plugin.json'])
        if not isinstance(record,dict): raise ValueError('plugin.json须为对象')
        pid=record.get('id')
        if not isinstance(pid,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}',pid) or pid in BUILTIN_IDS:
            raise ValueError('插件ID无效或与内置功能冲突')
        if any(not isinstance(record.get(k),str) or not 1<=len(record[k])<=500 for k in ('name','description','worker')):
            raise ValueError('插件名称、说明和worker不能为空')
        worker=PurePosixPath(record['worker'])
        if str(worker) not in files or worker.suffix!='.py': raise ValueError('worker须指向包内Python文件')
        commands=record.get('commands')
        if not isinstance(commands,list) or not 1<=len(commands)<=20 or any(not isinstance(c,str) or not c.startswith('/') or not 2<=len(c)<=40 or any(x.isspace() for x in c) for c in commands):
            raise ValueError('commands须为1—20个以/开头的无空白指令')
        for field in ('version','minimum_core_version'):
            if field in record and not re.fullmatch(r'[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}',str(record[field])):raise ValueError('插件版本格式无效')
        if 'minimum_core_version' in record and tuple(map(int,record['minimum_core_version'].split('.')))>tuple(map(int,__version__.split('.'))):raise ValueError('请先升级内核至 '+record['minimum_core_version'])
        if 'result_protocol' in record and record['result_protocol']!=1:raise ValueError('不支持该插件结果协议')
        record={k:v for k,v in record.items() if k in ('id','name','description','worker','commands','config_schema','auto_url_hosts','auto_url_paths','silent_ack','version','minimum_core_version','result_protocol')}
        return record,files
    except (zipfile.BadZipFile,KeyError,UnicodeError,json.JSONDecodeError,RuntimeError,NotImplementedError) as exc:
        raise ValueError('插件ZIP无效，须在根目录提供UTF-8 plugin.json和对应Python源码') from None


async def install_package(manager,payload):
    with _mutation(manager):
        record,files=await asyncio.to_thread(inspect_package,payload)
        if any(i['id']==record['id'] for i in manager.items): raise ValueError('插件ID已存在，请先移除旧插件并保留备份')
        manager.root.mkdir(parents=True,exist_ok=True)
        manifest=manager.root/'manifest.json'
        records=json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else []
        if not isinstance(records,list) or len(records)>=MAX_EXTERNAL_PLUGINS:
            raise ValueError(f'插件清单无效或已达到{MAX_EXTERNAL_PLUGINS}个外部插件上限')
        if any(isinstance(i,dict) and i.get('id')==record['id'] for i in records): raise ValueError('插件ID已存在，请先移除旧插件并保留备份')
        # Bound the complete manifest, not just individual ZIP uploads.
        preview={**record,'worker':'packages/'+record['id']+'-00000000/'+record['worker']}
        if len(json.dumps([*records,preview],ensure_ascii=False,indent=2).encode('utf-8'))>MAX_MANIFEST_BYTES:
            raise ValueError('插件清单超过1MiB大小上限')
        packages=manager.root/'packages'
        if packages.is_symlink() or packages.resolve()!=manager.root.resolve()/'packages': raise ValueError('插件包目录不能指向实例之外')
        created_name=record['id']+'-'+uuid.uuid4().hex[:8]
        target=packages/created_name
        previous=manifest.read_bytes() if manifest.exists() else None
        config_snapshot=_snapshot_config(manager)
        created=False
        try:
            target.mkdir(parents=True,mode=0o700)
            created=True
            for name,raw in files.items():
                destination=target/name
                if not destination.resolve().is_relative_to(target.resolve()): raise ValueError('插件源码路径越界')
                destination.parent.mkdir(parents=True,exist_ok=True)
                destination.write_bytes(raw); destination.chmod(0o600)
            installed={**record,'worker':str((target/record['worker']).relative_to(manager.root)).replace('\\','/')}
            # The boot loader validates the complete manifest without importing
            # source. New code stays disabled until explicitly enabled.
            atomic_json(manifest,[*records,installed])
            candidate=type(manager)(manager.config,manager.root)
            if candidate.manifest_error or not any(i['id']==record['id'] for i in candidate.items): raise ValueError('插件清单校验失败')
            manager.config.plugin_flags[record['id']]=False
            manager.config.save()
        except BaseException:
            try:
                try: _restore_file(manifest,previous)
                finally: _rollback_config(manager,config_snapshot)
            finally:
                if created: _cleanup_created_package(manager,target,created_name)
            raise
        manager.items=candidate.items
        return {'installed':True,'id':record['id'],'enabled':False,'code_executed':False}


async def remove_package(manager,pid):
    with _mutation(manager):
        if pid in BUILTIN_IDS: raise ValueError('内置功能只能停用，不能移除')
        if not any(i['id']==pid for i in manager.items): raise ValueError('插件不存在')
        # IDs from old hand-written manifests must never become filesystem paths.
        if not isinstance(pid,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}',pid): raise ValueError('插件ID无效')
        manifest=manager.root/'manifest.json'
        previous=manifest.read_bytes()
        records=json.loads(previous)
        if not isinstance(records,list) or any(not isinstance(r,dict) for r in records): raise ValueError('插件清单无效')
        removed=[r for r in records if r.get('id')==pid]
        if not removed: raise ValueError('插件清单中不存在此插件')
        backup=manager.root/'removed-manifests'/(pid+'-'+uuid.uuid4().hex[:8]+'.json')
        if backup.parent.is_symlink() or backup.resolve().parent!=manager.root.resolve()/'removed-manifests': raise ValueError('移除备份目录越界')
        # End an idle worker before starting the synchronous disk transaction.
        # Other configuration endpoints may run while process exit is awaited;
        # snapshot afterwards so rollback cannot discard their saved changes.
        await manager._kill(pid)
        config_snapshot=_snapshot_config(manager)
        try:
            atomic_json(backup,removed)
            atomic_json(manifest,[r for r in records if r.get('id')!=pid])
            manager.config.plugin_flags.pop(pid,None)
            manager.config.save()
        except BaseException:
            try: _restore_file(manifest,previous)
            finally: _rollback_config(manager,config_snapshot)
            raise
        manager.items=[i for i in manager.items if i['id']!=pid]
        manager.errors.pop(pid,None)
        return {'removed':True,'files_retained':True,'id':pid}
