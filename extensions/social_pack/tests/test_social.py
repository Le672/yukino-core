"""Privacy, ownership, capacity and transaction acceptance for social tools."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


common = load('social_pack_common_acceptance', ROOT / 'common.py')
with patch.dict(sys.modules, {'common': common}):
    social = load('social_pack_tools_acceptance', ROOT / 'social.py')


class SocialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.now = datetime(2030, 1, 1, 4, tzinfo=timezone.utc).timestamp()  # noon Beijing
        self.clock = patch.object(common.time, 'time', return_value=self.now)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.tmp.cleanup()

    def ctx(self, pid, uid='101', sid=None, **settings):
        return dict(data_dir=str(self.base / pid), user_id=uid,
                    session_id=sid or 'p:' + uid, owner_ids=['999'], settings=settings)

    def call(self, pid, arg='', uid='101', sid=None, **settings):
        return social.handle(pid, social.HELPS[pid][0] + ' ' + arg, self.ctx(pid, uid, sid, **settings))

    def count(self, pid):
        db = common.connect(self.ctx(pid))
        try:
            return db.execute('SELECT count(*) FROM entries').fetchone()[0]
        finally:
            db.close()

    def test_manifests_and_wrappers_are_four_independent_workers(self):
        for pid in ('s_bottle', 's_capsule', 's_mood', 's_sleep'):
            manifest = json.loads((ROOT / 'plugins' / pid / 'plugin.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['id'], pid)
            self.assertEqual(manifest['commands'], [social.HELPS[pid][0]])
            self.assertEqual(len(manifest['config_schema']), 4)
            with patch.dict(sys.modules, {'social': social}):
                wrapper = load('wrapper_' + pid, ROOT / 'plugins' / pid / 'worker.py')
            self.assertIn(social.HELPS[pid][0], wrapper.handle(social.HELPS[pid][0], self.ctx(pid)))

    def test_invalid_identity_and_personal_group_requests_never_create_storage(self):
        for uid, sid in [('101', 'p:202'), ('../../etc', 'g:5'), ('101', 'g:../5'), ('', 'p:1')]:
            out = self.call('s_bottle', '投递 猫', uid, sid)
            self.assertIn('身份', out)
        for pid in ('s_capsule', 's_mood', 's_sleep'):
            for cmd in ('列表', '统计', '清空', '记 4 | 秘密', '存 2030-01-02 09:00 | 秘密'):
                self.assertIn('私聊', self.call(pid, cmd, sid='g:5'))
        self.assertEqual(list(self.base.iterdir()), [])

    def test_bottle_scope_anonymity_persistence_and_withdrawal(self):
        self.assertIn('本群公开', self.call('s_bottle', '投递 猫猫秘密', sid='g:5'))
        self.assertIn('猫猫秘密', self.call('s_bottle', '捞一个', uid='202', sid='g:5'))
        self.assertNotIn('101', self.call('s_bottle', '捞一个', uid='202', sid='g:5'))
        self.assertIn('没有', self.call('s_bottle', '捞一个', sid='g:6'))
        self.assertIn('没有', self.call('s_bottle', '撤回 1', uid='202', sid='g:5'))
        self.assertIn('没有', self.call('s_bottle', '撤回 1', sid='g:6'))
        self.assertIn('猫猫秘密', self.call('s_bottle', '我的', sid='g:5'))
        self.assertIn('撤回', self.call('s_bottle', '撤回 1', sid='g:5'))
        self.assertEqual(self.count('s_bottle'), 0)

    def test_bottle_owner_can_only_clear_current_group(self):
        self.call('s_bottle', '投递 A', sid='g:5')
        self.call('s_bottle', '投递 B', sid='g:6')
        self.assertIn('仅机器人主人', self.call('s_bottle', '清空', sid='g:5'))
        self.assertIn('1 个', self.call('s_bottle', '清空', uid='999', sid='g:5'))
        self.assertIn('B', self.call('s_bottle', '捞一个', sid='g:6'))
        self.assertIn('群聊', self.call('s_bottle', '投递 C'))

    def test_capsule_before_exact_due_and_take_once(self):
        self.assertIn('封存', self.call('s_capsule', '存 2030-01-01 12:01 | 未来的私密正文'))
        for result in [self.call('s_capsule', '列表'), self.call('s_capsule', '取 1')]:
            self.assertNotIn('私密正文', result)
        self.assertIn('尚未到期', self.call('s_capsule', '取 1'))
        self.assertIn('没有', self.call('s_capsule', '取 1', uid='202'))
        self.assertIn('没有', self.call('s_capsule', '取 1', uid='999'))
        with patch.object(common.time, 'time', return_value=self.now + 60):
            self.assertIn('私密正文', self.call('s_capsule', '取 1'))
        self.assertIn('没有', self.call('s_capsule', '取 1'))

    def test_capsule_cancel_and_date_validation(self):
        for when in ['2030-01-01 12:00', '2029-01-01 12:00', '2031-01-02 12:00', '2030-02-30 12:00', '2030-1-2 3:00']:
            self.assertNotIn('封存', self.call('s_capsule', '存 ' + when + ' | 猫'))
        self.assertIn('封存', self.call('s_capsule', '存 2030-01-02 09:00 | 不回显的私密正文'))
        self.assertNotIn('私密正文', self.call('s_capsule', '撤销 1'))
        self.assertEqual(self.count('s_capsule'), 0)

    def test_capsule_future_retention_uses_due_date(self):
        self.call('s_capsule', '存 2030-01-10 12:00 | 仍应保存', retention_days=1)
        with patch.object(common.time, 'time', return_value=self.now + 2 * 86400):
            self.assertIn('#1', self.call('s_capsule', '列表', retention_days=1))
        with patch.object(common.time, 'time', return_value=self.now + 11 * 86400):
            self.assertIn('没有', self.call('s_capsule', '列表', retention_days=1))

    def test_mood_optional_notes_statistics_user_isolation_delete_and_clear(self):
        self.assertIn('已记录', self.call('s_mood', '记 4 | 自己的猫'))
        self.call('s_mood', '记 2')
        self.call('s_mood', '记 5 | 别人的猫', uid='202')
        self.assertIn('平均 3.00', self.call('s_mood', '统计'))
        self.assertNotIn('别人的猫', self.call('s_mood', '列表'))
        self.assertNotIn('自己的猫', self.call('s_mood', '列表', uid='202'))
        self.assertNotIn('自己的猫', self.call('s_mood', '列表', uid='999'))
        self.assertIn('没有', self.call('s_mood', '删除 1', uid='202'))
        self.assertIn('已删除', self.call('s_mood', '删除 1'))
        self.assertIn('1 条', self.call('s_mood', '清空'))
        self.assertIn('平均 5.00', self.call('s_mood', '统计', uid='202'))

    def test_sleep_cross_midnight_durations_and_invalid_boundaries(self):
        self.assertIn('8.00 小时', self.call('s_sleep', '记 2029-12-31 23:00 | 2030-01-01 07:00'))
        self.assertIn('不作健康判断', self.call('s_sleep', '统计'))
        for text in ['记 2030-01-01 07:00 | 2030-01-01 07:00',
                     '记 2030-01-01 08:00 | 2030-01-01 07:00',
                     '记 2029-12-30 23:00 | 2030-01-01 07:00',
                     '记 2030-01-01 08:00 | 2030-01-01 13:00', '记 invalid | now']:
            self.assertNotIn('已记录', self.call('s_sleep', text))
        self.assertIn('没有', self.call('s_sleep', '删除 1', uid='202'))
        self.assertIn('8.00', self.call('s_sleep', '列表'))
        self.assertIn('已删除', self.call('s_sleep', '删除 1'))

    def test_bad_input_and_settings_do_not_write(self):
        for score in ['0', '6', '1.5', 'NaN', '4\n5']:
            self.assertNotIn('已记录', self.call('s_mood', '记 ' + score))
        self.assertNotIn('已投递', self.call('s_bottle', '投递 ' + '猫' * 501, sid='g:5'))
        self.assertIn('控制字符', self.call('s_bottle', '投递 猫\x00猫', sid='g:5'))
        self.assertIn('设置', self.call('s_mood', '记 3', max_per_user='50'))
        self.assertIn('设置', self.call('s_mood', '记 3', max_records=True))
        self.assertIn('有效', self.call('s_mood', '删除 1 OR 1=1'))
        self.assertEqual(self.count('s_mood'), 0)

    def test_capacity_transaction_and_cross_group_user_quota(self):
        self.assertIn('投递', self.call('s_bottle', '投递 A', sid='g:5', max_per_user=1))
        self.assertIn('你的记录数', self.call('s_bottle', '投递 B', sid='g:6', max_per_user=1))
        self.assertIn('总记录数', self.call('s_bottle', '投递 C', uid='202', sid='g:5', max_records=1))
        self.assertEqual(self.count('s_bottle'), 1)

    def test_concurrent_capacity_is_atomic_and_unique(self):
        def insert(i):
            return self.call('s_mood', '记 4 | 并发' + str(i), uid=str(1000 + i), max_records=5)
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(insert, range(18)))
        self.assertEqual(sum('已记录' in x for x in results), 5, results)
        self.assertEqual(sum('总记录数' in x for x in results), 13, results)
        self.assertEqual(self.count('s_mood'), 5)

    def test_retention_deletes_expired_ordinary_entries(self):
        self.call('s_mood', '记 4 | 旧记录', retention_days=1)
        with patch.object(common.time, 'time', return_value=self.now + 86401):
            self.assertIn('没有', self.call('s_mood', '列表', retention_days=1))

    def test_configured_text_limit_and_list_output_is_bounded(self):
        self.assertIn('正文', self.call('s_mood', '记 4 | 四个汉字', max_text_length=3))
        self.assertIn('已记录', self.call('s_mood', '记 4 | 三个字', max_text_length=3))
        for i in range(24):
            self.call('s_mood', '记 4 | ' + '长' * 1000, max_text_length=1000)
        output = self.call('s_mood', '列表')
        self.assertEqual(len(output.splitlines()), 20)
        self.assertLess(len(output), 3000)

    def test_hardlinked_database_and_symlinked_parent_are_rejected(self):
        root = self.base / 's_mood'
        root.mkdir()
        elsewhere = self.base / 'outside.db'
        elsewhere.write_text('unchanged', encoding='utf-8')
        try:
            os.link(elsewhere, root / 'social.sqlite3')
        except OSError:
            self.skipTest('Current filesystem does not permit hardlinks')
        self.assertIn('拒绝', self.call('s_mood', '记 4'))
        self.assertEqual(elsewhere.read_text(encoding='utf-8'), 'unchanged')
        actual = self.base / 'actual'
        actual.mkdir()
        linked = self.base / 'linked'
        try:
            linked.symlink_to(actual, target_is_directory=True)
        except OSError:
            self.skipTest('Current Windows token does not permit symlink creation')
        ctx = self.ctx('s_sleep')
        ctx['data_dir'] = str(linked / 'child')
        self.assertIn('拒绝', social.handle('s_sleep', '/睡眠 列表', ctx))
        self.assertEqual(list(actual.iterdir()), [])

    def test_database_symlink_is_rejected(self):
        root = self.base / 's_mood'
        root.mkdir()
        elsewhere = self.base / 'elsewhere.db'
        elsewhere.write_text('unchanged', encoding='utf-8')
        try:
            (root / 'social.sqlite3').symlink_to(elsewhere)
        except OSError:
            self.skipTest('Current Windows token does not permit symlink creation')
        self.assertIn('拒绝', self.call('s_mood', '记 4'))
        self.assertEqual(elsewhere.read_text(encoding='utf-8'), 'unchanged')


if __name__ == '__main__':
    unittest.main()
