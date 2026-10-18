"""Original, bounded command utilities. Copied into each independent local plugin ZIP."""
from __future__ import annotations
import base64
from collections import Counter
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sqlite3
import time
from urllib.parse import quote,unquote
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError

RNG=random.SystemRandom()
WORDS=tuple('apple beach bread brain chair charm chess cloud coral crane dream drink earth flame flour fresh frost giant grape grass green heart honey horse house lemon light lucky mango metal mouse music ocean olive paper peach pearl piano plant plate queen quiet radio river robot round royal salad scale shape sheep shine shore skill sleep smile snake snows sound space spark spice spoon sport stage stamp stone storm sugar sweet table tiger toast torch touch train treat trust under urban video voice water whale wheel white world young zebra'.split())
WORDS=tuple(w for w in WORDS if len(w)==5)
CARDS=(('愚者','留一点空间给新的尝试'),('魔术师','先使用手边已有的资源'),('女祭司','先观察，再作判断'),('皇后','照顾好正在成长的事物'),('皇帝','让规则和计划更加清晰'),('教皇','请教有经验的人'),('恋人','把你的选择说明白'),('战车','选定方向后稳步前进'),('力量','温和也可以很有力量'),('隐者','给自己一段安静的思考时间'),('命运之轮','变化中留一点余地'),('正义','把事实和感受分开看'),('倒吊人','换一个角度再看问题'),('死神','允许旧计划结束'),('节制','找到可以持续的节奏'),('恶魔','留意让你分心的习惯'),('高塔','先检查计划里脆弱的地方'),('星星','给长期目标留一盏灯'),('月亮','暂时不确定也没有关系'),('太阳','记下今天已经完成的小事'),('审判','重新审视上一次决定'),('世界','给这一段旅程一个收尾'))
ANSWERS=('可以先做一个小尝试。','再收集一点信息。','给它一天时间。','把目标拆小一点。','先完成最容易的一步。','听听不同的意见。','先把手头的事情做好。','答案也许藏在你已经知道的事情里。','换个角度看一看。','留一点调整的余地。','今天适合整理思路。','不妨把问题写下来。','把决定权留给自己。','下一次尝试可能会更清楚。','先确认最重要的条件。','做完准备再出发。','先问问自己真正想要什么。','慢一点也能走到。','给新想法一点空间。','这件事值得认真想一想。')

def now():return datetime.now(timezone(timedelta(hours=8)))

def clean(value,maximum=300):return re.sub(r'[\x00-\x08\x0b-\x1f\x7f]',' ',str(value))[:maximum]

def option(ctx,key,default,lo,hi):
    value=ctx.get('settings',{}).get(key,default)
    return max(lo,min(hi,value)) if type(value) is int else default

def identity(ctx):
    scope,user=str(ctx.get('session_id','')),str(ctx.get('user_id',''))
    if not scope or not user or len(scope)>100 or len(user)>40:raise ValueError('缺少有效会话或用户标识。')
    return scope,user

def database(ctx):
    value=ctx.get('data_dir');root=Path(value or '')
    if not value or not root.is_absolute():raise ValueError('插件需要内核分配独立数据目录。')
    root.mkdir(parents=True,exist_ok=True);path=root/'plugin.sqlite3'
    db=sqlite3.connect(path,timeout=2,isolation_level=None);path.chmod(0o600)
    db.executescript('''CREATE TABLE IF NOT EXISTS states(kind TEXT,scope TEXT,user TEXT,body TEXT,PRIMARY KEY(kind,scope,user));
    CREATE TABLE IF NOT EXISTS entries(id INTEGER PRIMARY KEY,kind TEXT,scope TEXT,user TEXT,body TEXT,active INTEGER DEFAULT 1,created REAL);
    CREATE INDEX IF NOT EXISTS entry_lookup ON entries(kind,scope,user,active);
    CREATE TABLE IF NOT EXISTS polls(id TEXT PRIMARY KEY,scope TEXT,user TEXT,title TEXT,options TEXT,active INTEGER,created REAL);
    CREATE TABLE IF NOT EXISTS votes(poll TEXT,user TEXT,choice INTEGER,PRIMARY KEY(poll,user));
    CREATE TABLE IF NOT EXISTS greetings(scope TEXT,user TEXT,kind TEXT,day TEXT,stamp REAL,PRIMARY KEY(scope,user,kind,day));''')
    return db

