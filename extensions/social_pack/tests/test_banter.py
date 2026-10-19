import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import banter


class BanterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx = {'data_dir': self.tmp.name, 'session_id': 'p:42', 'user_id': '42', 'settings': {}}

    def test_real_bank_categories_and_unknown_are_bounded(self):
        b = banter.bank()
        self.assertEqual(set(b), set(banter.CATEGORIES))
        for category in b:
            self.assertIn(banter.handle('/搭话 ' + category, self.ctx), b[category])
        self.assertIn('没有这个分类', banter.handle('/搭话 other', self.ctx))
        self.assertIn('嘴硬关心', banter.handle('/搭话 分类', self.ctx))

    def test_no_immediate_repeat_survives_worker_reload(self):
        with patch.object(banter.RNG, 'sample', side_effect=lambda rows, n: rows[:n]):
            first = banter.handle('/搭话 闲聊', self.ctx)
            second = banter.handle('/搭话 闲聊', self.ctx)
            self.assertNotEqual(first, second)
            spec = importlib.util.spec_from_file_location('reloaded_banter', ROOT / 'banter.py')
            reloaded = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(reloaded)
            with patch.object(reloaded.RNG, 'sample', side_effect=lambda rows, n: rows[:n]):
                self.assertEqual(first, reloaded.handle('/搭话 闲聊', self.ctx))

    def test_custom_bank_count_and_identity_isolation(self):
        custom = {key: ['句子一', '句子二', '句子三'] for key in banter.CATEGORIES}
        self.ctx['settings'] = {'custom_copy_json': json.dumps(custom), 'reply_count': 2}
        reply = banter.handle('/搭话 招呼', self.ctx)
        self.assertEqual(len(reply.splitlines()), 2)
        self.assertEqual(len(set(reply.splitlines())), 2)
        self.assertIn('不匹配', banter.handle('/搭话', {**self.ctx, 'session_id': 'p:99'}))
        self.ctx['settings']['custom_copy_json'] = '{bad'
        self.assertIn('JSON格式无效', banter.handle('/搭话', self.ctx))

    def test_packaged_wrapper_runs_through_real_json_lines_host(self):
        package = Path(self.tmp.name) / 'package'
        package.mkdir()
        shutil.copy2(ROOT / 'plugins/s_banter/worker.py', package / 'worker.py')
        for name in ('banter.py', 'common.py'):
            shutil.copy2(ROOT / name, package / name)
        bank_path = ROOT / 'bank.json'
        if not bank_path.is_file():
            bank_path = ROOT.parents[1] / 'resources/banter.zh-CN.json'
        shutil.copy2(bank_path, package / 'bank.json')
        context = {**self.ctx, 'data_dir': str(Path(self.tmp.name) / 'state')}
        request = json.dumps({'text': '/搭话 闲聊', 'context': context}, ensure_ascii=False) + '\n'
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
        result = subprocess.run(
            [sys.executable, '-B', '-u', '-m', 'yukino_core.plugin_worker', str(package / 'worker.py')],
            input=request, capture_output=True, encoding='utf-8', env=env,
            cwd=ROOT.parents[1], timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(messages), 2, result.stdout)
        self.assertEqual(messages[0], {'ready': True})
        self.assertIn(messages[1].get('text'), banter.bank()['闲聊'])
        self.assertTrue((Path(context['data_dir']) / 'social.sqlite3').is_file())
