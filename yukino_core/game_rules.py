"""Public observations and legal game actions; hidden answers are never candidates."""
from __future__ import annotations
import copy,hashlib,itertools,json,re
from .game_catalog import BOARDS,GAMES,INTERACTION,MULTIPLAYER
from .game_state import alive


def task(frame,module,state,meta):
    p=frame.pid;b=frame.bot;s=state
    if not meta.get('enabled') or not alive(s):return None
    if p in MULTIPLAYER and b not in s.get('players',[]):return None
    if s.get('phase')=='lobby' and p not in ('i_truth','i_topics'):return None
    actions=[];instruction='选择一步合法行动，考虑眼前局面和后续策略。';mode='choice';act='move';secret=None
    view={}
    if p in BOARDS:
        if len(s['players'])!=2 or s['players'][s['turn']]!=b:return None
        n={'x_connect4':7,'x_tictactoe':3,'x_gomoku':9,'x_reversi':6}[p]
        if p=='x_connect4':actions=[str(c+1) for c in range(7) if not s['board'][c]]
        elif p=='x_reversi':
            helper=module.engine.load_helper(p);actions=[f'{r+1} {c+1}' for r,c in helper.reversi_moves(s['board'],s['turn']+1)] or ['跳过']
        else:actions=[f'{i//n+1} {i%n+1}' for i,v in enumerate(s['board']) if not v]
        view['your_piece']=s['turn']+1
    elif p=='m_uno':
        if s['players'][s['turn']]!=b:return None
        cards=module.engine.CARDS;hand=s['hands'][b];top=cards[s['discard'][-1]][1]
        view['your_hand']=[module.engine.label(v) for v in hand]
        for i,v in enumerate(hand,1):
            color,value=cards[v]
            if s.get('drawn') is not None and s['drawn']!=v:continue
            if color and color!=s['color'] and value!=top:continue
            if value=='+4' and any(cards[x][0]==s['color'] for x in hand):continue
            actions.extend([f'出 {i} {c}' for c in module.engine.COLORS] if not color else [f'出 {i}'])
        actions+=['摸牌'] if s.get('drawn') is None else ['跳过']
        instruction='你只能知道自己的手牌与公开牌面。选择出牌、指定万能牌颜色或摸牌；内核会在剩一张时自动喊UNO。'
    elif p=='i_undercover':
        players=[u for u in s['players'] if u not in s['eliminated']]
        if b not in players:return None
        clues=[c for c in meta.get('clues',[]) if c['round']==s['round'] and c['user'] in players]
        view['descriptions']=[dict(player=frame.label(c['user'],s),text=c['text']) for c in clues]
        described={c['user'] for c in clues}
        if len(described)<len(players):
            next_user=next(u for u in players if u not in described)
            if next_user!=b:return None
            mode='text';act='undercover_clue';secret=s['hands'][b];view['your_word']=secret
            instruction='你是普通参赛者，只知道自己的词语，不知道谁是卧底或其他人的词。用一句不直接包含原词的短描述发言，不说出身份、不暗示规则外信息。'
        elif b not in s['votes']:
            actions=['投票 '+u for u in players if u!=b];act='undercover_vote'
            instruction='根据公开描述选出最可疑的一名其他存活玩家。你不知道真实身份，不根据未公开信息投票。'
        else:return None
    elif p=='i_story':
        if s['players'][s['turn']%len(s['players'])]!=b:return None
        mode='text';act='story';instruction='接着公开故事续写一小段（不超过180字），保持人物和情节连贯。'
    elif p=='i_turtle':
        if s['host']==b:return None
        act='turtle';mode='text';view={'question':s['question'],'answered_questions':s['log'][-20:]}
        if any(q['user']==b for q in s['questions']) or (s.get('pending') or {}).get('user')==b:return None
        instruction='你是解谜玩家，只能看到汤面和公开问答。提出一个尚未问过、能用是/否/无关回答的问题，不假装知道汤底。'
    elif p=='i_buzzer':
        if s['host']==b or s.get('claim') or b in s.get('attempted',[]):return None
        act='buzzer';mode='text';view={'question':s['question']};instruction='回答公开抢答题，给出简短实际答案，不含解题过程或报幕；由真人主持判定正误。'
    elif p=='i_guess':
        if s['host']==b or not meta.get('guess_clues'):return None
        act='guess';mode='text';view={'clues':meta['guess_clues'][-8:],'previous_guesses':meta.get('guess_history',[])[-10:]}
        instruction='根据主持人公开线索猜一个词，不知道标准答案；只输出你的猜测。'
    elif p=='i_truth':
        if s.get('selected')!=b:return None
        act='truth';mode='text';view={'question':s.get('task') or '真心话或大冒险选择'}
        instruction='以当前角色自然回答这道小游戏题。可以明确说是假设或角色设定，不编造真实生活经历，不要求现实危险动作。'
    elif p=='i_topics':
        if not s.get('last'):return None
        act='topic';mode='text';view={'topic':s['last']['text']};instruction='围绕刚抽到的话题自然聊一句，最多80字，不必追问。'
    elif p=='i_teams':
        if not s.get('order') or s['order'][s['turn']%4]!=b:return None
        used={u for group in s['teams'].values() for u in group};actions=['选 '+u for u in s['players'] if u not in used]
    elif p=='i_dots':
        if s['players'][s['turn']]!=b:return None
        actions=[f'落子 {k} {r} {c}' for k in ('H','V') for r in range(1,4 if k=='H' else 3) for c in range(1,3 if k=='H' else 4) if f'{k}{r}{c}' not in s['edges']]
    elif p=='i_nim':
        if s['players'][s['turn']]!=b:return None
        actions=[f'取 {i+1} {n}' for i,v in enumerate(s['piles']) for n in range(1,min(v,s['max_take'])+1)]
    elif p=='i_set':
        actions=['出 '+' '.join(str(i+1) for i in indexes) for indexes in itertools.combinations(range(len(s['board'])),3) if all(len({s['board'][i][a] for i in indexes}) in (1,3) for a in range(4))]
        if not actions and s['deck']:actions=['加牌']
    elif p=='i_auction':
        if s.get('bidder')==b or s['host']==b or s['bid']>=s['budget']:return None
        actions=['出价 '+str(n) for n in sorted({s['bid']+1,min(s['budget'],s['bid']+5),s['budget']})]
        instruction='这只是无付款、无奖品的虚拟游戏。根据标的和公开价格选择预算内的一次出价。'
    elif p=='rps':
        if meta.get('commit'):return None
        act='rps';actions=['石头','剪刀','布'];view={'previous_results':meta.get('rps_history',[])[-6:]}
        instruction='选择本轮出拳，随后会先公布承诺哈希再等待对手出拳。你不知道对手本轮选择。'
    else:
        actions,mode,instruction=solo_actions(p,module,s)
    if mode=='choice' and not actions:return None
    if not view:
        public=module.engine.public(s) if p=='m_uno' else module.handle('/'+GAMES[p]+' 状态',frame.actor())
        view={'board':frame.public(public,s)}
    if p in MULTIPLAYER:view['players']=[frame.label(u,s) for u in s.get('players',[])]
    if p not in ('rps','m_uno') and meta.get('seen_public'):view['recent_public_results']=meta['seen_public'][-3:]
    # Tags prevent repeat answers on status/hand queries, while true extra turns
    # in board/card games can produce another bounded action automatically.
    tag_body=s
    if p=='i_turtle':tag_body=['turtle',len(s['log'])]
    elif p=='i_guess':tag_body=['guess',s.get('code'),meta.get('guess_clues',[])]
    elif p=='i_topics':tag_body=['topic',s['last']]
    tag=hashlib.sha256(json.dumps([act,tag_body],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    if meta.get('last_tag')==tag:return None
    return dict(mode=mode,act=act,view=view,actions=actions,instruction=instruction,tag=tag,secret=secret)


def solo_actions(p,module,s):
    instruction='你是与真人轮流操作的游戏搭档，只依据公开题面作答；不知道隐藏答案。';mode='choice';a=[]
    if p in ('x_sliding','x_2048','x_sokoban','x_maze'):
        helper=module.engine.load_helper(p)
        for direction in ('上','下','左','右'):
            try:
                updated,_=helper.run(p,direction,copy.deepcopy(s),{})
                if updated!=s:a.append(direction)
            except ValueError:pass
    elif p=='x_minesweeper':
        a=[f'开 {i//s["size"]+1} {i%s["size"]+1}' for i in range(s['size']**2) if i not in s['opened'] and i not in s['flags']]
        a += [f'旗 {i//s["size"]+1} {i%s["size"]+1}' for i in range(s['size']**2) if i not in s['opened'] and (i in s['flags'] or len(s['flags'])<s['bombs'])]
        instruction+=' 根据已翻数字推断，无法确定时可合理冒险；未开的格子没有提供雷的位置。'
    elif p=='x_lights':a=[f'按 {r+1} {c+1}' for r in range(s['size']) for c in range(s['size'])]
    elif p=='x_memory':a=['翻 '+str(i+1) for i in range(len(s['cards'])) if i not in s['matched'] and i not in s['faceup']]
    elif p=='x_battleship':a=['攻击 '+chr(65+i%5)+str(i//5+1) for i in range(25) if i not in s['shots']]
    elif p=='x_highlow':a=['高','低']
    elif p=='x_knight':
        h=module.engine.load_helper(p);a=['移动 '+h.label(i,s['size']) for i in h.knight_moves(s)]
    elif p=='x_yahtzee':
        h=module.engine.load_helper(p);a=['计分 '+name for name in h.YAHTZEE_CATEGORIES if name not in s['scores']]
        if s['rolls']<3:a+=['重掷 '+' '.join(str(i+1) for i in range(5) if mask & 1<<i) for mask in range(1,32)]
    elif p=='x_trivia':a=list('ABCD')
    elif p=='x_wordchain':
        h=module.engine.load_helper(p);a=[w for w in h.IDIOMS if w.startswith(s['last'][-1]) and w not in s['used']]
    elif p=='s_sudoku':
        for i,v in enumerate(s['board']):
            if s['puzzle'][i]:continue
            for n in range(1,5):
                if n==v:continue
                board=s['board'][:];board[i]=n
                if module.run.__globals__['valid_sudoku'](board):a.append(f'填 {i//4+1} {i%4+1} {n}')
        a += [f'清除 {i//4+1} {i%4+1}' for i,v in enumerate(s['board']) if v and not s['puzzle'][i]]
    elif p=='s_nonogram':a=[f'{cmd} {i//5+1} {i%5+1}' for i,v in enumerate(s['board']) for cmd,n in (('涂黑',1),('标空',0),('清除',-1)) if n!=v]
    elif p=='s_expedition':
        if s['energy']>=2:a.append('探索')
        if s['food'] and (s['energy']<8 or s['health']<12):a.append('休整')
        if s['medicine'] and s['health']<12:a.append('使用 药包')
    else:
        mode='text'
        instruction+=dict(wordle=' 只给5个英文字母。',guess_number=' 只给4位不重复数字，可含开头0。',
            x_hangman=' 给单个未猜字母或完整英文词（只输出字母）。',x_anagram=' 根据打乱字母给3至12字母英文单词。',
            x_arithmetic=' 只给一个整数答案。',x_twentyfour=' 只给使用这四个数字各一次、结果为24的四则算式。',
            m_mahjong=' 找出全部听牌候选，按123m456p789s1234567z的牌组格式只列候选种类。')[p]
    return a,mode,instruction
