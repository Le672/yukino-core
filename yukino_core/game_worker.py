"""Trusted game-only bridge, executed inside each existing plugin worker."""
from __future__ import annotations
import hashlib,json,re,secrets
from .game_catalog import AMBIENT,GAMES,INTERACTION,MULTIPLAYER
from .game_rules import task
from .game_state import Frame,GameError,alive,display_player_ids,revision

JOIN={'加入AI','AI加入','邀请AI','机器人加入','人机','AI'}
LEAVE={'AI退出','退出AI','移除AI'}
HELP='AI玩法：创建/开始时加“人机”，或本局发送 加入AI；加入后按轮次自动行动。AI状态、AI重试、AI退出。开局加“纯玩家”可只玩真人局。'


def _state(frame,meta):
    return {'phase':'playing'} if frame.pid=='rps' and meta.get('enabled') else frame.read()


def _actor(frame,user):return {**frame.ctx,'user_id':user}


def _public_output(frame,text):
    name=str(frame.ctx.get('_game_ai',{}).get('bot_name') or '机器人')
    text=display_player_ids(text or '',{frame.bot:name})
    return re.sub(r'(^|\n)机器人(?=[:：]|行动|作答|先锁定)',lambda m:m[1]+name,text)[:8000]


def _packet(frame,module,state,meta,text='',consumed=False):
    active=bool(meta.get('enabled') and alive(state))
    if frame.pid in MULTIPLAYER:active=active and frame.bot in state.get('players',[])
    spec=task(frame,module,state,meta) if active and frame.ctx.get('settings',{}).get('ai_enabled',True) else None
    pending=None
    if spec:
        pending=dict(nonce=revision(state,meta),mode=spec['mode'],instruction=spec['instruction'],observation=spec['view'],
                     choices=[dict(id=str(i+1),label=frame.public(cmd,state)) for i,cmd in enumerate(spec['actions'])])
    return dict(version=2,text=_public_output(frame,text),game_ai=dict(version=1,consumed=consumed,
        tracking=dict(active=active,owner_id=meta.get('owner',frame.owner),members=[u for u in state.get('players',[frame.owner]) if isinstance(u,str) and re.fullmatch('[1-9][0-9]{0,19}',u)] if state else [],ambient=frame.pid in AMBIENT),task=pending))


def _authorised(frame,state,meta):
    user=str(frame.ctx.get('user_id',''))
    owner=(state or {}).get('host',(state or {}).get('creator',meta.get('owner',frame.owner)))
    if user!=owner and user not in frame.ctx.get('owner_ids',[]):raise GameError('请由本局发起者或机器人主人邀请/移除AI。')


def _join(frame,module,state,meta):
    _authorised(frame,state,meta)
    if meta.get('enabled') and alive(state) and (frame.pid not in MULTIPLAYER or frame.bot in state.get('players',[])):
        return state,'AI已经在本局中，不会重复加入或抢占额外回合。'
    if frame.pid in MULTIPLAYER:
        if not alive(state):raise GameError('请先创建本游戏房间，再发送 加入AI。')
        if frame.bot not in state['players']:
            result=module.handle('/'+GAMES[frame.pid]+' 加入',_actor(frame,frame.bot));state=frame.read()
            if frame.bot not in state.get('players',[]):raise GameError(str(result))
    elif frame.pid!='rps' and not alive(state):
        result=module.handle('/'+GAMES[frame.pid]+' 开始',_actor(frame,frame.owner));state=frame.read()
        if not alive(state):raise GameError(str(result))
    meta.update(enabled=True,owner=meta.get('owner',frame.owner),last_tag=None)
    return _state(frame,meta),'AI已加入'+('本局，作为独立玩家。' if frame.pid in MULTIPLAYER or frame.pid=='rps' else '，与你轮流操作当前题目。')


def _clue(frame,state,meta,text,user):
    if state.get('phase')!='playing':raise GameError('请先开始游戏。')
    players=[u for u in state['players'] if u not in state['eliminated']]
    if user not in players:raise GameError('只有本局存活玩家可以描述。')
    clues=[c for c in meta.get('clues',[]) if c['round']==state['round'] and c['user'] in players]
    done={c['user'] for c in clues}
    if user in done:raise GameError('本轮已经描述过，请等其他玩家。')
    next_user=next(u for u in players if u not in done)
    if next_user!=user:raise GameError('现在轮到 '+('机器人' if next_user==frame.bot else next_user)+' 描述。')
    text=_safe_text(text,180)
    compact=re.sub(r'\W|\s','',text).lower()
    if any(re.sub(r'\W|\s','',w).lower() in compact for w in set(state['hands'].values())):raise GameError('描述里包含题目原词，请换个说法。')
    meta.setdefault('clues',[]).append(dict(round=state['round'],user=user,text=text));meta['clues']=meta['clues'][-144:]
    remaining=[u for u in players if u not in done and u!=user]
    return ('机器人' if user==frame.bot else user)+'：'+text+('\n轮到 '+('机器人' if remaining[0]==frame.bot else remaining[0])+' 描述。' if remaining else '\n本轮描述完成，可以投票。')


