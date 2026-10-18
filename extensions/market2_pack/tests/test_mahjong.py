"""Structural hand algorithms, original generation, and SQLite scope tests."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('mahjong_under_test',ROOT/'mahjong_engine.py')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class StructureTests(unittest.TestCase):
    def test_known_standard_wait_and_theoretical_remaining(self):
        counts=m.parse_tiles('123m123p123s1112z',13); result=m.waits(counts)
        self.assertEqual([m.tile_name(i) for i,_,_ in result],['2z'])
        self.assertEqual(result[0][2],3); self.assertIn('4面子1雀头',result[0][1])

    def test_complete_standard_hand_and_invalid_cross_suit_sequences(self):
        self.assertIn('4面子1雀头',m.winning_types(m.parse_tiles('123m123p123s11122z',14)))
        self.assertEqual(m.winning_types(m.parse_tiles('89m1p123s11122233z',14)),[])

    def test_honors_do_not_form_sequences(self):
        counts=m.parse_tiles('123m123p123s12344z',14)
        self.assertEqual(m.winning_types(counts),[])

    def test_seven_distinct_pairs_are_recognized(self):
        types=m.winning_types(m.parse_tiles('1122m3344p5566s77z',14))
        self.assertIn('七对子（7种不同对子）',types)

    def test_four_identical_tiles_do_not_count_as_two_distinct_pairs(self):
        types=m.winning_types(m.parse_tiles('1111m22p33p44s55s66z',14))
        self.assertNotIn('七对子（7种不同对子）',types)

    def test_kokushi_complete_and_thirteen_sided_wait(self):
        counts=m.parse_tiles('19m19p19s1234567z',13)
        self.assertEqual({i for i,_,_ in m.waits(counts)},set(m.ORPHANS))
        self.assertTrue(all(left==3 for _,_,left in m.waits(counts)))
        self.assertIn('国士无双',m.winning_types(m.parse_tiles('119m19p19s1234567z',14)))

    def test_kokushi_missing_one_terminal_has_single_wait(self):
        counts=m.parse_tiles('1m19p19s11234567z',13)
        self.assertEqual([m.tile_name(i) for i,_,_ in m.waits(counts)],['9m'])

    def test_fifth_copy_is_never_a_wait_candidate(self):
        counts=m.parse_tiles('1111m234p234s111z',13)
        self.assertNotIn(0,[i for i,_,_ in m.waits(counts)])

    def test_bad_notation_counts_and_duplicate_answer_are_rejected(self):
        cases=['0m','8z','123','123x','11111m','1m,2m','__import__("os")']
        for raw in cases:
            with self.subTest(raw=raw),self.assertRaises(ValueError): m.parse_tiles(raw)
        with self.assertRaises(ValueError): m.parse_tiles('11m',unique=True)
        with self.assertRaises(ValueError): m.parse_tiles('123m',13)

    def test_notation_roundtrip_preserves_all_counts(self):
        for raw in ('123m123p123s1112z','1122m3344p5566s77z','19m19p19s1234567z'):
            counts=m.parse_tiles(raw); self.assertEqual(m.parse_tiles(m.compact_hand(counts)),counts)

    def test_original_generators_produce_valid_solvable_hands(self):
        for style in ('标准','七对','国士'):
            for _ in range(50):
                counts=m.generate_hand(style); self.assertEqual(sum(counts),13); self.assertLessEqual(max(counts),4)
                result=m.waits(counts); self.assertTrue(result)
                if style=='七对': self.assertTrue(any('七对子（7种不同对子）' in kinds for _,kinds,_ in result))
                if style=='国士': self.assertTrue(any('国士无双' in kinds for _,kinds,_ in result))

    def test_wait_candidate_limit_is_real_and_explicit(self):
        result=m.handle('/听牌 19m19p19s1234567z',{'settings':{'candidate_limit':1}})
        self.assertIn('13种',result); self.assertIn('只显示前1种',result)
        self.assertEqual(result.count('手牌外理论'),1)

    def test_structure_analysis_never_creates_storage(self):
        with tempfile.TemporaryDirectory(prefix='.mahjong-stateless-',dir=ROOT/'tests') as temporary:
            target=Path(temporary)/'not-created'
            result=m.handle('/听牌 123m123p123s1112z',{'settings':{},'data_dir':str(target)})
            self.assertIn('2z',result); self.assertFalse(target.exists())


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='.mahjong-test-',dir=ROOT/'tests'); self.root=Path(self.tmp.name)

    def tearDown(self): self.tmp.cleanup()

    def ctx(self,user='2',scope='g:1',settings=None): return {'user_id':user,'session_id':scope,'data_dir':str(self.root/'data'),'settings':settings or {}}

    def send(self,arg,user='2',scope='g:1',settings=None): return m.handle('/麻将训练 '+arg,self.ctx(user,scope,settings))

    def state(self,user='2',scope='g:1'):
        with closing(sqlite3.connect(self.root/'data/mahjong.sqlite3')) as db:
            row=db.execute('SELECT body FROM training WHERE scope=? AND user=?',(scope,user)).fetchone()
        return json.loads(row[0]) if row else None

    def correct_answer(self,state):
        counts=[0]*34
        for i,_,_ in m.waits(tuple(state['counts'])): counts[i]=1
        return m.compact_hand(counts)

    def test_start_persist_reimport_and_solve_all_candidates(self):
        self.assertIn('麻将训练已开始',self.send('开始 标准')); before=self.state()
        spec=importlib.util.spec_from_file_location('mahjong_restarted',ROOT/'mahjong_engine.py'); restarted=importlib.util.module_from_spec(spec); spec.loader.exec_module(restarted)
        result=restarted.handle('/麻将训练 状态',self.ctx())
        self.assertIn(m.compact_hand(before['counts']),result); self.assertEqual(self.state(),before)
        self.assertIn('全部候选答对',self.send('答 '+self.correct_answer(before)))
        self.assertTrue(self.state()['correct']); self.assertTrue(self.state()['done'])

    def test_user_and_session_isolation_without_arbitrary_state_queries(self):
        self.send('开始 国士'); before=self.state()
        self.assertIn('还没有你的训练题',self.send('状态',user='3'))
        self.assertIn('还没有你的训练题',self.send('答案',scope='g:2'))
        self.assertIn('还没有你的训练题',self.send('状态',scope='p:2'))
        self.assertIn('不接受其他QQ',self.send('状态 3'))
        self.assertIn('不接受其他用户',self.send('答案 3'))
        self.assertEqual(self.state(),before)

    def test_invalid_answer_does_not_consume_attempt_or_change_state(self):
        self.send('开始 七对'); before=self.state()
        self.assertIn('未完成',self.send('答 11m')); self.assertIn('未完成',self.send('答 __import__("os")'))
        self.assertEqual(self.state(),before)

    def test_attempt_limit_ends_and_reveals_without_preanswer_leak(self):
        self.send('开始 国士',settings={'max_attempts':1}); before=self.state(); winning={i for i,_,_ in m.waits(tuple(before['counts']))}
        wrong=next(i for i in range(34) if i not in winning)
        self.assertIn('次数已用完',self.send('答 '+m.tile_name(wrong)))
        self.assertTrue(self.state()['done']); self.assertFalse(self.state()['correct']); self.assertEqual(len(self.state()['attempts']),1)

    def test_wrong_answer_before_limit_does_not_reveal_candidate_list(self):
        self.send('开始 国士'); before=self.state(); winning={i for i,_,_ in m.waits(tuple(before['counts']))}; wrong=next(i for i in range(34) if i not in winning)
        result=self.send('答 '+m.tile_name(wrong))
        self.assertNotIn('结构听牌候选',result); self.assertFalse(self.state()['done']); self.assertEqual(len(self.state()['attempts']),1)

    def test_schema_default_style_and_per_question_attempts_are_effective(self):
        self.assertIn('七对出题',self.send('开始',settings={'training_style':'七对','max_attempts':2}))
        self.assertEqual(self.state()['max_attempts'],2)
        self.send('状态',settings={'max_attempts':20}); self.assertEqual(self.state()['max_attempts'],2)

    def test_running_question_is_preserved_and_reveal_allows_new_question(self):
        self.send('开始 标准'); before=self.state()
        self.assertIn('还在进行',self.send('开始 国士')); self.assertEqual(before,self.state())
        self.assertIn('本题已结束',self.send('答案')); self.assertTrue(self.state()['done'])
        self.assertIn('麻将训练已开始',self.send('开始 国士')); self.assertEqual(self.state()['attempts'],[])

    def test_help_length_scope_and_relative_directory_errors(self):
        self.assertIsNone(m.handle('普通聊天',self.ctx()))
        self.assertIn('不判断役',self.send('帮助')); self.assertFalse((self.root/'data').exists())
        self.assertIn('最多1000',self.send('答 '+'x'*1000))
        ctx=self.ctx(); ctx['data_dir']='relative'; self.assertIn('绝对',m.handle('/麻将训练 开始',ctx))
        self.assertIn('未完成',self.send('开始',scope='unknown'))

    def test_public_worker_wrapper_and_catalog(self):
        catalog=json.loads((ROOT/'local_catalog.json').read_text(encoding='utf-8')); item=next(p for p in catalog if p['id']=='m_mahjong')
        worker_spec=importlib.util.spec_from_file_location('mahjong_worker',ROOT/'plugins/m_mahjong/worker.py'); worker=importlib.util.module_from_spec(worker_spec); worker_spec.loader.exec_module(worker)
        self.assertIsNone(worker.handle('/other',self.ctx()))
        self.assertIn(item['expected_contains'],worker.handle(item['smoke_command'],self.ctx()))


if __name__=='__main__': unittest.main()