def get_state(db,kind,scope,user):
    row=db.execute('SELECT body FROM states WHERE kind=? AND scope=? AND user=?',(kind,scope,user)).fetchone()
    return json.loads(row[0]) if row else None

def put_state(db,kind,scope,user,value):
    db.execute('INSERT INTO states VALUES(?,?,?,?) ON CONFLICT(kind,scope,user) DO UPDATE SET body=excluded.body',(kind,scope,user,json.dumps(value,ensure_ascii=False)))

def score_word(answer,guess):
    result=['⬜']*5;remaining=Counter()
    for i,(a,g) in enumerate(zip(answer,guess)):
        if a==g:result[i]='🟩'
        else:remaining[a]+=1
    for i,g in enumerate(guess):
        if result[i]=='⬜' and remaining[g]>0:result[i]='🟨';remaining[g]-=1
    return ''.join(result)

def _games(kind,arg,ctx):
    scope,user=identity(ctx)
    db=database(ctx)
    try:
        db.execute('BEGIN IMMEDIATE')
        state=get_state(db,kind,scope,user)
        if arg in ('开始','new','start'):
            if state and not state.get('done'):db.rollback();return '已有进行中的游戏，先猜或发送 投降。'
            answer=RNG.choice(WORDS) if kind=='wordle' else ''.join(RNG.sample('0123456789',4))
            state={'answer':answer,'guesses':[],'done':False,'started':time.time()};put_state(db,kind,scope,user,state);db.commit()
            return '猜词开始：发送 /猜词 五个英文字母，🟩位置正确，🟨字母存在但位置不同，最多6次。' if kind=='wordle' else '猜数字开始：4位不重复数字，可含开头0。/猜数字 1234；A是数字位置都对，B是数字对位置错。'
        if not state:db.rollback();return ('/猜词' if kind=='wordle' else '/猜数字')+' 开始 创建本人的游戏。'
        if arg in ('投降','结束'):
            if state.get('done'):db.rollback();return '游戏已结束，发送 开始 可重开。'
            state['done']=True;put_state(db,kind,scope,user,state);db.commit();return '本局答案：'+state['answer']
        if arg in ('状态',''):
            db.rollback();return ('已结束。' if state.get('done') else '进行中。')+'\n'+('\n'.join(state['guesses']) or '尚未猜测。')
        if state.get('done'):db.rollback();return '本局已结束，发送 开始 可重开。'
        arg=arg.lower()
        if kind=='wordle':
            if not re.fullmatch('[a-z]{5}',arg):db.rollback();return '请输入5个英文字母；本版接受任意5字母组合，不验证词典。'
            result=score_word(state['answer'],arg);row=arg+' '+result;maximum=6
        else:
            if not re.fullmatch('[0-9]{4}',arg) or len(set(arg))!=4:db.rollback();return '请输入4位不重复数字。'
            a=sum(x==y for x,y in zip(arg,state['answer']));b=len(set(arg)&set(state['answer']))-a
            row=arg+f' → {a}A{b}B';maximum=option(ctx,'max_guesses',10,4,20)
        state['guesses'].append(row);won=arg==state['answer'];state['done']=won or len(state['guesses'])>=maximum
        put_state(db,kind,scope,user,state);db.commit()
        suffix='\n猜中了！' if won else '\n次数用完，答案：'+state['answer'] if state['done'] else f'\n剩余{maximum-len(state["guesses"])}次。'
        return '\n'.join(state['guesses'])+suffix
    finally:db.close()

