"""Original offline games; each ZIP embeds this module beside its worker.

State transitions run inside BEGIN IMMEDIATE. Solo games are scoped by both
session and user; multiplayer tables belong to a session and lock both seats.
"""
from __future__ import annotations

from collections import Counter, deque
from fractions import Fraction
import ast
import json
import importlib.util
from pathlib import Path
import random
import re
import sqlite3
import time

RNG = random.SystemRandom()
MULTI = {'x_connect4', 'x_tictactoe', 'x_gomoku', 'x_reversi'}
COMMANDS = {
    'x_connect4':'/四子棋', 'x_tictactoe':'/井字棋', 'x_gomoku':'/五子棋',
    'x_reversi':'/黑白棋', 'x_minesweeper':'/扫雷', 'x_sliding':'/滑块拼图',
    'x_2048':'/2048', 'x_hangman':'/吊小人', 'x_battleship':'/海战',
    'x_yahtzee':'/快艇骰子', 'x_highlow':'/高低牌', 'x_knight':'/骑士巡游',
    'x_lights':'/熄灯游戏', 'x_sokoban':'/推箱子', 'x_maze':'/迷宫',
    'x_wordchain':'/成语接龙', 'x_arithmetic':'/口算挑战', 'x_twentyfour':'/24点',
    'x_trivia':'/知识问答', 'x_anagram':'/乱序猜词', 'x_memory':'/翻牌记忆',
}
DIRECTIONS = {'上':(-1,0), '下':(1,0), '左':(0,-1), '右':(0,1)}

def setting(ctx, key, default, low, high):
    value = ctx.get('settings', {}).get(key, default)
    return min(high, max(low, value)) if type(value) is int else default

def database(ctx):
    path = Path(ctx.get('data_dir', ''))
    if not path.is_absolute(): raise ValueError('需要内核分配的独立数据目录。')
    path.mkdir(parents=True, exist_ok=True)
    target = path / 'games.sqlite3'
    db = sqlite3.connect(target, timeout=3, isolation_level=None)
    target.chmod(0o600)
    db.execute('CREATE TABLE IF NOT EXISTS games(kind TEXT, scope TEXT, who TEXT, body TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(kind,scope,who))')
    return db

_HELPERS={}
def load_helper(kind):
    filename='helper_board.py' if kind in MULTI or kind in {'x_yahtzee','x_wordchain','x_twentyfour'} else 'helper_puzzles.py' if kind in {'x_minesweeper','x_sliding','x_2048','x_lights','x_sokoban','x_maze','x_memory'} else 'helper_extra.py'
    if filename not in _HELPERS:
        spec=importlib.util.spec_from_file_location('yukino_games_'+filename[:-3],Path(__file__).with_name(filename))
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);_HELPERS[filename]=mod
    return _HELPERS[filename]

def handle(kind,text,context):
    if kind not in COMMANDS or not isinstance(text,str): return None
    bits=text.strip().split(maxsplit=1)
    if not bits or bits[0]!=COMMANDS[kind]: return None
    arg=bits[1].strip() if len(bits)>1 else ''
    if len(arg)>500: return '输入太长，最多500字符。'
    scope,user=str(context.get('session_id','')),str(context.get('user_id',''))
    if not scope or not user or len(scope)>100 or len(user)>50 or any(ord(c)<32 for c in scope+user): return '缺少有效会话或用户标识。'
    db=None
    try:
        db=database(context); db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM games WHERE updated<?',(time.time()-31*86400,))
        who='*' if kind in MULTI else user
        row=db.execute('SELECT body FROM games WHERE kind=? AND scope=? AND who=?',(kind,scope,who)).fetchone()
        s=json.loads(row[0]) if row else None
        module=load_helper(kind)
        updated,result=module.run(kind,arg,s,context)
        if updated is not None:
            body=json.dumps(updated,ensure_ascii=False,separators=(',',':'))
            if len(body)>30000: raise ValueError('游戏记录超过限制，请结束后重开。')
            db.execute('INSERT INTO games VALUES(?,?,?,?,?) ON CONFLICT(kind,scope,who) DO UPDATE SET body=excluded.body,updated=excluded.updated',(kind,scope,who,body,time.time()))
            db.execute('DELETE FROM games WHERE updated<?',(time.time()-31*86400,))
            # Bound the number of idle historical tables across this plugin.
            db.execute('DELETE FROM games WHERE rowid IN (SELECT rowid FROM games ORDER BY updated DESC LIMIT -1 OFFSET 5000)')
        db.commit(); return result
    except ValueError as exc:
        if db: db.rollback()
        return str(exc)
    except (sqlite3.Error, OSError, json.JSONDecodeError):
        if db: db.rollback()
        return '本次游戏数据未能保存，请稍后重试。'
    finally:
        if db: db.close()
