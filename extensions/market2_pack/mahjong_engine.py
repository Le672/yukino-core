"""Original structural Mahjong hand analysis and generated tenpai exercises.

No upstream question bank, scoring tables, money, network, or LLM is used.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import json
from pathlib import Path
import random
import re
import sqlite3

RNG=random.SystemRandom()
SUITS='mpsz'
ORPHANS=(0,8,9,17,18,26,27,28,29,30,31,32,33)
COMMANDS=('/听牌','/和牌','/麻将训练')
USAGE=('/听牌 123m123p123s1112z（13张）；/和牌 123m123p123s11122z（14张）；'
       '/麻将训练 开始 [标准|七对|国士]；状态；答 牌组；答案；投降。'
       'm万、p筒、s索、z字牌1东2南3西4北5白6发7中；不支持0赤牌或副露。'
       '仅判断4面子1雀头、7种各2张的七对子、国士无双结构，不判断役、番、点数或振听。')


def need(value,message):
    if not value: raise ValueError(message)


def setting(ctx,key,default,low,high):
    settings=ctx.get('settings',{}); value=settings.get(key,default) if isinstance(settings,dict) else default
    return value if type(value) is int and low<=value<=high else default


def parse_tiles(raw,expected=None,unique=False):
    need(isinstance(raw,str) and 0<len(raw)<=200,'请提供最多200字的牌组。')
    compact=''.join(raw.lower().split()); need(bool(compact),'牌组不能为空。')
    counts=[0]*34; cursor=0
    for match in re.finditer(r'([0-9]+)([mpsz])',compact):
        need(match.start()==cursor,'牌组格式为123m456p789s1234z；每段数字后接m/p/s/z。'); cursor=match.end()
        digits,suit=match.groups(); limit=7 if suit=='z' else 9; offset=SUITS.index(suit)*9
        for digit in digits:
            value=int(digit); need(1<=value<=limit,'序数牌仅1～9，字牌仅1～7；不支持0赤牌。')
            index=offset+value-1; counts[index]+=1
            need(counts[index]<=4,'同一种牌不能超过4张。')
            if unique: need(counts[index]<=1,'答案候选不能重复同一种牌。')
    need(cursor==len(compact),'牌组中存在未识别的字符或缺少花色。')
    if expected is not None: need(sum(counts)==expected,f'需要恰好{expected}张牌，当前{sum(counts)}张。')
    return tuple(counts)


def tile_name(index):
    return str(index%9+1)+SUITS[index//9]


def compact_hand(counts):
    result=[]
    for suit in range(4):
        size=7 if suit==3 else 9
        digits=''.join(str(i+1)*counts[suit*9+i] for i in range(size))
        if digits: result.append(digits+SUITS[suit])
    return ''.join(result)


@lru_cache(maxsize=8192)
def all_melds(counts):
    first=next((i for i,v in enumerate(counts) if v),None)
    if first is None: return True
    work=list(counts)
    if work[first]>=3:
        work[first]-=3
        if all_melds(tuple(work)): return True
        work[first]+=3
    if first<27 and first%9<=6 and work[first+1] and work[first+2]:
        work[first]-=1; work[first+1]-=1; work[first+2]-=1
        if all_melds(tuple(work)): return True
    return False


def winning_types(counts):
    need(len(counts)==34 and all(type(v) is int and 0<=v<=4 for v in counts),'内部牌组格式无效。')
    if sum(counts)!=14: return []
    result=[]
    for index,value in enumerate(counts):
        if value>=2:
            work=list(counts); work[index]-=2
            if all_melds(tuple(work)): result.append('4面子1雀头'); break
    if sum(v==2 for v in counts)==7 and all(v in (0,2) for v in counts): result.append('七对子（7种不同对子）')
    if all(counts[i]>=1 for i in ORPHANS) and sum(counts[i] for i in ORPHANS)==14 and sum(counts[i]==2 for i in ORPHANS)==1:
        result.append('国士无双')
    return result


def waits(counts):
    need(sum(counts)==13,'听牌分析需要13张。'); result=[]
    for index,value in enumerate(counts):
        if value>=4: continue
        work=list(counts); work[index]+=1; kinds=winning_types(tuple(work))
        if kinds: result.append((index,kinds,4-value))
    return result


def describe_waits(counts,limit=34):
    result=waits(counts)
    if not result: return '未听牌：补任意一张都不能形成支持的14张和牌结构。'
    lines=[f'结构听牌候选：{len(result)}种']
    for index,kinds,left in result[:limit]: lines.append(f'{tile_name(index)} · {" / ".join(kinds)} · 手牌外理论最多{left}张')
    if len(result)>limit: lines.append(f'只显示前{limit}种；面板可调整显示上限。')
    lines.append('仅结构候选；未扣牌河/他家牌/副露，不判断役、番、点数和振听。')
    return '\n'.join(lines)


def generate_hand(style):
    need(style in ('标准','七对','国士'),'训练类型可选：标准、七对、国士。')
    if style=='七对':
        work=[0]*34
        for index in RNG.sample(range(34),7): work[index]=2
    elif style=='国士':
        work=[0]*34
        for index in ORPHANS: work[index]=1
        work[RNG.choice(ORPHANS)]+=1
    else:
        work=None
        for _ in range(300):
            candidate=[0]*34; candidate[RNG.randrange(34)]=2
            for _ in range(4):
                if RNG.randrange(2):
                    index=RNG.randrange(34); candidate[index]+=3
                else:
                    index=RNG.randrange(3)*9+RNG.randrange(7)
                    for tile in (index,index+1,index+2): candidate[tile]+=1
            if max(candidate)<=4: work=candidate; break
        need(work is not None,'本次出题未完成，请重新开始。')
    removed=RNG.choice([i for i,v in enumerate(work) if v]); work[removed]-=1; counts=tuple(work)
    need(any(index==removed for index,_,_ in waits(counts)),'本次出题校验失败，请重新开始。')
    return counts


def identity(ctx):
    scope,user=str(ctx.get('session_id','')),str(ctx.get('user_id',''))
    need(scope.startswith(('p:','g:')) and 2<len(scope)<=100 and 0<len(user)<=40 and not any(ord(c)<32 for c in scope+user),'需要有效的当前会话和用户标识。')
    return scope,user


def db_open(ctx):
    value=ctx.get('data_dir'); root=Path(str(value or '')); need(value and root.is_absolute(),'需要内核分配的独立绝对数据目录。')
    root.mkdir(parents=True,exist_ok=True); path=root/'mahjong.sqlite3'; db=sqlite3.connect(path,timeout=3)
    try:
        path.chmod(0o600)
        db.execute('CREATE TABLE IF NOT EXISTS training(scope TEXT,user TEXT,body TEXT NOT NULL,PRIMARY KEY(scope,user))'); db.commit(); db.execute('BEGIN IMMEDIATE')
    except Exception: db.close(); raise
    return db


def save_state(db,scope,user,state):
    db.execute('INSERT INTO training VALUES(?,?,?) ON CONFLICT(scope,user) DO UPDATE SET body=excluded.body',(scope,user,json.dumps(state,ensure_ascii=False,separators=(',',':'))))


def training(arg,ctx):
    scope,user=identity(ctx); parts=arg.split(maxsplit=1); action=parts[0] if parts else '状态'; tail=parts[1] if len(parts)>1 else ''
    need(action in ('开始','状态','答','答案','投降','结束'),'用法：/麻将训练 开始 [标准|七对|国士]；状态；答 牌组；答案；投降。')
    db=db_open(ctx)
    try:
        row=db.execute('SELECT body FROM training WHERE scope=? AND user=?',(scope,user)).fetchone(); state=json.loads(row[0]) if row else None
        if action=='开始':
            need(state is None or state['done'],'本人的训练还在进行中，先提交答案或投降。')
            default=ctx.get('settings',{}).get('training_style','混合'); default=default if default in ('混合','标准','七对','国士') else '混合'
            style=tail or (RNG.choice(('标准','七对','国士')) if default=='混合' else default)
            counts=generate_hand(style); state={'counts':list(counts),'style':style,'attempts':[],'done':False,'correct':False,'max_attempts':setting(ctx,'max_attempts',5,1,20)}
            save_state(db,scope,user,state); db.commit()
            return f'麻将训练已开始（{style}出题）\n{compact_hand(counts)}\n请找出全部结构听牌候选，用 /麻将训练 答 牌组 提交；最多{state["max_attempts"]}次。'
        need(state is not None,'本会话还没有你的训练题，请先发送 /麻将训练 开始。')
        counts=tuple(state['counts'])
        if action=='状态':
            need(not tail,'状态只查询当前用户，不接受其他QQ或会话参数。'); db.rollback()
            return f"本人的麻将训练 · {'已结束' if state['done'] else '进行中'}\n{compact_hand(counts)}\n尝试{len(state['attempts'])}/{state['max_attempts']}次。" + ('\n已答对。' if state['correct'] else '')
        if action in ('答案','投降','结束'):
            need(not tail,'答案或结束只作用于本人的当前题，不接受其他用户参数。'); state['done']=True; save_state(db,scope,user,state); db.commit()
            return '本题已结束。\n'+describe_waits(counts)
        need(not state['done'],'本题已经结束，请重新开始。')
        submitted=parse_tiles(tail,unique=True); guesses=[i for i,v in enumerate(submitted) if v]
        need(1<=len(guesses)<=34,'请至少提交一种候选牌。'); correct={i for i,_,_ in waits(counts)}
        state['attempts'].append(guesses); state['correct']=set(guesses)==correct; state['done']=state['correct'] or len(state['attempts'])>=state['max_attempts']
        save_state(db,scope,user,state); db.commit()
        if state['correct']: return '全部候选答对了！\n'+describe_waits(counts)
        if state['done']: return '尝试次数已用完，本题结束。\n'+describe_waits(counts)
        return f"还不完整或存在错误候选，已记录第{len(state['attempts'])}次；剩余{state['max_attempts']-len(state['attempts'])}次。"
    finally: db.close()


def handle(text,context):
    if not isinstance(text,str) or not text.strip() or text.strip().split()[0] not in COMMANDS: return None
    try:
        need(len(text)<=1000,'麻将命令最多1000字。')
        head,*parts=text.strip().split(maxsplit=1); arg=parts[0] if parts else ''
        if arg in ('帮助','help','?'): return USAGE
        if head=='/听牌': return describe_waits(parse_tiles(arg,13),setting(context,'candidate_limit',34,1,34))
        if head=='/和牌':
            kinds=winning_types(parse_tiles(arg,14))
            return ('和牌结构：'+' / '.join(kinds) if kinds else '未形成支持的和牌结构。')+'\n仅结构判定，不计算役、番、点数或振听。'
        return training(arg,context)
    except (ValueError,TypeError,OverflowError) as exc: return '麻将操作未完成：'+str(exc)[:250]
    except (sqlite3.Error,OSError,json.JSONDecodeError): return '麻将训练存储暂不可用，本次操作未保存。'
