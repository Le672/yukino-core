"""Real SQLite/worker tests; no network, host configuration, or QQ messages."""
import concurrent.futures
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('test_life_engine', ROOT / 'life_engine.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
CATALOG = json.loads((ROOT / 'catalog.json').read_text(encoding='utf-8'))
COMMANDS = {r['id']: r['commands'][0] for r in CATALOG}


class LifeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='.test-', dir=ROOT)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def ctx(self, kind, user='11', scope='g:22'):
        return {'user_id': user, 'session_id': scope, 'data_dir': str(self.root / kind), 'owner_ids': ['99'], 'settings': {}}

    def run_command(self, kind, argument, user='11', scope='g:22'):
        return engine.handle(kind, COMMANDS[kind] + ' ' + argument, self.ctx(kind, user, scope))

    def create(self, kind, argument, user='11', scope='g:22'):
        result = self.run_command(kind, argument, user, scope)
        self.assertNotIn('未完成', result, result)
        match = re.search(r'#(\d+)', result)
        self.assertIsNotNone(match, result)
        return int(match[1])

    def bad(self, kind, argument, user='11', scope='g:22'):
        result = self.run_command(kind, argument, user, scope)
        self.assertIn('操作未完成', result, result)
        return result

    def test_all_21_real_worker_wrappers_and_catalog_smokes(self):
        self.assertEqual(len(CATALOG), 21)
        for item in CATALOG:
            with self.subTest(plugin=item['id']):
                manifest = json.loads((ROOT / 'plugins' / item['id'] / 'plugin.json').read_text(encoding='utf-8'))
                self.assertEqual(manifest['commands'], item['commands'])
                worker_spec = importlib.util.spec_from_file_location('worker_' + item['id'], ROOT / 'plugins' / item['id'] / 'worker.py')
                worker = importlib.util.module_from_spec(worker_spec); worker_spec.loader.exec_module(worker)
                self.assertIsNone(worker.handle('/other', self.ctx(item['id'])))
                result = worker.handle(item['smoke_command'], self.ctx(item['id']))
                self.assertIn(item['expected_contains'], result)
                self.assertTrue((self.root / item['id'] / 'life.sqlite3').is_file())

    def test_habits_lifecycle(self):
        k = 'x_habits'; i = self.create(k, '添加 喝水')
        self.assertIn('打卡成功', self.run_command(k, f'打卡 {i}'))
        self.assertIn('累计1天', self.run_command(k, '统计'))
        self.assertIn('取消打卡成功', self.run_command(k, f'取消打卡 {i}'))
        self.assertIn('已归档', self.run_command(k, f'归档 {i}'))

    def test_habits_duplicate_and_private_isolation(self):
        k = 'x_habits'; i = self.create(k, '添加 喝水'); self.run_command(k, f'打卡 {i}')
        self.bad(k, f'打卡 {i}'); self.bad(k, f'打卡 {i}', user='12'); self.bad(k, f'打卡 {i}', scope='g:23')

    def test_ledger_decimal_and_statistics(self):
        k = 'x_ledger'; self.create(k, '添加 收入 100 工资'); i = self.create(k, '添加 支出 0.10 食物'); self.create(k, '添加 支出 0.20 食物')
        result = self.run_command(k, '统计'); self.assertIn('支出 0.30', result); self.assertIn('结余 99.70', result)
        self.assertIn('已归档', self.run_command(k, f'撤销 {i}')); self.assertIn('支出 0.20', self.run_command(k, '统计'))

    def test_ledger_invalid_amount_and_scope(self):
        k = 'x_ledger'; self.bad(k, '添加 支出 1.999 食物'); self.bad(k, '添加 支出 NaN 食物'); self.bad(k, '统计 2026-13')
        i = self.create(k, '添加 支出 1 食物'); self.bad(k, f'撤销 {i}', user='12')

    def test_reading_progress(self):
        k = 'x_reading'; i = self.create(k, '添加 一本书|200')
        self.assertIn('50.0%', self.run_command(k, f'进度 {i} 100')); self.assertIn('100/200', self.run_command(k, '列表'))

    def test_reading_invalid_total_and_overflow(self):
        k = 'x_reading'; self.bad(k, '添加 一本书|0'); i = self.create(k, '添加 一本书|200'); self.bad(k, f'进度 {i} 201'); self.bad(k, f'进度 {i} 1', user='12')

    def test_watchlist_status_and_rating(self):
        k = 'x_watchlist'; i = self.create(k, '添加 影片'); self.run_command(k, f'看完 {i}'); self.run_command(k, f'评分 {i} 8.5 很好')
        self.assertIn('已看', self.run_command(k, '列表')); self.assertIn('8.5', self.run_command(k, '列表'))

    def test_watchlist_invalid_rating(self):
        k = 'x_watchlist'; i = self.create(k, '添加 影片'); self.bad(k, f'评分 {i} 10.1'); self.bad(k, f'评分 {i} NaN'); self.bad(k, f'看完 {i}', scope='p:11')

    def test_spaced_cards_due_and_grade(self):
        k = 'x_spaced_cards'; i = self.create(k, '添加 问题|答案'); self.assertIn('问题', self.run_command(k, '到期')); self.assertIn('答案', self.run_command(k, f'回答 {i}'))
        self.assertIn('3天后', self.run_command(k, f'评估 {i} 5')); self.assertIn('暂无', self.run_command(k, '到期'))
        self.assertIn('1天后', self.run_command(k, f'评估 {i} 0'))

    def test_spaced_cards_invalid_grade_and_privacy(self):
        k = 'x_spaced_cards'; i = self.create(k, '添加 问题|答案'); self.bad(k, f'评估 {i} 6'); self.bad(k, f'回答 {i}', user='12')

    def test_pomodoro_persistence_and_finish(self):
        k = 'x_pomodoro'
        with patch.object(engine.time, 'time', return_value=1000): i = self.create(k, '开始 1 阅读')
        with patch.object(engine.time, 'time', return_value=1061):
            self.assertIn('已到时', self.run_command(k, '状态')); self.assertIn('实际61秒', self.run_command(k, '完成'))
        self.assertIn('61秒', self.run_command(k, '历史')); self.assertIn('已归档', self.run_command(k, f'归档 {i}'))

    def test_pomodoro_errors(self):
        k = 'x_pomodoro'; self.bad(k, '开始 0'); i = self.create(k, '开始 25 阅读'); self.bad(k, '开始 10'); self.bad(k, f'归档 {i}')
        self.assertIn('没有', self.run_command(k, '状态', user='12'))

    def test_goals_progress(self):
        k = 'x_goals'; i = self.create(k, '添加 写作|100'); self.run_command(k, f'进度 {i} 25'); self.assertIn('50/100', self.run_command(k, f'增加 {i} 25'))

    def test_goals_invalid_input_and_privacy(self):
        k = 'x_goals'; self.bad(k, '添加 写作|0'); i = self.create(k, '添加 写作|100'); self.bad(k, f'增加 {i} -1'); self.bad(k, f'进度 {i} 50', user='12')

    def test_inventory_moves_and_ledger(self):
        k = 'x_inventory'; i = self.create(k, '添加 笔 本'); self.assertIn('5本', self.run_command(k, f'变动 {i} +5 入库')); self.assertIn('3本', self.run_command(k, f'变动 {i} -2 领用')); self.assertIn('-2 → 3', self.run_command(k, f'流水 {i}'))

    def test_inventory_no_negative_partial_update(self):
        k = 'x_inventory'; i = self.create(k, '添加 笔 本'); self.bad(k, f'变动 {i} -1 丢失'); self.assertIn('0本', self.run_command(k, '列表')); self.bad(k, f'变动 {i} 1.5'); self.bad(k, f'流水 {i}', user='12')

    def test_split_bill_exact_remainder_and_settlement(self):
        k = 'x_split_bill'; i = self.create(k, '创建 聚餐|A|B|C'); self.run_command(k, f'支出 {i} A|0.10|餐费'); result = self.run_command(k, f'结算 {i}')
        self.assertIn('总支出0.10', result); self.assertIn('建议 B → A：0.03', result); self.assertIn('建议 C → A：0.03', result); self.assertIn('不会转账', result)

    def test_split_bill_permission_and_group_scope(self):
        k = 'x_split_bill'; self.bad(k, '创建 聚餐|A|B', scope='p:11'); i = self.create(k, '创建 聚餐|A|B'); self.bad(k, f'支出 {i} A|10', user='12'); self.bad(k, f'归档 {i}', user='12'); self.assertIn('成员已添加', self.run_command(k, f'成员 {i} C', user='99'))

    def test_signup_capacity_join_exit_close(self):
        k = 'x_signup'; i = self.create(k, '创建 桌游|1'); self.assertIn('加入成功', self.run_command(k, f'加入 {i} 小红', user='12')); self.assertIn('1/1', self.run_command(k, f'名单 {i}'))
        self.run_command(k, f'退出 {i}', user='12'); self.run_command(k, f'关闭 {i}'); self.assertIn('已关闭', self.run_command(k, f'名单 {i}'))

    def test_signup_full_and_no_other_user_close(self):
        k = 'x_signup'; i = self.create(k, '创建 桌游|1'); self.run_command(k, f'加入 {i}', user='12'); self.bad(k, f'加入 {i}', user='13'); self.bad(k, f'退出 {i}', user='13'); self.bad(k, f'关闭 {i}', user='12'); self.bad(k, f'归档 {i}', user='12')

    def test_round_robin_cycle(self):
        k = 'x_round_robin'; i = self.create(k, '创建 值日|A|B'); self.assertIn('本次轮值：A', self.run_command(k, f'下一个 {i}')); self.assertIn('本次轮值：B', self.run_command(k, f'下一个 {i}')); self.assertIn('本次轮值：A', self.run_command(k, f'下一个 {i}'))

    def test_round_robin_permissions_and_duplicates(self):
        k = 'x_round_robin'; self.bad(k, '创建 值日|A|A'); i = self.create(k, '创建 值日|A|B'); self.bad(k, f'下一个 {i}', user='12'); self.bad(k, f'设置 {i} C|D', user='12'); self.assertIn('本次轮值', self.run_command(k, f'下一个 {i}', user='99'))

    def test_queue_flow(self):
        k = 'x_queue'; i = self.create(k, '创建 队伍'); self.run_command(k, f'入列 {i} A', user='12'); self.run_command(k, f'入列 {i} B', user='13'); self.assertIn('已推进：A', self.run_command(k, f'推进 {i}')); self.run_command(k, f'退出 {i}', user='13'); self.assertIn('无人', self.run_command(k, f'状态 {i}'))

    def test_queue_permission_and_duplicates(self):
        k = 'x_queue'; i = self.create(k, '创建 队伍'); self.run_command(k, f'入列 {i}', user='12'); self.bad(k, f'入列 {i}', user='12'); self.bad(k, f'推进 {i}', user='12'); self.bad(k, f'关闭 {i}', user='12')

    def test_wishlist_priority_and_complete(self):
        k = 'x_wishlist'; i = self.create(k, '添加 键盘|3'); self.run_command(k, f'优先 {i} 1'); self.run_command(k, f'完成 {i}'); self.assertIn('P1 已完成', self.run_command(k, '列表'))

    def test_wishlist_priority_bounds_and_scope(self):
        k = 'x_wishlist'; self.bad(k, '添加 键盘|0'); i = self.create(k, '添加 键盘|3'); self.bad(k, f'优先 {i} 6'); self.bad(k, f'完成 {i}', user='12')

    def test_recipe_search_modify(self):
        k = 'x_recipes'; i = self.create(k, '添加 炒蛋|鸡蛋|炒熟'); self.assertIn('炒蛋', self.run_command(k, '搜索 鸡蛋')); self.run_command(k, f'修改 {i} 蒸蛋|鸡蛋、水|蒸熟'); self.assertIn('蒸熟', self.run_command(k, f'查看 {i}'))

    def test_recipe_invalid_and_privacy(self):
        k = 'x_recipes'; self.bad(k, '添加 只有名字'); i = self.create(k, '添加 炒蛋|鸡蛋|炒熟'); self.bad(k, f'修改 {i} 蒸蛋|鸡蛋|蒸熟', user='12')

    def test_bookmark_search_and_modify(self):
        k = 'x_bookmarks'; i = self.create(k, '添加 文档|https://example.com/docs|学习'); self.assertIn('文档', self.run_command(k, '搜索 学习')); self.run_command(k, f'修改 {i} 新文档|https://example.com/new|资料'); self.assertIn('/new', self.run_command(k, f'查看 {i}'))

    def test_bookmark_invalid_url_and_privacy(self):
        k = 'x_bookmarks'; self.bad(k, '添加 X|javascript:alert(1)|标签'); self.bad(k, '添加 X|https://user:secret@example.com|标签'); self.bad(k, '添加 X|https://example.com:bad|标签'); i = self.create(k, '添加 X|https://example.com|标签'); self.bad(k, f'查看 {i}', user='12')

    def test_anniversary_leap_date(self):
        k = 'x_anniversary'; self.create(k, '添加 闰年|02-29')
        from datetime import date
        with patch.object(engine, 'today', return_value=date(2026, 10, 17)):
            self.assertIn('2028-02-29', self.run_command(k, '列表'))

    def test_anniversary_invalid_date_and_privacy(self):
        k = 'x_anniversary'; self.bad(k, '添加 日期|02-30'); self.bad(k, '添加 日期|1-1'); i = self.create(k, '添加 日期|10-01'); self.bad(k, f'修改 {i} 改|10-02', user='12')

    def test_packing_template_check_and_uncheck(self):
        k = 'x_packing'; i = self.create(k, '模板 短途'); self.run_command(k, f'勾选 {i} 1'); self.assertIn('☑ 证件', self.run_command(k, f'状态 {i}')); self.run_command(k, f'取消 {i} 1'); self.assertIn('☐ 证件', self.run_command(k, f'状态 {i}')); self.run_command(k, f'添加 {i} 雨伞'); self.assertIn('雨伞', self.run_command(k, f'状态 {i}'))

    def test_packing_invalid_item_and_scope(self):
        k = 'x_packing'; self.bad(k, '模板 火星'); i = self.create(k, '创建 行李|手机'); self.bad(k, f'勾选 {i} 2'); self.bad(k, f'勾选 {i} 1', user='12')

    def test_agenda_collect_order_advance(self):
        k = 'x_agenda'; i = self.create(k, '创建 周会'); self.run_command(k, f'添加 {i} 议题A', user='12'); self.run_command(k, f'添加 {i} 议题B', user='13'); self.run_command(k, f'排序 {i} 2,1'); self.assertIn('议题B', self.run_command(k, f'推进 {i}')); self.assertIn('2. ☑', self.run_command(k, f'状态 {i}'))

    def test_agenda_invalid_permutation_and_permission(self):
        k = 'x_agenda'; i = self.create(k, '创建 周会'); self.run_command(k, f'添加 {i} 议题A'); self.bad(k, f'排序 {i} 1,1'); self.bad(k, f'推进 {i}', user='12'); self.bad(k, f'关闭 {i}', user='12')

    def test_time_budget_accumulates_real_seconds(self):
        k = 'x_time_budget'
        with patch.object(engine.time, 'time', return_value=1000): i = self.create(k, '开始 项目')
        with patch.object(engine.time, 'time', return_value=1060): self.assertIn('60秒', self.run_command(k, '结束'))
        with patch.object(engine.time, 'time', return_value=2000): self.create(k, '开始 项目')
        with patch.object(engine.time, 'time', return_value=2120): self.run_command(k, '结束')
        self.assertIn('180秒', self.run_command(k, '统计')); self.assertIn('已归档', self.run_command(k, f'归档 {i}'))

    def test_time_budget_single_running_and_scope(self):
        k = 'x_time_budget'; self.bad(k, '结束'); i = self.create(k, '开始 项目'); self.bad(k, '开始 其他'); self.bad(k, f'归档 {i}'); self.assertIn('没有', self.run_command(k, '状态', user='12'))

    def test_decision_matrix_weighted_scores_and_update(self):
        k = 'x_decision_matrix'; i = self.create(k, '创建 设备|性能:3|续航:1'); self.run_command(k, f'评分 {i} A|8|4'); self.run_command(k, f'评分 {i} B|6|10'); self.assertIn('A：7.0000', self.run_command(k, f'结果 {i}'))
        self.run_command(k, f'权重 {i} 性能:1|续航:1'); self.assertIn('B：8.0000', self.run_command(k, f'结果 {i}')); self.run_command(k, f'移除 {i} A'); self.assertNotIn('A：', self.run_command(k, f'结果 {i}'))

    def test_decision_matrix_bad_weight_score_and_privacy(self):
        k = 'x_decision_matrix'; self.bad(k, '创建 设备|性能:0'); self.bad(k, '创建 设备|性能:1|性能:2'); i = self.create(k, '创建 设备|性能:3|续航:1'); self.bad(k, f'评分 {i} A|8'); self.bad(k, f'评分 {i} A|11|2'); self.bad(k, f'评分 {i} A|8|4', user='12')

    def test_wiki_headings_history_restore_preserves_all(self):
        k = 'x_wiki'; i = self.create(k, '创建 指南|# 一级\\n## 二级\\n正文'); self.run_command(k, f'更新 {i} # 新版\\n新的正文'); self.assertIn('新的正文', self.run_command(k, f'查看 {i}')); self.assertIn('版本1', self.run_command(k, f'版本 {i} 1')); self.run_command(k, f'恢复 {i} 1')
        result = self.run_command(k, f'查看 {i}'); self.assertIn('版本3', result); self.assertIn('## 二级', result); self.assertIn('新的正文', self.run_command(k, f'版本 {i} 2'))

    def test_wiki_group_editor_and_private_isolation(self):
        k = 'x_wiki'; i = self.create(k, '创建 指南|# 标题'); self.assertIn('标题', self.run_command(k, f'查看 {i}', user='12')); self.bad(k, f'更新 {i} 新内容', user='12'); self.bad(k, f'恢复 {i} 1', user='12'); self.assertIn('新增版本2', self.run_command(k, f'更新 {i} 新内容', user='99'))
        j = self.create(k, '创建 私密|正文', scope='p:11'); self.bad(k, f'查看 {j}', user='12', scope='p:11'); self.bad(k, f'查看 {j}', scope='p:12')

    def test_archive_is_soft_and_state_is_durable(self):
        k = 'x_recipes'; i = self.create(k, '添加 蛋|鸡蛋|煮熟'); self.run_command(k, f'归档 {i}')
        with closing(sqlite3.connect(self.root / k / 'life.sqlite3')) as db:
            row = db.execute('SELECT active,body FROM records WHERE id=?', (i,)).fetchone()
        self.assertEqual(row[0], 0); self.assertIn('煮熟', row[1]); self.bad(k, f'查看 {i}')

    def test_limits_and_input_are_bounded(self):
        k = 'x_habits'; self.bad(k, '添加 ' + '字' * 81)
        self.assertIn('过长', engine.handle(k, '/习惯 ' + 'x' * 2200, self.ctx(k)))
        for i in range(200): self.create(k, f'添加 习惯{i}')
        self.assertIn('上限', self.bad(k, '添加 超额'))

    def test_missing_context_and_relative_data_rejected(self):
        result = engine.handle('x_habits', '/习惯 添加 X', {'data_dir': 'relative'})
        self.assertIn('操作未完成', result)
        ctx = self.ctx('x_habits'); ctx['data_dir'] = 'relative'; self.assertIn('绝对', engine.handle('x_habits', '/习惯 添加 X', ctx))

    def test_configured_active_limit_is_effective_and_isolated(self):
        k = 'x_habits'; ctx = self.ctx(k); ctx['settings'] = {'max_active_records': 10}
        for i in range(10):
            self.assertIn('已添加', engine.handle(k, f'/习惯 添加 事项{i}', ctx))
        self.assertIn('活跃10条', engine.handle(k, '/习惯 添加 超额', ctx))
        self.assertIn('已归档', engine.handle(k, '/习惯 归档 1', ctx))
        self.assertIn('已添加', engine.handle(k, '/习惯 添加 归档后释放活跃名额', ctx))
        other = self.ctx(k, user='12'); other['settings'] = {'max_active_records': 10}
        self.assertIn('已添加', engine.handle(k, '/习惯 添加 自己的记录', other))
        with closing(sqlite3.connect(self.root / k / 'life.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM records').fetchone()[0], 12)

    def test_configured_active_limit_rejects_invalid_types_and_bounds(self):
        k = 'x_habits'
        for value in (True, '10', 9, 201, None):
            ctx = self.ctx(k); ctx['settings'] = {'max_active_records': value}
            store = engine.Store(k, ctx)
            try:
                self.assertEqual(store.max_active_records, 200)
            finally:
                store.db.rollback(); store.db.close()

    def test_concurrent_signup_does_not_overbook(self):
        k = 'x_signup'; i = self.create(k, '创建 并发活动|5')
        def join(user): return self.run_command(k, f'加入 {i}', user=str(user))
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(join, range(20, 40)))
        self.assertEqual(sum('加入成功' in x for x in results), 5)
        self.assertIn('5/5', self.run_command(k, f'名单 {i}'))

    def test_unknown_commands_do_not_create_records(self):
        for item in CATALOG:
            result = self.run_command(item['id'], '不存在')
            self.assertIn('未知子命令', result)
            with closing(sqlite3.connect(self.root / item['id'] / 'life.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM records').fetchone()[0], 0)


if __name__ == '__main__': unittest.main()