def _settle(frame,module,state,meta):
    if frame.pid!='i_undercover' or not alive(state) or state.get('phase')!='playing':return ''
    players=[u for u in state['players'] if u not in state['eliminated']]
    if len(state['votes'])!=len(players):return ''
    # Automatic rule transition after every alive player has voted; this is a
    # fixed operation, never a model-generated host/admin command.
    result=module.handle('/谁是卧底 结算',_actor(frame,state['host']))
    meta['clues']=[]
    return str(result)


def _safe_text(value,limit):
    if not isinstance(value,str) or not 1<=len(value.strip())<=limit or any(ord(c)<32 and c not in '\n\t' for c in value) or '[CQ:' in value or re.search(r'https?://|<script|\[\[emoji:',value,re.I):raise GameError('模型没有给出符合本游戏格式的动作。')
    return value.strip()


def _answer(frame,module,state,meta,spec,response):
    if not isinstance(response,dict) or set(response)-{'action','text'}:raise GameError('模型动作格式无效。')
    p=frame.pid;act=spec['act'];body='';command=''
    if spec['mode']=='choice':
        index=response.get('action')
        if not isinstance(index,str) or not re.fullmatch('[1-9][0-9]{0,3}',index) or not 1<=int(index)<=len(spec['actions']):raise GameError('模型没有选择合法的动作编号。')
        command=spec['actions'][int(index)-1]
    else:body=_safe_text(response.get('text'),180 if act!='story' else 300)
    if act=='rps':
        nonce=secrets.token_hex(16);commit=hashlib.sha256((nonce+'|'+command).encode()).hexdigest()
        meta['commit']=dict(choice=command,nonce=nonce,hash=commit)
        return '本轮出拳已锁定，承诺 SHA-256：'+commit+'\n现在你可以出石头、剪刀或布。',state
    if act=='undercover_clue':return _clue(frame,state,meta,body,frame.bot),state
    if act=='topic':return '机器人：'+body,state
    if act=='truth':
        result=module.handle('/真心冒险 完成',frame.actor());return '机器人：'+body+'\n'+str(result),frame.read()
    if act=='buzzer':
        result=module.handle('/抢答 抢答',frame.actor());updated=frame.read()
        if updated.get('claim')!=frame.bot:raise GameError('抢答席位已变化，这次不再代答。')
        return str(result)+'\n机器人作答：'+body,updated
    if act=='story':command='写 '+body
    elif act=='turtle':command='提问 '+body
    elif act=='guess':command='猜 '+body;meta.setdefault('guess_history',[]).append(body)
    elif spec['mode']=='text':
        patterns=dict(wordle=r'[A-Za-z]{5}',guess_number=r'[0-9]{4}',x_hangman=r'[A-Za-z]{1,12}',x_anagram=r'[A-Za-z]{3,12}',x_arithmetic=r'[+-]?[0-9]{1,6}',x_twentyfour=r'[0-9+*/()\s-]{1,120}',m_mahjong=r'[0-9mpsz\s]{2,100}')
        if not re.fullmatch(patterns[p],body):raise GameError('模型提交的答案格式不符合游戏规则。')
        if p=='guess_number' and len(set(body))!=4:raise GameError('猜数字需要四个不同数字。')
        command=('猜 ' if p in ('x_hangman','x_anagram') else '答 ' if p in ('x_arithmetic','m_mahjong') else '')+body
    before=json.dumps(state,sort_keys=True)
    result=module.handle('/'+GAMES[p]+' '+command,frame.actor());updated=frame.read()
    if json.dumps(updated,sort_keys=True)==before and act not in ('turtle',):raise GameError('模型动作被原游戏规则拒绝：'+str(result)[:120])
    if p=='m_uno' and updated.get('uno_pending')==frame.bot:
        extra=module.handle('/UNO 喊UNO',frame.actor());result=str(result)+'\n'+str(extra);updated=frame.read()
    extra=_settle(frame,module,updated,meta)
    if extra:result=str(result)+'\n'+extra;updated=frame.read()
    return '机器人行动：'+frame.public(command,state)+'\n'+str(result),updated