def _rps(arg,ctx):
    scope,user=identity(ctx);choices=('石头','剪刀','布')
    db=database(ctx)
    try:
        db.execute('BEGIN IMMEDIATE');state=get_state(db,'rps',scope,user) or {'胜':0,'负':0,'平':0}
        if arg=='战绩':db.rollback();return '本会话猜拳战绩：'+str(state)
        if arg not in choices:db.rollback();return '用法：/猜拳 石头、/猜拳 剪刀、/猜拳 布、/猜拳 战绩。'
        other=RNG.choice(choices);result='平' if arg==other else '胜' if (choices.index(arg)+1)%3==choices.index(other) else '负'
        state[result]+=1;put_state(db,'rps',scope,user,state);db.commit();return f'你出{arg}，我出{other}：'+{'胜':'你赢了！','负':'我赢了。','平':'平局。'}[result]
    finally:db.close()

def _entries(kind,arg,ctx):
    scope,user=identity(ctx);action,_,body=arg.partition(' ');kind=kind or 'todo_notes'
    is_quote=kind=='quotes';is_memo=kind=='memo';table_kind='quotes' if is_quote else 'memo' if is_memo else 'todo'
    db=database(ctx)
    try:
        if action in ('添加','收录'):
            if not 1<=len(body.strip())<=300:return '内容须为1—300字。'
            db.execute('BEGIN IMMEDIATE')
            count=db.execute('SELECT count(*) FROM entries WHERE kind=? AND scope=? AND user=? AND active=1',(table_kind,scope,user)).fetchone()[0]
            if count>=option(ctx,'max_entries',100,1,500):db.rollback();return '条目达到上限，请先整理旧记录。'
            rid=db.execute('INSERT INTO entries(kind,scope,user,body,created) VALUES(?,?,?,?,?)',(table_kind,scope,user,clean(body),time.time())).lastrowid;db.commit()
            return f'已保存，编号{rid}。'
        if action in ('完成','删除'):
            if not body.isdigit():return '请填写有效编号。'
            query='UPDATE entries SET active=0 WHERE id=? AND kind=? AND scope=? AND active=1';params=[int(body),table_kind,scope]
            if not is_quote or user not in set(map(str,ctx.get('owner_ids',[]))):query+=' AND user=?';params.append(user)
            n=db.execute(query,params).rowcount
            return '已归档这条记录。' if n else '未找到你可以修改的本会话记录。'
        conditions='kind=? AND scope=? AND active=1';params=[table_kind,scope]
        if not is_quote:conditions+=' AND user=?';params.append(user)
        rows=db.execute('SELECT id,user,body FROM entries WHERE '+conditions+' ORDER BY id DESC LIMIT 500',params).fetchall()
        if is_quote and action in ('随机',''):
            if not rows:return '暂无语录，可用 /语录 收录 内容 添加。'
            rid,author,value=RNG.choice(rows);return f'语录#{rid}｜收录者{author}\n'+value
        if is_memo and action=='查' and body:rows=[r for r in rows if body in r[2]]
        if action not in ('列表','查','', '随机'):return ('/语录 收录 内容、随机、列表、删除 编号。' if is_quote else '/便签 添加 内容、列表、查 关键词、删除 编号。' if is_memo else '/待办 添加 内容、列表、完成 编号、删除 编号。')
        return ('本会话语录' if is_quote else '本会话我的便签' if is_memo else '本会话我的待办')+'：\n'+('\n'.join(f'{rid}. {value}' for rid,_,value in rows[:12]) or '暂无记录。')
    finally:db.close()

