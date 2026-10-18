"""Functional parsing, per-user RSS persistence and public-API error fixtures."""
import importlib.util
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('network_plugins_test', ROOT / 'network_engine.py')
engine = importlib.util.module_from_spec(spec); spec.loader.exec_module(engine)
CATALOG = json.loads((ROOT / 'network_catalog.json').read_text(encoding='utf-8'))
RSS = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title><item><guid>id1</guid><title>First &amp; one</title><link>https://example.com/1</link><pubDate>today</pubDate></item><item><guid>id2</guid><title>Second</title><link>/2</link></item></channel></rss>'''


def response(body, mime='application/json', url='https://example.com/feed'):
    if not isinstance(body, bytes): body = json.dumps(body, ensure_ascii=False).encode()
    return engine.NET.Response(url, 200, {'content-type': mime}, body)


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.network-tests-', dir=ROOT / 'tests')
        self.base = Path(self.temp.name)

    def tearDown(self): self.temp.cleanup()

    def ctx(self, user='11', session='g:22', **settings):
        return dict(session_id=session, user_id=user, plugin_data_dir=str(self.base), settings=settings)

    def call(self, kind, command, ctx=None):
        return engine.handle(kind, command, self.ctx() if ctx is None else ctx)

    def add(self, title='Example', user='11', session='g:22', url='https://example.com/feed', **settings):
        with patch.object(engine.NET, 'check_target', side_effect=lambda value, **kw: engine.NET.canonical_url(value)):
            result = self.call('m_rss', '/订阅源 添加 ' + title + '|' + url, self.ctx(user, session, **settings))
        self.assertIn('已添加', result)
        return int(result.split('#', 1)[1].split('：', 1)[0])

    def test_all_five_wrappers_and_help_smokes(self):
        self.assertEqual(5, len(CATALOG))
        for entry in CATALOG:
            with self.subTest(plugin=entry['id']):
                path = ROOT / 'plugins' / entry['id'] / 'worker.py'
                ws = importlib.util.spec_from_file_location('test_worker_' + entry['id'], path)
                worker = importlib.util.module_from_spec(ws); ws.loader.exec_module(worker)
                self.assertIn(entry['expected_contains'], worker.handle(entry['smoke_command'], {}))
                self.assertIsNone(worker.handle('普通消息', {}))
                self.assertIsNone(worker.handle(entry['commands'][0] + 'extra', {}))
                self.assertEqual(25, entry['settings']['timeout'])
                manifest = json.loads(path.with_name('plugin.json').read_text(encoding='utf-8'))
                self.assertTrue(manifest['config_schema'])

    def test_rss_wrapper_maps_actual_host_data_dir(self):
        path = ROOT / 'plugins' / 'm_rss' / 'worker.py'
        ws = importlib.util.spec_from_file_location('test_mapping', path)
        worker = importlib.util.module_from_spec(ws); ws.loader.exec_module(worker)
        ctx = dict(session_id='p:11', user_id='11', data_dir=str(self.base), plugin_data_dir=str(self.base / 'must-not-use'), settings={})
        self.assertIn('暂无订阅', worker.handle('/订阅源 列表', ctx))
        self.assertTrue((self.base / 'market2-rss.sqlite3').exists())
        self.assertFalse((self.base / 'must-not-use').exists())

    def test_packaged_helpers_are_self_contained(self):
        directory = self.base / 'package'; directory.mkdir()
        for source, target in ((ROOT / 'network_engine.py', '_network.py'), (ROOT / 'safe_net.py', '_safe_net.py'), (ROOT / 'plugins' / 'm_bing' / 'worker.py', 'worker.py')):
            (directory / target).write_bytes(source.read_bytes())
        ws = importlib.util.spec_from_file_location('packaged_network_test', directory / 'worker.py')
        worker = importlib.util.module_from_spec(ws); ws.loader.exec_module(worker)
        self.assertIn('/每日壁纸', worker.handle('/每日壁纸 帮助', {}))

    def test_rss_add_duplicate_list_view_delete(self):
        identifier = self.add()
        self.assertIn(f'#{identifier}', self.call('m_rss', '/订阅源 列表'))
        self.assertIn('https://example.com/feed', self.call('m_rss', f'/订阅源 查看 {identifier}'))
        with patch.object(engine.NET, 'check_target', side_effect=lambda value, **kw: value):
            self.assertIn('已订阅', self.call('m_rss', '/订阅源 添加 duplicate|https://example.com/feed'))
        self.assertIn('已删除', self.call('m_rss', f'/订阅源 删除 {identifier}'))
        self.assertIn('暂无订阅', self.call('m_rss', '/订阅源 列表'))

    def test_rss_strict_user_session_private_boundaries(self):
        identifier = self.add('Secret title')
        for ctx in (self.ctx(user='12'), self.ctx(session='g:23'), self.ctx(session='p:11')):
            self.assertNotIn('Secret title', self.call('m_rss', '/订阅源 列表', ctx))
            for action in ('查看', '删除', '刷新'):
                self.assertIn('不属于', self.call('m_rss', f'/订阅源 {action} {identifier}', ctx))
        self.assertIn('Secret title', self.call('m_rss', '/订阅源 列表'))

    def test_rss_refresh_deduplicates_and_other_user_is_independent(self):
        first = self.add()
        second = self.add(user='12')
        with patch.object(engine.NET, 'request', return_value=response(RSS, 'application/rss+xml')):
            result = self.call('m_rss', f'/订阅源 刷新 {first}')
            self.assertIn('新条目 2 条', result)
            self.assertIn('First & one', result)
            self.assertIn('https://example.com/2', result)
            self.assertIn('新条目 0 条', self.call('m_rss', f'/订阅源 刷新 {first}'))
            self.assertIn('新条目 2 条', self.call('m_rss', f'/订阅源 刷新 {second}', self.ctx(user='12')))
        with closing(sqlite3.connect(self.base / 'market2-rss.sqlite3')) as db:
            self.assertEqual(4, db.execute('SELECT COUNT(*) FROM seen').fetchone()[0])

    def test_rss_failed_refresh_does_not_mark_items_seen(self):
        identifier = self.add()
        with patch.object(engine.NET, 'request', side_effect=engine.NET.NetworkError('HTTP 503')):
            self.assertIn('刷新失败', self.call('m_rss', f'/订阅源 刷新 {identifier}'))
        with closing(sqlite3.connect(self.base / 'market2-rss.sqlite3')) as db:
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM seen').fetchone()[0])

    def test_rss_deletion_cascades_seen(self):
        identifier = self.add()
        with patch.object(engine.NET, 'request', return_value=response(RSS)):
            self.call('m_rss', f'/订阅源 刷新 {identifier}')
        self.call('m_rss', f'/订阅源 删除 {identifier}')
        with closing(sqlite3.connect(self.base / 'market2-rss.sqlite3')) as db:
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM seen').fetchone()[0])

    def test_rss_limits_are_real_and_per_user(self):
        self.add(url='https://example.com/a', max_subscriptions=1)
        with patch.object(engine.NET, 'check_target', side_effect=lambda value, **kw: value):
            self.assertIn('数量上限', self.call('m_rss', '/订阅源 添加 extra|https://example.com/b', self.ctx(max_subscriptions=1)))
        self.add(user='12', max_subscriptions=1)

    def test_rss_seen_window_is_bounded_to_500_entries(self):
        identifier = self.add()
        for batch in range(6):
            raw = ('<rss><channel>' + ''.join(f'<item><guid>{batch}-{index}</guid><title>Item {index}</title></item>' for index in range(100)) + '</channel></rss>').encode()
            with patch.object(engine.NET, 'request', return_value=response(raw)):
                self.assertIn('新条目 100 条', self.call('m_rss', f'/订阅源 刷新 {identifier}'))
        with closing(sqlite3.connect(self.base / 'market2-rss.sqlite3')) as db:
            self.assertEqual(500, db.execute('SELECT COUNT(*) FROM seen').fetchone()[0])

    def test_rss_multi_feed_limit_is_enforced(self):
        self.add(url='https://example.com/a')
        self.add(url='https://example.com/b')
        with patch.object(engine.NET, 'request', return_value=response(RSS)) as request:
            self.call('m_rss', '/订阅源 刷新', self.ctx(refresh_feed_limit=1))
        self.assertEqual(1, request.call_count)
        self.assertLessEqual(request.call_args.kwargs['timeout'], 12)

    def test_rss_atom_namespace_and_duplicate_guid(self):
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>same</id><title>Atom</title><link href="/article"/><updated>2026-01-01</updated></entry><entry><id>same</id><title>dup</title></entry></feed>'
        entries = engine.feed_entries(atom, 'https://example.com/feed')
        self.assertEqual(1, len(entries))
        self.assertEqual('https://example.com/article', entries[0]['url'])

    def test_rss_entity_and_utf16_doctype_blocked_before_parser(self):
        payload = '<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x "123">]><rss>&x;</rss>'
        for raw in (payload.encode(), payload.encode('utf-16'), b'<rss><!ENTITY x SYSTEM "file:///etc/passwd"></rss>'):
            with patch.object(engine.ET, 'fromstring') as parser, self.assertRaises(ValueError): engine.feed_entries(raw, 'https://example.com/')
            parser.assert_not_called()

    def test_rss_invalid_xml_and_private_links(self):
        for raw in (b'<broken>', b'<html/>'):
            with self.assertRaises(ValueError): engine.feed_entries(raw, 'https://example.com/')
        entries = engine.feed_entries(b'<rss><item><title>x</title><link>https://127.0.0.1/a</link></item></rss>', 'https://example.com/')
        self.assertEqual('', entries[0]['url'])

    def test_rss_missing_identity_data_path_and_unknown_action(self):
        for ctx in ({}, self.ctx(user='name'), {**self.ctx(), 'plugin_data_dir': '../data'}):
            self.assertIn('操作未完成', self.call('m_rss', '/订阅源 列表', ctx))
        self.assertIn('操作未完成', self.call('m_rss', '/订阅源 任意指令'))

    def test_storage_failure_does_not_disclose_local_path(self):
        with patch.object(engine, 'database', side_effect=OSError('private/path/config')):
            result = self.call('m_rss', '/订阅源 列表')
        self.assertIn('存储暂不可用', result)
        self.assertNotIn('private/path', result)

    def test_bangumi_search_details_calendar(self):
        fixtures = [dict(data=[dict(id=253, name='Cowboy Bebop', name_cn='星际牛仔', rating={'score': 9})]), dict(id=253, name='Cowboy Bebop', name_cn='星际牛仔', summary='<p>公开简介</p><script>evil</script>', date='1998-04-03', eps=26, rating={'score': 9, 'total': 100}), [dict(weekday={'id': 1}, items=[dict(id=253, name_cn='星际牛仔')])]]
        with patch.object(engine, 'api', side_effect=fixtures) as api:
            self.assertIn('#253 星际牛仔', self.call('m_bangumi', '/番剧 搜索 星际牛仔'))
            result = self.call('m_bangumi', '/番剧 详情 253')
            self.assertIn('公开简介', result); self.assertNotIn('evil', result)
            self.assertIn('来源排期', self.call('m_bangumi', '/番剧 放送 1'))
            self.assertEqual([2], api.call_args_list[0].args[2]['filter']['type'])

    def test_bangumi_empty_bad_data_and_input(self):
        with patch.object(engine, 'api', return_value={'data': []}):
            self.assertIn('没有匹配', self.call('m_bangumi', '/番剧 搜索 no-results'))
        with patch.object(engine, 'api', return_value={'error': 'failure'}):
            self.assertIn('格式异常', self.call('m_bangumi', '/番剧 搜索 abc'))
        for command in ('/番剧 详情 -1', '/番剧 放送 8', '/番剧 搜索'):
            self.assertIn('操作未完成', self.call('m_bangumi', command))

    def test_abbreviations_exact_terms_fallback_and_missing(self):
        rows = [{'name': 'yyds', 'trans': ['永远的神', '<b>一种赞叹</b>']}, {'name': 'awsl', 'inputting': ['啊我死了']}]
        with patch.object(engine, 'api', return_value=rows):
            result = self.call('m_abbr', '/缩写 yyds,awsl,xxxx', self.ctx(explanation_limit=1))
        self.assertIn('yyds：永远的神', result)
        self.assertNotIn('一种赞叹', result)
        self.assertIn('awsl：啊我死了', result)
        self.assertIn('xxxx：没有查到', result)
        self.assertIn('社区释义', result)

    def test_abbreviations_bad_input_does_not_network(self):
        with patch.object(engine, 'api') as api:
            self.assertIn('操作未完成', self.call('m_abbr', '/缩写 私人聊天内容'))
            api.assert_not_called()

    def test_web_title_visible_content_ignores_script_style_navigation(self):
        html = b'<html><head><title>Title &amp; data</title><style>secretstyle</style></head><body><nav>secret-nav</nav><article><h1>Heading</h1><p>Visible <b>bold</b></p><script>evil-code</script><p>Another</p></article><footer>secret-footer</footer></body></html>'
        with patch.object(engine.NET, 'request', return_value=response(html, 'text/html')):
            result = self.call('m_webtext', '/网页正文 https://example.com/')
        self.assertIn('Title & data', result); self.assertIn('Visible bold', result); self.assertIn('Another', result)
        for word in ('secretstyle', 'secret-nav', 'evil-code', 'secret-footer'): self.assertNotIn(word, result)

    def test_web_dynamic_empty_unsupported_mime_and_limit(self):
        with patch.object(engine.NET, 'request', return_value=response(b'<script>render()</script>', 'text/html')):
            self.assertIn('未提取到', self.call('m_webtext', '/网页正文 https://example.com/'))
        with patch.object(engine.NET, 'request', return_value=response(b'%PDF', 'application/pdf')):
            self.assertIn('不解析文件', self.call('m_webtext', '/网页正文 https://example.com/'))
        with patch.object(engine.NET, 'request', return_value=response(b'x' * 250, 'text/plain')):
            result = self.call('m_webtext', '/网页正文 https://example.com/', self.ctx(text_max_chars=200))
        self.assertIn('摘录达到配置上限', result)
        self.assertNotIn('x' * 201, result)

    def test_web_meta_gbk_decode(self):
        raw = '<meta charset="gbk"><title>中文标题</title><p>正确正文</p>'.encode('gbk')
        with patch.object(engine.NET, 'request', return_value=response(raw, 'text/html')):
            result = self.call('m_webtext', '/网页正文 https://example.com/')
        self.assertIn('中文标题', result); self.assertIn('正确正文', result)

    def test_bing_title_copyright_uhd_and_no_picture_claim(self):
        with patch.object(engine, 'api', return_value={'images': [{'title': 'Nature', 'copyright': 'Author © 2026', 'startdate': '20261017', 'urlbase': '/th?id=OHR.Example'}]}):
            result = self.call('m_bing', '/每日壁纸')
        self.assertIn('Nature', result); self.assertIn('Author © 2026', result)
        self.assertIn('https://www.bing.com/th?id=OHR.Example_UHD.jpg', result)
        self.assertIn('未下载', result)

    def test_bing_bad_index_empty_data_and_internal_image(self):
        self.assertIn('操作未完成', self.call('m_bing', '/每日壁纸 8'))
        with patch.object(engine, 'api', return_value={'images': []}): self.assertIn('没有返回', self.call('m_bing', '/每日壁纸'))
        with patch.object(engine, 'api', return_value={'images': [{'urlbase': 'https://127.0.0.1/private'}]}): self.assertIn('未提供可用', self.call('m_bing', '/每日壁纸'))

    def test_all_external_failures_are_clear_and_not_success(self):
        for kind, command in (('m_bangumi', '/番剧 详情 253'), ('m_abbr', '/缩写 yyds'), ('m_bing', '/每日壁纸')):
            with patch.object(engine, 'api', side_effect=engine.NET.NetworkError('远端返回 HTTP 429。')):
                self.assertIn('操作未完成：远端返回 HTTP 429', self.call(kind, command))
        with patch.object(engine.NET, 'request', side_effect=engine.NET.NetworkError('DNS 查询超时。')):
            self.assertIn('DNS 查询超时', self.call('m_webtext', '/网页正文 https://example.com/'))


if __name__ == '__main__': unittest.main()