def handle(module,text,context):
    control=context.get('_game_ai',{});pid=control.get('pid');op=control.get('op','command')
    if pid not in GAMES:return module.handle(text,context)
    if not re.fullmatch('[1-9][0-9]{0,19}',str(context.get('self_id',''))) and op=='command':
        arg=text.partition(' ')[2].strip()
        if arg not in JOIN|LEAVE|{'AI状态','AI重试'} and '人机' not in arg.split() and not context.get('settings',{}).get('ai_auto_join',False):return module.handle(text,context)
    db=None
    try:
        frame=Frame(pid,context,control.get('scope_owner'));db=frame.meta_db();db.execute('BEGIN IMMEDIATE');meta=frame.meta(db);state=_state(frame,meta)
        # Restored jobs can use only a saved seat/partner; the model cannot
        # nominate a different user, room, host or source path.
        if control.get('scope_owner') and str(control['scope_owner'])!=str(meta.get('owner',frame.owner)):raise GameError('AI参与记录不匹配。')
        arg=text.partition(' ')[2].strip();parts=arg.split();result='';consumed=False
        if op=='apply':
            if not context.get('settings',{}).get('ai_enabled',True) or not control.get('enabled',True):raise GameError('本游戏的AI参与已关闭。')
            if control.get('nonce')!=revision(state,meta):
                packet=_packet(frame,module,state,meta);packet['game_ai']['stale']=True;db.commit();return packet
            spec=task(frame,module,state,meta)
            if not spec:raise GameError('现在没有轮到AI的动作。')
            result,state=_answer(frame,module,state,meta,spec,control.get('response'))
            meta['last_tag']=spec['tag']
            if pid not in MULTIPLAYER and pid!='rps' and alive(state):
                # One cooperative step per actual human move, not an endless
                # solver that consumes the entire puzzle and model allowance.
                next_spec=task(frame,module,state,{**meta,'last_tag':None})
                if next_spec:meta['last_tag']=next_spec['tag']
            if pid=='i_set' and alive(state):
                next_spec=task(frame,module,state,{**meta,'last_tag':None})
                if next_spec:meta['last_tag']=next_spec['tag']
        elif op=='observe':
            user=str(context.get('user_id',''))
            if not meta.get('enabled') or not alive(state) or user==frame.bot or user not in state.get('players',[frame.owner]):
                db.commit();return _packet(frame,module,state,meta)
            if pid=='i_undercover' and state.get('phase')=='playing':
                consumed=True
                players=[u for u in state['players'] if u not in state['eliminated']]
                described={c['user'] for c in meta.get('clues',[]) if c['round']==state['round']}
                next_user=next((u for u in players if u not in described),None)
                if next_user==user:
                    result=_clue(frame,state,meta,text,user)
            elif pid=='i_buzzer' and state.get('claim')==user:
                # The original engine lets a claimed player answer in normal
                # group text. Keep it out of ordinary model conversation.
                consumed=True
            elif pid=='i_truth' and state.get('selected')==user:consumed=True
        elif op in ('refresh','retry'):
            if op=='retry':_authorised(frame,state,meta);meta['last_tag']=None
        else:
            wants=arg in JOIN or any(v in ('人机','AI') for v in parts[:2] if parts and parts[0] in ('开始','创建'))
            pure='纯玩家' in parts[:2]
            if (wants or context.get('settings',{}).get('ai_auto_join',False)) and not context.get('settings',{}).get('ai_enabled',True):wants=False
            if arg in LEAVE:
                _authorised(frame,state,meta)
                if pid in MULTIPLAYER and alive(state) and frame.bot in state.get('players',[]):
                    if state.get('phase')=='lobby':
                        state['players'].remove(frame.bot)
                        if 'hands' in state:state['hands'].pop(frame.bot,None)
                        frame.write(state)
                    else:result=str(module.handle('/'+GAMES[pid]+' 结束',context));state=frame.read()
                meta['enabled']=False;meta.pop('commit',None);result=(result+'\n' if result else '')+'AI已退出本局。'
            elif arg=='AI状态':result=('AI已加入本局。' if meta.get('enabled') and alive(state) else '本局尚未邀请AI。')+'\n'+HELP
            elif arg=='AI重试':_authorised(frame,state,meta);meta['last_tag']=None;result='正在重新检查AI回合。'
            elif arg in JOIN:
                if not context.get('settings',{}).get('ai_enabled',True):raise GameError('AI参与已关闭，请检查对话策略和本插件的AI开关。')
                state,result=_join(frame,module,state,meta)
            elif pid=='rps' and (arg in ('开始','开始 人机','创建') or meta.get('enabled')):
                if not meta.get('enabled'):meta.update(enabled=True,owner=frame.owner,rps_history=[])
                state={'phase':'playing'}
                if arg in ('石头','剪刀','布') and meta.get('commit'):
                    c=meta.pop('commit');choices=('石头','剪刀','布');outcome='平局' if arg==c['choice'] else '你赢了' if (choices.index(arg)+1)%3==choices.index(c['choice']) else '机器人赢了'
                    result=f"你出{arg}，机器人出{c['choice']}：{outcome}。\n承诺原文：{c['nonce']}|{c['choice']}\n可计算SHA-256核对上一轮承诺。"
                    meta.setdefault('rps_history',[]).append(dict(human=arg,bot=c['choice'],result=outcome));meta['rps_history']=meta['rps_history'][-10:];meta['last_tag']=None
                else:result='机器人先锁定本轮出拳，请等承诺公布后再出。'
            else:
                cleaned=' '.join(v for v in parts if v not in ('人机','纯玩家') and not (v=='AI' and wants))
                text=text.partition(' ')[0]+(' '+cleaned if cleaned else '')
                # Convenient AI aliases never become another player's account.
                if pid in ('i_undercover','i_teams'):
                    text=re.sub(r'(?<= )(?i:AI|机器人|雪乃)(?= |$)',frame.bot,text)
                if meta.get('enabled') and pid=='i_undercover' and parts and parts[0]=='描述':
                    result=_clue(frame,state,meta,arg.partition(' ')[2],str(context['user_id']));consumed=True
                else:
                    if wants and parts and parts[0]=='开始' and alive(state) and state.get('phase')=='lobby':
                        state,joined=_join(frame,module,state,meta)
                    if meta.get('enabled') and pid=='i_undercover' and parts and parts[0]=='投票':
                        players=[u for u in state['players'] if u not in state['eliminated']]
                        described={c['user'] for c in meta.get('clues',[]) if c['round']==state['round']}
                        if any(u not in described for u in players):raise GameError('请先按顺序描述完，再投票。可正常发言或发送 描述 内容。')
                    before=state;result=module.handle(text,context);state=frame.read()
                    if pid=='i_guess' and parts and parts[0]=='开始' and state and state.get('phase')=='playing' and (before or {}).get('phase')=='lobby':
                        meta['guess_clues']=[];meta['guess_history']=[];meta['last_tag']=None
                    created=bool(parts and parts[0] in ('开始','创建') and alive(state) and (not alive(before) or (state.get('code') and state.get('code')!=(before or {}).get('code'))))
                    if created:
                        meta={}
                        if not pure and (wants or context.get('settings',{}).get('ai_auto_join',False)) and context.get('settings',{}).get('ai_enabled',True):
                            state,joined=_join(frame,module,state,meta);result=str(result)+'\n'+joined
                    if meta.get('enabled') and pid=='i_guess' and parts and parts[0]=='线索' and state.get('host')==str(context['user_id']):
                        if state['answer'] not in arg.partition(' ')[2]:meta.setdefault('guess_clues',[]).append(arg.partition(' ')[2]);meta['guess_clues']=meta['guess_clues'][-12:]
                    if meta.get('enabled') and pid=='i_truth' and state.get('selected')==frame.bot and state.get('task') is None:
                        result=str(result)+'\n'+str(module.handle('/真心冒险 真心话',frame.actor()));state=frame.read()
                    if meta.get('enabled'):
                        extra=_settle(frame,module,state,meta)
                        if extra:result=str(result)+'\n'+extra;state=frame.read()
                if not parts or parts[0] in ('帮助','help','?'):result=str(result)+'\n'+HELP
        if meta.get('enabled') and result and op not in ('refresh','retry'):
            # Only actual public replies are remembered. Secret words and hands
            # never pass through this buffer, including private hand queries.
            if not frame.sid.startswith('p:') or pid not in MULTIPLAYER:
                meta.setdefault('seen_public',[]).append(frame.public(result,state or {})[:1500]);meta['seen_public']=meta['seen_public'][-4:]
        frame.save_meta(db,meta);packet=_packet(frame,module,state,meta,result,consumed);db.commit();return packet
    except GameError as exc:
        if db:db.rollback()
        return dict(version=2,text=str(exc),game_ai=dict(version=1,consumed=op=='observe',error=True,tracking=None,task=None))
    finally:
        if db:db.close()
