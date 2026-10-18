"""Original text-only 108-card UNO. No network, models, or proactive messages."""
from __future__ import annotations

import json
from pathlib import Path
import random
import re
import sqlite3
import time

RNG=random.SystemRandom()
COLORS=('红','黄','绿','蓝')
ALPHABET='ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def make_cards():
    cards=[]
    for color in COLORS:
        cards.append((color,'0'))
        for value in tuple(str(n) for n in range(1,10))+('跳过','反转','+2'):
            cards.extend([(color,value),(color,value)])
    cards.extend([('', '万能')]*4+[('', '+4')]*4)
    return tuple(cards)


CARDS=make_cards()
HELP='''/UNO 创建：在群里建立随机房间码的新桌；/UNO 加入；创建者或机器人主人 /UNO 开始（2—6人）。
主动私聊机器人 /UNO 手牌 房间码 [页码] 查看自己的编号手牌；群内禁止查看手牌。
轮到你时 /UNO 出 编号 [红/黄/绿/蓝]；万能牌和+4必须指定颜色；或 /UNO 摸牌，摸一张后只能出刚摸的牌或 /UNO 跳过。
手中剩1张后、下一个有效行动前发送 /UNO 喊UNO；漏喊将在下一次有效行动前自动补抽2张。
同色、同数字/符号或万能可出；+4要求手中没有当前颜色的牌，不叠罚、不质疑；动作罚抽后跳过受罚者，两人反转等于跳过。最后动作牌先执行罚抽再结算。
/UNO 状态 只显示顶牌、手牌数量和座位；/UNO 结束 仅创建者或机器人主人。房间操作按群隔离，不能冒领座位。仅积分娱乐，没有下注。'''


def need(test,message):
    if not test: raise ValueError(message)


def option(ctx,key,default,low,high):
    settings=ctx.get('settings',{})
    value=settings.get(key,default) if isinstance(settings,dict) else default
    return value if type(value) is int and low<=value<=high else default


def identity(ctx):
    user=str(ctx.get('user_id','')); scope=str(ctx.get('session_id',''))
    need(re.fullmatch(r'[1-9][0-9]{0,19}',user) is not None,'缺少有效的QQ用户标识。')
    need(re.fullmatch(r'[gp]:[1-9][0-9]{0,19}',scope) is not None,'缺少有效的QQ会话标识。')
    if scope.startswith('p:'): need(scope=='p:'+user,'私聊会话与当前用户不匹配。')
    return scope,user


def database(ctx):
    raw=ctx.get('data_dir');need(isinstance(raw,str) and Path(raw).is_absolute(),'需要内核分配的独立data_dir。')
    root=Path(raw);root.mkdir(parents=True,exist_ok=True)
    target=root/'uno.sqlite3'
    db=sqlite3.connect(target,timeout=3,isolation_level=None)
    try:
        target.chmod(0o600)
        db.execute('CREATE TABLE IF NOT EXISTS rooms(code TEXT PRIMARY KEY,scope TEXT NOT NULL,phase TEXT NOT NULL,body TEXT NOT NULL,updated REAL NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS room_scope ON rooms(scope,updated DESC)')
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS room_active_scope ON rooms(scope) WHERE phase IN ('lobby','playing')")
    except BaseException:
        db.close();raise
    return db


def label(card_id):
    color,value=CARDS[card_id]
    return color+value


