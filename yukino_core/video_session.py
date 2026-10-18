"""Administrator-entered site session; fixed private file, never returned to clients."""
import json
import re
import time
from pathlib import Path


def status(root):
    path = Path(root) / 'douyin-session.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        expires = float(data['expires_at'])
        return {'configured': bool(data['cookies']) and expires > time.time(), 'expires_at': expires}
    except (OSError, ValueError, TypeError, KeyError):
        return {'configured': False, 'expires_at': None}


def save(root, value):
    if not isinstance(value, str) or len(value) > 16384 or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise ValueError()
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / 'douyin-session.json'
    if not value.strip():
        path.unlink(missing_ok=True)
        return status(root)
    cookies = {}
    for pair in value.strip().split(';'):
        name, equal, content = pair.strip().partition('=')
        if not equal or not re.fullmatch(r'[A-Za-z0-9_!#$%&*+.^`|~-]{1,100}', name) or not content or len(content) > 8192:
            raise ValueError()
        cookies[name] = content
    if not 1 <= len(cookies) <= 100:
        raise ValueError()
    temporary = root / '.douyin-session.tmp'
    # Seven days is a local storage lifetime, not a claim about site validity.
    data = {'cookies': cookies, 'saved_at': time.time(), 'expires_at': time.time() + 7 * 86400}
    temporary.write_text(json.dumps(data), encoding='utf-8')
    temporary.chmod(0o600)
    temporary.replace(path)
    return status(root)
