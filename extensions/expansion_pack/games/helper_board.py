"""Original multiplayer boards and three stateful offline games."""
from __future__ import annotations
import ast
from collections import Counter
from fractions import Fraction
import random
import re

RNG=random.SystemRandom()
COMMANDS={'x_connect4':'/四子棋','x_tictactoe':'/井字棋','x_gomoku':'/五子棋','x_reversi':'/黑白棋','x_yahtzee':'/快艇骰子','x_wordchain':'/成语接龙','x_twentyfour':'/24点'}

def coord(arg, n):
    parts = re.fullmatch(r'(\d{1,2})[ ,，]+(\d{1,2})', arg.strip())
    if not parts: raise ValueError(f'请输入 行 列，范围1—{n}。')
    r,c = (int(x)-1 for x in parts.groups())
    if not (0 <= r < n and 0 <= c < n): raise ValueError(f'行列必须在1—{n}之间。')
    return r,c

def board_text(board, n, pieces=('·','●','○')):
    lines = ['  '+' '.join(str(i+1) for i in range(n))]
    for r in range(len(board)//n):
        lines.append(str(r+1)+' '+' '.join(pieces[v] for v in board[r*n:(r+1)*n]))
    return '\n'.join(lines)

def line_win(board, n, height, r, c, length):
    p = board[r*n+c]
    if not p: return False
    for dr,dc in ((1,0),(0,1),(1,1),(1,-1)):
        count=1
        for sign in (-1,1):
            rr,cc=r+sign*dr,c+sign*dc
            while 0<=rr<height and 0<=cc<n and board[rr*n+cc]==p:
                count+=1; rr+=sign*dr; cc+=sign*dc
        if count>=length: return True
    return False

def flips(board,r,c,p,n=6):
    if board[r*n+c]: return []
    found=[]
    for dr in (-1,0,1):
        for dc in (-1,0,1):
            if dr==dc==0: continue
            rr,cc=r+dr,c+dc; run=[]
            while 0<=rr<n and 0<=cc<n and board[rr*n+cc]==3-p:
                run.append(rr*n+cc); rr+=dr; cc+=dc
            if run and 0<=rr<n and 0<=cc<n and board[rr*n+cc]==p: found.extend(run)
    return found

def reversi_moves(board,p):
    return [(r,c) for r in range(6) for c in range(6) if flips(board,r,c,p)]

def mp_view(kind,s):
    n={'x_connect4':7,'x_tictactoe':3,'x_gomoku':9,'x_reversi':6}[kind]
    text=board_text(s['board'],n)
    if s.get('done'): return text+'\n'+s.get('result','本局已结束。')
    if len(s['players'])<2: return text+'\n等待另一位玩家发送 '+COMMANDS[kind]+' 加入。'
    next_name='机器人' if s['players'][s['turn']]=='BOT' else '玩家'+str(s['turn']+1)
    return text+'\n轮到'+next_name+'；'+('发送列号。' if kind=='x_connect4' else '发送 行 列。')

def multiplayer(kind,arg,s,user,ctx):
    if arg=='帮助': return s,USAGE[kind]
    if arg in ('开始','开始 人机'):
        if s and not s.get('done'): return s,'本桌正在进行，请先完成或由玩家结束。\n'+mp_view(kind,s)
        if arg=='开始 人机' and kind!='x_tictactoe': return s,'只有井字棋支持人机模式，其余棋类需要另一位玩家加入。'
        n={'x_connect4':7,'x_tictactoe':3,'x_gomoku':9,'x_reversi':6}[kind]
        s={'board':[0]*(42 if kind=='x_connect4' else n*n),'players':[user],'turn':0,'done':False,'creator':user}
        if arg=='开始 人机': s['players'].append('BOT')
        if kind=='x_reversi':
            for r,c,p in ((2,2,2),(3,3,2),(2,3,1),(3,2,1)): s['board'][r*n+c]=p
        return s,'对局已开始。\n'+mp_view(kind,s)
    if not s: return None,COMMANDS[kind]+' 开始 创建对局；第二位玩家发送 加入。井字棋还支持 开始 人机。'
    if arg in ('','状态'): return s,mp_view(kind,s)
    if s.get('done'): return s,'本局已结束，发送 开始 重开。\n'+mp_view(kind,s)
    if arg=='加入':
        if user in s['players']: return s,'你已经在本桌。'
        if len(s['players'])>=2: return s,'座位已满，旁观者不能替换玩家。'
        s['players'].append(user); return s,'玩家2已加入。\n'+mp_view(kind,s)
    if user not in s['players']: return s,'你是旁观者，不能落子、认输或结束本桌。'
    if arg in ('结束','认输','投降'):
        s['done']=True; s['result']='玩家'+str(s['players'].index(user)+1)+'认输/结束本桌。'
        return s,mp_view(kind,s)
    if len(s['players'])<2: return s,'等待另一位玩家加入。'
    if user!=s['players'][s['turn']]: return s,'还没轮到你。'
    board=s['board']; p=s['turn']+1
    if kind=='x_connect4':
        if not re.fullmatch('[1-7]',arg): return s,'请输入1—7的列号。'
        c=int(arg)-1; empties=[r for r in range(6) if not board[r*7+c]]
        if not empties: return s,'这一列已经满了，请选其他列。'
        r=max(empties); board[r*7+c]=p; won=line_win(board,7,6,r,c,4)
    elif kind=='x_reversi':
        moves=reversi_moves(board,p)
        if arg=='跳过':
            if moves: return s,'你还有合法落点，不能跳过。'
            s['turn']=1-s['turn']; return s,'已跳过。\n'+mp_view(kind,s)
        if not moves: return s,'你没有合法落点，请发送 跳过。'
        r,c=coord(arg,6); changed=flips(board,r,c,p)
        if not changed: return s,'非法落点：必须夹住并翻转至少一颗对方棋子。'
        board[r*6+c]=p
        for at in changed: board[at]=p
        if not reversi_moves(board,1) and not reversi_moves(board,2):
            a,b=board.count(1),board.count(2); s['done']=True
            s['result']=f'黑白棋结束：黑{a}，白{b}；'+('平局。' if a==b else f'玩家{1 if a>b else 2}获胜。')
        s['turn']=1-s['turn']; return s,mp_view(kind,s)
    else:
        n=3 if kind=='x_tictactoe' else 9; r,c=coord(arg,n)
        if board[r*n+c]: return s,'该位置已有棋子，不能覆盖。'
        board[r*n+c]=p; won=line_win(board,n,n,r,c,3 if n==3 else 5)
    if won: s['done']=True; s['result']=f'玩家{p}获胜！'
    elif all(board): s['done']=True; s['result']='棋盘已满，平局。'
    else: s['turn']=1-s['turn']
    if kind=='x_tictactoe' and not s['done'] and s['players'][s['turn']]=='BOT':
        available=[i for i,x in enumerate(board) if not x]; selected=None
        for piece in (2,1):
            for i in available:
                trial=board.copy(); trial[i]=piece
                if line_win(trial,3,3,i//3,i%3,3): selected=i; break
            if selected is not None: break
        if selected is None: selected=4 if 4 in available else RNG.choice(available)
        board[selected]=2
        if line_win(board,3,3,selected//3,selected%3,3): s['done']=True; s['result']='机器人获胜。'
        elif all(board): s['done']=True; s['result']='平局。'
        s['turn']=0
    return s,mp_view(kind,s)


def option(ctx,key,default,low,high):
    value=ctx.get('settings',{}).get(key,default)
    return max(low,min(high,value)) if type(value) is int else default


YAHTZEE_CATEGORIES=('一','二','三','四','五','六','三条','四条','葫芦','小顺','大顺','快艇','机会')

def dice_score(dice,category):
    counts=Counter(dice); values=set(dice); total=sum(dice)
    if category in YAHTZEE_CATEGORIES[:6]:
        face=YAHTZEE_CATEGORIES.index(category)+1
        return counts[face]*face
    if category=='三条': return total if max(counts.values())>=3 else 0
    if category=='四条': return total if max(counts.values())>=4 else 0
    if category=='葫芦': return 25 if sorted(counts.values())==[2,3] else 0
    if category=='小顺': return 30 if any(set(range(start,start+4))<=values for start in (1,2,3)) else 0
    if category=='大顺': return 40 if values in (set(range(1,6)),set(range(2,7))) else 0
    if category=='快艇': return 50 if max(counts.values())==5 else 0
    if category=='机会': return total
    raise ValueError('未知计分类别：'+ '、'.join(YAHTZEE_CATEGORIES))

def dice_view(s):
    rows='；'.join(k+'='+str(v) for k,v in s['scores'].items()) or '尚未计分'
    upper=sum(s['scores'].get(c,0) for c in YAHTZEE_CATEGORIES[:6])
    bonus=35 if upper>=63 else 0
    total=sum(s['scores'].values())+bonus
    if s.get('done'): return f'快艇骰子结束。总分 {total}，上半区奖励 {bonus}。\n'+rows
    return f'第 {len(s["scores"])+1}/{s["round_limit"]} 回合，已掷{s["rolls"]}/3次\n位置 1 2 3 4 5\n骰子 '+ ' '.join(map(str,s['dice']))+'\n'+rows+'\n/快艇骰子 保留 1 3（重掷其余）；/快艇骰子 计分 类别'

def yahtzee(arg,s,ctx):
    if arg=='帮助': return s,USAGE['x_yahtzee']
    if arg=='开始':
        if s and not s.get('done'): return s,'本局尚未结束。\n'+dice_view(s)
        s={'dice':[RNG.randint(1,6) for _ in range(5)],'rolls':1,'scores':{},'round_limit':option(ctx,'round_limit',13,3,13),'done':False}
        return s,'快艇骰子已开始；仅为积分游戏。\n'+dice_view(s)
    if not s: return None,USAGE['x_yahtzee']
    if arg in ('','状态'): return s,dice_view(s)
    if s.get('done'): return s,'本局已结束，发送 开始 重开。\n'+dice_view(s)
    if arg in ('结束','投降'): s['done']=True; return s,dice_view(s)
    if arg.startswith(('保留','重掷')):
        if s['rolls']>=3: raise ValueError('本回合已掷3次，请选择类别计分。')
        pieces=arg.split(); action=pieces[0]
        if action not in ('保留','重掷') or any(not re.fullmatch('[1-5]',p) for p in pieces[1:]): raise ValueError('位置必须为1—5，例如 保留 1 3；重掷 2 4。')
        indexes=[int(v)-1 for v in pieces[1:]]
        if len(set(indexes))!=len(indexes): raise ValueError('位置不能重复。')
        reroll=[i for i in range(5) if i not in indexes] if action=='保留' else indexes
        if not reroll: raise ValueError('至少选择一颗骰子重掷；可直接发送 计分 类别。')
        for i in reroll: s['dice'][i]=RNG.randint(1,6)
        s['rolls']+=1; return s,dice_view(s)
    if arg.startswith('计分 '):
        category=arg[3:].strip()
        if category in s['scores']: raise ValueError('这个类别已经用过，每类只能计分一次。')
        score=dice_score(s['dice'],category); s['scores'][category]=score
        if len(s['scores'])>=s['round_limit']: s['done']=True
        else: s['dice']=[RNG.randint(1,6) for _ in range(5)]; s['rolls']=1
        return s,f'{category}记 {score} 分。\n'+dice_view(s)
    raise ValueError(USAGE['x_yahtzee'])


# A small, manually selected common idiom graph. No remote dictionary or assets.
IDIOMS=tuple('一心一意 意气风发 发扬光大 大功告成 成千上万 万众一心 心想事成 成竹在胸 胸有成竹 竹报平安 安居乐业 业精于勤 勤能补拙 拙嘴笨舌 舌战群儒 儒雅风流 流连忘返 返老还童 童叟无欺 欺人太甚 甚嚣尘上 上善若水 水到渠成 成家立业 业余时间 间不容发 发愤图强 强词夺理 理直气壮 壮志凌云 云开见日 日新月异 异口同声 声东击西 西装革履 履险如夷 夷然自若 若无其事 事半功倍 倍道而进 进退两难 难能可贵 贵人多忘 忘恩负义 义不容辞 辞旧迎新 新陈代谢 谢天谢地 地大物博 博学多才 才高八斗 斗志昂扬 扬眉吐气 气象万千 千方百计 计上心来 来日方长 长治久安 安然无恙 恙虫传病 病从口入 入木三分 分秒必争 争先恐后 后起之秀 秀外慧中 中流砥柱 柱石之坚 坚持不懈 懈怠成疾 疾风知劲草 草木皆兵 兵临城下 下不为例 例行公事 事与愿违 违心之论 论功行赏 赏心悦目 目不转睛 睛若秋波 波澜壮阔 阔步高谈 谈笑风生 生龙活虎 虎头蛇尾 尾大不掉 掉以轻心 心平气和 和风细雨 雨过天晴 晴空万里 里应外合 合情合理 理所当然 然荻读书 书香门第 第一次见 见多识广 广开言路 路不拾遗 遗世独立 立竿见影 影影绰绰 绰绰有余 余音绕梁 梁上君子 子虚乌有 有备无患 患得患失 失而复得 得心应手 手到擒来'.split())
# Exclude a few ordinary phrases: only four-character idioms used by this game.
IDIOMS=tuple(w for w in IDIOMS if len(w)==4 and w not in {'业余时间','恙虫传病','第一次见','懈怠成疾','贵人多忘','睛若秋波'})

def chain_view(s):
    return ('成语接龙已结束。' if s.get('done') else f'第{s["rounds"]}/{s["max_rounds"]}轮；请接「{s["last"][-1]}」开头的词典成语。')+'\n最近：'+' → '.join(s['used'][-8:])

def wordchain(arg,s,ctx):
    if arg=='帮助': return s,USAGE['x_wordchain']
    if arg=='开始':
        if s and not s.get('done'): return s,'本局尚未结束。\n'+chain_view(s)
        s={'last':'一心一意','used':['一心一意'],'rounds':0,'max_rounds':option(ctx,'max_rounds',20,3,30),'done':False}
        return s,'成语接龙已开始；本地小词典，按文字首尾相接，不按拼音。\n'+chain_view(s)
    if not s: return None,USAGE['x_wordchain']
    if arg in ('','状态'): return s,chain_view(s)
    if s.get('done'): return s,'本局已结束，发送 开始 重开。'
    if arg in ('结束','投降'): s['done']=True; return s,'你结束了本局。\n'+chain_view(s)
    choices=[w for w in IDIOMS if w.startswith(s['last'][-1]) and w not in s['used']]
    if arg=='提示': return s,'可用提示：'+('、'.join(choices[:3]) if choices else '本地词典已无可用词，你可发送 结束。')
    if arg not in IDIOMS: raise ValueError('本地小词典未收录这个成语。发送 提示 查看可选词；不代表它一定不是成语。')
    if arg in s['used']: raise ValueError('这个成语已经用过。')
    if arg[0]!=s['last'][-1]: raise ValueError('需要以「'+s['last'][-1]+'」开头。')
    s['used'].append(arg); s['last']=arg; s['rounds']+=1
    replies=[w for w in IDIOMS if w.startswith(arg[-1]) and w not in s['used']]
    if not replies: s['done']=True; return s,'我接不出了，你赢了！\n'+chain_view(s)
    response=RNG.choice(replies); s['used'].append(response); s['last']=response
    if s['rounds']>=s['max_rounds']: s['done']=True; return s,'达到设定轮数，挑战完成！\n'+chain_view(s)
    return s,'我接：'+response+'\n'+chain_view(s)


def solve24(numbers):
    def visit(items):
        if len(items)==1: return items[0][1] if items[0][0]==24 else None
        for i in range(len(items)):
            for j in range(i+1,len(items)):
                a,ea=items[i];b,eb=items[j]; rest=[v for k,v in enumerate(items) if k not in (i,j)]
                options=[(a+b,f'({ea}+{eb})'),(a*b,f'({ea}*{eb})'),(a-b,f'({ea}-{eb})'),(b-a,f'({eb}-{ea})')]
                if b: options.append((a/b,f'({ea}/{eb})'))
                if a: options.append((b/a,f'({eb}/{ea})'))
                seen=set()
                for value,expr in options:
                    if value in seen: continue
                    seen.add(value); found=visit(rest+[(value,expr)])
                    if found: return found
        return None
    return visit([(Fraction(v),str(v)) for v in numbers])

def calculate24(expression,numbers):
    if len(expression)>120 or not re.fullmatch(r'[0-9+*/()\s-]+',expression): raise ValueError('仅允许给定整数、括号和 + - * /，不支持函数、乘方或小数。')
    try: tree=ast.parse(expression.strip(),mode='eval')
    except (SyntaxError,RecursionError): raise ValueError('算式格式错误。') from None
    if len(list(ast.walk(tree)))>40: raise ValueError('算式过长。')
    leaves=[]
    def calc(node,depth=0):
        if depth>12: raise ValueError('括号嵌套太深。')
        if isinstance(node,ast.Constant) and type(node.value) is int:
            if not 1<=node.value<=13: raise ValueError('只能使用给定的四个整数。')
            leaves.append(node.value); return Fraction(node.value)
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
            v=calc(node.operand,depth+1); return -v if isinstance(node.op,ast.USub) else v
        if not isinstance(node,ast.BinOp) or not isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)): raise ValueError('仅允许四则运算。')
        left,right=calc(node.left,depth+1),calc(node.right,depth+1)
        if isinstance(node.op,ast.Add): return left+right
        if isinstance(node.op,ast.Sub): return left-right
        if isinstance(node.op,ast.Mult): return left*right
        if not right: raise ValueError('不能除以零。')
        return left/right
    result=calc(tree.body)
    if Counter(leaves)!=Counter(numbers): raise ValueError('必须把给定的四个数字各用一次，不能遗漏、拼接或增添。')
    return result

def twentyfour(arg,s,ctx):
    if arg=='帮助': return s,USAGE['x_twentyfour']
    if arg=='开始':
        if s and not s.get('done'): return s,'本局尚未结束。数字：'+' '.join(map(str,s['numbers']))
        for _ in range(40):
            numbers=[RNG.randint(1,10) for _ in range(4)]; solution=solve24(numbers)
            if solution: break
        else: numbers=[3,3,8,8];solution='8/(3-8/3)'
        s={'numbers':numbers,'solution':solution,'tries':0,'max_attempts':option(ctx,'max_attempts',10,3,30),'done':False}
        return s,'24点已开始。数字：'+' '.join(map(str,numbers))+'\n每数恰用一次，四则运算与括号，发送 /24点 你的算式。'
    if not s: return None,USAGE['x_twentyfour']
    if arg in ('','状态'): return s,('已结束。' if s.get('done') else '进行中。')+'数字：'+' '.join(map(str,s['numbers']))+f'；已尝试{s["tries"]}/{s["max_attempts"]}次。'
    if s.get('done'): return s,'本局已结束，发送 开始 重开。'
    if arg in ('结束','答案','投降'): s['done']=True; return s,'本局参考解：'+s['solution']+' = 24'
    result=calculate24(arg,s['numbers']);s['tries']+=1
    if result==24: s['done']=True;return s,'恰好等于24，答对了！'
    if s['tries']>=s['max_attempts']: s['done']=True; return s,'次数用完。参考解：'+s['solution']+' = 24'
    return s,'结果是 '+str(result)+'，还没到24；剩余'+str(s['max_attempts']-s['tries'])+'次。'


USAGE={
 'x_connect4':'/四子棋 开始；另一人 /四子棋 加入；当前玩家 /四子棋 列号（1—7）。固定7列6行，有重力，横竖斜连4获胜；状态、认输、结束。',
 'x_tictactoe':'/井字棋 开始；另一人 加入；或 /井字棋 开始 人机。/井字棋 行 列（1—3），连3获胜；状态、认输、结束。',
 'x_gomoku':'/五子棋 开始；另一人 加入；/五子棋 行 列（1—9），固定9×9自由规则，连5及以上获胜，无禁手；状态、认输、结束。',
 'x_reversi':'/黑白棋 开始；另一人 加入；/黑白棋 行 列（1—6）。必须夹住对手棋子；无合法步时发送 跳过；双方均无步按子数结算。状态、认输、结束。',
 'x_yahtzee':'/快艇骰子 开始；保留 1 3 表示保留位置1、3并重掷其余；重掷 2 4 指定重掷位置。每回合最多掷3次。计分 一/二/三/四/五/六/三条/四条/葫芦/小顺/大顺/快艇/机会。每类一次，13回合；上半区≥63加35分。不采用额外快艇或万能规则。状态、结束。',
 'x_wordchain':'/成语接龙 开始；/成语接龙 意气风发。与机器人轮流按文字首尾接龙，仅本地有限常见词典，不按读音；同词不能重复。提示、状态、结束。机器人无词则玩家赢；达到轮数即完成挑战。',
 'x_twentyfour':'/24点 开始；/24点 (1+2+3)*4。必须将给定的四个数字各使用一次，只允许四则运算、括号；精确分数判定，不用浮点误差。状态、答案/投降。每道题保证有解，答错次数可配置。',
}
CATALOG=[
 {'id':k,'name':name,'commands':[COMMANDS[k]],'smoke_command':COMMANDS[k]+(' 开始 人机' if k=='x_tictactoe' else ' 开始'),'expected_contains':expected,'config_schema':schema,'usage':USAGE[k]}
 for k,name,expected,schema in [
 ('x_connect4','四子棋','对局已开始',[]),('x_tictactoe','井字棋','对局已开始',[]),
 ('x_gomoku','九路五子棋','对局已开始',[]),('x_reversi','六路黑白棋','对局已开始',[]),
 ('x_yahtzee','快艇骰子','快艇骰子已开始',[{'key':'round_limit','type':'integer','label':'一局最多计分类别数','default':13,'min':3,'max':13}]),
 ('x_wordchain','成语接龙','成语接龙已开始',[{'key':'max_rounds','type':'integer','label':'完成挑战的轮数','default':20,'min':3,'max':30}]),
 ('x_twentyfour','24点','24点已开始',[{'key':'max_attempts','type':'integer','label':'每道题有效尝试次数','default':10,'min':3,'max':30}]),
 ]
]

def run(kind,arg,state,context):
    if arg=='帮助': return state,USAGE[kind]
    if kind in {'x_connect4','x_tictactoe','x_gomoku','x_reversi'}:
        if str(context.get('session_id','')).startswith('p:'):
            if kind!='x_tictactoe': return state,'双人棋局仅支持群聊，请在群中创建，让另一位玩家加入。'
            if arg=='加入' or arg=='开始': return state,'私聊井字棋只支持人机模式，请发送 /井字棋 开始 人机。'
            if state and state.get('players',[])[-1:]!=['BOT'] and arg not in ('结束','投降','认输'):
                return state,'私聊井字棋只支持人机模式，请先结束旧桌，再发送 /井字棋 开始 人机。'
        return multiplayer(kind,arg,state,str(context['user_id']),context)
    if kind=='x_yahtzee': return yahtzee(arg,state,context)
    if kind=='x_wordchain': return wordchain(arg,state,context)
    if kind=='x_twentyfour': return twentyfour(arg,state,context)
    raise ValueError('不支持的游戏。')