def validate(s):
    need(isinstance(s,dict) and s.get('version')==1,'房间数据格式异常，请管理员检查插件数据。')
    players=s.get('players')
    need(isinstance(players,list) and 1<=len(players)<=6 and len(set(players))==len(players),'房间座位数据异常。')
    need(all(isinstance(p,str) and re.fullmatch(r'[1-9][0-9]{0,19}',p) for p in players),'房间玩家数据异常。')
    need(s.get('creator') in players and s.get('phase') in ('lobby','playing','done'),'房间状态异常。')
    need(type(s.get('max_players')) is int and 2<=s['max_players']<=6 and len(players)<=s['max_players'],'房间人数上限异常。')
    need(isinstance(s.get('scope'),str) and re.fullmatch(r'g:[1-9][0-9]{0,19}',s['scope']),'房间会话数据异常。')
    need(isinstance(s.get('code'),str) and len(s['code'])==6 and all(c in ALPHABET for c in s['code']),'房间码数据异常。')
    need(isinstance(s.get('hands'),dict) and set(s['hands'])==set(players),'房间手牌数据异常。')
    all_ids=[]
    for value in list(s['hands'].values())+[s.get('deck'),s.get('discard')]:
        need(isinstance(value,list) and len(value)<=108 and all(type(v) is int and 0<=v<108 for v in value),'房间牌堆数据异常。')
        all_ids.extend(value)
    if s.get('started'):
        need(sorted(all_ids)==list(range(108)) and len(s['discard'])>=1,'房间牌数不守恒，请管理员检查插件数据。')
        need(type(s.get('turn')) is int and 0<=s['turn']<len(players) and s.get('direction') in (-1,1),'房间回合数据异常。')
        need(s.get('color') in COLORS,'房间颜色数据异常。')
        drawn=s.get('drawn')
        need(drawn is None or (type(drawn) is int and (drawn==-1 or drawn in s['hands'][players[s['turn']]])),'房间摸牌状态异常。')
        need(s.get('uno_pending') is None or (s['uno_pending'] in players and len(s['hands'][s['uno_pending']])==1),'房间UNO状态异常。')
        need(type(s.get('moves')) is int and 0<=s['moves']<=5000 and type(s.get('max_moves')) is int and 50<=s['max_moves']<=5000,'房间回合计数异常。')
    else:
        need(s['phase'] in ('lobby','done') and not all_ids,'等待房间不应持有已发牌。')


def load(row):
    if row is None: return None
    need(len(row[0])<=30000,'房间记录过大，请管理员检查插件数据。')
    s=json.loads(row[0]);validate(s);return s


def save(db,s):
    validate(s)
    body=json.dumps(s,ensure_ascii=False,separators=(',',':'))
    need(len(body)<=30000,'房间记录过大。')
    db.execute('INSERT INTO rooms VALUES(?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET phase=excluded.phase,body=excluded.body,updated=excluded.updated',
        (s['code'],s['scope'],s['phase'],body,time.time()))


def public(s):
    lines=['UNO房间 '+s['code']+' · '+{'lobby':'等待加入','playing':'进行中','done':'已结束'}[s['phase']]]
    for i,user in enumerate(s['players']):
        lines.append(f'玩家{i+1}：'+str(len(s['hands'][user]))+'张'+('（创建者）' if user==s['creator'] else ''))
    if s.get('started'):
        lines.append('顶牌：'+label(s['discard'][-1])+'；当前颜色：'+s['color'])
        if s['phase']=='playing':
            lines.append(f'轮到玩家{s["turn"]+1}；方向：'+('顺序' if s['direction']==1 else '逆序'))
            if s.get('drawn') is not None: lines.append('当前玩家已摸牌，可出刚摸的牌或跳过。')
            if s.get('uno_pending'): lines.append('有人剩1张，别忘了喊UNO。')
    if s['phase']=='lobby': lines.append(f'人数{len(s["players"])}/{s["max_players"]}；/UNO 加入，创建者 /UNO 开始。')
    if s['phase']=='done': lines.append(s.get('result','本桌已结束。'))
    lines.append('手牌只在私聊获取：/UNO 手牌 '+s['code'])
    return '\n'.join(lines)