def _polls(arg,ctx):
    scope,user=identity(ctx);action,_,rest=arg.partition(' ');db=database(ctx)
    try:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT id,user,title,options FROM polls WHERE scope=? AND active=1 ORDER BY created DESC LIMIT 1',(scope,)).fetchone()
        if action=='发起':
            if row:db.rollback();return '本会话已有进行中的投票，先关闭旧投票。'
            pieces=[p.strip() for p in rest.split('|')]
            if not 3<=len(pieces)<=11 or not all(1<=len(x)<=80 for x in pieces) or len(set(pieces[1:]))!=len(pieces)-1:db.rollback();return '用法：/投票 发起 问题|选项1|选项2（2—10个不同选项）。'
            rid=hashlib.sha256((scope+str(time.time_ns())).encode()).hexdigest()[:10]
            db.execute('INSERT INTO polls VALUES(?,?,?,?,?,1,?)',(rid,scope,user,pieces[0],json.dumps(pieces[1:],ensure_ascii=False),time.time()));db.commit()
            return f'投票 {rid}：{pieces[0]}\n'+'\n'.join(f'{i}. {v}' for i,v in enumerate(pieces[1:],1))+'\n用 /投票 选 编号 投票，每个QQ一票。'
        if not row:db.rollback();return '没有进行中的投票。用法：/投票 发起 问题|选项1|选项2。'
        rid,creator,title,encoded=row;options=json.loads(encoded)
        if action in ('选','投'):
            if not rest.isdigit() or not 1<=int(rest)<=len(options):db.rollback();return '请输入有效选项编号。'
            if not ctx.get('settings',{}).get('allow_change_vote',True) and db.execute('SELECT 1 FROM votes WHERE poll=? AND user=?',(rid,user)).fetchone():db.rollback();return '已经投票，本投票不允许改票。'
            db.execute('INSERT INTO votes VALUES(?,?,?) ON CONFLICT(poll,user) DO UPDATE SET choice=excluded.choice',(rid,user,int(rest)));db.commit();return '已记录你的这一票。'
        if action=='关闭':
            if user!=creator and user not in set(map(str,ctx.get('owner_ids',[]))):db.rollback();return '只有发起者或机器人主人能关闭此投票。'
            db.execute('UPDATE polls SET active=0 WHERE id=?',(rid,));db.commit()
        elif action not in ('结果','状态',''):db.rollback();return '/投票 选 编号、结果、关闭。'
        counts=dict(db.execute('SELECT choice,count(*) FROM votes WHERE poll=? GROUP BY choice',(rid,)).fetchall())
        if db.in_transaction:db.commit()
        return ('投票已关闭｜' if action=='关闭' else '投票结果｜')+title+'\n'+'\n'.join(f'{i}. {x}：{counts.get(i,0)}票' for i,x in enumerate(options,1))
    finally:db.close()

def _greeting(command,ctx):
    scope,user=identity(ctx);kind='早安' if command=='/早安' else '晚安';moment=now();day=moment.date().isoformat();db=database(ctx)
    try:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM greetings WHERE scope=? AND user=? AND kind=? AND day=?',(scope,user,kind,day)).fetchone():db.rollback();return '今天的'+kind+'已经记下了。'
        rank=db.execute('SELECT count(*) FROM greetings WHERE scope=? AND kind=? AND day=?',(scope,kind,day)).fetchone()[0]+1
        previous=db.execute("SELECT max(stamp) FROM greetings WHERE scope=? AND user=? AND kind='晚安'",(scope,user)).fetchone()[0]
        db.execute('INSERT INTO greetings VALUES(?,?,?,?,?)',(scope,user,kind,day,moment.timestamp()));db.commit()
        suffix=''
        if kind=='早安' and previous and 0<moment.timestamp()-previous<172800:
            minutes=int((moment.timestamp()-previous)/60);suffix=f'\n距上次晚安消息过去{minutes//60}小时{minutes%60}分钟。'
        return f'{kind}！你是本会话今天第{rank}位说{kind}的人（UTC+8）。'+suffix
    finally:db.close()

