"""No live traffic: validate URL/DNS boundaries and numeric-IP HTTPS transport."""
import importlib.util
import gzip
import io
import json
from pathlib import Path
import socket
import ssl
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('market2_safety_test', ROOT / 'safe_net.py')
net = importlib.util.module_from_spec(spec)
spec.loader.exec_module(net)


def address(ip='8.8.8.8'):
    return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (ip, 443))


class FakeSocket:
    def __init__(self, response, peer='8.8.8.8'):
        self.response, self.peer, self.endpoints, self.closed = response, peer, [], False

    def settimeout(self, timeout): self.timeout = timeout
    def connect(self, endpoint): self.endpoints.append(endpoint)
    def getpeername(self): return (self.peer, 443)
    def sendall(self, value): self.sent = value
    def makefile(self, mode): return io.BytesIO(self.response)
    def close(self): self.closed = True
    def shutdown(self, how): self.closed = True


def response(body=b'ok', status='200 OK', headers=b''):
    return ('HTTP/1.1 ' + status + '\r\n').encode() + headers + b'\r\n' + body


class SafetyTests(unittest.TestCase):
    def test_reject_bad_schemes_ports_credentials_and_control(self):
        for url in ('http://example.com', 'file:///etc/passwd', 'https://example.com:444/', 'https://u:p@example.com/', 'https://example.com/\r\nHost:x', 'https://example.com/has space', 'https://example.com\\@8.8.8.8/', 'https://localhost/', 'https://x.local/', 'https://localhost./'):
            with self.subTest(url=url), self.assertRaises(net.NetworkError): net.canonical_url(url)

    def test_reject_nonpublic_and_transition_literals(self):
        for ip in ('127.0.0.1', '10.0.0.1', '172.16.1.2', '192.168.1.2', '169.254.169.254', '100.64.0.1', '198.18.1.2', '0.0.0.0', '[::1]', '[fe80::1]', '[::ffff:8.8.8.8]', '[2002:0808:0808::]', '[fe80::1%25eth0]'):
            with self.subTest(ip=ip), self.assertRaises(net.NetworkError): net.canonical_url('https://' + ip + '/')

    def test_public_canonical_preserves_encoded_path_query(self):
        self.assertEqual('https://example.com/a%20b?q=%E4%B8%AD', net.canonical_url('https://EXAMPLE.com:443/a%20b?q=中#section'))
        self.assertEqual('https://[2606:4700:4700::1111]/', net.canonical_url('https://[2606:4700:4700::1111]'))

    def test_dns_mixed_public_private_is_fully_rejected(self):
        with patch.object(net.socket, 'getaddrinfo', return_value=[address(), address('10.0.0.1')]), self.assertRaisesRegex(net.NetworkError, '非公网'):
            net.resolve_public('example.com', time.monotonic() + 1)

    def test_dns_duplicates_empty_and_too_many(self):
        with patch.object(net.socket, 'getaddrinfo', return_value=[address(), address()]):
            self.assertEqual([(socket.AF_INET, ('8.8.8.8', 443))], net.resolve_public('example.com', time.monotonic() + 1))
        for values in ([], [address()] * 33):
            with patch.object(net.socket, 'getaddrinfo', return_value=values), self.assertRaises(net.NetworkError):
                net.resolve_public('example.com', time.monotonic() + 1)

    def test_dns_timeout_returns_without_waiting_for_system_resolver(self):
        def slow(*args, **kwargs):
            time.sleep(.15)
            return [address()]
        start = time.monotonic()
        with patch.object(net.socket, 'getaddrinfo', side_effect=slow), self.assertRaises(net.NetworkError):
            net.resolve_public('example.com', time.monotonic() + .01)
        self.assertLess(time.monotonic() - start, .1)

    def test_ip_pinning_single_resolution_and_tls_original_hostname(self):
        sock = FakeSocket(response(b'hello', headers=b'Content-Length: 5\r\n'))
        context = Mock()
        context.wrap_socket.return_value = sock
        with patch.object(net.socket, 'getaddrinfo', side_effect=[[address()], [address('127.0.0.1')]]) as dns, patch.object(net.socket, 'socket', return_value=sock), patch.object(net.ssl, 'create_default_context', return_value=context):
            result = net.request('https://example.com/path')
        self.assertEqual(1, dns.call_count)
        self.assertEqual([('8.8.8.8', 443)], sock.endpoints)
        context.wrap_socket.assert_called_once_with(sock, server_hostname='example.com')
        self.assertEqual(b'hello', result.body)
        self.assertIn(b'Host: example.com\r\n', sock.sent)
        self.assertIn(b'Accept-Encoding: gzip, deflate, identity', sock.sent)
        self.assertTrue(sock.closed)

    def test_actual_tls_context_requires_certificate_and_hostname(self):
        context = ssl.create_default_context()
        self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)
        self.assertTrue(context.check_hostname)

    def test_certificate_error_and_peer_mismatch_stop(self):
        sock = FakeSocket(response())
        context = Mock()
        context.wrap_socket.side_effect = ssl.SSLCertVerificationError('bad certificate')
        with patch.object(net, 'resolve_public', return_value=[(socket.AF_INET, ('8.8.8.8', 443))]), patch.object(net.socket, 'socket', return_value=sock), patch.object(net.ssl, 'create_default_context', return_value=context), self.assertRaisesRegex(net.NetworkError, '证书'):
            net.request('https://example.com/')
        self.assertTrue(sock.closed)
        sock = FakeSocket(response(), peer='1.1.1.1')
        with patch.object(net, 'resolve_public', return_value=[(socket.AF_INET, ('8.8.8.8', 443))]), patch.object(net.socket, 'socket', return_value=sock), self.assertRaisesRegex(net.NetworkError, '实际地址'):
            net.request('https://example.com/')
        self.assertTrue(sock.closed)

    def test_redirect_to_private_literal_is_rejected_before_connect(self):
        first = net.Response('https://example.com/', 302, {'location': 'https://127.0.0.1/admin'}, b'')
        with patch.object(net, '_one', return_value=first) as one, self.assertRaises(net.NetworkError):
            net.request('https://example.com/')
        self.assertEqual(1, one.call_count)

    def test_each_redirect_revalidates_and_pins_new_dns(self):
        first = FakeSocket(response(status='302 Found', headers=b'Location: https://second.example.com/\r\n'))
        context = Mock(); context.wrap_socket.return_value = first
        with patch.object(net.socket, 'getaddrinfo', side_effect=[[address()], [address('10.1.2.3')]]) as dns, patch.object(net.socket, 'socket', return_value=first) as sockets, patch.object(net.ssl, 'create_default_context', return_value=context), self.assertRaisesRegex(net.NetworkError, '非公网'):
            net.request('https://example.com/')
        self.assertEqual(2, dns.call_count)
        self.assertEqual(1, sockets.call_count)

    def test_redirect_budget_loop_and_api_host_lock(self):
        def next_hop(url, *args): return net.Response(url, 302, {'location': url + 'a'}, b'')
        with patch.object(net, '_one', side_effect=next_hop) as one, self.assertRaises(net.NetworkError): net.request('https://example.com/')
        self.assertEqual(4, one.call_count)
        with patch.object(net, '_one', return_value=net.Response('https://example.com/', 302, {'location': 'https://example.com/'}, b'')), self.assertRaisesRegex(net.NetworkError, '循环'): net.request('https://example.com/')
        with patch.object(net, '_one', return_value=net.Response('https://example.com/', 302, {'location': 'https://another.example.com/'}, b'')), self.assertRaisesRegex(net.NetworkError, '未授权'): net.request('https://example.com/', allowed_hosts={'example.com'})

    def fake_body(self, raw, **kwargs):
        sock = FakeSocket(raw); context = Mock(); context.wrap_socket.return_value = sock
        with patch.object(net, 'resolve_public', return_value=[(socket.AF_INET, ('8.8.8.8', 443))]), patch.object(net.socket, 'socket', return_value=sock), patch.object(net.ssl, 'create_default_context', return_value=context):
            return net.request('https://example.com/', **kwargs)

    def test_body_limit_declared_and_streamed(self):
        for raw in (response(b'a', headers=b'Content-Length: 1025\r\n'), response(b'a' * 1025)):
            with self.assertRaisesRegex(net.NetworkError, '大小'):
                self.fake_body(raw, max_bytes=1024)

    def test_small_gzip_response_is_decompressed_with_both_limits(self):
        body = gzip.compress(b'hello world')
        value = self.fake_body(response(body, headers=b'Content-Encoding: gzip\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n'))
        self.assertEqual(b'hello world', value.body)

    def test_compression_bomb_is_stopped_at_decoded_limit(self):
        body = gzip.compress(b'x' * 100000)
        self.assertLess(len(body), 1024)
        with self.assertRaisesRegex(net.NetworkError, '解压后'):
            self.fake_body(response(body, headers=b'Content-Encoding: gzip\r\n'), max_bytes=1024)

    def test_invalid_truncated_multi_member_and_unknown_compression_reject(self):
        for raw in (b'invalid', gzip.compress(b'hello')[:-2], gzip.compress(b'a') + gzip.compress(b'b')):
            with self.assertRaises(net.NetworkError): self.fake_body(response(raw, headers=b'Content-Encoding: gzip\r\n'))
        with self.assertRaises(net.NetworkError): self.fake_body(response(b'br', headers=b'Content-Encoding: br\r\n'))

    def test_ambiguous_length_huge_headers_and_incomplete_body(self):
        for raw in (response(b'0\r\n\r\n', headers=b'Content-Length: 0\r\nTransfer-Encoding: chunked\r\n'), response(headers=b'X-Large: ' + b'a' * 9000 + b'\r\n'), response(b'abc', headers=b'Content-Length: 10\r\n')):
            with self.assertRaises(net.NetworkError): self.fake_body(raw)

    def test_chunked_body_and_public_json_error(self):
        value = self.fake_body(response(b'4\r\nWiki\r\n0\r\n\r\n', headers=b'Transfer-Encoding: chunked\r\n'))
        self.assertEqual(b'Wiki', value.body)
        for raw in (b'not json', b'NaN'):
            with self.assertRaises(net.NetworkError): net.Response('https://example.com/', 200, {}, raw).json()

    def test_status_429_is_explicit(self):
        with self.assertRaisesRegex(net.NetworkError, '429'):
            self.fake_body(response(status='429 Too Many Requests'))

    def test_fakeip_only_uses_trusted_fallback(self):
        expected = [(socket.AF_INET, ('1.1.1.1', 443))]
        with patch.object(net.socket, 'getaddrinfo', return_value=[address('198.18.1.230'), address('198.19.1.5')]), patch.object(net, '_trusted_doh', return_value=expected) as doh:
            self.assertEqual(expected, net.resolve_public('example.com', 9999999999))
        doh.assert_called_once_with('example.com', 9999999999)

    def test_real_private_and_mixed_answers_never_trigger_fallback(self):
        for rows in ([address('10.0.0.1')], [address('198.18.1.2'), address('10.0.0.1')], [address('198.18.1.2'), address('8.8.8.8')]):
            with patch.object(net.socket, 'getaddrinfo', return_value=rows), patch.object(net, '_trusted_doh') as doh, self.assertRaises(net.NetworkError):
                net.resolve_public('example.com', time.monotonic() + 1)
            doh.assert_not_called()

    def doh_response(self, record_type, addresses=None, **overrides):
        values = addresses if addresses is not None else ['1.1.1.1'] if record_type == 1 else ['2606:4700:4700::1111']
        body = dict(Status=0, TC=False, Question=[dict(name='example.com.', type=record_type)], Answer=[dict(name='example.com.', type=record_type, TTL=300, data=value) for value in values])
        body.update(overrides)
        return net.Response('https://dns.google/resolve', 200, {}, json.dumps(body).encode())

    def test_doh_a_and_aaaa_share_deadline_and_disable_client_subnet(self):
        deadline = time.monotonic() + 1
        with patch.object(net, '_one', side_effect=[self.doh_response(1), self.doh_response(28)]) as one:
            endpoints = net._trusted_doh('example.com', deadline)
        self.assertEqual(2, len(endpoints))
        self.assertEqual(2, one.call_count)
        for call in one.call_args_list:
            self.assertTrue(call.kwargs['_bootstrap'])
            self.assertEqual(deadline, call.args[3])
            self.assertEqual(65536, call.args[4])
            self.assertIn('edns_client_subnet=0.0.0.0%2F0', call.args[0])
            self.assertIn('https://dns.google/resolve?', call.args[0])

    def test_doh_private_mixed_and_mapped_answers_are_rejected(self):
        for item in (self.doh_response(1, ['10.0.0.1']), self.doh_response(1, ['1.1.1.1', '127.0.0.1']), self.doh_response(1, ['198.18.1.1']), self.doh_response(1, ['::ffff:1.1.1.1'])):
            with patch.object(net, '_one', return_value=item), self.assertRaises(net.NetworkError): net._trusted_doh('example.com', time.monotonic() + 1)

    def test_doh_bad_status_truncation_question_or_records_are_rejected(self):
        for item in (self.doh_response(1, Status=2), self.doh_response(1, TC=True), self.doh_response(1, Question=[dict(name='wrong.example.com.', type=1)]), self.doh_response(1, Answer=[dict(name='example.com.', type=1, TTL=-1, data='1.1.1.1')]), self.doh_response(1, Answer=[dict(name='unrelated.example.com.', type=1, TTL=3, data='1.1.1.1')])):
            with patch.object(net, '_one', return_value=item), self.assertRaises(net.NetworkError): net._trusted_doh('example.com', time.monotonic() + 1)

    def test_doh_cname_chain_allows_only_related_public_records(self):
        first = self.doh_response(1, Answer=[dict(name='example.com.', type=5, TTL=300, data='cdn.example.net.'), dict(name='cdn.example.net.', type=1, TTL=300, data='1.1.1.1')])
        with patch.object(net, '_one', side_effect=[first, self.doh_response(28, [])]):
            self.assertEqual([(socket.AF_INET, ('1.1.1.1', 443))], net._trusted_doh('example.com', time.monotonic() + 1))

    def test_doh_absolute_budget_is_not_reset_for_aaaa(self):
        with patch.object(net, '_one', return_value=self.doh_response(1)) as one, patch.object(net.time, 'monotonic', side_effect=[0, 2]), self.assertRaises(net.NetworkError):
            net._trusted_doh('example.com', 1)
        self.assertEqual(1, one.call_count)

    def test_bootstrap_pins_google_ip_sni_and_never_resolves_dns(self):
        sock = FakeSocket(response(b'{}', headers=b'Content-Length: 2\r\n'))
        context = Mock(); context.wrap_socket.return_value = sock
        with patch.object(net, 'resolve_public', side_effect=AssertionError('must not recurse')), patch.object(net.socket, 'socket', return_value=sock), patch.object(net.ssl, 'create_default_context', return_value=context):
            net._one('https://dns.google/resolve?name=example.com&type=1', 'GET', None, time.monotonic() + 1, 65536, _bootstrap=True)
        self.assertEqual([('8.8.8.8', 443)], sock.endpoints)
        context.wrap_socket.assert_called_once_with(sock, server_hostname='dns.google')
        self.assertIn(b'Host: dns.google\r\n', sock.sent)
        for bad_url in ('https://example.com/resolve', 'http://dns.google/resolve', 'https://dns.google/arbitrary'):
            with self.assertRaises(net.NetworkError): net._one(bad_url, 'GET', None, time.monotonic() + 1, 65536, _bootstrap=True)


if __name__ == '__main__': unittest.main()