def private_hand(s,user,page,size):
    need(user in s['players'],'你不是这个房间的玩家，不能查看手牌。')
    hand=s['hands'][user]
    if not s.get('started'): return 'UNO房间 '+s['code']+' 尚未开始，尚未发牌。'
    pages=max(1,(len(hand)+size-1)//size)
    need(1<=page<=pages,f'页码范围1—{pages}。')
    start=(page-1)*size
    lines=[f'UNO房间 {s["code"]} · 你的手牌（玩家{s["players"].index(user)+1}）',f'共{len(hand)}张；第{page}/{pages}页']
    lines.extend(f'{i+1}. {label(v)}' for i,v in enumerate(hand[start:start+size],start))
    if not hand: lines.append('手牌已空。')
    if s['phase']=='playing':
        lines.append('顶牌：'+label(s['discard'][-1])+'；当前颜色：'+s['color'])
        if s['players'][s['turn']]==user:
            lines.append('现在轮到你，请回原群发送 /UNO 出 编号 [颜色]。')
            if s.get('drawn') is not None:
                lines.append('本回合没有可摸的牌，只能跳过。' if s['drawn']==-1 else '本回合刚摸的牌编号：'+str(hand.index(s['drawn'])+1))
    return '\n'.join(lines)


def draw(s,user,amount):
    taken=0
    for _ in range(amount):
        if not s['deck'] and len(s['discard'])>1:
            s['deck']=s['discard'][:-1];s['discard']=s['discard'][-1:];RNG.shuffle(s['deck'])
        if not s['deck']: break
        s['hands'][user].append(s['deck'].pop());taken+=1
    return taken


def missed_uno(s):
    user=s.get('uno_pending')
    if not user: return ''
    s['uno_pending']=None
    if len(s['hands'][user])!=1: return ''
    count=draw(s,user,2)
    return f'玩家{s["players"].index(user)+1}漏喊UNO，补抽{count}张'+('（牌堆不足）' if count<2 else '')+'。\n'


def advance(s,steps=1):
    s['turn']=(s['turn']+s['direction']*steps)%len(s['players']);s['drawn']=None


def is_owner(ctx,user):
    owners=ctx.get('owner_ids',[])
    return isinstance(owners,(list,tuple)) and user in {str(v) for v in owners}


def start_game(s,ctx):
    need(s['phase']=='lobby','本桌已经开始或结束。')
    need(2<=len(s['players'])<=s['max_players'],'至少需要2位玩家加入。')
    deck=list(range(108));RNG.shuffle(deck)
    s['deck']=deck;s['discard']=[]
    for _ in range(7):
        for user in s['players']: s['hands'][user].append(s['deck'].pop())
    # A numeric first discard avoids ambiguous opening action-card effects.
    at=next(i for i,v in enumerate(s['deck']) if CARDS[v][1].isdigit())
    top=s['deck'].pop(at);s['discard']=[top]
    s.update(phase='playing',started=True,turn=0,direction=1,color=CARDS[top][0],
        drawn=None,uno_pending=None,moves=0,max_moves=option(ctx,'max_moves',1000,50,5000))


def limit_end(s):
    if s['phase']=='playing' and s['moves']>=s['max_moves']:
        minimum=min(len(v) for v in s['hands'].values())
        winners=[str(i+1) for i,u in enumerate(s['players']) if len(s['hands'][u])==minimum]
        s['phase']='done';s['uno_pending']=None
        s['result']='达到设定回合上限；手牌最少：玩家'+ '、'.join(winners)+f'（{minimum}张，并列同胜）。'


def play_card(s,user,parts):
    need(len(parts) in (2,3) and re.fullmatch(r'[1-9][0-9]{0,2}',parts[1]),'用法：/UNO 出 编号 [红/黄/绿/蓝]。')
    hand=s['hands'][user];index=int(parts[1])-1
    need(0<=index<len(hand),'手牌编号不存在，请私聊 /UNO 手牌 房间码 获取当前编号。')
    chosen=hand[index];color,value=CARDS[chosen]
    if not color:
        need(len(parts)==3 and parts[2] in COLORS,'万能牌或+4必须指定红/黄/绿/蓝之一。')
        next_color=parts[2]
    else:
        need(len(parts)==2,'有颜色的牌不需要额外指定颜色。');next_color=color
    need(s.get('drawn') is None or s['drawn']==chosen,'摸牌后只能出刚摸到的那一张，或发送 /UNO 跳过。')
    note=missed_uno(s)
    top_value=CARDS[s['discard'][-1]][1]
    need(not color or color==s['color'] or value==top_value,'这张牌与当前颜色、数字或符号都不匹配。')
    if value=='+4': need(not any(CARDS[v][0]==s['color'] for v in hand),'你手中仍有当前颜色的牌，不能使用+4。')
    hand.remove(chosen);s['discard'].append(chosen);s['color']=next_color
    s['drawn']=None
    steps=1
    if value=='反转':
        s['direction']*=-1
        if len(s['players'])==2: steps=2
    elif value=='跳过': steps=2
    elif value in ('+2','+4'):
        victim_index=(s['turn']+s['direction'])%len(s['players']);victim=s['players'][victim_index]
        amount=2 if value=='+2' else 4;taken=draw(s,victim,amount);steps=2
        note+=f'玩家{victim_index+1}罚抽{taken}张并跳过'+('（牌堆不足）' if taken<amount else '')+'。\n'
    s['moves']+=1
    note+='玩家'+str(s['players'].index(user)+1)+'打出 '+label(chosen)+('，选择'+next_color if not color else '')+'。\n'
    if not hand:
        s['phase']='done';s['uno_pending']=None;s['result']='玩家'+str(s['players'].index(user)+1)+'手牌出完，获胜！'
        # Drawing penalties above finish before this winner is recorded.
        advance(s,steps)
    else:
        if len(hand)==1: s['uno_pending']=user
        advance(s,steps);limit_end(s)
    return note+public(s)


def turn_action(s,user,parts):
    need(s['phase']=='playing','本桌尚未开始或已经结束。')
    need(user in s['players'],'你不是本桌玩家，不能操作。')
    need(s['players'][s['turn']]==user,'还没轮到你，不能代替其他玩家行动。')
    action=parts[0]
    if action=='出': return play_card(s,user,parts)
    need(len(parts)==1,'此操作不需要额外参数。')
    if action=='摸牌':
        need(s.get('drawn') is None,'本回合已经摸过一次，请出刚摸的牌或跳过。')
        note=missed_uno(s);taken=draw(s,user,1)
        s['drawn']=s['hands'][user][-1] if taken else -1
        return note+('已摸1张。请主动私聊查看手牌；可出刚摸的牌或跳过。\n' if taken else '抽牌堆与可回收弃牌均不足，请发送 /UNO 跳过。\n')+public(s)
    if action=='跳过':
        need(s.get('drawn') is not None,'请先摸一张牌，摸牌后才能跳过；不能直接放弃回合。')
        note=missed_uno(s);advance(s);s['moves']+=1;limit_end(s)
        return note+'已跳过本回合。\n'+public(s)
    raise ValueError('未知回合操作。')


def room_code(raw):
    value=raw.upper()
    need(len(value)==6 and all(c in ALPHABET for c in value),'房间码应为6位字母或数字，请复制群内公开的房间码。')
    return value


def process(db,parts,scope,user,ctx):
    action=parts[0]
    if action=='手牌':
        need(scope=='p:'+user,'群内禁止展示手牌。请主动私聊机器人：/UNO 手牌 房间码 [页码]。')
        need(len(parts) in (2,3),'私聊用法：/UNO 手牌 房间码 [页码]。')
        code=room_code(parts[1]);row=db.execute('SELECT body FROM rooms WHERE code=?',(code,)).fetchone()
        if row is None: return '房间不存在或已经过期。'
        s=load(row);need(user in s['players'],'你不是这个房间的玩家，不能查看手牌。')
        if len(parts)==3: need(re.fullmatch(r'[1-9][0-9]{0,2}',parts[2]),'页码必须为正整数。')
        return private_hand(s,user,int(parts[2]) if len(parts)==3 else 1,option(ctx,'hand_page_size',30,10,60))
    need(scope.startswith('g:'),'请在群聊创建和操作UNO房间；私聊只用于 /UNO 手牌 房间码。')
    if action=='创建':
        need(len(parts)==1,'创建房间会自动生成随机码，不需要自己指定。')
        row=db.execute("SELECT body FROM rooms WHERE scope=? AND phase IN ('lobby','playing')",(scope,)).fetchone()
        if row: return '本群已有未结束房间，请先使用当前桌或由创建者结束。\n'+public(load(row))
        cap=option(ctx,'max_rooms',200,10,1000)
        count=db.execute('SELECT COUNT(*) FROM rooms').fetchone()[0]
        if count>=cap:
            db.execute("DELETE FROM rooms WHERE code IN (SELECT code FROM rooms WHERE phase='done' ORDER BY updated ASC LIMIT ?)",(count-cap+1,))
        need(db.execute('SELECT COUNT(*) FROM rooms').fetchone()[0]<cap,'房间总量达到上限，请等待旧桌结束或过期。')
        code=None
        for _ in range(12):
            candidate=''.join(RNG.choice(ALPHABET) for _ in range(6))
            if db.execute('SELECT 1 FROM rooms WHERE code=?',(candidate,)).fetchone() is None: code=candidate;break
        need(code is not None,'暂未分配到房间码，请稍后重试。')
        s={'version':1,'code':code,'scope':scope,'phase':'lobby','creator':user,'players':[user],
            'hands':{user:[]},'deck':[],'discard':[],'started':False,'max_players':option(ctx,'max_players',6,2,6)}
        save(db,s);return 'UNO房间已创建。\n'+public(s)
    need(action in ('加入','开始','状态','结束','出','摸牌','跳过','喊UNO','喊uno'),'未知指令。发送 /UNO 帮助 查看完整玩法。')
    row=db.execute("SELECT body FROM rooms WHERE scope=? ORDER BY CASE WHEN phase IN ('lobby','playing') THEN 0 ELSE 1 END,updated DESC LIMIT 1",(scope,)).fetchone()
    if row is None: return '本群没有有效UNO房间，请发送 /UNO 创建。'
    s=load(row)
    if action in ('加入','开始','状态','结束'):
        need(len(parts) in (1,2),'此操作只接受可选的当前房间码。')
        if len(parts)==2: need(room_code(parts[1])==s['code'],'房间码不是本群当前房间，不能跨群操作。')
    if action=='状态': return public(s)
    if action=='加入':
        need(s['phase']=='lobby','游戏已开始或结束，不能中途加入或替换座位。')
        need(user not in s['players'],'你已经在本桌，不能重复加入或冒领其他座位。')
        need(len(s['players'])<s['max_players'],'房间座位已满。')
        s['players'].append(user);s['hands'][user]=[];save(db,s)
        return f'已加入玩家{len(s["players"])}座位。\n'+public(s)
    if action in ('开始','结束'):
        need(user==s['creator'] or is_owner(ctx,user),'只有创建者或机器人主人可以开始/结束本桌。')
        if action=='开始': start_game(s,ctx);reply='UNO已开始，每人7张。主动私聊机器人领取自己的手牌编号。\n'
        else:
            need(s['phase']!='done','本桌已经结束。')
            s['phase']='done';s['uno_pending']=None;s['result']='创建者或机器人主人结束了本桌。';reply=''
        save(db,s);return reply+public(s)
    if action in ('喊UNO','喊uno'):
        need(len(parts)==1,'喊UNO不接受指定别人或座位参数。')
        need(s['phase']=='playing' and user in s['players'],'仅进行中对局的本人玩家可以喊UNO。')
        need(len(s['hands'][user])==1,'只有自己手中恰好剩1张时才能喊UNO。')
        if s.get('uno_pending')==user: s['uno_pending']=None;save(db,s)
        return '玩家'+str(s['players'].index(user)+1)+'喊UNO！'
    reply=turn_action(s,user,parts);save(db,s);return reply


def handle(text,context):
    if not isinstance(text,str): return None
    if len(text)>200:
        return '输入过长；UNO指令最多200字符、5段。' if re.match(r'^\s*/(?:UNO|uno)(?:\s|$)',text) else None
    pieces=text.strip().split()
    if not pieces or pieces[0] not in ('/UNO','/uno'): return None
    if len(pieces)>5: return '输入过长；UNO指令最多200字符、5段。'
    if len(pieces)==1 or pieces[1] in ('帮助','help','?'): return HELP
    db=None
    try:
        scope,user=identity(context)
        db=database(context);db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM rooms WHERE updated<?',(time.time()-option(context,'expiry_days',31,1,31)*86400,))
        reply=process(db,pieces[1:],scope,user,context);db.commit();return reply
    except ValueError as exc:
        if db: db.rollback()
        return str(exc)
    except (sqlite3.Error,OSError,json.JSONDecodeError,KeyError,TypeError,IndexError):
        if db: db.rollback()
        return 'UNO房间数据暂不可用，本次操作未提交，请稍后重试或请管理员检查。'
    finally:
        if db: db.close()
