"""Finite local games: dots and boxes, bounded Nim, SET and virtual auctions."""
from itertools import combinations,product
from common import RNG,need,words,integer,host,active,member,new_room,join,listing

def square_edges(row,col):return {f'H{row}{col}',f'H{row+1}{col}',f'V{row}{col}',f'V{row}{col+1}'}
def dot_board(r):
    lines=[];edges=set(r['edges'])
    for row in range(1,4):
        lines.append(''.join('+'+('---' if f'H{row}{col}' in edges else '   ') for col in range(1,3))+'+')
        if row<=2:
            lines.append(''.join(('|' if f'V{row}{col}' in edges else ' ')+(' '+str(r['boxes'].get(str(row)+str(col),'.'))+' ') for col in range(1,3))+('|' if f'V{row}3' in edges else ' '))
    return '\n'.join(lines)
def set_valid(cards):return len(cards)==3 and len(set(cards))==3 and all(len({c[i] for c in cards}) in (1,3) for i in range(4))
def has_set(cards):return any(set_valid(c) for c in combinations(cards,3))
def set_board(r):return '\n'.join(str(i+1)+': '+card for i,card in enumerate(r['board']))+'\n每位数字代表一种属性（各1—3）；三张的每种属性须全同或全异。'

def dispatch(pid,cmd,tail,sid,uid,ctx,db,r):
    need(sid.startswith('g:'),'请在群聊里创建和进行本游戏。')
    if cmd=='创建':
        r=new_room(db,sid,uid,ctx)
        if pid=='i_dots':r.update(edges=[],boxes={},turn=0);r['max_players']=2
        if pid=='i_nim':
            piles=[integer(t,1,30) for t in (tail or '3 4 5').split()];need(1<=len(piles)<=7,'支持1—7堆。')
            r.update(piles=piles,turn=0,max_take=int(ctx.get('settings',{}).get('nim_max_take',3)));r['max_players']=2
        if pid=='i_set':
            deck=[''.join(c) for c in product('123',repeat=4)];RNG.shuffle(deck);r.update(deck=deck[12:],board=deck[:12],scores={},phase='playing')
        if pid=='i_auction':r.update(item=words(tail,150),bid=0,bidder=None,budget=int(ctx.get('settings',{}).get('auction_budget',100)),phase='lobby')
        message='房间已创建 · '+r['code']
        if pid=='i_set':message+='\n'+set_board(r)
        if pid=='i_auction':message+='\n纯虚拟拍卖，无付款、交易或奖品。标的：'+r['item']
        return message,r
    need(r is not None,'请先创建房间。')
    if cmd=='状态':
        if pid=='i_dots':return dot_board(r)+'\n状态：'+r['phase']+('，轮到 '+r['players'][r['turn']%len(r['players'])] if r['phase']=='playing' else ''),None
        if pid=='i_nim':return '石堆：'+' / '.join(str(i+1)+'号='+str(n) for i,n in enumerate(r['piles']))+'\n状态：'+r['phase'],None
        if pid=='i_set':return set_board(r)+'\n积分：'+str(r['scores'])+'，牌库剩余 '+str(len(r['deck'])),None
        return '标的：'+r['item']+'\n当前：'+str(r['bid'])+' 虚拟分，竞拍者 '+str(r['bidder'] or '暂无')+' · '+r['phase'],None
    active(r)
    if cmd=='结束':host(r,uid,ctx);r['phase']='done';return '房间已结束。',r
    if cmd=='加入':
        if pid=='i_set':
            need(uid not in r['players'],'已经加入。');need(len(r['players'])<r['max_players'],'人数已满。');r['players'].append(uid)
        else:join(r,uid)
        return '已加入：'+listing(r),r
    if cmd=='开始':
        host(r,uid,ctx);need(r['phase']=='lobby','已开始。');need(len(r['players'])>=2,'至少需要2人加入。');r['phase']='playing'
        return ('拍卖已开始，每人预算 '+str(r['budget'])+' 虚拟分。' if pid=='i_auction' else '对局开始，轮到 '+r['players'][0]),r
    member(r,uid)
    need(r['phase']=='playing','请先由发起者开始。')
    if pid=='i_dots' and cmd=='落子':
        need(uid==r['players'][r['turn']],'还没轮到你。');args=tail.upper().split();need(len(args)==3 and args[0] in ('H','V'),'用法：落子 H 行 列；或 V 行 列。')
        kind=args[0];row=integer(args[1],1,3 if kind=='H' else 2);col=integer(args[2],1,2 if kind=='H' else 3);edge=kind+str(row)+str(col)
        need(edge not in r['edges'],'这条边已经画过。');r['edges'].append(edge);made=0
        for row in range(1,3):
            for col in range(1,3):
                key=str(row)+str(col)
                if key not in r['boxes'] and square_edges(row,col)<=set(r['edges']):r['boxes'][key]=r['turn']+1;made+=1
        if len(r['boxes'])==4:
            r['phase']='done';scores=[sum(v==i for v in r['boxes'].values()) for i in (1,2)]
            result='平局。' if scores[0]==scores[1] else r['players'][0 if scores[0]>scores[1] else 1]+' 获胜。'
            return dot_board(r)+'\n'+result,r
        if not made:r['turn']=1-r['turn']
        return dot_board(r)+'\n'+('闭合'+str(made)+'格，继续落子。' if made else '轮到 '+r['players'][r['turn']]),r
    if pid=='i_nim' and cmd=='取':
        need(uid==r['players'][r['turn']],'还没轮到你。');args=tail.split();need(len(args)==2,'用法：取 堆号 数量。')
        index=integer(args[0],1,len(r['piles']))-1;n=integer(args[1],1,r['max_take']);need(n<=r['piles'][index],'这堆没有这么多石头。');r['piles'][index]-=n
        if not sum(r['piles']):r['phase']='done';return uid+' 取走最后一颗，获胜。',r
        r['turn']=1-r['turn'];return '剩余：'+str(r['piles'])+'，轮到 '+r['players'][r['turn']],r
    if pid=='i_set':
        if cmd=='出':
            args=tail.split();need(len(args)==3,'指定三张不同位置的牌。');indexes=[integer(x,1,len(r['board']))-1 for x in args]
            need(len(set(indexes))==3,'不能重复选同一张。');need(set_valid([r['board'][i] for i in indexes]),'这三张不满足SET规则。')
            for i in sorted(indexes,reverse=True):r['board'].pop(i)
            while len(r['board'])<12 and r['deck']:r['board'].append(r['deck'].pop())
            r['scores'][uid]=r['scores'].get(uid,0)+1
            if not r['deck'] and not has_set(r['board']):r['phase']='done';return '牌局结束。积分：'+str(r['scores']),r
            return '有效SET，得1分。\n'+set_board(r),r
        if cmd=='加牌':
            need(not has_set(r['board']),'桌上仍有有效SET，请继续寻找。');need(r['deck'],'牌库已空。');need(len(r['board'])<=18,'牌面已达21张上限。')
            for _ in range(min(3,len(r['deck']))):r['board'].append(r['deck'].pop())
            return set_board(r),r
    if pid=='i_auction':
        if cmd=='出价':
            need(uid!=r['host'],'主持人不能参与自己的拍卖。');n=integer(tail,1,r['budget']);need(n>r['bid'],'出价必须高于当前最高价。');r['bid']=n;r['bidder']=uid;return uid+' 出价 '+str(n)+' 虚拟分。',r
        if cmd=='落槌':
            host(r,uid,ctx);need(r['bidder'] is not None,'还没有出价。');r['phase']='done';return '虚拟落槌：'+r['bidder']+' 以 '+str(r['bid'])+' 分竞得「'+r['item']+'」。无付款、交易或奖品。',r
    need(False,'未知操作，请查看帮助。')
