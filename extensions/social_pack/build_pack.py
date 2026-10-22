"""Build ten original, bounded social/puzzle/query workers and a source pack."""
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

MODULES = {
    **{pid: ('common.py', 'social.py') for pid in ('s_bottle', 's_capsule', 's_mood', 's_sleep')},
    **{pid: ('puzzles.py',) for pid in ('s_sudoku', 's_nonogram', 's_expedition')},
    's_banter': ('common.py', 'banter.py'),
    **{pid: ('common.py', 'network.py', 'safe_net.py') for pid in ('s_github', 's_package')},
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def archive(path, files):
    with zipfile.ZipFile(path, 'x', zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for name, payload in sorted(files.items()):
            item = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            output.writestr(item, payload)


def build(destination):
    destination.mkdir(parents=True, exist_ok=True)
    entries, commands = [], set()
    bank = ROOT / 'bank.json'
    if not bank.is_file():
        bank = ROOT.parents[1] / 'resources/banter.zh-CN.json'
    bank_bytes = bank.read_bytes()
    for pid, modules in sorted(MODULES.items()):
        folder = ROOT / 'plugins' / pid
        files = {name: (folder / name).read_bytes() for name in ('worker.py', 'plugin.json', 'README.md')}
        files.update({name: (ROOT / name).read_bytes() for name in modules})
        files['LICENSE'] = (ROOT / 'LICENSE').read_bytes()
        files['NOTICE'] = (ROOT / 'SOURCES.md').read_bytes()
        if pid == 's_banter':
            files['bank.json'] = bank_bytes
        settings = {'timeout': 20 if pid in ('s_github', 's_package') else 6, 'output_max_chars': 6000}
        files['recommended_settings.json'] = (json.dumps(settings, indent=2) + '\n').encode()
        for name, payload in files.items():
            payload.decode('utf-8-sig')
            if name.endswith('.py'):
                compile(payload, name, 'exec')
        output = destination / (pid + '-1.2.0.zip')
        archive(output, files)
        payload = output.read_bytes()
        record, checked = inspect_package(payload)
        assert record['id'] == pid and not commands.intersection(record['commands'])
        commands.update(record['commands'])
        entries.append({k: record[k] for k in ('id', 'name', 'commands')} | {
            'archive': output.name, 'sha256': digest(payload), 'bytes': len(payload), 'files': len(checked), 'settings': settings})
        output.with_name(output.name + '.sha256').write_text(digest(payload) + '  ' + output.name + '\n', encoding='ascii')
    sources = {}
    for path in ROOT.rglob('*'):
        relative = path.relative_to(ROOT)
        if (not path.is_file() or path.is_symlink() or any(p.startswith('.') or p in ('__pycache__', 'dist', 'data') for p in relative.parts)
                or not (path.suffix in ('.py', '.md', '.json', '.txt') or path.name in ('LICENSE', 'NOTICE'))):
            continue
        sources['social_pack/' + relative.as_posix()] = path.read_bytes()
    sources['social_pack/bank.json'] = bank_bytes
    source = destination / 'yukino-social-pack-1.2.0-source.zip'
    archive(source, sources)
    source.with_name(source.name + '.sha256').write_text(digest(source.read_bytes()) + '  ' + source.name + '\n', encoding='ascii')
    manifest = {'version': '1.2.0', 'minimum_core_version': '0.8.0', 'plugin_count': len(entries), 'plugins': entries,
                'source_archive': source.name, 'source_sha256': digest(source.read_bytes()), 'upstream_code_included': False,
                'credentials_included': False}
    (destination / 'pack-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    result = build(parser.parse_args().output)
    print(json.dumps({'plugins': result['plugin_count'], 'source_sha256': result['source_sha256']}))