def handle(kind,text,ctx):
    try:
        parts=text.strip().split(maxsplit=1)
        if not parts:return None
        command=parts[0];arg=parts[1] if len(parts)>1 else ''
        if len(arg)>4000:return '输入太长，请缩短后再试。'
        if kind=='daily_luck':
            scope,user=identity(ctx);day=now().date().isoformat();seed=hashlib.sha256((day+'|'+scope+'|'+user).encode()).digest();score=int.from_bytes(seed[:4],'big')%101
            return f'今日小签｜{day} UTC+8\n幸运值 {score}/100\n今日灵感：'+ANSWERS[seed[4]%len(ANSWERS)]+'\n娱乐小签，同一会话当天固定。'
        if kind=='tarot':
            count=3 if arg in ('三张','3') else 1 if arg in ('','单张','1') else 0
            if not count:return '用法：/塔罗 单张 或 /塔罗 三张。'
            rows=RNG.sample(CARDS,count)
            return '娱乐抽牌｜写作与思考灵感\n'+'\n'.join(f'{i}. {name}（{RNG.choice(("正位","逆位"))}）：{idea}' for i,(name,idea) in enumerate(rows,1))
        if kind=='answer_book':return ('答案之书｜'+clean(arg,120)+'\n' if arg else '答案之书｜\n')+RNG.choice(ANSWERS)+'\n随机文字签，仅供娱乐。'
        if kind=='decision':
            if command=='/硬币':return RNG.choice(('正面','反面'))
            if command=='/随机数':
                m=re.fullmatch(r'(-?\d{1,9})\s+(-?\d{1,9})',arg)
                if not m or int(m[1])>int(m[2]):return '用法：/随机数 最小整数 最大整数（各最多9位）。'
                return str(RNG.randint(int(m[1]),int(m[2])))
            choices=[clean(x.strip(),80) for x in re.split(r'[|｜]',arg) if x.strip()]
            if not 2<=len(choices)<=30 or len(choices)!=len(set(choices)):return '用法：/选择 选项1|选项2，支持2—30个不同选项。'
            return '随机选中了：'+RNG.choice(choices)
        if kind in ('wordle','guess_number'):return _games(kind,arg,ctx)
        if kind=='rps':return _rps(arg,ctx)
        if kind=='todo_notes':return _entries('memo' if command=='/便签' else 'todo',arg,ctx)
        if kind=='quotes':return _entries('quotes',arg,ctx)
        if kind=='polls':return _polls(arg,ctx)
        if kind=='daily_greetings':return _greeting(command,ctx)
        if kind=='time_tools':
            if command=='/倒数':
                try:date=datetime.strptime(arg,'%Y-%m-%d').date()
                except ValueError:return '用法：/倒数 YYYY-MM-DD，按UTC+8日历日计算。'
                days=(date-now().date()).days;return arg+(' 还有'+str(days)+'天。' if days>0 else ' 已过去'+str(-days)+'天。' if days<0 else ' 就是今天。')
            zone=timezone(timedelta(hours=8));label=arg or 'UTC+8'
            match=re.fullmatch(r'UTC([+-])(\d{1,2})(?::(00|15|30|45))?',label,re.I)
            if label.upper()=='UTC':zone=timezone.utc
            elif match:
                hours=int(match[2]);minutes=int(match[3] or 0)
                if hours>14 or (hours==14 and minutes):return 'UTC偏移绝对值最多14小时。'
                zone=timezone((1 if match[1]=='+' else -1)*timedelta(hours=hours,minutes=minutes))
            elif arg:
                try:zone=ZoneInfo(label)
                except (ZoneInfoNotFoundError,ValueError):return '时区未知或本环境没有该时区数据。可用 /时间 UTC+8、UTC、UTC-5:30。'
            return datetime.now(zone).strftime('%Y-%m-%d %H:%M:%S %z')+'｜'+label
        if kind=='text_tools':
            if command=='/字数':return f'字符{len(arg)}，去空白字符{len(re.sub(r"\s","",arg))}，行数{len(arg.splitlines()) if arg else 0}。'
            if command=='/Base64':
                if len(arg.encode())>1000:return '编码原文最多1000个UTF-8字节，避免发送不完整的编码结果。'
                return base64.b64encode(arg.encode()).decode()
            if command=='/解Base64':
                try:return clean(base64.b64decode(arg,validate=True).decode('utf-8'),3000)
                except (ValueError,UnicodeError):return '不是有效的UTF-8 Base64文本。'
            if command=='/哈希':return 'SHA-256：'+hashlib.sha256(arg.encode()).hexdigest()
            if command=='/URL编码':
                if len(arg.encode())>1000:return '编码原文最多1000个UTF-8字节。'
                return quote(arg,safe='')
            if command=='/URL解码':return clean(unquote(arg,errors='strict'),3000)
        return '请查看插件指令说明。'
    except (ValueError,OverflowError) as exc:return clean(str(exc),200) or '输入格式不正确。'
    except (sqlite3.Error,OSError):return '插件数据暂不可用，请稍后再试。'
