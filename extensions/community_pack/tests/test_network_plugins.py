"""No internet or real accounts required: upstream formats, transport bounds and failure paths."""
import hashlib
import importlib.util
import io
import json
import pathlib
import unittest
import urllib.error
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1] / 'plugins'
NAMES = ('weather_query','steam_query','hot_trends','music_search','mc_status','bili_query')

def load(name):
    spec = importlib.util.spec_from_file_location('test_network_'+name, ROOT/name/'worker.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class Response(io.BytesIO):
    def __init__(self, value, headers=None):
        super().__init__(value if isinstance(value, bytes) else json.dumps(value).encode())
        self.headers = headers or {}

class NetworkHelpers(unittest.TestCase):
    def setUp(self):
        self.net = load('weather_query').net

    def opened(self, value, headers=None):
        opener = mock.Mock()
        opener.open.side_effect = lambda *a, **k: Response(value, headers)
        return opener

    def test_helpers_are_identical_and_manifests_are_valid(self):
        hashes = set()
        for name in NAMES:
            with self.subTest(name=name):
                folder = ROOT/name
                manifest = json.loads((folder/'plugin.json').read_text(encoding='utf-8'))
                self.assertEqual(manifest['id'], name)
                self.assertEqual(manifest['version'], '1.0.1' if name=='hot_trends' else '1.0.0')
                self.assertEqual(manifest['worker'], 'worker.py')
                self.assertTrue((folder/'README.md').exists())
                hashes.add(hashlib.sha256((folder/'_net.py').read_bytes()).digest())
        self.assertEqual(len(hashes), 1)

    def test_only_fixed_https_origins_and_no_embedded_credentials(self):
        for url in ('http://api.bilibili.com/x','https://evil.example/x',
                    'https://user:pass@api.bilibili.com/x','https://api.bilibili.com:443/x',
                    'https://api.bilibili.com/x?q=1','https://api.bilibili.com/x#frag'):
            with self.subTest(url=url), self.assertRaises(self.net.LookupFailure):
                self.net.fetch(url)

    def test_success_cache_and_encoded_query(self):
        opener = self.opened({'ok':True})
        with mock.patch.object(self.net.urllib.request, 'build_opener', return_value=opener):
            for _ in range(2):
                self.assertEqual(self.net.fetch('https://api.bilibili.com/x', {'keyword':'x&token=secret'}), {'ok':True})
        self.assertEqual(opener.open.call_count, 1)
        req = opener.open.call_args.args[0]
        self.assertIn('x%26token%3Dsecret', req.full_url)
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 5)
        self.assertNotIn('Authorization', req.headers)
        self.assertNotIn('Cookie', req.headers)

    def test_post_is_form_encoded(self):
        opener = self.opened({'code':200})
        with mock.patch.object(self.net.urllib.request, 'build_opener', return_value=opener):
            self.net.fetch('https://music.163.com/api/search/get', data={'s':'hello & world'})
        req = opener.open.call_args.args[0]
        self.assertEqual(req.method if hasattr(req,'method') else req.get_method(), 'POST')
        self.assertEqual(req.data, b's=hello+%26+world')

    def test_errors_cached_without_sensitive_exception_text(self):
        opener = mock.Mock()
        opener.open.side_effect = OSError('super-private-password https://secret.example')
        with mock.patch.object(self.net.urllib.request, 'build_opener', return_value=opener):
            for _ in range(2):
                with self.assertRaises(self.net.LookupFailure) as caught:
                    self.net.fetch('https://api.bilibili.com/x')
                self.assertNotIn('private', str(caught.exception))
                self.assertNotIn('secret.example', str(caught.exception))
        self.assertEqual(opener.open.call_count, 1)

    def test_http_status_error_is_sanitized(self):
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError('https://secret.example', 403, 'private', {}, None)
        with mock.patch.object(self.net.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(self.net.LookupFailure, 'HTTP 403'):
                self.net.fetch('https://api.bilibili.com/x')

    def test_redirect_handler_blocks_every_origin(self):
        with self.assertRaises(self.net.LookupFailure):
            self.net.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://api.bilibili.com/y')

    def test_response_read_bound(self):
        opener = self.opened(b'x'*(self.net.MAX_BYTES+1))
        with mock.patch.object(self.net.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(self.net.LookupFailure, '过大'):
                self.net.fetch('https://api.bilibili.com/x')

    def test_response_content_length_bound(self):
        opener = self.opened({}, {'Content-Length':str(self.net.MAX_BYTES+1)})
        with mock.patch.object(self.net.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(self.net.LookupFailure, '过大'):
                self.net.fetch('https://api.bilibili.com/x')

    def test_bad_json_and_nonobject_are_errors(self):
        for value in (b'not-json secret-password', b'[]', b'null'):
            self.net._CACHE.clear()
            opener = self.opened(value)
            with self.subTest(value=value), mock.patch.object(self.net.urllib.request, 'build_opener', return_value=opener):
                with self.assertRaises(self.net.LookupFailure) as caught:
                    self.net.fetch('https://api.bilibili.com/x')
                self.assertNotIn('secret', str(caught.exception))

    def test_cache_byte_budget(self):
        for i in range(6):
            self.net.store((str(i),None), 500, True, {'x':i}, 1024*1024)
        self.assertLessEqual(sum(x[3] for x in self.net._CACHE.values()), self.net.CACHE_BUDGET)
        self.assertEqual(len(self.net._CACHE), 2)

    def test_cache_item_budget(self):
        for i in range(70):
            self.net.store((str(i),None), 500, True, {}, 1)
        self.assertLessEqual(len(self.net._CACHE), 64)

    def test_expired_cache_requeries(self):
        opener = self.opened({'ok':True})
        with mock.patch.object(self.net.time, 'monotonic', side_effect=[0,301]), mock.patch.object(
                self.net.urllib.request,'build_opener',return_value=opener):
            self.net.fetch('https://api.bilibili.com/x', ttl=300)
            self.net.fetch('https://api.bilibili.com/x', ttl=300)
        self.assertEqual(opener.open.call_count, 2)

    def test_total_deadline_and_remaining_timeout(self):
        opener = self.opened({'ok':True})
        with mock.patch.object(self.net.time,'monotonic',side_effect=[100,112,112]), mock.patch.object(
                self.net.urllib.request,'build_opener',return_value=opener):
            self.net.begin()
            self.net.fetch('https://api.bilibili.com/x')
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 2)
        with mock.patch.object(self.net.time,'monotonic',return_value=115):
            with self.assertRaisesRegex(self.net.LookupFailure, '预算'):
                self.net.fetch('https://api.bilibili.com/y')

    def test_markup_and_controls_are_not_returned(self):
        self.assertEqual(self.net.clean('<em>标题</em>\n\x00&amp;'), '标题 &')

class PluginFormats(unittest.TestCase):
    def setUp(self):
        self.modules = {name:load(name) for name in NAMES}

    def test_unmatched_commands_never_call_network(self):
        for name, module in self.modules.items():
            with self.subTest(name=name), mock.patch.object(module.net,'fetch') as fetch:
                self.assertIsNone(module.handle('随便聊天', {}))
                self.assertIsNone(module.handle(None, {}))
                fetch.assert_not_called()

    def test_missing_arguments_have_usage(self):
        for name, command in [('weather_query','/天气'),('steam_query','/steam'),
                    ('music_search','/搜歌'),('mc_status','/mc'),('bili_query','/B站搜索')]:
            module = self.modules[name]
            with self.subTest(name=name), mock.patch.object(module.net,'fetch') as fetch:
                self.assertIn('用法', module.handle(command, {}))
                fetch.assert_not_called()

    def test_control_characters_and_long_inputs_rejected(self):
        commands={'weather_query':'/天气','steam_query':'/steam','hot_trends':'/热榜',
                  'music_search':'/搜歌','mc_status':'/mc','bili_query':'/B站搜索'}
        for name, module in self.modules.items():
            for value in ('x\x00y', 'x'*400):
                with self.subTest(name=name,value=value[:10]), mock.patch.object(module.net,'fetch') as fetch:
                    self.assertIn('失败', module.handle(commands[name]+' '+value, {}))
                    fetch.assert_not_called()

    def test_network_failure_is_explicit_no_model_fallback(self):
        commands={'weather_query':'/天气 北京','steam_query':'/steam 620','hot_trends':'/热榜 b站',
                  'music_search':'/搜歌 晴天','mc_status':'/mc mc.hypixel.net','bili_query':'/直播 6'}
        for name, module in self.modules.items():
            with self.subTest(name=name), mock.patch.object(module.net,'fetch',side_effect=module.net.LookupFailure('数据源暂不可用')):
                reply = module.handle(commands[name], {})
                self.assertIn('失败', reply)
                self.assertIn('不会转交模型', reply)

    def test_weather_actual_place_current_time_and_three_days(self):
        module=self.modules['weather_query']
        geo={'results':[{'name':'Beijing','admin1':'北京','country':'中国','latitude':39.9,'longitude':116.4}]}
        weather={'current':{'time':'2026-10-17T12:00','temperature_2m':20,'relative_humidity_2m':40,
            'weather_code':0,'wind_speed_10m':10},'timezone':'Asia/Shanghai','daily':{'time':['2026-10-17','2026-10-18','2026-10-19'],
            'weather_code':[0,3,61],'temperature_2m_min':[12,11,10],'temperature_2m_max':[22,20,19],
            'precipitation_probability_max':[0,20,70]}}
        with mock.patch.object(module.net,'fetch',side_effect=[geo,weather]) as fetch:
            reply=module.handle('/天气 北京',{'settings':{'country_code':'cn'}})
        self.assertIn('Beijing / 北京 / 中国',reply)
        self.assertIn('Asia/Shanghai',reply)
        self.assertIn('2026-10-19',reply)
        self.assertIn('70%',reply)
        self.assertEqual(fetch.call_args_list[0].args[1]['countryCode'],'CN')

    def test_weather_unresolved_never_substitutes(self):
        module=self.modules['weather_query']
        with mock.patch.object(module.net,'fetch',return_value={'results':[]}):
            reply=module.handle('/天气 不存在之城',{})
        self.assertIn('未找到',reply)
        self.assertIn('没有使用其他城市代替',reply)

    def test_weather_default_city(self):
        module=self.modules['weather_query']
        with mock.patch.object(module.net,'fetch',return_value={'results':[]}) as fetch:
            module.handle('/天气',{'settings':{'default_city':'London'}})
        self.assertEqual(fetch.call_args.args[1]['name'],'London')

    def test_weather_country_validation(self):
        module=self.modules['weather_query']
        with mock.patch.object(module.net,'fetch') as fetch:
            self.assertIn('国家代码',module.handle('/天气 北京',{'settings':{'country_code':'../'}}))
            fetch.assert_not_called()

    def test_weather_missing_upstream_fields_is_failure(self):
        module=self.modules['weather_query']
        with mock.patch.object(module.net,'fetch',return_value={'results':[{}]}):
            self.assertIn('失败',module.handle('/天气 北京',{}))

    def test_steam_id_region_currency_and_discount(self):
        module=self.modules['steam_query']
        payload={'620':{'success':True,'data':{'name':'Portal 2','is_free':False,
            'price_overview':{'currency':'CNY','final':4200,'final_formatted':'¥ 42.00','discount_percent':50}}}}
        with mock.patch.object(module.net,'fetch',return_value=payload) as fetch:
            reply=module.handle('/steam 620',{})
        self.assertIn('CNY',reply); self.assertIn('50%',reply); self.assertIn('/app/620/',reply)
        self.assertEqual(fetch.call_args.args[1]['cc'],'cn')

    def test_steam_search_passes_only_numeric_ids(self):
        module=self.modules['steam_query']
        with mock.patch.object(module.net,'fetch',side_effect=[{'items':[{'id':620},{'id':'../evil'}]},
                {'620':{'success':True,'data':{'name':'Portal 2','is_free':True}}}]):
            reply=module.handle('/steam Portal',{})
        self.assertIn('免费游戏',reply)

    def test_steam_missing_price_and_delisted_explicit(self):
        module=self.modules['steam_query']
        for payload, phrase in [({'620':{'success':False}},'无可用详情'),
                ({'620':{'success':True,'data':{'name':'A'}}},'未提供该地区售价')]:
            with self.subTest(phrase=phrase),mock.patch.object(module.net,'fetch',return_value=payload):
                self.assertIn(phrase,module.handle('/steam 620',{}))

    def test_steam_bad_region_and_empty_search(self):
        module=self.modules['steam_query']
        self.assertIn('代码设置无效',module.handle('/steam A',{'settings':{'country_code':'../../'}}))
        with mock.patch.object(module.net,'fetch',return_value={'items':[]}):
            self.assertIn('未找到',module.handle('/steam game',{}))

    def test_bili_popular_returns_titles_only(self):
        module=self.modules['hot_trends']
        payload={'code':0,'data':{'list':[{'title':'<em>真实</em>热门','bvid':'BV1xx411c7mD'}]}}
        with mock.patch.object(module.net,'fetch',return_value=payload):
            reply=module.handle('/热榜 b站',{})
        self.assertEqual(reply,'1. 真实热门')

    def test_tieba_ignores_upstream_arbitrary_url(self):
        module=self.modules['hot_trends']
        payload={'data':{'bang_topic':{'topic_list':[{'topic_name':'真实话题','topic_id':123,'topic_url':'https://evil.example'}]}}}
        with mock.patch.object(module.net,'fetch',return_value=payload):
            reply=module.handle('/热榜 贴吧',{})
        self.assertEqual(reply,'1. 真实话题')

    def test_hot_source_validation_empty_and_api_risk(self):
        module=self.modules['hot_trends']
        self.assertIn('用法',module.handle('/热榜 任意来源',{}))
        for payload in ({'code':-412},{'code':0,'data':{'list':[]}}):
            with mock.patch.object(module.net,'fetch',return_value=payload):
                self.assertIn('失败',module.handle('/热榜 b站',{}))

    def test_music_names_artists_and_official_link_only(self):
        module=self.modules['music_search']
        payload={'code':200,'result':{'songs':[{'id':123,'name':'<b>歌名</b>',
            'artists':[{'name':'歌手'}],'url':'https://evil.example/audio'}]}}
        with mock.patch.object(module.net,'fetch',return_value=payload) as fetch:
            reply=module.handle('/搜歌 歌名',{})
        self.assertIn('歌名 — 歌手',reply);self.assertIn('/#/song?id=123',reply)
        self.assertNotIn('evil',reply);self.assertIn('不提供音频下载',reply)
        self.assertEqual(fetch.call_args.kwargs['data']['type'],1)

    def test_music_alt_artist_shape_and_empty(self):
        module=self.modules['music_search']
        with mock.patch.object(module.net,'fetch',return_value={'code':200,'result':{'songs':[{'id':123,'name':'a','ar':[{'name':'b'}]}]}}):
            self.assertIn('a — b',module.handle('/搜歌 a',{}))
        with mock.patch.object(module.net,'fetch',return_value={'code':200,'result':{}}):
            self.assertIn('未找到',module.handle('/搜歌 a',{}))
        with mock.patch.object(module.net,'fetch',return_value={'code':301}):
            self.assertIn('要求登录',module.handle('/搜歌 a',{}))

    def test_mc_host_public_examples(self):
        module=self.modules['mc_status']
        for host in ('mc.hypixel.net','mc.hypixel.net:25565','8.8.8.8','[2606:4700:4700::1111]:25565'):
            with self.subTest(host=host): self.assertEqual(module.validate_host(host),host)

    def test_mc_host_rejects_internal_url_credentials_numeric_aliases(self):
        module=self.modules['mc_status']
        for host in ('localhost','foo.localhost','foo.local','foo.internal','127.0.0.1','10.0.0.1',
                '172.16.0.1','192.168.0.1','169.254.169.254','100.64.0.1','0.0.0.0','224.0.0.1',
                '[::1]','[fc00::1]','[fe80::1]','https://mc.hypixel.net','a@mc.hypixel.net',
                'mc.hypixel.net/path','mc.hypixel.net?key=x','2130706433','0x7f000001','127.1',
                'mc.hypixel.net:0','mc.hypixel.net:65536','mc.hypixel.net:abc','[2606:4700::1]:',
                'mc.hypixel.net%2fabc','host.example','a..com','-a.com'):
            with self.subTest(host=host),self.assertRaises(module.net.LookupFailure):module.validate_host(host)

    def test_mc_online_and_no_local_latency_claim(self):
        module=self.modules['mc_status']
        payload={'online':True,'version':'1.21','players':{'online':12,'max':100},'motd':{'clean':['Welcome']}}
        with mock.patch.object(module.net,'fetch',return_value=payload) as fetch:
            reply=module.handle('/mc mc.hypixel.net:25565',{})
        self.assertIn('12/100',reply);self.assertIn('第三方探测',reply);self.assertIn('不提供本机延迟',reply)
        self.assertTrue(fetch.call_args.args[0].startswith('https://api.mcsrvstat.us/3/'))
        self.assertIn('%3A25565',fetch.call_args.args[0])

    def test_mc_offline_and_invalid_response(self):
        module=self.modules['mc_status']
        with mock.patch.object(module.net,'fetch',return_value={'online':False}):
            self.assertIn('离线或第三方探测不可达',module.handle('/mc mc.hypixel.net',{}))
        with mock.patch.object(module.net,'fetch',return_value={'online':'false'}):
            self.assertIn('失败',module.handle('/mc mc.hypixel.net',{}))

    def test_bili_video_titles_author_and_sanitized_links(self):
        module=self.modules['bili_query']
        payload={'code':0,'data':{'result':[{'title':'<em>视频</em>','author':'UP主','bvid':'BV1xx411c7mD','arcurl':'https://evil.example'}]}}
        with mock.patch.object(module.net,'fetch',return_value=payload):
            reply=module.handle('/B站搜索 测试',{})
        self.assertIn('视频｜UP：UP主',reply);self.assertNotIn('evil',reply)
        self.assertIn('www.bilibili.com/video/BV1xx411c7mD/',reply)

    def test_bili_search_empty_and_risk_control(self):
        module=self.modules['bili_query']
        with mock.patch.object(module.net,'fetch',return_value={'code':0,'data':{'result':[]}}):
            self.assertIn('未找到',module.handle('/B站搜索 x',{}))
        with mock.patch.object(module.net,'fetch',return_value={'code':-412}):
            self.assertIn('受限',module.handle('/B站搜索 x',{}))

    def test_live_room_numeric_only(self):
        module=self.modules['bili_query']
        for value in ('https://live.bilibili.com/6','abc','-1','6?token=x','1234567891234'):
            with self.subTest(value=value),mock.patch.object(module.net,'fetch') as fetch:
                self.assertIn('只接受数字',module.handle('/直播 '+value,{}));fetch.assert_not_called()

    def test_live_explicit_all_statuses(self):
        module=self.modules['bili_query']
        for value, status in ((0,'未开播'),(1,'正在直播'),(2,'轮播中'),(9,'状态未知')):
            payload={'code':0,'data':{'title':'房间','room_id':6,'live_status':value,'area_name':'游戏','parent_area_name':'游戏'}}
            with self.subTest(status=status),mock.patch.object(module.net,'fetch',return_value=payload):
                reply=module.handle('/直播 6',{})
                self.assertIn(status,reply);self.assertIn('https://live.bilibili.com/6',reply)

    def test_live_nonexistent_explicit(self):
        module=self.modules['bili_query']
        with mock.patch.object(module.net,'fetch',return_value={'code':1}):
            self.assertIn('不存在',module.handle('/直播 6',{}))

if __name__ == '__main__':
    unittest.main()
