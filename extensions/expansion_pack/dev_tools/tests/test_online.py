import importlib.util
import ipaddress
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('dev_online_test',ROOT/'helper_online.py');online=importlib.util.module_from_spec(spec);spec.loader.exec_module(online)
spec=importlib.util.spec_from_file_location('dev_engine_online_test',ROOT/'dev_engine.py');engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)

class OnlineTests(unittest.TestCase):
    def setUp(self):online._CACHE.clear()
    def response(self,data,status=200):
        import json
        return online.NET.Response('https://ipwho.is/8.8.8.8',status,{'content-type':'application/json'},json.dumps(data).encode())

    def test_ip_returns_real_provider_fields_and_caches(self):
        payload={'success':True,'ip':'8.8.8.8','country':'United States','region':'California','city':'Mountain View','connection':{'asn':15169,'isp':'Google LLC','org':'Google'},'timezone':{'id':'America/Los_Angeles'}}
        with patch.object(online.NET,'request',return_value=self.response(payload)) as fetch:
            text=online.geo(ipaddress.ip_address('8.8.8.8'),{});online.geo(ipaddress.ip_address('8.8.8.8'),{})
        self.assertIn('Google LLC',text);self.assertIn('15169',text);self.assertEqual(fetch.call_count,1)

    def test_private_address_never_leaves_server(self):
        with patch.object(online.NET,'request') as fetch:
            self.assertIn('未发送',online.geo(ipaddress.ip_address('127.0.0.1'),{}));self.assertFalse(fetch.called)

    def test_provider_failure_uses_real_fallback_not_invented_data(self):
        fallback={'ip':'1.1.1.1','country_name':'Australia','org':'Cloudflare','asn':'AS13335'}
        with patch.object(online.NET,'request',side_effect=[online.NET.NetworkError('offline'),self.response(fallback)]):
            text=online.geo(ipaddress.ip_address('1.1.1.1'),{})
        self.assertIn('Cloudflare',text);self.assertIn('ipapi.co',text)

    def test_mismatched_ip_cannot_be_reported_as_success(self):
        with patch.object(online.NET,'request',return_value=self.response({'success':True,'ip':'1.1.1.1'})):
            self.assertIn('暂不可用',online.geo(ipaddress.ip_address('8.8.8.8'),{}))

    def test_dns_actual_records_and_invalid_domain(self):
        with patch.object(online.NET,'request',return_value=self.response({'Status':0,'Answer':[{'name':'example.com.','type':1,'TTL':300,'data':'203.0.113.34'}]})):
            text=online.dns('example.com A',{})
        self.assertIn('203.0.113.34',text);self.assertIn('TTL 300',text)
        for host in ('localhost','127.0.0.1','https://example.com','x.local','999.999.999.999'):
            with self.assertRaises(ValueError):online.dns(host,{})

    def test_url_reports_real_head_status_and_rejects_private_redirects(self):
        response=online.NET.Response('https://example.com/',404,{'content-type':'text/html','content-length':'500'},b'')
        with patch.object(online.NET,'request',return_value=response) as fetch:
            text=online.website('https://example.com/',{})
        self.assertIn('HTTP状态：404',text);self.assertEqual(fetch.call_args.kwargs['method'],'HEAD')
        with patch.object(online.NET,'_one',return_value=online.NET.Response('https://example.com/',302,{'location':'https://127.0.0.1/'},b'')):
            with self.assertRaises(online.NET.NetworkError):online.NET.request('https://example.com/',method='HEAD')

    def test_network_can_be_disabled_and_numeric_http_stays_local(self):
        with patch.object(online.NET,'request') as fetch:
            self.assertIn('关闭',online.geo(None,{'settings':{'online_enabled':False}}));self.assertFalse(fetch.called)
        self.assertIn('Not Found',engine.handle('x_http','/HTTP状态 404',{}))
