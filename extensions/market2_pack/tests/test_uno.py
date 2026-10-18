"""UNO rules, exact 108-card conservation, privacy and durable transactions."""
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import json
from pathlib import Path
import random
import re
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import uno_engine as uno


def card(color,value,n=0):
    return [i for i,c in enumerate(uno.CARDS) if c==(color,value)][n]


class UnoTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.context={'data_dir':self.tmp.name,'session_id':'g:1','user_id':'2','owner_ids':['99'],'settings':{}}
    def tearDown(self):self.tmp.cleanup()
    def ctx(self,user='2',scope='g:1',**settings):
        return {**self.context,'user_id':user,'session_id':scope,'settings':{**self.context['settings'],**settings}}
    def call(self,arg,user='2',scope='g:1',**settings):
        return uno.handle('/UNO '+arg,self.ctx(user,scope,**settings))
    def db(self):return uno.database(self.context)
    def state(self,scope='g:1'):
        db=self.db()
        try:
            row=db.execute('SELECT body FROM rooms WHERE scope=? ORDER BY updated DESC LIMIT 1',(scope,)).fetchone()
            return json.loads(row[0]) if row else None
        finally:db.close()
    def seed(self,hands=None,top=None,turn=0,direction=1,discard=None,drawn=None,pending=None,phase='playing',scope='g:1',code='ABC234'):
        hands=hands or {'2':[card('红','7'),card('蓝','5')],'3':[card('黄','2'),card('绿','3')]}
        discard=discard or [top if top is not None else card('红','5')]
        used=[i for values in hands.values() for i in values]+discard
        self.assertEqual(len(used),len(set(used)))
        s={'version':1,'code':code,'scope':scope,'phase':phase,'creator':next(iter(hands)),
            'players':list(hands),'hands':hands,'deck':[i for i in range(108) if i not in used],
            'discard':discard,'started':True,'turn':turn,'direction':direction,'color':uno.CARDS[discard[-1]][0] or '红',
            'drawn':drawn,'uno_pending':pending,'moves':0,'max_moves':1000,'max_players':6}
        db=self.db()
        try:uno.save(db,s)
        finally:db.close()
        return s
    def save(self,s):
        db=self.db()
        try:uno.save(db,s)
        finally:db.close()
    def conserved(self,s=None):
        s=s or self.state();all_cards=s['deck']+s['discard']+[c for h in s['hands'].values() for c in h]
        self.assertEqual(sorted(all_cards),list(range(108)))
    def lobby(self,players=('2','3')):
        self.assertIn('已创建',self.call('创建',players[0]))
        for p in players[1:]:self.assertIn('已加入',self.call('加入',p))
        return self.state()

    def test_deck_distribution_exact_108(self):
        self.assertEqual(len(uno.CARDS),108)
        for color in uno.COLORS:
            self.assertEqual(sum(c==color for c,v in uno.CARDS),25)
            self.assertEqual(uno.CARDS.count((color,'0')),1)
            for v in ('1','9','跳过','反转','+2'):self.assertEqual(uno.CARDS.count((color,v)),2)
        self.assertEqual(uno.CARDS.count(('','万能')),4);self.assertEqual(uno.CARDS.count(('','+4')),4)
    def test_help_and_unrelated_text(self):
        self.assertIsNone(uno.handle('普通聊天',self.context))
        self.assertIn('主动私聊',uno.handle('/UNO',{}));self.assertIn('108', (ROOT/'plugins/m_uno/README.md').read_text(encoding='utf-8'))
        self.assertIn('同色',uno.handle('/uno 帮助',{}))
    def test_create_random_code_and_no_second_active_table(self):
        self.call('创建');s=self.state();self.assertRegex(s['code'],r'^[A-HJ-NP-Z2-9]{6}$')
        self.assertEqual(s['players'],['2']);self.assertIn('已有',self.call('创建','3'));self.assertEqual(self.state(),s)
    def test_private_cannot_create_or_act(self):
        for action in ('创建','加入','开始','出 1','摸牌','跳过','结束'):
            self.assertIn('群聊',self.call(action,scope='p:2'))
        self.assertIsNone(self.state())
    def test_join_stable_seats_and_cannot_claim_other_user(self):
        self.lobby();before=self.state();self.assertIn('重复',self.call('加入'))
        self.assertIn('房间码',self.call('加入 99','4'));self.assertEqual(self.state(),before)
        self.assertIn('玩家3',self.call('加入','4'));self.assertEqual(self.state()['players'],['2','3','4'])
    def test_two_minimum_and_six_maximum(self):
        self.call('创建');self.assertIn('至少',self.call('开始'))
        for p in ('3','4','5','6','7'):self.call('加入',p)
        self.assertIn('座位已满',self.call('加入','8'));self.assertEqual(len(self.state()['players']),6)
        self.assertIn('已开始',self.call('开始'));self.conserved()
    def test_max_players_config_snapshots_at_create(self):
        self.call('创建',max_players=2);self.call('加入','3',max_players=6)
        self.assertIn('座位已满',self.call('加入','4',max_players=6));self.assertEqual(self.state()['max_players'],2)
    def test_start_authorization_and_numeric_first_discard(self):
        self.lobby();before=self.state()
        self.assertIn('只有创建者',self.call('开始','3'));self.assertEqual(self.state(),before)
        self.assertIn('已开始',self.call('开始','99'));s=self.state()
        self.assertEqual([len(h) for h in s['hands'].values()],[7,7]);self.assertTrue(uno.CARDS[s['discard'][-1]][1].isdigit());self.conserved(s)
        self.assertIn('不能中途加入',self.call('加入','4'))
    def test_creator_or_owner_end_and_recreate(self):
        self.lobby();s=self.state();self.assertIn('只有创建者',self.call('结束','3'));self.assertEqual(self.state(),s)
        self.call('结束','99');self.assertEqual(self.state()['phase'],'done')
        self.assertIn('已创建',self.call('创建','4'));self.assertEqual(self.state()['creator'],'4')
    def test_public_status_hides_all_hands_and_deck(self):
        self.seed({'2':[card('蓝','9'),card('绿','8')],'3':[card('黄','7'),card('蓝','6')]},top=card('红','1'))
        for reply in (self.call('状态'),self.call('状态','8')):
            for secret in ('蓝9','绿8','黄7','蓝6'):self.assertNotIn(secret,reply)
            self.assertIn('红1',reply);self.assertIn('玩家1：2张',reply)
    def test_group_hand_is_always_refused(self):
        self.seed();code=self.state()['code']
        for user in ('2','3','99'):
            reply=self.call('手牌 '+code,user);self.assertIn('群内禁止',reply);self.assertNotIn('蓝5',reply)
    def test_private_hand_membership_even_owner_cannot_read_others(self):
        self.seed();code=self.state()['code']
        mine=self.call('手牌 '+code,scope='p:2');self.assertIn('红7',mine);self.assertIn('蓝5',mine);self.assertNotIn('黄2',mine)
        theirs=self.call('手牌 '+code,'3','p:3');self.assertIn('黄2',theirs);self.assertNotIn('红7',theirs)
        self.assertIn('不是这个房间',self.call('手牌 '+code,'99','p:99'))
    def test_private_identity_must_match_real_user(self):
        self.seed();self.assertIn('不匹配',self.call('手牌 ABC234','3','p:2'))
        self.assertIn('有效的QQ用户',self.call('状态','2\n99'))
    def test_private_code_case_and_missing_room(self):
        self.seed();self.assertIn('红7',self.call('手牌 abc234',scope='p:2'))
        self.assertIn('不存在',self.call('手牌 ZZZ999',scope='p:2'));self.assertIn('6位',self.call('手牌 xx',scope='p:2'))
    def test_group_scope_cannot_operate_other_room_by_code(self):
        self.seed();before=self.state();self.assertIn('没有有效',self.call('出 1',scope='g:2'))
        self.call('创建',scope='g:2');self.assertIn('不能跨群',self.call('开始 ABC234',scope='g:2'))
        self.assertEqual(self.state(),before)
    def test_hand_pagination_and_no_state_touch_on_read(self):
        top=card('红','0');own=[i for i in range(108) if i!=top][:45];others=[i for i in range(108) if i not in own+[top]][:2]
        self.seed({'2':own,'3':others},top=top)
        reply=self.call('手牌 ABC234 2',scope='p:2',hand_page_size=10)
        self.assertIn('第2/5页',reply);self.assertRegex(reply,r'\n11\. ');self.assertNotRegex(reply,r'\n21\. ')
        self.assertIn('页码范围',self.call('手牌 ABC234 6',scope='p:2',hand_page_size=10))
        db=self.db()
        try:
            before=db.execute('SELECT updated FROM rooms').fetchone()[0]
            self.call('状态');self.call('手牌 ABC234',scope='p:2')
            self.assertEqual(db.execute('SELECT updated FROM rooms').fetchone()[0],before)
        finally:db.close()
    def test_turn_owner_and_invalid_move_rollback(self):
        self.seed();before=self.state()
        self.assertIn('还没轮到',self.call('出 1','3'));self.assertIn('不是本桌',self.call('出 1','4'))
        self.assertIn('编号不存在',self.call('出 99'));self.assertEqual(self.state(),before)
    def test_same_color_and_same_value_playable(self):
        self.seed();self.assertIn('红7',self.call('出 1'));self.assertEqual(self.state()['turn'],1);self.conserved()
        self.seed({'2':[card('蓝','5'),card('黄','8')],'3':[card('绿','4')]})
        self.assertIn('打出 蓝5',self.call('出 1'));self.conserved()
    def test_mismatch_and_extra_color_rejected(self):
        self.seed();before=self.state()
        self.assertIn('不需要额外',self.call('出 1 蓝'));self.assertEqual(self.state(),before)
        self.seed({'2':[card('蓝','6'),card('黄','8')],'3':[card('绿','4')]})
        before=self.state();self.assertIn('不匹配',self.call('出 1'));self.assertEqual(self.state(),before)
    def test_skip_card_skips_next_in_three_player_table(self):
        self.seed({'2':[card('红','跳过'),card('蓝','5')],'3':[card('黄','8')],'4':[card('绿','4')]})
        self.call('出 1');self.assertEqual(self.state()['turn'],2);self.conserved()
    def test_reverse_three_players_changes_direction(self):
        self.seed({'2':[card('红','反转'),card('蓝','5')],'3':[card('黄','8')],'4':[card('绿','4')]})
        self.call('出 1');s=self.state();self.assertEqual(s['direction'],-1);self.assertEqual(s['turn'],2);self.conserved()
    def test_two_player_reverse_acts_as_skip(self):
        self.seed({'2':[card('红','反转'),card('蓝','5')],'3':[card('黄','8')]})
        self.call('出 1');self.assertEqual(self.state()['turn'],0);self.conserved()
    def test_draw_two_immediate_penalty_no_stacking(self):
        self.seed({'2':[card('红','+2'),card('蓝','5')],'3':[card('黄','+2'),card('黄','8')],'4':[card('绿','4')]})
        self.assertIn('罚抽2张并跳过',self.call('出 1'));s=self.state();self.assertEqual(len(s['hands']['3']),4);self.assertEqual(s['turn'],2)
        self.assertIn('还没轮到',self.call('出 1','3'));self.assertEqual(self.state(),s);self.conserved()
    def test_wild_requires_explicit_valid_color(self):
        self.seed({'2':[card('','万能'),card('蓝','5')],'3':[card('黄','8')]})
        before=self.state();self.assertIn('必须指定',self.call('出 1'));self.assertIn('必须指定',self.call('出 1 紫'));self.assertEqual(self.state(),before)
        self.call('出 1 绿');self.assertEqual(self.state()['color'],'绿');self.conserved()
    def test_draw_four_illegal_if_current_color_owned(self):
        self.seed({'2':[card('','+4'),card('红','8')],'3':[card('黄','8')]})
        before=self.state();self.assertIn('不能使用+4',self.call('出 1 蓝'));self.assertEqual(self.state(),before)
    def test_draw_four_legal_even_other_color_same_number(self):
        self.seed({'2':[card('','+4'),card('蓝','5')],'3':[card('黄','8')],'4':[card('绿','8')]})
        self.assertIn('罚抽4张',self.call('出 1 蓝'));s=self.state();self.assertEqual(len(s['hands']['3']),5);self.assertEqual(s['color'],'蓝');self.assertEqual(s['turn'],2);self.conserved()
    def test_draw_once_then_only_drawn_card_or_pass(self):
        self.seed();self.assertIn('已摸1张',self.call('摸牌'));s=self.state();self.assertIsNotNone(s['drawn'])
        self.assertIn('已经摸过',self.call('摸牌'));self.assertIn('只能出刚摸',self.call('出 1'));self.assertEqual(self.state(),s)
        self.call('跳过');self.assertEqual(self.state()['turn'],1);self.assertIsNone(self.state()['drawn']);self.conserved()
    def test_cannot_pass_before_drawing(self):
        self.seed();before=self.state();self.assertIn('请先摸',self.call('跳过'));self.assertEqual(self.state(),before)
    def test_drawn_card_play_and_draw_reply_hides_face(self):
        self.seed();s=self.state();drawn=card('红','9');s['deck'].remove(drawn);s['deck'].append(drawn);self.save(s)
        reply=self.call('摸牌');self.assertNotIn('红9',reply);self.assertIn('本回合刚摸的牌编号：3',self.call('手牌 ABC234',scope='p:2'))
        self.call('出 3');self.assertEqual(self.state()['discard'][-1],drawn);self.assertEqual(self.state()['turn'],1);self.conserved()
    def test_uno_call_only_self_one_card(self):
        self.seed();self.assertIn('恰好剩1张',self.call('喊UNO'));self.call('出 1');s=self.state();self.assertEqual(s['uno_pending'],'2')
        self.assertIn('恰好剩1张',self.call('喊UNO','3'));self.assertEqual(self.state(),s)
        self.assertIn('玩家1喊UNO',self.call('喊UNO'));self.assertIsNone(self.state()['uno_pending'])
    def test_missed_uno_penalty_before_next_valid_move(self):
        self.seed({'2':[card('红','7'),card('蓝','5')],'3':[card('红','8'),card('黄','4')]})
        self.call('出 1');self.assertIn('漏喊UNO，补抽2张',self.call('出 1','3'));s=self.state();self.assertEqual(len(s['hands']['2']),3);self.assertEqual(len(s['hands']['3']),1);self.conserved()
    def test_invalid_turn_does_not_consume_uno_window(self):
        self.seed({'2':[card('蓝','5')],'3':[card('黄','6'),card('蓝','6')]},turn=1,pending='2')
        before=self.state();self.assertIn('不匹配',self.call('出 1','3'));self.assertEqual(self.state(),before)
        self.call('状态','4');self.call('手牌 ABC234',scope='p:2');self.assertEqual(self.state(),before)
        self.call('喊UNO');self.assertIsNone(self.state()['uno_pending'])
    def test_called_uno_avoids_penalty(self):
        self.seed({'2':[card('红','7'),card('蓝','5')],'3':[card('红','8'),card('黄','4')]})
        self.call('出 1');self.call('喊UNO');self.call('出 1','3');self.assertEqual(len(self.state()['hands']['2']),1);self.conserved()
    def test_last_draw_two_penalty_before_victory(self):
        self.seed({'2':[card('红','+2')],'3':[card('黄','8')]})
        reply=self.call('出 1');s=self.state();self.assertIn('罚抽2张',reply);self.assertIn('获胜',reply);self.assertEqual(s['phase'],'done');self.assertEqual(len(s['hands']['3']),3);self.conserved()
    def test_last_draw_four_penalty_before_victory(self):
        self.seed({'2':[card('','+4')],'3':[card('黄','8')]})
        self.call('出 1 黄');s=self.state();self.assertEqual(s['phase'],'done');self.assertEqual(len(s['hands']['3']),5);self.conserved()
    def test_finished_game_rejects_further_moves(self):
        self.seed({'2':[card('红','7')],'3':[card('黄','8')]});self.call('出 1');before=self.state()
        self.assertIn('尚未开始或已经结束',self.call('摸牌','3'));self.assertEqual(self.state(),before)
    def test_reshuffle_keeps_top_and_current_color(self):
        s=self.seed();top=card('','万能');old=[card('蓝','1'),card('绿','2')]
        used=s['hands']['2']+[top]+old;s['hands']['3']=[i for i in range(108) if i not in used];s['deck']=[];s['discard']=old+[top];s['color']='黄';self.save(s)
        self.call('摸牌');new=self.state();self.assertEqual(new['discard'],[top]);self.assertEqual(new['color'],'黄');self.assertIn(new['drawn'],old);self.conserved()
    def test_exhausted_deck_can_draw_zero_then_pass(self):
        s=self.seed();s['hands']['3'].extend(s['deck']);s['deck']=[];self.save(s)
        self.assertIn('均不足',self.call('摸牌'));self.assertEqual(self.state()['drawn'],-1)
        self.call('跳过');self.assertEqual(self.state()['turn'],1);self.conserved()
    def test_penalty_with_insufficient_stock_never_creates_cards(self):
        s=self.seed({'2':[card('红','+2')],'3':[card('黄','8')]});s['hands']['3'].extend(s['deck']);s['deck']=[];self.save(s)
        reply=self.call('出 1');self.assertIn('牌堆不足',reply);self.assertIn('罚抽1张',reply);self.conserved()
    def test_max_moves_setting_and_tie_resolution(self):
        s=self.seed();s['max_moves']=50;s['moves']=49;self.save(s);self.call('出 1');s=self.state()
        self.assertEqual(s['phase'],'done');self.assertIn('回合上限',s['result']);self.assertIsNone(s['uno_pending']);self.conserved()
    def test_max_moves_config_applies_when_starting(self):
        self.lobby();self.call('开始',max_moves=50);self.assertEqual(self.state()['max_moves'],50)
    def test_expired_room_removed_and_not_readable(self):
        self.seed();db=self.db()
        try:db.execute('UPDATE rooms SET updated=?',(time.time()-2*86400,))
        finally:db.close()
        self.assertIn('不存在或已经过期',self.call('手牌 ABC234',scope='p:2',expiry_days=1));self.assertIsNone(self.state())
    def test_capacity_rejects_active_rooms_then_prunes_ended(self):
        for i in range(1,11):self.call('创建',scope='g:'+str(i),max_rooms=10)
        self.assertIn('达到上限',self.call('创建',scope='g:11',max_rooms=10))
        old=self.state();self.call('结束');self.assertIn('已创建',self.call('创建',scope='g:11',max_rooms=10))
        self.assertIn('不存在',self.call('手牌 '+old['code'],scope='p:2'))
        db=self.db()
        try:self.assertEqual(db.execute('SELECT COUNT(*) FROM rooms').fetchone()[0],10)
        finally:db.close()
    def test_restarts_read_existing_room(self):
        self.seed();before=self.state();db=self.db();db.close()
        self.assertIn('进行中',self.call('状态'));self.assertEqual(self.state(),before);self.assertIn('红7',self.call('手牌 ABC234',scope='p:2'))
    def test_bad_input_and_paths_do_not_mutate(self):
        self.seed();before=self.state()
        self.assertIn('输入过长',self.call('出 '+'1'*201));self.assertIn('未知指令',self.call('执行 import os'))
        self.assertIn('不需要额外',self.call('摸牌 99'));self.assertEqual(self.state(),before)
        self.assertIn('独立data_dir',uno.handle('/UNO 创建',{**self.context,'data_dir':'../bad'}))
    def test_corrupt_card_count_is_rejected_without_exposure(self):
        self.seed();db=self.db()
        try:
            s=self.state();s['hands']['2'].append(s['hands']['3'][0]);db.execute('UPDATE rooms SET body=?',(json.dumps(s),))
        finally:db.close()
        reply=self.call('手牌 ABC234',scope='p:2');self.assertIn('不守恒',reply);self.assertNotIn('红7',reply)
    def test_concurrent_duplicate_join_has_single_seat(self):
        self.call('创建')
        with ThreadPoolExecutor(max_workers=4) as pool:replies=list(pool.map(lambda _:self.call('加入','3'),range(4)))
        self.assertEqual(sum('已加入玩家2' in r for r in replies),1);self.assertEqual(self.state()['players'],['2','3'])
    def test_concurrent_players_respect_six_seat_cap(self):
        self.call('创建')
        with ThreadPoolExecutor(max_workers=6) as pool:replies=list(pool.map(lambda u:self.call('加入',u),('3','4','5','6','7','8')))
        self.assertEqual(sum('已加入' in r for r in replies),5);self.assertEqual(len(self.state()['players']),6);self.assertEqual(sum('座位已满' in r for r in replies),1)
    def test_concurrent_same_turn_play_commits_once(self):
        self.seed()
        with ThreadPoolExecutor(max_workers=2) as pool:replies=list(pool.map(lambda _:self.call('出 1'),range(2)))
        self.assertEqual(sum('打出' in r for r in replies),1);self.assertEqual(self.state()['moves'],1);self.assertEqual(len(self.state()['hands']['2']),1);self.conserved()
    def test_complete_generated_games_keep_all_108_cards(self):
        for seed in range(12):
            with self.subTest(seed=seed),patch.object(uno,'RNG',random.Random(seed)):
                players=[str(i+2) for i in range(2+seed%5)]
                s={'version':1,'code':'ABC234','scope':'g:1','phase':'lobby','creator':'2','players':players,'hands':{u:[] for u in players},'deck':[],'discard':[],'started':False,'max_players':6}
                uno.start_game(s,{'settings':{'max_moves':100}})
                for _ in range(400):
                    uno.validate(s)
                    if s['phase']=='done':break
                    # Simulate players announcing their last card immediately.
                    if s.get('uno_pending'):s['uno_pending']=None
                    user=s['players'][s['turn']];hand=s['hands'][user];top_value=uno.CARDS[s['discard'][-1]][1]
                    available=[]
                    for i,v in enumerate(hand):
                        color,value=uno.CARDS[v]
                        if s.get('drawn') is not None and s['drawn']!=v:continue
                        if color and color!=s['color'] and value!=top_value:continue
                        if value=='+4' and any(uno.CARDS[h][0]==s['color'] for h in hand):continue
                        available.append((i,v))
                    if available:
                        index,value=available[0];parts=['出',str(index+1)]
                        if not uno.CARDS[value][0]:
                            colors=Counter(uno.CARDS[v][0] for v in hand if uno.CARDS[v][0]);parts.append(colors.most_common(1)[0][0] if colors else '红')
                    else:parts=['摸牌'] if s.get('drawn') is None else ['跳过']
                    uno.turn_action(s,user,parts);self.conserved(s)
                self.assertEqual(s['phase'],'done');self.assertLessEqual(s['moves'],100)


if __name__=='__main__':unittest.main()
