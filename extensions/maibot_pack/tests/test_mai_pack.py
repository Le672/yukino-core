import base64
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
local=load('mai_local_test',ROOT/'local_engine.py')

class LocalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.ctx={'session_id':'g:777','user_id':'43','owner_ids':['42'],'data_dir':self.tmp.name,'settings':{}}
        self.rng=patch.object(local,'RNG',random.Random(100));self.rng.start()
    def tearDown(self):self.rng.stop();self.tmp.cleanup()
    def call(self,kind,text,ctx=None):return local.handle(kind,text,self.ctx if ctx is None else ctx)
    def other(self,**values):return {**self.ctx,**values}
    def test_luck_same_day_stable_and_scoped(self):
        a=self.call('daily_luck','/今日运势');self.assertEqual(a,self.call('daily_luck','/今日运势'))
        self.assertIn('娱乐',a);self.assertNotEqual(a,self.call('daily_luck','/今日运势',self.other(user_id='44')))
    def test_tarot_has_no_duplicate_cards_and_no_missing_forecast_claim(self):
        reply=self.call('tarot','/塔罗 三张');self.assertIn('娱乐抽牌',reply)
        names=[line.split('. ',1)[1].split('（')[0] for line in reply.splitlines()[1:]]
        self.assertEqual(len(set(names)),3);self.assertIn('用法',self.call('tarot','/塔罗 10000'))
    def test_decision_and_integer_bounds(self):
        self.assertIn(self.call('decision','/选择 米饭|面条').split('：')[1],('米饭','面条'))
        self.assertEqual(self.call('decision','/随机数 5 5'),'5')
        self.assertIn('用法',self.call('decision','/随机数 100 -1'));self.assertIn('2—30',self.call('decision','/选择 a|a'))
    def test_answer_book_uses_original_fixed_strings(self):
        reply=self.call('answer_book','/答案之书 如何开始')
        self.assertTrue(any(x in reply for x in local.ANSWERS))
    def test_wordle_repeated_letters_not_overcounted(self):self.assertEqual(local.score_word('apple','allee'),'🟩🟨⬜⬜🟩')
    def test_wordle_state_is_durable_and_scoped(self):
        self.assertIn('猜词开始',self.call('wordle','/猜词 开始'))
        self.assertIn('已有',self.call('wordle','/猜词 开始'))
        self.assertIn('创建',self.call('wordle','/猜词 状态',self.other(user_id='44')))
        db=local.database(self.ctx)
        try:answer=local.get_state(db,'wordle','g:777','43')['answer']
        finally:db.close()
        self.assertIn('猜中了',self.call('wordle','/猜词 '+answer))
        self.assertIn('已结束',self.call('wordle','/猜词 apple'))
    def test_wordle_only_valid_input_consumes_turn(self):
        self.call('wordle','/猜词 开始');self.assertIn('5个',self.call('wordle','/猜词 123'))
        self.assertIn('尚未猜测',self.call('wordle','/猜词 状态'))
    def test_guess_number_a_b_and_finish(self):
        self.call('guess_number','/猜数字 开始')
        db=local.database(self.ctx)
        try:answer=local.get_state(db,'guess_number','g:777','43')['answer']
        finally:db.close()
        self.assertIn('4位不重复',self.call('guess_number','/猜数字 1111'))
        rotated=answer[1:]+answer[0];self.assertIn('0A4B',self.call('guess_number','/猜数字 '+rotated))
        self.assertIn('猜中了',self.call('guess_number','/猜数字 '+answer))
    def test_guess_surrender_and_restart(self):
        self.call('guess_number','/猜数字 开始');self.assertIn('答案',self.call('guess_number','/猜数字 投降'))
        self.assertIn('开始',self.call('guess_number','/猜数字 开始'))
    def test_rps_all_nine_rules(self):
        choices=('石头','剪刀','布')
        for i,a in enumerate(choices):
            for j,b in enumerate(choices):
                with patch.object(local.RNG,'choice',return_value=b):reply=self.call('rps','/猜拳 '+a)
                expected='平局' if i==j else '你赢了' if (i+1)%3==j else '我赢了'
                self.assertIn(expected,reply)
        self.assertIn('本会话',self.call('rps','/猜拳 战绩'))
    def test_todo_cross_user_cannot_read_or_modify(self):
        self.call('todo_notes','/待办 添加 私人事项')
        self.assertNotIn('私人事项',self.call('todo_notes','/待办 列表',self.other(user_id='44')))
        self.assertIn('未找到',self.call('todo_notes','/待办 删除 1',self.other(user_id='44')))
        self.assertIn('归档',self.call('todo_notes','/待办 完成 1'));self.assertNotIn('私人事项',self.call('todo_notes','/待办 列表'))
    def test_notes_scope_and_search(self):
        self.call('todo_notes','/便签 添加 苹果资料');self.call('todo_notes','/便签 添加 香蕉资料')
        reply=self.call('todo_notes','/便签 查 苹果');self.assertIn('苹果',reply);self.assertNotIn('香蕉',reply)
        self.assertNotIn('苹果',self.call('todo_notes','/便签 列表',self.other(session_id='g:888')))
    def test_entry_limit_and_sql_text_does_not_become_query(self):
        self.ctx['settings']['max_entries']=1
        self.call('todo_notes',"/待办 添加 x');DROP TABLE entries;--")
        self.assertIn('上限',self.call('todo_notes','/待办 添加 another'))
        self.assertIn('DROP TABLE',self.call('todo_notes','/待办 列表'))
    def test_quotes_creator_owner_permission_and_group_isolation(self):
        self.call('quotes','/语录 收录 测试语录')
        self.assertIn('测试语录',self.call('quotes','/语录 随机',self.other(user_id='44')))
        self.assertIn('未找到',self.call('quotes','/语录 删除 1',self.other(user_id='44')))
        self.assertIn('暂无',self.call('quotes','/语录 列表',self.other(session_id='g:888')))
        self.assertIn('归档',self.call('quotes','/语录 删除 1',self.other(user_id='42')))
    def test_poll_unique_vote_change_and_close_permissions(self):
        self.assertIn('投票',self.call('polls','/投票 发起 题目|A|B'))
        self.call('polls','/投票 选 1');self.call('polls','/投票 选 2')
        reply=self.call('polls','/投票 结果');self.assertIn('A：0票',reply);self.assertIn('B：1票',reply)
        self.assertIn('只有',self.call('polls','/投票 关闭',self.other(user_id='44')))
        self.assertIn('已关闭',self.call('polls','/投票 关闭'))
    def test_poll_disallow_vote_change_and_invalid_input(self):
        self.ctx['settings']['allow_change_vote']=False
        self.assertIn('用法',self.call('polls','/投票 发起 题目|A|A'))
        self.call('polls','/投票 发起 题目|A|B');self.call('polls','/投票 选 1')
        self.assertIn('不允许',self.call('polls','/投票 选 2'));self.assertIn('有效',self.call('polls','/投票 选 -1'))
    def test_concurrent_poll_votes_are_counted_once_each(self):
        self.call('polls','/投票 发起 题目|A|B')
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda uid:self.call('polls','/投票 选 1',self.other(user_id=str(uid))),range(100,120)))
        self.assertIn('A：20票',self.call('polls','/投票 结果'))
    def test_greeting_idempotent_and_actual_interval_description(self):
        self.assertIn('第1位',self.call('daily_greetings','/晚安'));self.assertIn('已经',self.call('daily_greetings','/晚安'))
        self.assertIn('过去',self.call('daily_greetings','/早安'))
        self.assertIn('第2位',self.call('daily_greetings','/早安',self.other(user_id='44')))
    def test_time_offsets_do_not_claim_unknown_timezone(self):
        self.assertIn('+0830',self.call('time_tools','/时间 UTC+8:30'))
        self.assertIn('最多14',self.call('time_tools','/时间 UTC+15'))
        self.assertIn('时区未知',self.call('time_tools','/时间 Not/A/RealZone'))
        self.assertIn('用法',self.call('time_tools','/倒数 2026-02-30'))
    def test_text_encoding_complete_round_trip_and_bounds(self):
        encoded=self.call('text_tools','/Base64 你好')
        self.assertEqual(self.call('text_tools','/解Base64 '+encoded),'你好')
        self.assertIn('有效',self.call('text_tools','/解Base64 ####'))
        self.assertIn('最多1000',self.call('text_tools','/Base64 '+'中'*400))
        url=self.call('text_tools','/URL编码 你好 世界');self.assertEqual(self.call('text_tools','/URL解码 '+url),'你好 世界')
        self.assertIn('字符2',self.call('text_tools','/字数 你好'))
        self.assertIn('ba7816bf',self.call('text_tools','/哈希 abc'))
    def test_missing_state_directory_is_reported_not_written_to_cwd(self):
        ctx=self.other();ctx.pop('data_dir')
        self.assertIn('独立数据目录',self.call('wordle','/猜词 开始',ctx))
    def test_all_local_manifests_match_worker_and_settings(self):
        for folder in (ROOT/'plugins').iterdir():
            if folder.name in ('f1_info','game_news'):continue
            info=json.loads((folder/'plugin.json').read_text(encoding='utf-8'))
            module=load('worker_'+folder.name,folder/'worker.py')
            self.assertIsNone(module.handle('/unknown',self.ctx))
            self.assertTrue(info['commands']);self.assertEqual(info['id'],folder.name)

