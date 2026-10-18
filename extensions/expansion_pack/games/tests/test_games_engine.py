"""Integration tests for all 21 wrappers and persistent game boundaries."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import games_engine as engine
CATALOG=json.loads((ROOT/'catalog.json').read_text(encoding='utf-8'))


class EngineIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def ctx(self,pid,user='2',scope='g:1'):
        return {'data_dir':str(self.base/pid),'session_id':scope,'user_id':user,'settings':{}}
    def row(self,ctx,pid,user=None):
        db=sqlite3.connect(Path(ctx['data_dir'])/'games.sqlite3')
        try:
            r=db.execute('SELECT body FROM games WHERE kind=? AND scope=? AND who=?',(pid,ctx['session_id'],user or ('*' if pid in engine.MULTI else ctx['user_id']))).fetchone()
            return json.loads(r[0]) if r else None
        finally:db.close()
    def test_21_actual_start_and_help_before_start(self):
        self.assertEqual(len(CATALOG),21)
        for item in CATALOG:
            with self.subTest(plugin=item['id']):
                ctx=self.ctx(item['id']);spec=importlib.util.spec_from_file_location('test_worker_'+item['id'],ROOT/'plugins'/item['id']/'worker.py')
                worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
                self.assertIsNone(worker.handle('普通聊天',ctx))
                help_text=worker.handle(item['commands'][0]+' 帮助',ctx)
                self.assertTrue(help_text);self.assertIsNone(self.row(ctx,item['id']))
                reply=worker.handle(item['smoke_command'],ctx)
                self.assertIn(item['expected_contains'],reply)
                state=self.row(ctx,item['id']);self.assertIsInstance(state,dict);self.assertFalse(state['done'])
                self.assertIn('输入太长',worker.handle(item['commands'][0]+' '+'a'*501,ctx))
                self.assertEqual(self.row(ctx,item['id']),state)
    def test_all_17_solo_user_and_session_isolation(self):
        for item in CATALOG:
            pid=item['id']
            if pid in engine.MULTI: continue
            with self.subTest(plugin=pid):
                ctx=self.ctx(pid);engine.handle(pid,item['smoke_command'],ctx);first=self.row(ctx,pid)
                for other in (self.ctx(pid,user='3'),self.ctx(pid,scope='g:2')):
                    status=engine.handle(pid,item['commands'][0]+' 状态',other)
                    self.assertIsInstance(status,str);self.assertIsNone(self.row(other,pid))
                    engine.handle(pid,item['commands'][0]+' 结束',other)
                    self.assertIsNone(self.row(other,pid));self.assertEqual(self.row(ctx,pid),first)
                    engine.handle(pid,item['smoke_command'],other)
                    self.assertIsInstance(self.row(other,pid),dict);self.assertEqual(self.row(ctx,pid),first)
    def test_retention_discards_31_day_old_solo_state(self):
        pid='x_twentyfour';ctx=self.ctx(pid)
        engine.handle(pid,'/24点 开始',ctx)
        db=sqlite3.connect(Path(ctx['data_dir'])/'games.sqlite3')
        try:db.execute('UPDATE games SET updated=?',(time.time()-32*86400,));db.commit()
        finally:db.close()
        self.assertIn('开始',engine.handle(pid,'/24点 状态',ctx));self.assertIsNone(self.row(ctx,pid))
    def test_max_5000_states_and_current_state_preserved(self):
        pid='x_wordchain';ctx=self.ctx(pid);db=engine.database(ctx)
        try:
            db.executemany('INSERT INTO games VALUES(?,?,?,?,?)',[(pid,'g:old'+str(i),'2','{}',time.time()-100-i) for i in range(5002)])
        finally:db.close()
        engine.handle(pid,'/成语接龙 开始',ctx);db=engine.database(ctx)
        try:self.assertEqual(db.execute('SELECT COUNT(*) FROM games').fetchone()[0],5000)
        finally:db.close()
        self.assertEqual(self.row(ctx,pid)['last'],'一心一意')
    def test_same_user_concurrent_starts_one_durable_game(self):
        pid='x_twentyfour';ctx=self.ctx(pid)
        with ThreadPoolExecutor(max_workers=6) as executor:
            replies=list(executor.map(lambda _:engine.handle(pid,'/24点 开始',ctx),range(6)))
        self.assertEqual(sum('24点已开始' in r for r in replies),1)
        db=engine.database(ctx)
        try:self.assertEqual(db.execute('SELECT COUNT(*) FROM games').fetchone()[0],1)
        finally:db.close()
        s=self.row(ctx,pid);fresh=importlib.util.spec_from_file_location('fresh_games_engine',ROOT/'games_engine.py')
        mod=importlib.util.module_from_spec(fresh);fresh.loader.exec_module(mod)
        self.assertIn('数字',mod.handle(pid,'/24点 状态',ctx));self.assertEqual(self.row(ctx,pid),s)


if __name__=='__main__': unittest.main()
