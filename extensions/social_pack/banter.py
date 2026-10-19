"""Original, user-triggered Chinese conversation copy with repeat avoidance."""
import json
from pathlib import Path
import secrets
import sqlite3
import time

from common import RuleError, connect, identity, need, setting

CATEGORIES = ("招呼", "闲聊", "无聊", "夸夸", "安慰", "嘴硬关心")
RNG = secrets.SystemRandom()


def bank():
    packaged = Path(__file__).with_name('bank.json')
    source = packaged if packaged.is_file() else Path(__file__).resolve().parents[2] / 'resources/banter.zh-CN.json'
    need(source.is_file() and not source.is_symlink() and source.stat().st_size <= 65536, '文案资源缺失，请重新安装完整插件。')
    value = json.loads(source.read_text(encoding='utf-8-sig'))
    return validate(value.get('smalltalk'))


def validate(value):
    need(isinstance(value, dict) and set(value) == set(CATEGORIES), '文案须包含全部六个分类。')
    for rows in value.values():
        need(isinstance(rows, list) and 1 <= len(rows) <= 100, '每类须有1—100句。')
        need(all(isinstance(row, str) and 1 <= len(row.strip()) <= 80
                 and not any(ord(c) < 32 for c in row) for row in rows), '每句须为1—80字的单行文字。')
        need(len(rows) == len(set(rows)), '同类文案请去重。')
    return value


def handle(text, context):
    db = None
    try:
        sid, uid = identity(context)
        need(isinstance(text, str) and len(text) <= 200, '指令过长。')
        command, _, category = text.strip().partition(' ')
        need(command == '/搭话', '请使用 /搭话 [分类]。')
        category = category.strip()
        if category in ('分类', '帮助', 'help'):
            return '搭话分类：' + '、'.join(CATEGORIES) + '、随机\n/搭话 默认闲聊；/搭话 安慰。按指令回复，不会定时主动群发。'
        settings = context.get('settings', {})
        need(isinstance(settings, dict), '文案配置无效。')
        category = category or settings.get('default_category', '闲聊')
        if category == '随机':
            category = RNG.choice(CATEGORIES)
        need(category in CATEGORIES, '没有这个分类，请发送 /搭话 分类。')
        count = setting(context, 'reply_count', 1, 1, 3)
        custom = settings.get('custom_copy_json', '').strip()
        need(isinstance(custom, str) and len(custom) <= 12000, '自定义文案过长。')
        choices = validate(json.loads(custom)) if custom else bank()
        pool = list(choices[category])
        db = connect(context)
        db.execute('CREATE TABLE IF NOT EXISTS banter_last(scope TEXT,uid TEXT,category TEXT,body TEXT,updated REAL,PRIMARY KEY(scope,uid,category))')
        db.execute('BEGIN IMMEDIATE')
        now = time.time()
        db.execute('DELETE FROM banter_last WHERE updated<?', (now - 30 * 86400,))
        previous = db.execute('SELECT body FROM banter_last WHERE scope=? AND uid=? AND category=?', (sid, uid, category)).fetchone()
        if settings.get('avoid_repeat', True) and previous and len(pool) > 1:
            recent = set(previous['body'].split('\n'))
            available = [line for line in pool if line not in recent]
            if available:
                pool = available
        selected = RNG.sample(pool, min(count, len(pool)))
        reply = '\n'.join(selected)
        if not previous and db.execute('SELECT count(*) FROM banter_last').fetchone()[0] >= 10000:
            db.execute('DELETE FROM banter_last WHERE rowid IN (SELECT rowid FROM banter_last ORDER BY updated LIMIT 100)')
        db.execute('INSERT INTO banter_last VALUES(?,?,?,?,?) ON CONFLICT(scope,uid,category) DO UPDATE SET body=excluded.body,updated=excluded.updated',
                   (sid, uid, category, reply, now))
        db.commit()
        return reply
    except (RuleError, ValueError) as exc:
        if db:
            db.rollback()
        return str(exc) if isinstance(exc, RuleError) else '文案JSON格式无效，请检查六个分类。'
    except (OSError, sqlite3.Error, TypeError, AttributeError):
        if db:
            db.rollback()
        return '文案状态暂不可用，请管理员检查插件配置。'
    finally:
        if db:
            db.close()
