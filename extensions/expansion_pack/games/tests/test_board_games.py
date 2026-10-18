"""Board rules, exact arithmetic and persistent authorization tests."""
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import games_engine as engine
import helper_board as board


class Boards(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.ctx={'data_dir':self.temp.name,'session_id':'g:1','user_id':'2','settings':{}}
    def tearDown(self): self.temp.cleanup()
    def call(self,kind,arg,user='2',scope='g:1'):
        return engine.handle(kind,engine.COMMANDS[kind]+' '+arg,{**self.ctx,'user_id':user,'session_id':scope})
    def state(self,kind,scope='g:1',who='*'):
        db=sqlite3.connect(Path(self.temp.name)/'games.sqlite3')
        try:
            row=db.execute('SELECT body FROM games WHERE kind=? AND scope=? AND who=?',(kind,scope,who)).fetchone()
            return json.loads(row[0]) if row else None
        finally: db.close()
    def seed(self,kind,state,who='*'):
        db=engine.database(self.ctx)
        try: db.execute('INSERT OR REPLACE INTO games VALUES(?,?,?,?,?)',(kind,'g:1',who,json.dumps(state),9999999999))
        finally: db.close()
    def setup_mp(self,kind):
        self.assertIn('对局已开始',self.call(kind,'开始'))
        self.assertIn('玩家2已加入',self.call(kind,'加入',user='3'))

    def test_connect4_gravity_turn_and_spectator(self):
        k='x_connect4';self.setup_mp(k)
        self.assertIn('旁观者',self.call(k,'1',user='4'))
        self.assertIn('旁观者',self.call(k,'结束',user='4'))
        self.assertIn('还没轮到',self.call(k,'1',user='3'))
        self.call(k,'1');s=self.state(k)
        self.assertEqual(s['board'][35],1);self.assertEqual(s['turn'],1)
        before=copy.deepcopy(s);self.call(k,'9',user='3');self.assertEqual(self.state(k),before)
    def test_connect4_full_column_does_not_consume_turn(self):
        k='x_connect4';self.setup_mp(k)
        for i in range(6): self.call(k,'1',user='2' if i%2==0 else '3')
        before=self.state(k);self.assertIn('已经满',self.call(k,'1'));self.assertEqual(self.state(k),before)
    def test_connect4_vertical_win(self):
        k='x_connect4';self.setup_mp(k)
        for c,u in [('1','2'),('2','3'),('1','2'),('2','3'),('1','2'),('2','3')]: self.call(k,c,u)
        self.assertIn('玩家1获胜',self.call(k,'1'));self.assertTrue(self.state(k)['done'])
        before=self.state(k);self.call(k,'3',user='3');self.assertEqual(self.state(k),before)
    def test_line_wins_all_directions_and_no_false_wrap(self):
        for positions in ([0,1,2,3],[0,7,14,21],[0,8,16,24],[3,9,15,21]):
            b=[0]*42
            for i in positions:b[i]=1
            last=positions[-1];self.assertTrue(board.line_win(b,7,6,last//7,last%7,4))
        b=[0]*42
        for i in (5,6,7,8):b[i]=1
        self.assertFalse(board.line_win(b,7,6,1,1,4))
    def test_multiplayer_seats_and_scope(self):
        for k in engine.MULTI:
            self.setup_mp(k);self.assertIn('座位已满',self.call(k,'加入',user='4'))
            self.assertIn('创建对局',self.call(k,'状态',scope='g:2'))
            before=self.state(k);self.call(k,'开始',user='4');self.assertEqual(self.state(k),before)
    def test_multiplayer_private_requires_tictactoe_robot(self):
        for k in ('x_connect4','x_gomoku','x_reversi'):
            self.assertIn('群聊',self.call(k,'开始',scope='p:2'))
            self.assertIsNone(self.state(k,scope='p:2'))
        k='x_tictactoe'
        self.assertIn('只支持人机',self.call(k,'开始',scope='p:2'))
        self.assertIsNone(self.state(k,scope='p:2'))
        self.assertIn('对局已开始',self.call(k,'开始 人机',scope='p:2'))
        self.call(k,'1 1',scope='p:2');self.assertEqual(self.state(k,scope='p:2')['board'].count(2),1)
    def test_tictactoe_row_win_and_occupied(self):
        k='x_tictactoe';self.setup_mp(k);self.call(k,'1 1')
        old=self.state(k);self.assertIn('已有棋子',self.call(k,'1 1','3'));self.assertEqual(self.state(k),old)
        for a,u in [('2 1','3'),('1 2','2'),('2 2','3')]: self.call(k,a,u)
        self.assertIn('玩家1获胜',self.call(k,'1 3'))
    def test_tictactoe_robot_move_and_block(self):
        k='x_tictactoe';self.call(k,'开始 人机');self.call(k,'1 1');s=self.state(k)
        self.assertEqual(s['board'].count(1),1);self.assertEqual(s['board'].count(2),1)
        self.assertEqual(s['turn'],0);self.assertIn('座位已满',self.call(k,'加入','3'))
        s={'board':[1,1,0,0,2,0,0,0,0],'players':['2','BOT'],'turn':0,'done':False,'creator':'2'}
        self.seed(k,s);self.call(k,'3 3');self.assertEqual(self.state(k)['board'][2],2)
    def test_tictactoe_draw(self):
        k='x_tictactoe';s={'board':[1,2,1,1,2,2,2,1,0],'players':['2','3'],'turn':0,'done':False,'creator':'2'}
        self.seed(k,s);self.assertIn('平局',self.call(k,'3 3'));self.assertTrue(self.state(k)['done'])
    def test_gomoku_five_and_invalid_coordinates(self):
        k='x_gomoku';self.setup_mp(k);before=self.state(k)
        self.call(k,'0 9');self.assertEqual(self.state(k),before)
        for c in range(1,5):self.call(k,f'1 {c}');self.call(k,f'2 {c}','3')
        self.assertIn('玩家1获胜',self.call(k,'1 5'))
    def test_reversi_flip_illegal_and_skip(self):
        k='x_reversi';self.setup_mp(k);old=self.state(k)
        self.assertIn('不能跳过',self.call(k,'跳过'));self.assertEqual(self.state(k),old)
        self.assertIn('非法落点',self.call(k,'1 1'));self.assertEqual(self.state(k),old)
        self.call(k,'2 3');s=self.state(k)
        self.assertEqual(s['board'].count(1),4);self.assertEqual(s['board'].count(2),1);self.assertEqual(s['turn'],1)
    def test_reversi_multi_direction_and_terminal(self):
        b=[0]*36
        for i in (2,12,16,26):b[i]=1
        for i in (8,13,15,20):b[i]=2
        self.assertEqual(set(board.flips(b,2,2,1)),{8,13,15,20})
        k='x_reversi';s={'board':[1]*36,'players':['2','3'],'turn':0,'done':False,'creator':'2'}
        s['board'][0]=0;s['board'][1]=2;self.seed(k,s)
        self.assertIn('黑白棋结束',self.call(k,'1 1'));self.assertTrue(self.state(k)['done'])
    def test_reversi_no_move_pass_preserves_board(self):
        k='x_reversi';s={'board':[1]*36,'players':['2','3'],'turn':1,'done':False,'creator':'2'}
        s['board'][0]=0;s['board'][1]=2;self.seed(k,s)
        self.assertEqual(board.reversi_moves(s['board'],2),[])
        self.assertIn('已跳过',self.call(k,'跳过','3'));new=self.state(k)
        self.assertEqual(new['board'],s['board']);self.assertEqual(new['turn'],0)

    def test_dice_categories_exact(self):
        for dice,cat,value in [([2,2,2,3,3],'葫芦',25),([2]*5,'葫芦',0),([1,2,3,4,4],'小顺',30),([2,3,4,5,6],'大顺',40),([6]*5,'快艇',50),([3,3,3,1,2],'三条',12),([4,4,4,2,2],'四条',0),([2,2,5,5,5],'二',4)]:
            self.assertEqual(board.dice_score(dice,cat),value)
    def test_dice_keep_reroll_and_limits(self):
        k='x_yahtzee';self.call(k,'开始');s=self.state(k,who='2');old=s['dice'].copy()
        self.call(k,'保留 1 3');s=self.state(k,who='2')
        self.assertEqual([s['dice'][0],s['dice'][2]],[old[0],old[2]])
        self.call(k,'重掷 2');before=self.state(k,who='2');self.assertIn('已掷3次',self.call(k,'重掷 1'));self.assertEqual(self.state(k,who='2'),before)
        self.call(k,'计分 机会');self.assertEqual(self.state(k,who='2')['rolls'],1)
        before=self.state(k,who='2');self.call(k,'计分 机会');self.assertEqual(self.state(k,who='2'),before)
    def test_dice_round_setting_and_scope(self):
        k='x_yahtzee';self.ctx['settings']['round_limit']=3;self.call(k,'开始')
        for category in ('一','二','三'):self.call(k,'计分 '+category)
        self.assertTrue(self.state(k,who='2')['done'])
        self.assertIn('开始',self.call(k,'状态','3'));self.assertIsNone(self.state(k,who='3'))
        self.assertIn('开始',self.call(k,'状态',scope='p:2'))
    def test_wordchain_real_turn_and_invalid(self):
        k='x_wordchain';self.call(k,'开始');before=self.state(k,who='2')
        self.assertIn('未收录',self.call(k,'乱七八糟'));self.assertEqual(self.state(k,who='2'),before)
        self.assertIn('需要以',self.call(k,'水到渠成'));self.assertEqual(self.state(k,who='2'),before)
        self.assertIn('我接',self.call(k,'意气风发'));s=self.state(k,who='2')
        self.assertEqual(s['rounds'],1);self.assertEqual(len(s['used']),3)
        self.assertIn('开始',self.call(k,'状态','3'));self.assertIsNone(self.state(k,who='3'))
    def test_wordchain_repeat_and_win(self):
        k='x_wordchain';s={'last':'一心一意','used':['一心一意','意气风发'],'rounds':0,'max_rounds':3,'done':False};self.seed(k,s,'2')
        self.assertIn('已经用过',self.call(k,'意气风发'))
        s={'last':'后起之秀','used':['后起之秀'],'rounds':0,'max_rounds':3,'done':False};self.seed(k,s,'2')
        self.call(k,'秀外慧中');self.assertEqual(self.state(k,who='2')['rounds'],1)
    def test_24point_exact_and_security(self):
        from fractions import Fraction
        self.assertEqual(board.calculate24('8/(3-8/3)',[3,3,8,8]),24)
        self.assertEqual(board.calculate24('(1+2+3)/4',[1,2,3,4]),Fraction(3,2))
        for expr in ('24','3+3+8+8+1','33-8-1','8**3/3/8','__import__("os")','8/(3-3)*8','3.0+3+8+8','True+3+8+8'):
            with self.assertRaises(ValueError,msg=expr):board.calculate24(expr,[3,3,8,8])
    def test_24point_all_generated_solvable_and_scope(self):
        k='x_twentyfour'
        for _ in range(10):
            self.call(k,'开始');s=self.state(k,who='2')
            self.assertEqual(board.calculate24(s['solution'],s['numbers']),24)
            self.assertNotIn(s['solution'],self.call(k,'状态'))
            self.assertIn('答对',self.call(k,s['solution']))
        self.assertIn('开始',self.call(k,'状态','3'));self.assertIsNone(self.state(k,who='3'))
    def test_24point_invalid_does_not_use_attempt_and_loss(self):
        k='x_twentyfour';s={'numbers':[1,2,3,4],'solution':'(1+2+3)*4','tries':0,'max_attempts':3,'done':False};self.seed(k,s,'2')
        self.call(k,'2+2+3+4');self.assertEqual(self.state(k,who='2')['tries'],0)
        for _ in range(3):self.call(k,'1+2+3+4')
        self.assertTrue(self.state(k,who='2')['done'])
    def test_unknown_command_and_input_identity_validation(self):
        self.assertIsNone(engine.handle('x_connect4','hello',self.ctx))
        self.assertIn('会话',engine.handle('x_connect4','/四子棋 开始',{**self.ctx,'session_id':''}))


if __name__=='__main__': unittest.main()
