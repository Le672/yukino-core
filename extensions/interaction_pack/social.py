"""Original hosted social game rules; every hidden answer stays private."""
from common import RNG,need,words,integer,host,active,member,new_room,load,join,listing

TRUTHS=['最近一次笑出声是因为什么？','分享一个最近学会的小技巧。','你最想重温哪部作品？','说一个今年想完成的小目标。','如果多一天假期你会做什么？','你吃过最好吃的一顿是什么？','介绍一首最近喜欢的歌。','哪件小事让你觉得被照顾到了？']
DARES=['夸夸上一位发言者的一项优点。','用三个词描述今天的天气。','用一句话给群聊写个广告。','打出一段不超过20字的绕口令。','用拟声词描述你现在的状态。','给自己起一个仅本回合有效的外号。','推荐一道简单的家常菜。','模仿播音员介绍你桌上的一件物品。']
WORD_PAIRS=[('橙子','橘子'),('钢琴','电子琴'),('地铁','轻轨'),('奶茶','咖啡'),('雨衣','雨伞'),('书店','图书馆'),('月亮','星星'),('饼干','面包')]
GUESS_WORDS=['冰箱','雨伞','西瓜','火车','望远镜','企鹅','热气球','键盘','仙人掌','自行车','摩天轮','围巾']

def hidden(pid,cmd,tail,sid,uid,ctx,db):
    args=tail.split(maxsplit=1);need(args,'请提供群内公布的房间码。')
    room=load(db,code=args[0]);need(room is not None,'房间不存在或已过期。')
    need(sid=='p:'+uid,'此操作只能在与机器人的私聊中进行。')
    member(room,uid)
    if pid=='i_turtle' and cmd=='设题':
        host(room,uid,ctx);need(room['phase']=='lobby','请在开局前设置。')
        need(len(args)==2 and '|' in args[1],'用法：/海龟汤 设题 房间码 汤面 | 汤底（仅私聊）')
        q,a=args[1].split('|',1);room['question']=words(q,500);room['answer']=words(a,1000);return '题目已私密保存，请回原群发送 /海龟汤 开始。',room
    if pid=='i_undercover':
        need(cmd=='取词' and room['phase']=='playing','本局尚未开始或已结束。')
        need(uid not in room['eliminated'],'你已出局。')
        return '你的本局词语：'+room['hands'][uid]+'。不要把词直接发到群里。',None
    if pid=='i_guess':
        need(cmd=='取词' and room['phase']=='playing','本局尚未开始或已结束。');host(room,uid,ctx)
        return '主持词语：'+room['answer']+'。在原群给线索，勿直接包含答案。',None
    raise ValueError('unsupported private action')