class F1Tests(unittest.TestCase):
    def setUp(self):
        self.module=load('f1_worker',ROOT/'plugins/f1_info/worker.py');self.ctx={'settings':{'utc_offset_hours':8,'result_limit':3}}
    def run_with(self,command,payload):
        with patch.object(self.module.net,'fetch',return_value=payload) as fetch:
            reply=self.module.handle(command,self.ctx)
            return reply,fetch
    def test_next_weekend_converts_utc_and_missing_times_explicit(self):
        race={'season':'2026','round':'20','raceName':'Test GP','Circuit':{'circuitName':'Track'},'date':'2026-10-18','time':'18:00:00Z','Qualifying':{'date':'2026-10-17'}}
        reply,fetch=self.run_with('/f1 下一站',{'MRData':{'RaceTable':{'Races':[race]}}})
        self.assertIn('10-19 02:00',reply);self.assertIn('具体时间未公布',reply);self.assertIn('Jolpica',reply)
        self.assertIn('current/next.json',fetch.call_args.args[0])
    def test_empty_results_do_not_invent_race(self):
        reply,_=self.run_with('/f1 成绩',{'MRData':{'RaceTable':{'Races':[]}}})
        self.assertIn('尚未发布',reply)
    def test_driver_and_constructor_standings(self):
        payload={'MRData':{'StandingsTable':{'StandingsLists':[{'season':'2024','round':'5','DriverStandings':[{'position':'1','points':'99','wins':'2','Driver':{'code':'TST'}}],'ConstructorStandings':[{'position':'1','points':'200','wins':'4','Constructor':{'name':'Test Team'}}]}]}}}
        reply,_=self.run_with('/f1 积分 2024',payload);self.assertIn('TST｜99分',reply)
        reply,_=self.run_with('/f1 车队 2024',payload);self.assertIn('Test Team｜200分',reply)
    def test_results_and_qualifying_use_requested_season_round(self):
        race={'season':'2024','round':'5','raceName':'Test GP','Results':[{'position':'1','points':'25','status':'Finished','Driver':{'code':'TST'},'Constructor':{'name':'Test'}}], 'QualifyingResults':[{'position':'1','Q1':'1:20','Q2':'1:19','Q3':'1:18','Driver':{'code':'TST'}}]}
        payload={'MRData':{'RaceTable':{'Races':[race]}}}
        reply,fetch=self.run_with('/f1 成绩 2024 5',payload);self.assertIn('25分',reply);self.assertIn('2024/5/results.json',fetch.call_args.args[0])
        reply,_=self.run_with('/f1 排位 2024 5',payload);self.assertIn('1:20 / 1:19 / 1:18',reply)
    def test_invalid_year_and_round_never_send_arbitrary_path(self):
        with patch.object(self.module.net,'fetch') as fetch:
            self.assertIn('赛季',self.module.handle('/f1 成绩 ../../etc',self.ctx))
            self.assertIn('轮次',self.module.handle('/f1 成绩 2024 999',self.ctx));fetch.assert_not_called()
    def test_upstream_failure_and_bad_schema_do_not_leak_headers(self):
        with patch.object(self.module.net,'fetch',side_effect=OSError('sensitive-url-secret')):
            reply=self.module.handle('/f1 积分',self.ctx)
            self.assertIn('查询失败',reply);self.assertNotIn('sensitive',reply)
        reply,_=self.run_with('/f1 下一站',{});self.assertIn('查询失败',reply)
    def test_game_news_uses_only_public_numeric_appid(self):
        m=load('news_worker',ROOT/'plugins/game_news/worker.py')
        with patch.object(m.net,'fetch',return_value={'appnews':{'newsitems':[{'title':'New update','url':'javascript:alert(1)','contents':'<b>Test</b>'}]}}) as fetch:
            self.assertIn('AppID',m.handle('/游戏更新 ../etc',{}));fetch.assert_not_called()
            reply=m.handle('/游戏更新 730',{});self.assertNotIn('javascript:',reply);self.assertIn('Test',reply);self.assertIn('/app/730/',reply)

if __name__=='__main__':unittest.main()
