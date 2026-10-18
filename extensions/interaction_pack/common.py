"""Transactional per-plugin, per-conversation game rooms, without network IO."""
import json,re,secrets,sqlite3,time
from pathlib import Path

RNG=secrets.SystemRandom()
ALPHABET='ABCDEFGHJKMNPQRSTUVWXYZ23456789'
class RuleError(ValueError):pass
def need(value,message):
    if not value:raise RuleError(message)
def words(value,limit=400):
    value=str(value).strip()
    need(0<len(value)<=limit and not any(ord(c)<32 and c not in '\n\t' for c in value),'文字不能为空或超过长度限制。')
    return value
def integer(value,lo,hi):
    need(isinstance(value,str) and re.fullmatch(r'[0-9]{1,8}',value or ''),'请输入有效整数。')
    n=int(value);need(lo<=n<=hi,f'数字范围为{lo}—{hi}。');return n
def identity(ctx):
    uid=ctx.get('user_id');sid=ctx.get('session_id')
    need(isinstance(uid,str) and re.fullmatch(r'[1-9][0-9]{0,19}',uid),'用户身份无效。')
    need(isinstance(sid,str) and re.fullmatch(r'[pg]:[1-9][0-9]{0,19}',sid),'会话身份无效。')
    need(not sid.startswith('p:') or sid=='p:'+uid,'私聊身份不匹配。')
    return sid,uid
def host(room,uid,ctx):
    need(uid==room['host'] or uid in ctx.get('owner_ids',[]),'仅本房间发起者或机器人主人可操作。')
def active(room):need(room is not None and room['phase']!='done','当前没有进行中的房间，请先创建。')
def member(room,uid):need(uid in room['players'],'请先加入本房间。')
def connect(ctx):
    raw=ctx.get('data_dir');need(isinstance(raw,str) and Path(raw).is_absolute(),'缺少内核分配的数据目录。')
    root=Path(raw);root.mkdir(parents=True,exist_ok=True);target=root/'interaction.sqlite3'
    need(not target.is_symlink(),'拒绝链接状态文件。')
    db=sqlite3.connect(target,timeout=3,isolation_level=None);target.chmod(0o600)
    db.execute('CREATE TABLE IF NOT EXISTS rooms(scope TEXT PRIMARY KEY,code TEXT UNIQUE NOT NULL,body TEXT NOT NULL,updated REAL NOT NULL)')
    return db
def new_room(db,sid,uid,ctx,**extra):
    old=load(db,sid)
    need(old is None or old['phase']=='done','已有进行中的房间，请先结束。')
    limit=int(ctx.get('settings',{}).get('max_players',12))
    for _ in range(20):
        code=''.join(RNG.choice(ALPHABET) for _ in range(8))
        if not db.execute('SELECT 1 FROM rooms WHERE code=?',(code,)).fetchone():break
    else:raise RuleError('暂时无法分配房间码。')
    return dict(scope=sid,code=code,host=uid,players=[uid],phase='lobby',max_players=limit,**extra)
def load(db,sid=None,code=None):
    if code is not None:
        need(re.fullmatch(r'[A-Z2-9]{8}',code or ''),'房间码无效。')
        row=db.execute('SELECT body FROM rooms WHERE code=?',(code,)).fetchone()
    else:row=db.execute('SELECT body FROM rooms WHERE scope=?',(sid,)).fetchone()
    if not row:return None
    value=json.loads(row[0]);need(isinstance(value,dict) and value.get('phase') in ('lobby','playing','done'),'状态数据异常。');return value
def save(db,room):
    body=json.dumps(room,ensure_ascii=False,separators=(',',':'))
    need(len(body.encode())<=100000,'房间数据已达上限，请结束本局。')
    need(len(room['players'])<=room['max_players'],'人数已达上限。')
    db.execute('INSERT INTO rooms VALUES(?,?,?,?) ON CONFLICT(scope) DO UPDATE SET code=excluded.code,body=excluded.body,updated=excluded.updated',(room['scope'],room['code'],body,time.time()))
def join(room,uid):
    need(room['phase']=='lobby','本局已经开始。');need(uid not in room['players'],'你已经加入了。')
    need(len(room['players'])<room['max_players'],'人数已满。');room['players'].append(uid)
def listing(room):return '、'.join(room['players'])

def execute(pid,text,ctx,dispatch,help_text):
    db=None
    try:
        sid,uid=identity(ctx);arg=text.partition(' ')[2].strip();cmd,_,tail=arg.partition(' ')
        if cmd in ('','帮助','help'):return help_text
        db=connect(ctx);db.execute('BEGIN IMMEDIATE')
        # Remove only expired game rooms in this plugin; never source/user files.
        db.execute('DELETE FROM rooms WHERE updated<?',(time.time()-30*86400,))
        need(db.execute('SELECT count(*) FROM rooms').fetchone()[0]<2000 or db.execute('SELECT 1 FROM rooms WHERE scope=?',(sid,)).fetchone(),'房间数量已达上限。')
        room=load(db,sid)
        result,changed=dispatch(pid,cmd,tail,sid,uid,ctx,db,room)
        if changed is not None:save(db,changed)
        db.commit();return result
    except RuleError as e:
        if db:db.rollback()
        return str(e)
    except (sqlite3.Error,OSError,json.JSONDecodeError,KeyError,TypeError) as e:
        if db:db.rollback()
        return '状态暂不可用，请管理员检查插件数据；本次操作未保存。'
    finally:
        if db:db.close()