def dispatch(pid,cmd,tail,sid,uid,ctx,db,r):
    if (pid=='i_turtle' and cmd=='设题') or (pid in ('i_undercover','i_guess') and cmd=='取词'):
        return hidden(pid,cmd,tail,sid,uid,ctx,db)
    need(sid.startswith('g:'),'请在群聊使用；取词/设题操作在私聊进行。')
    if cmd=='创建':
        r=new_room(db,sid,uid,ctx)
        if pid=='i_turtle':r.update(question='',answer='',questions=[],pending=None,log=[])
        elif pid=='i_buzzer':r.update(question='',claim=None,attempted=[],scores={},round=0)
        elif pid=='i_story':r.update(lines=[words(tail,400)],turn=0)
        elif pid=='i_topics':r.update(topics=[],next_id=1,last=None)
        elif pid=='i_truth':r.update(selected=None,task=None,round=0,history=[])
        elif pid=='i_undercover':r.update(hands={},eliminated=[],votes={},round=1)
        elif pid=='i_guess':r.update(answer='',guesses=0,scores={})
        elif pid=='i_teams':r.update(captains=[],teams={},turn=0,order=[])
        message=f'房间已创建 · {r["code"]}，发起者 {uid}。'
        if pid=='i_turtle':message+='主持人私聊 /海龟汤 设题 '+r['code']+' 汤面 | 汤底。'
        return message,r
    active(r)
    if cmd=='结束':host(r,uid,ctx);r['phase']='done';return '本房间已结束。',r
    if cmd=='加入':join(r,uid);return '已加入：'+listing(r),r
    if cmd=='状态':
        if pid=='i_story':return '当前轮到 '+r['players'][r['turn']%len(r['players'])]+'\n'+'\n'.join(r['lines'][-8:]),None
        if pid=='i_topics':return '待抽话题 '+str(len(r['topics']))+' 条。',None
        if pid=='i_buzzer':return '问题：'+r['question']+'\n当前抢答：'+str(r['claim'] or '暂无'),None
        if pid=='i_undercover':return '第'+str(r['round'])+'轮，存活：'+'、'.join(p for p in r['players'] if p not in r['eliminated'])+'。已投票 '+str(len(r['votes']))+' 人。',None
        if pid=='i_teams':return '队伍：'+str(r['teams'])+'\n待选：'+'、'.join(p for p in r['players'] if p not in [x for t in r['teams'].values() for x in t]),None
        return '房间 '+r['code']+' · '+r['phase']+'\n玩家：'+listing(r),None
    if pid=='i_turtle':
        if cmd=='开始':host(r,uid,ctx);need(r['phase']=='lobby' and r['answer'],'请先私聊设题。');r['phase']='playing';return '汤面：'+r['question'],r
        need(r['phase']=='playing','请先由主持人开始。')
        if cmd=='汤面':return '汤面：'+r['question'],None
        if cmd=='提问':
            need(uid!=r['host'],'主持人无需提问。');need(len(r['questions'])<20,'问题队列已满。')
            r['questions'].append({'user':uid,'text':words(tail,200)});return '问题已排队，位置 '+str(len(r['questions']))+'。',r
        if cmd=='下一问':
            host(r,uid,ctx);need(r['pending'] is None,'请先回答当前问题。');need(r['questions'],'没有排队问题。');r['pending']=r['questions'].pop(0);return r['pending']['user']+' 问：'+r['pending']['text'],r
        if cmd=='回答':
            host(r,uid,ctx);need(r['pending'] is not None,'请先取下一问。');need(tail in ('是','否','无关'),'回答只支持 是/否/无关。')
            q=r['pending'];r['log'].append(q['text']+' → '+tail);r['log']=r['log'][-30:];r['pending']=None;return q['user']+'：'+tail,r
        if cmd=='记录':return '\n'.join(r['log']) or '还没有已回答的问题。',None
        if cmd=='揭底':host(r,uid,ctx);r['phase']='done';return '汤底：'+r['answer'],r
    if pid=='i_buzzer':
        if cmd=='开题':
            host(r,uid,ctx);need(not r['question'] or r['phase']!='playing','请先结束当前题：/抢答 结题。')
            r.update(question=words(tail,500),claim=None,attempted=[],phase='playing',round=r['round']+1);return '第'+str(r['round'])+'题：'+r['question']+'\n发送 /抢答 抢答 抢先。',r
        if cmd=='抢答':
            need(r['phase']=='playing','还没开题。');need(uid!=r['host'],'主持人不能抢自己的题。');need(r['claim'] is None,'已有抢答者，请等待判定。');need(uid not in r['attempted'],'本题你已尝试。')
            r['claim']=uid;r['attempted'].append(uid);return uid+' 抢到！请在群里作答，主持人判定。',r
        if cmd=='判定':
            host(r,uid,ctx);need(r['phase']=='playing' and r['claim'],'没有待判定的抢答。');need(tail in ('对','错'),'判定 对/错。');who=r['claim'];r['claim']=None
            if tail=='对':r['scores'][who]=r['scores'].get(who,0)+1;r['phase']='lobby'
            return who+' 回答'+tail+('，得1分。' if tail=='对' else '，其他人可继续抢答。'),r
        if cmd=='结题':host(r,uid,ctx);r['phase']='lobby';r['claim']=None;return '本题结束，可以开下一题。',r
        if cmd=='积分':return '\n'.join(p+'：'+str(n) for p,n in sorted(r['scores'].items(),key=lambda x:(-x[1],x[0]))) or '暂无积分。',None
    if pid=='i_story':
        if cmd=='开始':host(r,uid,ctx);need(r['phase']=='lobby','已开始。');r['phase']='playing';return '续写开始，轮到 '+r['players'][0],r
        if cmd in ('写','跳过'):
            need(r['phase']=='playing','请先开始。');need(uid==r['players'][r['turn']%len(r['players'])],'还没轮到你。');need(len(r['lines'])<80,'已达80段，请收尾。')
            if cmd=='写':r['lines'].append(uid+'：'+words(tail,300))
            r['turn']+=1;return '轮到 '+r['players'][r['turn']%len(r['players'])]+'\n'+r['lines'][-1],r
        if cmd=='全文':return '\n'.join(r['lines'])[-7500:],None
    if pid=='i_topics':
        if cmd=='添加':
            need(len(r['topics'])<100,'话题箱最多100条。');topic={'id':r['next_id'],'user':uid,'text':words(tail,200)};r['next_id']+=1;r['topics'].append(topic);return '话题已收录 #'+str(topic['id']),r
        if cmd=='抽取':
            need(r['topics'],'话题箱空了。');topic=RNG.choice(r['topics']);r['topics'].remove(topic);r['last']=topic;return '来聊聊：'+topic['text'],r
        if cmd=='删除':
            n=integer(tail,1,100000);topic=next((t for t in r['topics'] if t['id']==n),None);need(topic,'没有这个话题。')
            need(topic['user']==uid or uid==r['host'] or uid in ctx.get('owner_ids',[]),'只能删除自己添加的话题，主持人可管理全部。');r['topics'].remove(topic);return '话题已删除。',r
        if cmd=='列表':return '\n'.join('#'+str(t['id'])+' '+t['text'] for t in r['topics'][:20]) or '话题箱空了。',None
    if pid=='i_truth':
        member(r,uid)
        if cmd=='抽人':
            host(r,uid,ctx);need(r['selected'] is None,'当前玩家可完成或跳过。');r['selected']=RNG.choice(r['players']);r['round']+=1;r['task']=None;return '第'+str(r['round'])+'轮抽中 '+r['selected']+'，可选 真心话/大冒险，或跳过。',r
        if cmd in ('真心话','大冒险'):
            need(uid==r['selected'],'本轮只由被抽中的玩家选择。');need(r['task'] is None,'任务已抽取，可完成或跳过。')
            r['task']=RNG.choice(TRUTHS if cmd=='真心话' else DARES);return r['task']+'（不想做可 /真心冒险 跳过）',r
        if cmd in ('完成','跳过'):
            need(uid==r['selected'],'本轮只由被抽中的玩家决定。');r['history'].append({'user':uid,'skipped':cmd=='跳过'});r['history']=r['history'][-100:];r['selected']=r['task']=None;return '本轮'+cmd+'，没有惩罚。',r
    if pid=='i_undercover':
        if cmd=='开始':
            host(r,uid,ctx);need(r['phase']=='lobby' and 3<=len(r['players'])<=12,'需要3—12人加入。')
            pair=RNG.choice(WORD_PAIRS);spy=RNG.choice(r['players']);r['hands']={p:pair[1] if p==spy else pair[0] for p in r['players']};r['spy']=spy;r['phase']='playing'
            return '游戏开始。每人私聊 /谁是卧底 取词 '+r['code']+'；轮流描述后投票。',r
        need(r['phase']=='playing','请先开始。');member(r,uid)
        if cmd=='投票':
            need(uid not in r['eliminated'],'你已出局。')
            need(tail in r['players'] and tail not in r['eliminated'] and tail!=uid,'请选择另一位存活玩家的QQ号。');need(uid not in r['votes'],'本轮你已投票。');r['votes'][uid]=tail
            return '已记票 '+str(len(r['votes']))+' / '+str(len(r['players'])-len(r['eliminated'])),r
        if cmd=='结算':
            host(r,uid,ctx);alive=[p for p in r['players'] if p not in r['eliminated']];need(len(r['votes'])==len(alive),'所有存活玩家投完票后再结算。')
            counts={p:sum(v==p for v in r['votes'].values()) for p in alive};high=max(counts.values());targets=[p for p,n in counts.items() if n==high];r['votes']={};r['round']+=1
            if len(targets)!=1:return '平票，本轮无人出局，重新描述并投票。',r
            out=targets[0];r['eliminated'].append(out);remaining=len(alive)-1
            if out==r['spy']:r['phase']='done';return out+' 出局，平民获胜。卧底为 '+out+'。',r
            if remaining<=2:r['phase']='done';return out+' 出局，卧底获胜。卧底为 '+r['spy']+'。',r
            return out+' 出局，继续下一轮。',r
    if pid=='i_guess':
        if cmd=='积分':return str(r['scores']),None
        if cmd=='开始':
            host(r,uid,ctx);need(r['phase']=='lobby','本轮还未结束。');r['answer']=RNG.choice(GUESS_WORDS);r['phase']='playing';r['guesses']=0
            return '本轮已开始。主持人私聊 /你说我猜 取词 '+r['code']+'；群里发送 /你说我猜 线索 内容。',r
        need(r['phase']=='playing','请先开始。')
        if cmd=='线索':host(r,uid,ctx);need(r['answer'] not in tail,'线索包含答案，请换一个。');return '主持线索：'+words(tail,200),None
        if cmd=='猜':
            need(uid!=r['host'],'主持人不能猜。');need(r['guesses']<100,'已达100次猜测，请主持揭晓。');r['guesses']+=1
            if words(tail,60)==r['answer']:r['scores'][uid]=r['scores'].get(uid,0)+1;r['phase']='lobby';return uid+' 猜对了！答案：'+r['answer'],r
            return '还没猜中。',r
        if cmd=='揭晓':host(r,uid,ctx);r['phase']='lobby';return '答案：'+r['answer'],r
    if pid=='i_teams':
        if cmd=='选队长':
            host(r,uid,ctx);need(r['phase']=='lobby','已经开始选队。');args=tail.split();need(len(args)==2 and args[0]!=args[1] and all(p in r['players'] for p in args),'指定两名已加入的不同玩家QQ号。')
            need(len(r['players'])>=4,'至少4人加入后再选两名队长。')
            r['captains']=args;r['teams']={p:[p] for p in args};r['order']=[args[0],args[1],args[1],args[0]];r['phase']='playing';r['turn']=0;return '队长已确定，蛇形选人，先由 '+args[0]+' 选人。',r
        if cmd=='选':
            need(r['phase']=='playing','先确定两名队长。');who=r['order'][r['turn']%4];need(uid==who,'当前轮到队长 '+who)
            used=[p for team in r['teams'].values() for p in team];need(tail in r['players'] and tail not in used,'请选尚未分队的玩家QQ号。')
            r['teams'][who].append(tail);r['turn']+=1
            if len(used)+1==len(r['players']):r['phase']='done';return '选队完成：'+str(r['teams']),r
            return '已选 '+tail+'，轮到 '+r['order'][r['turn']%4],r
    need(False,'未知操作，请查看本插件帮助。')
