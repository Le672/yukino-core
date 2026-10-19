"""Fixed-domain metadata, HTTP status and bounded-cache regression tests."""
import importlib.util
import gzip
import io
import json
from pathlib import Path
import sys
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


common = load('social_net_common_tests', ROOT / 'common.py')
safe_net = load('social_safe_net_tests', ROOT / 'safe_net.py')
with patch.dict(sys.modules, {'common': common, 'safe_net': safe_net}):
    network = load('social_network_tests', ROOT / 'network.py')


def response(data, status=200):
    return safe_net.Response('https://api.github.com/', status, {'content-type': 'application/json'}, json.dumps(data).encode())


def github(assets=0):
    return dict(draft=False, prerelease=False, tag_name='v1.2.3', published_at='2030-01-01T12:00:00Z',
                html_url='https://github.com/example/demo/releases/tag/v1.2.3',
                assets=[dict(name=f'file{i}.zip', browser_download_url=f'https://github.com/example/demo/releases/download/v1.2.3/file{i}.zip') for i in range(assets)])


def pypi(name='requests'):
    return {'info': dict(name=name, version='2.0.0', summary='HTTP client', license_expression='Apache-2.0', requires_python='>=3.9', author='Example')}


class FakeSocket:
    def __init__(self, response):
        self.response = response

    def settimeout(self, timeout): pass
    def connect(self, endpoint): pass
    def getpeername(self): return ('8.8.8.8', 443)
    def sendall(self, value): pass
    def makefile(self, mode): return io.BytesIO(self.response)
    def close(self): pass
    def shutdown(self, how): pass


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.clock = patch.object(network.time, 'time', return_value=1893456000.0)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.tmp.cleanup()

    def ctx(self, pid='s_github', **settings):
        return dict(data_dir=str(self.base / pid), user_id='101', session_id='p:101', settings=settings)

    def call(self, text, pid='s_github', **settings):
        return network.handle(pid, text, self.ctx(pid, **settings))

    def test_manifests_wrappers_and_config_ranges(self):
        for pid, command in [('s_github', '/GitHub发行'), ('s_package', '/包版本')]:
            meta = json.loads((ROOT / 'plugins' / pid / 'plugin.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['commands'], [command])
            self.assertEqual({x['key'] for x in meta['config_schema']}, {'cache_seconds', 'timeout_seconds', 'max_output_chars'})
            with patch.dict(sys.modules, {'network': network}):
                wrapper = load(pid + '_wrapper_test', ROOT / 'plugins' / pid / 'worker.py')
            self.assertIn(command, wrapper.handle(command, self.ctx(pid)))

    def test_github_success_has_five_assets_no_release_notes(self):
        data = github(7)
        data['body'] = 'NEVER_COPY_THIS_RELEASE_BODY'
        with patch.object(safe_net, 'request', return_value=response(data)) as request:
            out = self.call('/GitHub发行 example/demo')
        self.assertIn('v1.2.3', out)
        self.assertIn('2030-01-01T12:00:00Z', out)
        self.assertEqual(out.count('附件：'), 5)
        self.assertNotIn('file5.zip', out)
        self.assertNotIn('NEVER_COPY', out)
        self.assertIn('本次查询时间', out)
        url, = request.call_args.args
        self.assertEqual(url, 'https://api.github.com/repos/example/demo/releases/latest')
        self.assertEqual(request.call_args.kwargs, dict(timeout=15, max_bytes=1048576, max_redirects=0, allowed_hosts={'api.github.com'}))

    def test_pypi_and_npm_scoped_fixed_endpoint_encoding(self):
        with patch.object(safe_net, 'request', return_value=response(pypi('test-package'))) as request:
            out = self.call('/包版本 PyPI Test_Package', 's_package')
            self.assertEqual(request.call_args.args[0], 'https://pypi.org/pypi/test-package/json')
            self.assertEqual(request.call_args.kwargs['max_bytes'], 8 * 1024 * 1024)
        self.assertIn('Apache-2.0', out)
        self.assertIn('>=3.9', out)
        self.assertIn('不代表安全', out)
        npm = dict(name='@types/node', version='24.0.0', description='types', license={'type': 'MIT'}, engines={'node': '>=18'}, _npmUser={'name': 'maintainer', 'email': 'do-not-output@example.invalid'})
        with patch.object(safe_net, 'request', return_value=response(npm)) as request:
            out = self.call('/包版本 npm @types/node', 's_package')
            self.assertEqual(request.call_args.args[0], 'https://registry.npmjs.org/%40types%2Fnode/latest')
            self.assertEqual(request.call_args.kwargs['allowed_hosts'], {'registry.npmjs.org'})
            self.assertEqual(request.call_args.kwargs['max_bytes'], 1024 * 1024)
        self.assertIn('MIT', out)
        self.assertIn('>=18', out)
        self.assertIn('maintainer', out)
        self.assertNotIn('email', out)

    def test_query_injection_and_mismatched_identity_do_not_reach_network(self):
        with patch.object(safe_net, 'request') as request:
            for text in ['/GitHub发行 https://github.com/a/b', '/GitHub发行 a/b?x=1', '/GitHub发行 ../b', '/GitHub发行 a/..', '/GitHub发行 a/b/c', '/GitHub发行 a%2Fb/c']:
                self.assertNotIn('正式发行：', self.call(text))
            for text in ['/包版本 npm http://127.0.0.1/x', '/包版本 npm @a/b/extra', '/包版本 PyPI a?x=1', '/包版本 npm %40a%2Fb', '/包版本 URL requests']:
                self.assertNotIn('版本：', self.call(text, 's_package'))
            ctx = self.ctx()
            ctx['session_id'] = 'p:202'
            self.assertIn('身份', network.handle('s_github', '/GitHub发行 example/demo', ctx))
            request.assert_not_called()

    def test_http_404_403_429_and_generic_failures_are_explicit(self):
        for status, needle in [(404, '未找到'), (403, '拒绝'), (429, '限流'), (500, 'HTTP 500')]:
            with self.subTest(status=status), patch.object(safe_net, 'request', side_effect=safe_net.NetworkError(f'远端返回 HTTP {status}。')):
                result = self.call('/GitHub发行 example/demo')
                self.assertIn(needle, result)
                self.assertIn('未使用过期缓存', result)
        with patch.object(safe_net, 'request', return_value=response({}, 404)):
            self.assertIn('HTTP 404', self.call('/GitHub发行 example/demo'))

    def test_malformed_metadata_json_and_foreign_links_never_cache_success(self):
        candidates = [[], {}, dict(github(), draft=True), dict(github(), prerelease=True),
                      dict(github(), published_at='not-a-date'), dict(github(), assets={}),
                      dict(github(), html_url='https://evil.example/releases/tag/a'),
                      dict(github(), assets=[dict(name='x', browser_download_url='https://github.com/other/repo/releases/download/v1/x')])]
        for data in candidates:
            with patch.object(safe_net, 'request', return_value=response(data)):
                self.assertNotIn('本次查询时间', self.call('/GitHub发行 example/demo'))
        bad = safe_net.Response('https://api.github.com/', 200, {}, b'{not-json')
        with patch.object(safe_net, 'request', return_value=bad):
            self.assertIn('有效 JSON', self.call('/GitHub发行 example/demo'))
        db = network.connect(self.ctx())
        try:
            self.assertEqual(db.execute('SELECT count(*) FROM cache').fetchone()[0], 0)
        finally:
            db.close()

    def test_wrong_package_names_and_missing_version_fail(self):
        for data in [pypi('wrong'), {'info': {'name': 'requests', 'version': []}}, {'info': None}]:
            with patch.object(safe_net, 'request', return_value=response(data)):
                self.assertNotIn('本次查询时间', self.call('/包版本 PyPI requests', 's_package'))
        with patch.object(safe_net, 'request', return_value=response(dict(name='wrong', version='1.0'))):
            self.assertIn('不匹配', self.call('/包版本 npm vue', 's_package'))

    def test_cache_hit_has_original_time_expiry_and_no_stale_fallback(self):
        with patch.object(safe_net, 'request', return_value=response(github())) as request:
            self.call('/GitHub发行 example/demo')
            with patch.object(network.time, 'time', return_value=1893456299.0):
                out = self.call('/GitHub发行 example/demo')
            self.assertEqual(request.call_count, 1)
            self.assertIn('原查询时间', out)
            self.assertIn('不代表实时', out)
        with patch.object(network.time, 'time', return_value=1893456300.0), patch.object(safe_net, 'request', side_effect=safe_net.NetworkError('远端返回 HTTP 429。')):
            result = self.call('/GitHub发行 example/demo')
            self.assertIn('限流', result)
            self.assertNotIn('v1.2.3', result)
            self.assertNotIn('成功结果缓存', result)

    def test_cache_disabled_and_clock_rollback_never_hit(self):
        with patch.object(safe_net, 'request', return_value=response(github())) as request:
            self.call('/GitHub发行 example/demo', cache_seconds=0)
            self.call('/GitHub发行 example/demo', cache_seconds=0)
            self.assertEqual(request.call_count, 2)
            self.call('/GitHub发行 example/demo')
            with patch.object(network.time, 'time', return_value=1893455999.0):
                self.assertIn('本次查询时间', self.call('/GitHub发行 example/demo'))
            self.assertEqual(request.call_count, 4)

    def test_cache_is_bounded_to_100_successful_items(self):
        def fake(url, **kwargs):
            name = url.split('/')[-2]
            return response(pypi(name))
        with patch.object(safe_net, 'request', side_effect=fake):
            for i in range(107):
                self.assertIn('本次查询时间', self.call('/包版本 PyPI pkg' + str(i), 's_package'))
        db = network.connect(self.ctx('s_package'))
        try:
            self.assertEqual(db.execute('SELECT count(*) FROM cache').fetchone()[0], 100)
        finally:
            db.close()

    def test_output_cap_and_settings_are_enforced(self):
        data = github(5)
        for asset in data['assets']:
            asset['name'] = 'x' * 80
        with patch.object(safe_net, 'request', return_value=response(data)):
            result = self.call('/GitHub发行 example/demo', max_output_chars=500)
        self.assertLessEqual(len(result), 500)
        self.assertIn('截断', result)
        with patch.object(safe_net, 'request') as request:
            for settings in [dict(cache_seconds=-1), dict(timeout_seconds=16), dict(max_output_chars=6001), dict(cache_seconds=True)]:
                self.assertIn('设置', self.call('/GitHub发行 example/demo', **settings))
            request.assert_not_called()

    def test_safe_net_keeps_public_ip_rules(self):
        for ip in ['127.0.0.1', '10.0.0.1', '169.254.169.254', '198.18.0.1', '::1', '::ffff:8.8.8.8']:
            self.assertFalse(safe_net.public_ip(ip))
        self.assertTrue(safe_net.public_ip('8.8.8.8'))

    def test_only_fixed_pypi_json_endpoint_gets_8mib_cap(self):
        cases = [
            ('https://pypi.org/pypi/numpy/json', {'allowed_hosts': {'pypi.org'}, 'max_redirects': 0}, 8 * 1024 * 1024),
            ('https://pypi.org/pypi/pillow/json', {'allowed_hosts': {'pypi.org'}, 'max_redirects': 0}, 8 * 1024 * 1024),
            ('https://registry.npmjs.org/vue/latest', {'allowed_hosts': {'registry.npmjs.org'}, 'max_redirects': 0}, 1024 * 1024),
            ('https://api.github.com/repos/a/b/releases/latest', {'allowed_hosts': {'api.github.com'}, 'max_redirects': 0}, 1024 * 1024),
            ('https://pypi.org/simple/', {'allowed_hosts': {'pypi.org'}, 'max_redirects': 0}, 1024 * 1024),
            ('https://pypi.org/pypi/numpy/json?x=1', {'allowed_hosts': {'pypi.org'}, 'max_redirects': 0}, 1024 * 1024),
            ('https://pypi.org/pypi/numpy/json', {'allowed_hosts': {'pypi.org'}, 'max_redirects': 1}, 1024 * 1024),
            ('https://pypi.org/pypi/numpy/json', {'max_redirects': 0}, 1024 * 1024),
            ('https://pypi.org/pypi/numpy/json', {'allowed_hosts': {'pypi.org'}, 'max_redirects': 0, 'method': 'POST'}, 1024 * 1024),
        ]
        for url, kwargs, expected in cases:
            with self.subTest(url=url, kwargs=kwargs), patch.object(safe_net, '_one', return_value=response({})) as one:
                safe_net.request(url, max_bytes=20 * 1024 * 1024, **kwargs)
                self.assertEqual(one.call_args.args[4], expected)

    def fake_pypi_http(self, body, headers=b''):
        wire = b'HTTP/1.1 200 OK\r\n' + headers + b'\r\n' + body
        sock = FakeSocket(wire)
        tls = Mock()
        tls.wrap_socket.return_value = sock
        with patch.object(safe_net, 'resolve_public', return_value=[(socket.AF_INET, ('8.8.8.8', 443))]), \
             patch.object(safe_net.socket, 'socket', return_value=sock), \
             patch.object(safe_net.ssl, 'create_default_context', return_value=tls):
            return safe_net.request('https://pypi.org/pypi/numpy/json', allowed_hosts={'pypi.org'}, max_redirects=0, max_bytes=8 * 1024 * 1024)

    def test_pypi_8mib_accepts_over_old_cap_and_rejects_wire_or_decoded_excess(self):
        accepted = b'x' * (1024 * 1024 + 1)
        self.assertEqual(self.fake_pypi_http(accepted).body, accepted)
        limit = 8 * 1024 * 1024
        self.assertEqual(len(self.fake_pypi_http(b'x' * limit).body), limit)
        with self.assertRaisesRegex(safe_net.NetworkError, '大小'):
            self.fake_pypi_http(b'a', headers=b'Content-Length: ' + str(limit + 1).encode() + b'\r\n')
        with self.assertRaisesRegex(safe_net.NetworkError, '大小'):
            self.fake_pypi_http(b'x' * (limit + 1))
        compressed = gzip.compress(b'x' * (limit + 1))
        self.assertLess(len(compressed), limit)
        with self.assertRaisesRegex(safe_net.NetworkError, '解压后'):
            self.fake_pypi_http(compressed, headers=b'Content-Encoding: gzip\r\n')

    def test_linked_cache_rejected_without_request_or_external_write(self):
        root = self.base/'s_github'
        root.mkdir()
        outside = self.base/'outside.txt'
        outside.write_text('unchanged', encoding='utf-8')
        try:
            (root/'network-cache.sqlite3').symlink_to(outside)
        except OSError:
            self.skipTest('Current Windows token cannot create symlinks')
        with patch.object(safe_net, 'request') as request:
            self.assertIn('拒绝', self.call('/GitHub发行 example/demo'))
            request.assert_not_called()
        self.assertEqual(outside.read_text(encoding='utf-8'), 'unchanged')


if __name__ == '__main__':
    unittest.main()
