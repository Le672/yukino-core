import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dev_engine_test', ROOT / 'dev_engine.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
CATALOG = json.loads((ROOT / 'catalog.json').read_text(encoding='utf-8'))
CONTEXT = dict(session_id='p:10001', user_id='10001', settings={})


def load_worker(plugin_id, root=ROOT / 'plugins'):
    path = root / plugin_id / 'worker.py'
    module_spec = importlib.util.spec_from_file_location('test_' + plugin_id, path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def invoke(plugin_id, text, settings=None):
    return engine.handle(plugin_id, text, {**CONTEXT, 'settings': settings or {}})


class PluginContractTests(unittest.TestCase):
    pass


for item in CATALOG:
    def valid(self, entry=item):
        result = load_worker(entry['id']).handle(entry['smoke_command'], CONTEXT)
        self.assertIsInstance(result, str)
        self.assertIn(entry['expected_contains'], result)
        self.assertFalse(result.startswith('输入错误'), result)
    setattr(PluginContractTests, 'test_valid_' + item['id'], valid)

    def invalid(self, entry=item):
        result = load_worker(entry['id']).handle(entry['invalid_command'], CONTEXT)
        self.assertTrue(result.startswith('输入错误'), result)
    setattr(PluginContractTests, 'test_invalid_' + item['id'], invalid)

    def ignore(self, entry=item):
        worker = load_worker(entry['id'])
        self.assertIsNone(worker.handle('普通聊天不要触发插件', CONTEXT))
        self.assertIsNone(worker.handle(None, CONTEXT))
        self.assertIsNone(worker.handle(entry['commands'][0] + 'xyz', CONTEXT))
    setattr(PluginContractTests, 'test_unrelated_' + item['id'], ignore)


class AlgorithmTests(unittest.TestCase):
    def test_manifest_schema_and_independent_ids(self):
        self.assertEqual(21, len({item['id'] for item in CATALOG}))
        commands = []
        for item in CATALOG:
            manifest = json.loads((ROOT / 'plugins' / item['id'] / 'plugin.json').read_text(encoding='utf-8'))
            self.assertEqual(item['id'], manifest['id'])
            self.assertEqual('1.0.0', manifest['version'])
            self.assertTrue(manifest['config_schema'])
            commands += manifest['commands']
        self.assertEqual(len(commands), len(set(commands)))

    def test_json_pointer_escaping_and_array(self):
        self.assertIn('42', invoke('x_json', '/JSON取值 /a~1b/~0key/0|{"a/b":{"~key":[42]}}'))
        self.assertIn('42', invoke('x_json', '/JSON取值 a.b[1]|{"a":{"b":[0,42]}}'))

    def test_json_path_error_is_not_null(self):
        self.assertTrue(invoke('x_json', '/JSON取值 a[4]|{"a":[1]}').startswith('输入错误'))
        self.assertTrue(invoke('x_json', '/JSON格式 NaN').startswith('输入错误'))
        self.assertTrue(invoke('x_json', '/JSON格式 ' + '[' * 42 + '0' + ']' * 42).startswith('输入错误'))

    def test_csv_quoted_delimiters_newlines_missing(self):
        result = invoke('x_csv', '/CSV统计 "姓名","备注",分数\n"小,明","第一行\n第二行",90\n小红,,80')
        self.assertIn('数据 2 行', result)
        self.assertIn('缺失 1', result)
        self.assertIn('平均 85', result)

    def test_csv_no_header_and_semicolon(self):
        result = invoke('x_csv', '/CSV统计 1;2\n3;4', {'has_header': False, 'delimiter': ';'})
        self.assertIn('数据 2 行', result)
        self.assertIn('平均 2', result)

    def test_regex_chinese_groups_and_json_alternation(self):
        result = invoke('x_regex', '/正则测试 (中文)+|中文中文')
        self.assertIn('[0:4]', result)
        self.assertIn('中文中文', result)
        result = invoke('x_regex', '/正则测试 {"pattern":"cat|dog","text":"a cat and dog"}')
        self.assertIn('2 项', result)

    def test_regex_redos_is_terminated(self):
        start = time.monotonic()
        result = invoke('x_regex', '/正则测试 (a+)+$|' + 'a' * 1500 + '!', {'timeout_ms': 200})
        self.assertIn('超时', result)
        self.assertLess(time.monotonic() - start, 3)

    def test_regex_no_match_and_ignore_case(self):
        self.assertIn('0 项', invoke('x_regex', '/正则测试 abc|DEF'))
        self.assertIn('1 项', invoke('x_regex', '/正则测试 abc|ABC', {'ignore_case': True}))

    def test_cron_steps_sunday_or(self):
        self.assertIn('2026-10-18T12:30+08:00', invoke('x_cron', '/Cron下次 30 12 * * 7|2026-10-17T12:00+08:00'))
        # Oct 19 is Monday but not the first: DOM/DOW use OR when both restricted.
        self.assertIn('2026-10-19T09:00+08:00', invoke('x_cron', '/Cron下次 0 9 1 * 1|2026-10-18T23:00+08:00'))
        self.assertIn('0,15,30,45', invoke('x_cron', '/Cron说明 */15 * * * *'))

    def test_cron_impossible_date_is_bounded(self):
        self.assertIn('扫描 30 天', invoke('x_cron', '/Cron下次 0 0 31 2 *|2026-01-01', {'lookahead_days': 30}))
        self.assertTrue(invoke('x_cron', '/Cron说明 */0 * * * *').startswith('输入错误'))

    def test_cron_step_wildcard_uses_vixie_and_semantics(self):
        result = invoke('x_cron', '/Cron下次 0 9 */1 * 1|2026-10-17T00:00:00+08:00', {'result_count': 1})
        self.assertIn('2026-10-19T09:00+08:00', result)
        self.assertNotIn('2026-10-17T09:00', result)
        result = invoke('x_cron', '/Cron下次 0 9 1 * */1|2026-10-17T00:00:00+08:00', {'result_count': 1})
        self.assertIn('2026-11-01T09:00+08:00', result)

    def test_cron_numeric_full_range_keeps_or_semantics(self):
        result = invoke('x_cron', '/Cron下次 0 9 1-31 * 1|2026-10-17T00:00:00+08:00', {'result_count': 1})
        self.assertIn('2026-10-17T09:00+08:00', result)

    def test_uuid_determinism_count_and_structure(self):
        result = invoke('x_uuid', '/UUID 5 dns|example.com')
        self.assertIn('cfbff0d1-9375-5685-968c-48ce8b15ae17', result)
        result = invoke('x_uuid', '/UUID 4 3')
        self.assertEqual(4, len(result.splitlines()))
        self.assertIn('版本：4', invoke('x_uuid', '/UUID解析 ' + result.splitlines()[1]))

    def test_ip_ipv6_and_point_to_point(self):
        self.assertIn('IPv6', invoke('x_ip', '/IP信息 2001:db8::123/64'))
        self.assertIn('常规可用：2', invoke('x_ip', '/IP信息 10.0.0.0/31'))
        self.assertIn('全局可路由：False', invoke('x_ip', '/IP信息 127.0.0.1'))

    def test_url_preserves_query_duplicates_blank_and_fragment(self):
        result = invoke('x_url', '/清理链接 https://example.com/a?v=1&v=2&empty=&utm_medium=x#section')
        self.assertIn('v=1&v=2&empty=#section', result)
        self.assertTrue(invoke('x_url', '/链接信息 https://user:secret@example.com').startswith('输入错误'))
        self.assertTrue(invoke('x_url', '/链接信息 https://example.com:99999').startswith('输入错误'))

    def test_color_roundtrip_and_wcag(self):
        self.assertIn('#FF0000', invoke('x_color', '/颜色 #f00'))
        self.assertIn('1.00:1', invoke('x_color', '/对比度 #123456|#123456'))
        self.assertTrue(invoke('x_color', '/颜色 rgb(1,2,3').startswith('输入错误'))

    def test_units_dimension_and_absolute_zero(self):
        self.assertIn('= 1 m', invoke('x_units', '/单位换算 100 cm m'))
        self.assertIn('= 1024 b', invoke('x_units', '/单位换算 1 kib b'))
        self.assertIn('= 0 K', invoke('x_units', '/单位换算 -273.15 C K'))
        self.assertTrue(invoke('x_units', '/单位换算 -1 K C').startswith('输入错误'))

    def test_radix_negative_zero_capacity(self):
        self.assertTrue(invoke('x_radix', '/进制转换 -255 10 16').endswith('-FF'))
        self.assertTrue(invoke('x_radix', '/进制转换 0 10 2').endswith('0'))
        self.assertTrue(invoke('x_radix', '/进制转换 ' + 'Z' * 600 + ' 36 2').startswith('输入错误'))

    def test_unicode_combining_and_categories(self):
        self.assertIn('U+4E2D', invoke('x_unicode', '/字符信息 中'))
        self.assertIn('码点数：2 → 1', invoke('x_unicode', '/文本规范化 NFC|e\u0301'))
        self.assertIn('UTF-8 e4 b8 ad', invoke('x_unicode', '/字符信息 中'))

    def test_markdown_fences_duplicates_and_table_escaping(self):
        result = invoke('x_markdown', '/Markdown目录 # Same\n```python\n# Hidden\n```\n# Same')
        self.assertIn('#same-1', result)
        self.assertNotIn('Hidden', result)
        self.assertIn('a\\|b', invoke('x_markdown', '/Markdown表格 col,x\n"a|b",2'))

    def test_diff_json_and_trailing_newline(self):
        self.assertIn('+three', invoke('x_diff', '/文本差异 {"a":"one\\ntwo","b":"one\\nthree"}'))
        self.assertIn('末尾换行符', invoke('x_diff', '/文本差异 {"a":"a\\n","b":"a"}'))
        self.assertIn('完全相同', invoke('x_diff', '/文本差异 same|same'))

    def test_semver_official_precedence_chain(self):
        chain = '1.0.0-alpha 1.0.0-alpha.1 1.0.0-alpha.beta 1.0.0-beta 1.0.0-beta.2 1.0.0-beta.11 1.0.0-rc.1 1.0.0'.split()
        for first, second in zip(chain, chain[1:]):
            self.assertLess(engine.semver_compare(first, second), 0)
        self.assertEqual(0, engine.semver_compare('1.0.0+abc', '1.0.0+xyz'))
        self.assertTrue(invoke('x_semver', '/版本比较 1.0.0-01 1.0.0').startswith('输入错误'))

    def test_http_mime_and_unassigned(self):
        self.assertIn('image/png', invoke('x_http', '/MIME .png'))
        self.assertIn('.json', invoke('x_http', '/MIME application/json'))
        self.assertIn('未注册', invoke('x_http', '/HTTP状态 499').replace('没有注册', '未注册'))

    def test_epoch_zero_negative_explicit_ms_timezone(self):
        self.assertIn('1970-01-01T00:00:00+00:00', invoke('x_epoch', '/时间戳转换 s:0'))
        self.assertIn('1969-12-31T23:59:59+00:00', invoke('x_epoch', '/时间戳转换 -1'))
        self.assertIn('Unix 秒：1', invoke('x_epoch', '/时间戳转换 ms:1000'))
        self.assertIn('Unix 秒：0', invoke('x_epoch', '/时间戳转换 1970-01-01T08:00:00+08:00'))

    def test_decimal_exact_fraction_and_code_rejection(self):
        self.assertTrue(invoke('x_decimal', '/精确计算 分数 1/3+1/6').endswith('= 1/2'))
        self.assertTrue(invoke('x_decimal', '/精确计算 (0.1+0.2)*10').endswith('= 3'))
        for expression in ('2**1000000', '(1).__class__', '[x for x in []]', 'True+1', '1/0', '0xFF'):
            self.assertTrue(invoke('x_decimal', '/精确计算 ' + expression).startswith('输入错误'), expression)

    def test_matrix_fraction_transpose_product_singular(self):
        self.assertIn('1/100', invoke('x_matrix', '/矩阵 行列式 [[0.1,0],[0,0.1]]'))
        self.assertIn('[ 1, 3 ]', invoke('x_matrix', '/矩阵 转置 [[1,2],[3,4]]'))
        self.assertIn('[ 11 ]', invoke('x_matrix', '/矩阵 乘法 [[1,2]]|[[3],[4]]'))
        self.assertIn('奇异矩阵', invoke('x_matrix', '/矩阵 行列式 [[1,2],[2,4]]'))
        self.assertTrue(invoke('x_matrix', '/矩阵 转置 [[true]]').startswith('输入错误'))

    def test_stats_population_sample_quantiles(self):
        result = invoke('x_stats', '/数据统计 1 2 3 4')
        self.assertIn('Q1：1.75', result)
        self.assertIn('总体方差：1.25', result)
        self.assertIn('样本方差：1.66666666667', result)
        self.assertIn('至少需要 2', invoke('x_stats', '/数据统计 1'))

    def test_stats_large_offset_preserves_mean_spread_and_sample(self):
        n = 10 ** 60
        result = invoke('x_stats', f'/数据统计 {n},{n + 1}')
        self.assertIn(f'平均：{n}.5', result)
        self.assertIn('总体方差：0.25；总体标准差：0.5', result)
        self.assertIn('样本方差：0.5', result)

    def test_stats_large_negative_offset_preserves_fraction(self):
        n = 10 ** 60
        result = invoke('x_stats', f'/数据统计 {-n},{-n - 1}')
        self.assertIn(f'平均：-{n}.5', result)
        self.assertIn('总体方差：0.25', result)

    def test_probability_boundaries_and_combinatorics(self):
        self.assertIn('= 120', invoke('x_probability', '/概率计算 组合 10 3'))
        self.assertIn('= 720', invoke('x_probability', '/概率计算 排列 10 3'))
        self.assertIn('P(X=0) = 1', invoke('x_probability', '/概率计算 二项 0 0 0'))
        self.assertIn('P(X=3) = 1', invoke('x_probability', '/概率计算 二项 3 3 1'))
        self.assertTrue(invoke('x_probability', '/概率计算 组合 2001 1').startswith('输入错误'))

    def test_polynomial_derivative_real_complex_and_degenerate(self):
        self.assertIn('[2, -3]', invoke('x_polynomial', '/多项式 导数 1 -3 2'))
        self.assertIn('x₁=2', invoke('x_polynomial', '/多项式 二次 1 -3 2'))
        self.assertIn('± 1i', invoke('x_polynomial', '/多项式 二次 1 0 1'))
        self.assertIn('一次方程：x=2', invoke('x_polynomial', '/多项式 二次 0 2 -4'))
        self.assertIn('任意 x', invoke('x_polynomial', '/多项式 二次 0 0 0'))

    def test_quadratic_stable_small_root_both_signs(self):
        for coefficient, expected_sign in [('1e30', -1), ('-1e30', 1)]:
            result = invoke('x_polynomial', f'/多项式 二次 1 {coefficient} 1')
            roots = [Decimal(line.split('=', 1)[1]) for line in result.splitlines() if line.startswith(('x₁=', 'x₂='))]
            self.assertEqual(2, len(roots), result)
            small = min(roots, key=abs)
            self.assertEqual(Decimal(expected_sign) * Decimal('1e-30'), small)

    def test_quadratic_repeated_root_zero_q_and_near_discriminant(self):
        self.assertIn('x₁=0\nx₂=0', invoke('x_polynomial', '/多项式 二次 1 0 0'))
        self.assertIn('x₁=-1\nx₂=-1', invoke('x_polynomial', '/多项式 二次 1 2 1'))
        c = 10 ** 60 // 4 + 1
        result = invoke('x_polynomial', f'/多项式 二次 1 1e30 {c}')
        self.assertIn('判别式 -4', result)
        self.assertIn('± 1i', result)

    def test_input_and_output_caps(self):
        self.assertTrue(invoke('x_json', '/JSON格式 ' + 'a' * 24000).startswith('输入错误'))
        result = invoke('x_unicode', '/字符信息 ' + '中' * 40, {'max_output_chars': 1000})
        self.assertIn('输出已达配置上限', result)

    def test_packaged_helper_works_without_source_fallback(self):
        temp_root = ROOT / 'tests' / '.temp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as tmp:
            directory = Path(tmp) / 'x_decimal'
            directory.mkdir()
            (directory / 'worker.py').write_bytes((ROOT / 'plugins' / 'x_decimal' / 'worker.py').read_bytes())
            (directory / '_engine.py').write_bytes((ROOT / 'dev_engine.py').read_bytes())
            worker = load_worker('x_decimal', Path(tmp))
            self.assertIn('= 0.3', worker.handle('/精确计算 0.1+0.2', CONTEXT))


if __name__ == '__main__':
    unittest.main()
