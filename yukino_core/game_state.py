"""Worker-local state adapter. No database state is forwarded to a model."""
from __future__ import annotations
import contextlib,hashlib,json,re,sqlite3,time
from pathlib import Path
from .game_catalog import BOARDS,INTERACTION,MULTIPLAYER


class GameError(ValueError):pass


def display_player_ids(text,labels):
    """Render identity slots in game replies, never arbitrary numeric content.

    The same renderer is used for public bot names and anonymous model views.
    In particular, hashes, room codes, bids, puzzle answers and story text may
    happen to contain (or equal) a player's numeric ID.
    """
    text=str(text)
    # Each expression captures only an engine-owned identity field. Keep the
    # rest of a speaker's text or a score's numeric value byte-for-byte intact.
    patterns=(
        r'(?P<prefix>轮到(?:队长)? |先由 |抽中 |卧底为 |发起者 |竞拍者 |虚拟落槌：|已选 |当前抢答：)(?P<ids>[1-9][0-9]{0,19})(?![A-Za-z0-9_])',
        r'(?m)^(?P<prefix>)(?P<ids>[1-9][0-9]{0,19})(?=：| (?:问：|抢到|回答[对错]|猜对了|获胜|出局|取走最后一颗|出价 ))',
        r'(?P<prefix>玩家：|已加入：|存活：|待选：)(?P<ids>[1-9][0-9]{0,19}(?:、[1-9][0-9]{0,19})*)(?![A-Za-z0-9_])',
        r'\A(?P<prefix>投票 |选 )(?P<ids>[1-9][0-9]{0,19})\Z',
        # Teams and score tables use repr(dict): IDs are quoted, scores are not.
        r"(?m)^(?P<prefix>(?:队伍：|选队完成：|(?:牌局结束。)?积分：)?)(?P<ids>\{[0-9'\", :\[\]]*\})",
    )
    for pattern in patterns:
        urls=[m.span() for m in re.finditer(r'https?://[^\s<>]+',text,re.I)]
        def render(match):
            start,end=match.span('ids')
            if any(start<b and end>a for a,b in urls):return match[0]
            ids=match['ids']
            if ids.startswith('{'):
                ids=re.sub(r"(['\"])([1-9][0-9]{0,19})\1",lambda m:m[1]+labels.get(m[2],m[2])+m[1],ids)
            else:ids='、'.join(labels.get(uid,uid) for uid in ids.split('、'))
            return match['prefix']+ids
        text=re.sub(pattern,render,text)
    return text


class Frame:
    def __init__(self,pid,context,owner=None):
        self.pid=pid;self.ctx=context;self.sid=str(context.get('session_id',''));self.owner=str(owner or context.get('user_id',''))
        self.bot=str(context.get('self_id',''))
        if not re.fullmatch('[1-9][0-9]{0,19}',self.bot):raise GameError('请先在平台连接中配置机器人QQ号，再邀请AI。')
        if not re.fullmatch('[pg]:[1-9][0-9]{0,19}',self.sid) or not re.fullmatch('[1-9][0-9]{0,19}',self.owner):raise GameError('游戏会话身份无效。')
        if self.sid.startswith('p:') and self.sid!='p:'+self.owner:raise GameError('私聊游戏身份不匹配。')
        self.root=Path(context.get('data_dir',''))
        if not self.root.is_absolute() or self.root.is_symlink():raise GameError('游戏数据目录无效。')
        self.root.mkdir(parents=True,exist_ok=True)
        self.key='*' if pid in MULTIPLAYER else self.owner
        self.file,self.where,self.params,self.table=self.location()

    def location(self):
        p,s,u=self.pid,self.sid,self.owner
        if p in INTERACTION:return 'interaction.sqlite3','scope=?',(s,),'rooms'
        if p.startswith('x_'):return 'games.sqlite3','kind=? AND scope=? AND who=?',(p,s,'*' if p in BOARDS else u),'games'
        if p.startswith('s_'):return 'puzzles.sqlite3','pid=? AND sid=? AND uid=?',(p,s,u),'games'
        if p in ('wordle','guess_number'):return 'plugin.sqlite3','kind=? AND scope=? AND user=?',(p,s,u),'states'
        if p=='m_mahjong':return 'mahjong.sqlite3','scope=? AND user=?',(s,u),'training'
        if p=='m_uno':return 'uno.sqlite3','scope=?',(s,),'rooms'
        return '', '', (), ''

    def read(self):
        if not self.file:return None
        path=self.root/self.file
        if not path.exists():return None
        if path.is_symlink():raise GameError('拒绝链接游戏状态。')
        with contextlib.closing(sqlite3.connect(path,timeout=3)) as db:
            query='SELECT body FROM '+self.table+' WHERE '+self.where
            if self.pid=='m_uno':query+=" ORDER BY CASE WHEN phase IN ('lobby','playing') THEN 0 ELSE 1 END,updated DESC"
            row=db.execute(query+' LIMIT 1',self.params).fetchone()
        if not row:return None
        if len(row[0].encode())>120000:raise GameError('游戏状态超过限制。')
        result=json.loads(row[0])
        if not isinstance(result,dict):raise GameError('游戏状态无效。')
        return result

    def write(self,state):
        # Only original worker rules or the specialised undercover public-turn
        # extension call this; model text never controls a table/path/query.
        body=json.dumps(state,ensure_ascii=False,separators=(',',':'))
        if len(body.encode())>100000:raise GameError('游戏状态超过限制。')
        with contextlib.closing(sqlite3.connect(self.root/self.file,timeout=3)) as db:
            with db:db.execute('UPDATE '+self.table+' SET body=? WHERE '+self.where,(body,*self.params))

    def meta_db(self):
        path=self.root/'game-ai.sqlite3'
        if path.is_symlink():raise GameError('拒绝链接AI记录。')
        db=sqlite3.connect(path,timeout=3,isolation_level=None);path.chmod(0o600)
        db.execute('CREATE TABLE IF NOT EXISTS participants(scope TEXT,who TEXT,body TEXT,updated REAL,PRIMARY KEY(scope,who))')
        return db

    def meta(self,db):
        row=db.execute('SELECT body FROM participants WHERE scope=? AND who=?',(self.sid,self.key)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_meta(self,db,meta):
        body=json.dumps(meta,ensure_ascii=False,separators=(',',':'))
        if len(body.encode())>50000:raise GameError('AI游戏记录已满。')
        db.execute('INSERT INTO participants VALUES(?,?,?,?) ON CONFLICT(scope,who) DO UPDATE SET body=excluded.body,updated=excluded.updated',(self.sid,self.key,body,time.time()))
        db.execute('DELETE FROM participants WHERE updated<?',(time.time()-31*86400,))
        db.execute('DELETE FROM participants WHERE rowid IN (SELECT rowid FROM participants ORDER BY updated DESC LIMIT -1 OFFSET 2000)')

    def actor(self):
        return {**self.ctx,'user_id':self.bot if self.pid in MULTIPLAYER else self.owner}

    def label(self,uid,state):
        if uid==self.bot:return '你'
        players=state.get('players',[])
        return '玩家'+str(players.index(uid)+1) if uid in players else '主持人'

    def public(self,text,state):
        labels={uid:self.label(uid,state) for uid in set(state.get('players',[]))|{self.owner,self.bot}}
        return display_player_ids(text,labels)[:7000]


def alive(state):
    return bool(state) and not state.get('done') and state.get('phase','playing') not in ('done','quit','won','lost')


def revision(state,meta):
    relevant={k:v for k,v in meta.items() if k!='seen_public'}
    return hashlib.sha256(json.dumps([state,relevant],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
