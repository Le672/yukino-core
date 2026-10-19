"""Bounded, transactional local storage for the original social workers."""
import re
import sqlite3
import time
from pathlib import Path


class RuleError(ValueError):
    pass


def need(value, message):
    if not value:
        raise RuleError(message)


def identity(ctx):
    uid, sid = ctx.get('user_id'), ctx.get('session_id')
    need(isinstance(uid, str) and re.fullmatch(r'[1-9][0-9]{0,19}', uid), '用户身份无效。')
    need(isinstance(sid, str) and re.fullmatch(r'[pg]:[1-9][0-9]{0,19}', sid), '会话身份无效。')
    need(not sid.startswith('p:') or sid == 'p:' + uid, '私聊身份不匹配。')
    return sid, uid


def setting(ctx, key, default, lo, hi):
    settings = ctx.get('settings', {})
    need(isinstance(settings, dict), '插件设置无效。')
    value = settings.get(key, default)
    need(type(value) is int and lo <= value <= hi, f'设置 {key} 应为 {lo}—{hi} 的整数。')
    return value


def words(value, limit):
    need(isinstance(value, str), '正文格式无效。')
    value = value.strip()
    need(0 < len(value) <= limit and len(value.encode('utf-8')) <= limit * 4
         and not any(ord(c) < 32 and c not in '\n\t' for c in value), f'正文需为 1—{limit} 字，不能包含控制字符。')
    return value


def record_id(value):
    need(isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{0,17}', value), '请提供有效的记录编号。')
    return int(value)


def _plain(path):
    need(not path.is_symlink() and not getattr(path, 'is_junction', lambda: False)(), '拒绝链接数据路径。')


def connect(ctx):
    raw = ctx.get('data_dir')
    need(isinstance(raw, str) and Path(raw).is_absolute(), '缺少内核分配的绝对数据目录。')
    root = Path(raw)
    # Do not accept symlinks/junctions in an existing ancestor or SQLite sidecar.
    for path in [*reversed(root.parents), root]:
        _plain(path)
    root.mkdir(parents=True, exist_ok=True)
    target = root / 'social.sqlite3'
    for name in ('social.sqlite3', 'social.sqlite3-journal', 'social.sqlite3-wal', 'social.sqlite3-shm'):
        path = root / name
        _plain(path)
        if path.exists():
            need(path.is_file() and path.stat().st_nlink == 1, '拒绝非普通或硬链接状态文件。')
            need(path.stat().st_size <= 64 * 1024 * 1024, '状态文件超过 64 MiB 上限，请管理员检查。')
    db = sqlite3.connect(target, timeout=5, isolation_level=None)
    try:
        target.chmod(0o600)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=5000')
        page_size = db.execute('PRAGMA page_size').fetchone()[0]
        db.execute(f'PRAGMA max_page_count={64 * 1024 * 1024 // page_size}')
        db.execute('PRAGMA secure_delete=ON')
        db.execute('CREATE TABLE IF NOT EXISTS entries('
                   'id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, '
                   'scope TEXT NOT NULL, uid TEXT NOT NULL, body TEXT NOT NULL, '
                   'created REAL NOT NULL, due REAL, score INTEGER, start REAL, finish REAL)')
        db.execute('CREATE INDEX IF NOT EXISTS entry_owner ON entries(kind,scope,uid,id)')
        return db
    except BaseException:
        db.close()
        raise


def add(db, pid, sid, uid, body, now, limits, **extra):
    need(db.execute('SELECT count(*) FROM entries').fetchone()[0] < limits['total'], '插件总记录数已达上限，请先删除旧记录。')
    count = db.execute('SELECT count(*) FROM entries WHERE kind=? AND uid=?', (pid, uid)).fetchone()[0]
    need(count < limits['user'], '你的记录数已达上限，请先删除旧记录。')
    cur = db.execute('INSERT INTO entries(kind,scope,uid,body,created,due,score,start,finish) VALUES(?,?,?,?,?,?,?,?,?)',
                     (pid, sid, uid, body, now, extra.get('due'), extra.get('score'), extra.get('start'), extra.get('finish')))
    return cur.lastrowid


def owned(db, pid, sid, uid, value):
    row = db.execute('SELECT * FROM entries WHERE id=? AND kind=? AND scope=? AND uid=?',
                     (record_id(value), pid, sid, uid)).fetchone()
    need(row is not None, '没有这条属于你的记录。')
    return row


def execute(pid, text, ctx, dispatch, helps):
    db = None
    try:
        need(pid in helps, '未知插件。')
        need(isinstance(ctx, dict), '上下文无效。')
        sid, uid = identity(ctx)
        need(isinstance(text, str) and len(text) <= 5000, '指令过长或格式无效。')
        parts = text.strip().split(maxsplit=1)
        need(parts and parts[0] == helps[pid][0], '指令不属于本插件。')
        arg = parts[1].strip() if len(parts) == 2 else ''
        cmd, _, tail = arg.partition(' ')
        if cmd in ('', '帮助', 'help'):
            return helps[pid][1]
        if pid != 's_bottle':
            need(sid == 'p:' + uid, '这些是你的私人记录，请与机器人私聊使用；群内不会读取或写入。')
        else:
            need(sid.startswith('g:'), '漂流瓶仅在本群共享，请到群聊使用。')
        limits = dict(user=setting(ctx, 'max_per_user', 50, 1, 500),
                      total=setting(ctx, 'max_records', 2000, 1, 10000),
                      text=setting(ctx, 'max_text_length', 500, 1, 1000),
                      retention=setting(ctx, 'retention_days', 365, 1, 730))
        db = connect(ctx)
        db.execute('BEGIN IMMEDIATE')
        now = time.time()
        cutoff = now - limits['retention'] * 86400
        # Future time letters survive until at least retention_days after their due date.
        db.execute('DELETE FROM entries WHERE created<? AND (kind!=? OR due<?)', (cutoff, 's_capsule', cutoff))
        out = dispatch(pid, cmd, tail.strip(), sid, uid, ctx, db, now, limits)
        db.commit()
        return out
    except RuleError as exc:
        if db:
            db.rollback()
        return str(exc)
    except (OSError, sqlite3.Error, TypeError, KeyError, OverflowError, UnicodeError):
        if db:
            db.rollback()
        return '状态暂不可用，请管理员检查插件数据；本次操作未保存。'
    finally:
        if db:
            db.close()
