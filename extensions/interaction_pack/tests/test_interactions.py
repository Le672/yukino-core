"""Rule, persistence, secrecy, permissions and transaction acceptance."""
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
import copy,importlib.util,json,random,sqlite3,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import common,boards,social,engine
CAT=json.loads((ROOT/'catalog.json').read_text(encoding='utf-8'))

class InteractionTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def ctx(self,pid,user='2',scope='g:1',**settings):return {'data_dir':str(self.base/pid),'session_id':scope,'user_id':user,'owner_ids':['99'],'settings':settings}
 def call(self,pid,arg='',user='2',scope='g:1',**settings):
  spec=next(x for x in CAT if x['id']==pid);return engine.handle(pid,spec['commands'][0]+' '+arg,self.ctx(pid,user,scope,**settings))
 def state(self,pid,sid='g:1'):
  db=common.connect(self.ctx(pid));r=common.load(db,sid);db.close();return r
 def put(self,pid,r):
  db=common.connect(self.ctx(pid));db.execute('BEGIN IMMEDIATE');common.save(db,r);db.commit();db.close()
 def create(self,pid,tail='',players=()):
  self.assertIn('已创建',self.call(pid,'创建 '+tail))
  for p in players:self.assertIn('已加入',self.call(pid,'加入',user=p))
  return self.state(pid)
 def test_twelve_unique_independent_plugins_help_and_create(self):
  self.assertEqual(len(CAT),12);self.assertEqual(len({c['commands'][0] for c in CAT}),12)
  for spec in CAT:
   with self.subTest(id=spec['id']):
    pid=spec['id'];self.assertIn(spec['commands'][0],self.call(pid));self.assertIn(spec['expected_contains'],engine.handle(pid,spec['smoke_command'],self.ctx(pid)))
    self.assertEqual(self.state(pid)['host'],'2');self.assertTrue((self.base/pid/'interaction.sqlite3').exists())
 def test_all_rooms_block_uninvited_reset_and_are_scope_isolated(self):
  for spec in CAT:
   pid=spec['id'];arg=spec['smoke_command'].partition(' ')[2];self.call(pid,arg)
   original=self.state(pid);self.assertIn('进行中',self.call(pid,arg,user='3'));self.assertEqual(original,self.state(pid))
   self.assertIn('仅本房间',self.call(pid,'结束',user='3'));self.assertIn('没有',self.call(pid,'状态',scope='g:8')) if pid not in ('i_dots','i_nim','i_set','i_auction') else self.assertIn('先创建',self.call(pid,'状态',scope='g:8'))
   self.assertIn('结束',self.call(pid,'结束',user='99'))
 def test_bad_identity_rejected_without_database(self):
  for uid,sid in [('3','p:2'),('../2','g:1'),('2','g:../1'),('','g:1')]:
   out=self.call('i_topics','创建',user=uid,scope=sid);self.assertTrue('身份' in out or '会话' in out)
  self.assertFalse((self.base/'i_topics').exists())
 def test_turtle_secret_setup_only_host_private_and_question_queue(self):
  r=self.create('i_turtle');code=r['code']
  self.assertIn('私聊',self.call('i_turtle','设题 '+code+' 门开了 | 风吹开了'))
  self.assertIn('先加入',self.call('i_turtle','设题 '+code+' 门开了 | 风吹开了',user='3',scope='p:3'))
  self.assertIn('私密保存',self.call('i_turtle','设题 '+code+' 门开了 | 风吹开了',scope='p:2'))
  self.assertEqual(self.call('i_turtle','开始'),'汤面：门开了')
  self.assertNotIn('风吹',self.call('i_turtle','状态'));self.assertIn('排队',self.call('i_turtle','提问 是人为的吗',user='3'))
  self.assertIn('仅本房间',self.call('i_turtle','下一问',user='3'));self.assertIn('人为',self.call('i_turtle','下一问'))
  self.assertIn('否',self.call('i_turtle','回答 否'));self.assertIn('人为',self.call('i_turtle','记录'));self.assertEqual(self.call('i_turtle','揭底'),'汤底：风吹开了')
 def test_buzzer_first_claim_wrong_retry_and_scores_survive_next_round(self):
  self.create('i_buzzer');self.call('i_buzzer','开题 1+1等于几')
  self.assertIn('抢到',self.call('i_buzzer','抢答',user='3'));self.assertIn('已有抢答',self.call('i_buzzer','抢答',user='4'))
  self.assertIn('仅本房间',self.call('i_buzzer','判定 对',user='4'));self.call('i_buzzer','判定 错')
  self.assertIn('已尝试',self.call('i_buzzer','抢答',user='3'));self.call('i_buzzer','抢答',user='4');self.assertIn('得1分',self.call('i_buzzer','判定 对'))
  self.call('i_buzzer','开题 下一题');self.assertIn('4：1',self.call('i_buzzer','积分'));self.assertEqual(self.state('i_buzzer')['round'],2)
 def test_story_turns_skip_bounds_and_persistence(self):
  self.create('i_story','从前',['3']);self.call('i_story','开始');before=self.state('i_story')
  self.assertIn('没轮到',self.call('i_story','写 后来',user='3'));self.assertEqual(before,self.state('i_story'))
  self.assertIn('3',self.call('i_story','写 山里有只猫'));self.assertIn('2',self.call('i_story','跳过',user='3'))
  self.assertIn('山里有只猫',self.call('i_story','全文'));self.assertEqual(self.state('i_story')['turn'],2)
  self.assertIn('已开始',self.call('i_story','开始'))
 def test_topics_unique_ids_ownership_draw_removal_and_concurrent_writes(self):
  self.create('i_topics');self.call('i_topics','添加 猫猫',user='3');self.assertIn('只能删除',self.call('i_topics','删除 1',user='4'))
  self.assertIn('猫猫',self.call('i_topics','抽取'));self.assertIn('空',self.call('i_topics','抽取'))
  def add(i):return self.call('i_topics','添加 话题'+str(i),user=str(i+10))
  with ThreadPoolExecutor(max_workers=6) as ex:results=list(ex.map(add,range(24)))
  self.assertTrue(all('已收录' in r for r in results));s=self.state('i_topics');self.assertEqual(len(s['topics']),24);self.assertEqual(len({t['id'] for t in s['topics']}),24)
  self.assertIn('删除',self.call('i_topics','删除 '+str(s['topics'][0]['id'])))
 def test_truth_selected_player_only_and_optional_skip(self):
  self.create('i_truth',players=['3']);out=self.call('i_truth','抽人');who=self.state('i_truth')['selected'];other='3' if who=='2' else '2'
  self.assertIn('被抽中',self.call('i_truth','真心话',user=other));self.assertIn('可',self.call('i_truth','真心话',user=who))
  self.assertIn('任务已抽',self.call('i_truth','大冒险',user=who));self.assertIn('没有惩罚',self.call('i_truth','跳过',user=who));self.assertIsNone(self.state('i_truth')['selected'])
 def test_undercover_roles_private_membership_votes_and_host_can_moderate_after_elimination(self):
  r=self.create('i_undercover',players=['3','4','5']);self.call('i_undercover','开始');r=self.state('i_undercover');code=r['code']
  self.assertIn('只能在',self.call('i_undercover','取词 '+code))
  self.assertIn('先加入',self.call('i_undercover','取词 '+code,user='7',scope='p:7'))
  self.assertIn(r['hands']['3'],self.call('i_undercover','取词 '+code,user='3',scope='p:3'))
  self.assertNotIn(r['hands']['3'],self.call('i_undercover','状态'))
  # Explicit fixture isolates the eliminated-host regression from random roles.
  r['spy']='3';r['hands']={'2':'平民词','3':'卧底词','4':'平民词','5':'平民词'};self.put('i_undercover',r)
  for user,target in [('2','4'),('3','2'),('4','2'),('5','2')]:self.assertIn('已记票',self.call('i_undercover','投票 '+target,user=user))
  self.assertIn('出局',self.call('i_undercover','结算'));self.assertIn('已出局',self.call('i_undercover','投票 3'))
  for user,target in [('3','4'),('4','3'),('5','3')]:self.call('i_undercover','投票 '+target,user=user)
  self.assertIn('平民获胜',self.call('i_undercover','结算'));self.assertEqual(self.state('i_undercover')['phase'],'done')
 def test_undercover_rejects_duplicate_self_vote_and_tie_resets(self):
  self.create('i_undercover',players=['3','4','5']);self.call('i_undercover','开始')
  self.assertIn('另一位',self.call('i_undercover','投票 2'));self.call('i_undercover','投票 3');self.assertIn('已投票',self.call('i_undercover','投票 4'))
  self.assertIn('所有',self.call('i_undercover','结算'))
  for u,t in [('3','2'),('4','5'),('5','4')]:self.call('i_undercover','投票 '+t,user=u)
  self.assertIn('平票',self.call('i_undercover','结算'));self.assertEqual(self.state('i_undercover')['votes'],{})
 def test_guess_secret_clue_filter_win_and_score(self):
  self.create('i_guess');self.call('i_guess','开始');r=self.state('i_guess');code=r['code'];answer=r['answer']
  self.assertIn('先加入',self.call('i_guess','取词 '+code,user='3',scope='p:3'))
  self.assertIn(answer,self.call('i_guess','取词 '+code,scope='p:2'));self.assertIn('包含答案',self.call('i_guess','线索 答案是'+answer))
  self.assertIn('主持线索',self.call('i_guess','线索 这是日常物品'));self.assertIn('主持人不能',self.call('i_guess','猜 '+answer))
  self.assertIn('猜对',self.call('i_guess','猜 '+answer,user='3'));self.assertIn("'3': 1",self.call('i_guess','积分'));self.call('i_guess','开始');self.assertEqual(self.state('i_guess')['scores']['3'],1)
 def test_secret_code_does_not_grant_nonmember_or_cross_plugin_access(self):
  self.create('i_undercover',players=['3','4']);self.call('i_undercover','开始');r=self.state('i_undercover')
  self.assertIn('先加入',self.call('i_undercover','取词 '+r['code'],user='5',scope='p:5'))
  self.assertIn('不存在',self.call('i_guess','取词 '+r['code'],scope='p:2'))
  self.assertIn('无效',self.call('i_undercover','取词 ../../etc',scope='p:2'))
 def test_team_snake_order_unique_selection_complete(self):
  self.create('i_teams',players=['3','4','5','6','7']);self.call('i_teams','选队长 2 3')
  self.assertIn('轮到',self.call('i_teams','选 4',user='3'))
  for user,player in [('2','4'),('3','5'),('3','6'),('2','7')]:out=self.call('i_teams','选 '+player,user=user)
  self.assertIn('完成',out);r=self.state('i_teams');self.assertEqual(r['teams'],{'2':['2','4','7'],'3':['3','5','6']});self.assertEqual(r['phase'],'done')
 def test_dots_closed_square_extra_turn_duplicate_rejected_full_board(self):
  self.create('i_dots',players=['3']);self.call('i_dots','开始')
  for move,user in [('H 1 1','2'),('V 1 1','3'),('H 2 1','2')]:self.call('i_dots','落子 '+move,user=user)
  self.assertIn('闭合1格',self.call('i_dots','落子 V 1 2',user='3'));r=self.state('i_dots');self.assertEqual(r['turn'],1);self.assertEqual(r['boxes'],{'11':2})
  before=copy.deepcopy(r);self.assertIn('画过',self.call('i_dots','落子 V 1 2',user='3'));self.assertEqual(before,self.state('i_dots'))
  all_edges=[('H',i,j) for i in range(1,4) for j in range(1,3)]+[('V',i,j) for i in range(1,3) for j in range(1,4)]
  for k,i,j in all_edges:
   r=self.state('i_dots')
   if k+str(i)+str(j) not in r['edges']:out=self.call('i_dots',f'落子 {k} {i} {j}',user=r['players'][r['turn']])
  self.assertEqual(self.state('i_dots')['phase'],'done');self.assertEqual(len(self.state('i_dots')['boxes']),4);self.assertTrue('获胜' in out or '平局' in out)
 def test_nim_bounds_turns_and_last_stone_wins(self):
  self.create('i_nim','2',players=['3']);self.call('i_nim','开始');before=self.state('i_nim')
  self.assertIn('没有这么多',self.call('i_nim','取 1 3'));self.assertEqual(before,self.state('i_nim'))
  self.assertIn('没轮到',self.call('i_nim','取 1 1',user='3'));self.call('i_nim','取 1 1');self.assertIn('获胜',self.call('i_nim','取 1 1',user='3'))
  self.assertEqual(self.state('i_nim')['piles'],[0]);self.assertIn('done',self.call('i_nim','状态'))
 def test_set_mathematical_rules_distinctness_and_card_conservation(self):
  self.assertTrue(boards.set_valid(['1111','2222','3333']));self.assertTrue(boards.set_valid(['1111','1122','1133']))
  self.assertFalse(boards.set_valid(['1111','1111','1111']));self.assertFalse(boards.set_valid(['1111','1122','1233']))
  self.create('i_set');r=self.state('i_set');self.assertEqual(len(set(r['deck']+r['board'])),81)
  for _ in range(50):
   r=self.state('i_set');found=next((ix for ix in combinations(range(len(r['board'])),3) if boards.set_valid([r['board'][i] for i in ix])),None)
   if found:
    out=self.call('i_set','出 '+' '.join(str(i+1) for i in found));self.assertTrue('有效SET' in out or '牌局结束' in out)
   elif r['deck']:self.call('i_set','加牌')
   else:break
   r=self.state('i_set');cards=r['deck']+r['board'];self.assertEqual(len(cards),len(set(cards)));self.assertEqual(len(cards)+3*sum(r['scores'].values()),81)
   if r['phase']=='done':break
  self.assertEqual(self.state('i_set')['phase'],'done')
 def test_set_cannot_deal_over_valid_set_and_invalid_move_is_atomic(self):
  self.create('i_set');r=self.state('i_set');r['board']=['1111','2222','3333'];r['deck']=['1231'];self.put('i_set',r)
  self.assertIn('仍有',self.call('i_set','加牌'));self.assertIn('重复',self.call('i_set','出 1 1 2'));self.assertEqual(self.state('i_set'),r)
 def test_virtual_auction_join_budget_monotonic_price_and_host_settle(self):
  self.create('i_auction','云朵',players=['3','4']);self.call('i_auction','开始')
  self.assertIn('主持人不能',self.call('i_auction','出价 10'));self.assertIn('范围',self.call('i_auction','出价 101',user='3'))
  self.assertIn('10',self.call('i_auction','出价 10',user='3'));self.assertIn('高于',self.call('i_auction','出价 9',user='4'))
  self.assertIn('先加入',self.call('i_auction','出价 20',user='5'));self.assertIn('仅本房间',self.call('i_auction','落槌',user='3'))
  out=self.call('i_auction','落槌');self.assertIn('无付款',out);self.assertEqual(self.state('i_auction')['bidder'],'3');self.assertEqual(self.state('i_auction')['phase'],'done')
 def test_room_configuration_snapshotted_at_creation(self):
  self.call('i_nim','创建 5',nim_max_take=1,max_players=3);self.call('i_nim','加入',user='3');self.call('i_nim','开始')
  self.assertIn('范围为1—1',self.call('i_nim','取 1 2',nim_max_take=30));self.assertEqual(self.state('i_nim')['max_players'],2)
  self.call('i_auction','创建 星星',auction_budget=250);self.assertEqual(self.state('i_auction')['budget'],250)
 def test_expired_room_replaced_but_active_room_not_overwritten(self):
  self.create('i_topics');db=common.connect(self.ctx('i_topics'));db.execute('UPDATE rooms SET updated=0');db.close()
  self.assertIn('已创建',self.call('i_topics','创建'));self.assertIn('进行中',self.call('i_topics','创建'))

if __name__=='__main__':unittest.main()
