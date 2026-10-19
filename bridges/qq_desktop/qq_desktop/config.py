import json
import os
import re
from pathlib import Path


def require(value, message):
    if not value:
        raise ValueError(message)


def selector(value):
    require(isinstance(value, dict) and set(value) <= {'name', 'role', 'path'}, 'Invalid UI selector')
    require(value.get('name') or value.get('path') is not None, 'A selector needs an exact name or path')
    if 'name' in value:
        require(isinstance(value['name'], str) and 0 < len(value['name']) <= 300, 'Invalid exact name')
    if 'role' in value:
        require(isinstance(value['role'], str) and 0 < len(value['role']) <= 60, 'Invalid role')
    if 'path' in value:
        require(isinstance(value['path'], list) and len(value['path']) <= 30 and
                all(type(i) is int and 0 <= i <= 2000 for i in value['path']), 'Invalid UI path')
    return dict(value)


def numeric(value):
    require(isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{3,19}', value), 'QQ identifiers must be numeric strings')
    return value


def validate(raw):
    require(isinstance(raw, dict), 'Configuration must be an object')
    out = dict(raw)
    out.setdefault('host', '127.0.0.1'); out.setdefault('port', 3002)
    out.setdefault('enabled', False); out.setdefault('app_name', 'qq')
    out.setdefault('poll_seconds', 2); out.setdefault('send_interval_seconds', 2)
    require(type(out['enabled']) is bool, 'enabled must be boolean')
    require(out['host'] in ('127.0.0.1', '::1') or raw.get('allow_network_bind') is True, 'Non-loopback bind requires explicit configuration')
    require(type(out['port']) is int and 1024 <= out['port'] <= 65535, 'Invalid port')
    require(isinstance(out['app_name'], str) and 0 < len(out['app_name']) <= 100, 'Invalid application name')
    for key in ('poll_seconds', 'send_interval_seconds'):
        require(type(out[key]) in (int, float) and 1 <= out[key] <= 60, 'Polling/send intervals must be 1..60 seconds')
    require(isinstance(raw.get('bindings', []), list) and len(raw.get('bindings', [])) <= 32, 'At most 32 conversations')
    bindings = []
    for entry in raw.get('bindings', []):
        require(isinstance(entry, dict) and entry.get('kind') in ('private', 'group'), 'Invalid conversation binding')
        b = dict(entry); numeric(b['id'])
        for key in ('contact', 'header', 'target_identity', 'history', 'editor', 'send', 'body', 'sender_id'):
            b[key] = selector(b.get(key))
        require(b.get('row_key', 'accessible_id') in ('accessible_id', 'description', 'name'), 'Invalid stable row key source')
        b.setdefault('row_key', 'accessible_id')
        b.setdefault('verified', False)
        require(type(b['verified']) is bool, 'verified must be boolean')
        bindings.append(b)
    require(len({(b['kind'], b['id']) for b in bindings}) == len(bindings), 'Duplicate conversation bindings')
    out['bindings'] = bindings
    out.setdefault('blocked_labels', ['二维码登录', '安全验证', '账号安全限制'])
    require(isinstance(out['blocked_labels'], list) and len(out['blocked_labels']) <= 30 and
            all(isinstance(s, str) and 0 < len(s) <= 200 for s in out['blocked_labels']), 'Invalid blocked labels')
    if out['enabled']:
        numeric(out.get('self_id'))
        out['identity'] = selector(out.get('identity'))
        require(out['bindings'] and all(b['verified'] for b in out['bindings']), 'Calibrate and verify all enabled bindings first')
    return out


def load(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 131072, 'Configuration is not an ordinary small file')
    return validate(json.loads(path.read_text(encoding='utf-8-sig')))


def token_from_environment():
    token = os.environ.get('QQ_DESKTOP_TOKEN', '')
    require(len(token) >= 32 and len(token) <= 256 and not any(c.isspace() for c in token), 'QQ_DESKTOP_TOKEN needs 32..256 non-whitespace characters')
    return token
