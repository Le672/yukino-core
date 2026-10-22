"""Original bounded public-HTTPS client with DNS/IP pinning and TLS hostname checks.

No urllib connection, ambient proxy, cookie jar or unbounded decompression is
used. DNS is validated in full and the selected numeric address is used directly
for connect; TLS SNI/certificate verification keeps the original hostname.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import queue
import re
import socket
import ssl
import threading
import time
import zlib
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

MAX_BYTES = 1_048_576
_DNS_SLOTS = threading.BoundedSemaphore(4)
_FAKE_IP = ipaddress.ip_network('198.18.0.0/15')


class NetworkError(ValueError):
    pass


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise NetworkError('网络请求达到总耗时上限。')
    return value


def public_ip(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not address.is_global or any((address.is_private, address.is_loopback, address.is_link_local, address.is_multicast, address.is_reserved, address.is_unspecified)):
        return False
    if isinstance(address, ipaddress.IPv6Address):
        # Transition mechanisms can embed a non-public endpoint in a global
        # IPv6-looking value. Refuse them rather than infer local routing.
        if address.ipv4_mapped is not None or address.sixtofour is not None or address.teredo is not None:
            return False
    return True


def canonical_url(url):
    if not isinstance(url, str) or not 1 <= len(url) <= 4096 or re.search(r'[\s\x00-\x20\x7f\\]', url):
        raise NetworkError('URL 长度无效或包含空白、控制字符、反斜线。')
    try:
        parsed = urlsplit(url)
        host, port = parsed.hostname, parsed.port
        if parsed.scheme.lower() != 'https' or not host or port not in (None, 443) or parsed.username is not None or parsed.password is not None:
            raise NetworkError('只允许无用户名密码、标准443端口的 HTTPS URL。')
        if '%' in host or host.endswith('.'):
            raise NetworkError('不支持带区域标识、编码字符或尾随点的主机名。')
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            if not public_ip(str(literal)):
                raise NetworkError('拒绝非公网 IP 地址。')
            host = str(literal)
        else:
            host = host.encode('idna').decode('ascii').lower()
            labels = host.split('.')
            if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal', '.lan', '.home', '.test', '.invalid', '.onion')) or len(labels) < 2 or len(host) > 253:
                raise NetworkError('拒绝本地、保留或无完整域名的主机。')
            if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels):
                raise NetworkError('主机名格式无效。')
        authority = '[' + host + ']' if ':' in host else host
        path = quote(parsed.path or '/', safe="/%:@!$&'()*+,;=-._~")
        query = quote(parsed.query, safe="%/?@:!$&'()*+,;=-._~")
        return urlunsplit(('https', authority, path, query, ''))
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, NetworkError):
            raise
        raise NetworkError('URL 或主机名格式无效。') from None


def resolve_public(host, deadline):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if not public_ip(str(address)):
            raise NetworkError('拒绝非公网 IP。')
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        return [(family, (str(address), 443, 0, 0) if address.version == 6 else (str(address), 443))]
    if not _DNS_SLOTS.acquire(blocking=False):
        raise NetworkError('DNS 查询暂忙，请稍后重试。')
    result = queue.Queue(maxsize=1)

    def lookup():
        try:
            result.put((True, socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)))
        except OSError:
            result.put((False, None))
        finally:
            _DNS_SLOTS.release()

    thread = threading.Thread(target=lookup, name='yukino-public-dns', daemon=True)
    thread.start()
    try:
        succeeded, records = result.get(timeout=remaining(deadline))
    except queue.Empty:
        raise NetworkError('DNS 查询超时。') from None
    if not succeeded or not records:
        raise NetworkError('域名无法解析。')
    if len(records) > 32:
        raise NetworkError('DNS 返回地址过多。')
    # Fake-IP DNS used by some desktop proxies is not a routable destination.
    # Fall back only when *every* system result is in that one reserved block.
    # A real private address, a mixed public/Fake-IP answer, or DNS failure never
    # enables this path. The fallback itself has a fixed bootstrap IP and SNI.
    try:
        fake_only = all(family == socket.AF_INET and ipaddress.ip_address(sockaddr[0]) in _FAKE_IP for family, socktype, protocol, canonname, sockaddr in records)
    except ValueError:
        fake_only = False
    if fake_only:
        return _trusted_doh(host, deadline)
    unique = []
    for family, socktype, protocol, canonname, sockaddr in records:
        if family not in (socket.AF_INET, socket.AF_INET6) or not public_ip(sockaddr[0]):
            raise NetworkError('DNS 含非公网地址，已拒绝整个请求。')
        endpoint = (family, sockaddr)
        if endpoint not in unique:
            unique.append(endpoint)
    remaining(deadline)
    return unique


def _dns_name(value):
    if not isinstance(value, str) or len(value) > 254:
        raise NetworkError('可信DoH响应中的域名无效。')
    name = (value[:-1] if value.endswith('.') else value).lower()
    try:
        result = canonical_url('https://' + name + '/')
    except NetworkError:
        raise NetworkError('可信DoH响应中的域名无效。') from None
    if urlsplit(result).hostname != name:
        raise NetworkError('可信DoH响应的域名不规范。')
    return name


def _trusted_doh(host, deadline):
    """Google JSON DoH, fixed 8.8.8.8 bootstrap; never uses system DNS.

    Google receives only the requested hostname and record type. EDNS client
    subnet is disabled. A+AAAA share the caller's original absolute deadline.
    """
    endpoints = []
    for wanted in (1, 28):
        remaining(deadline)
        url = 'https://dns.google/resolve?name=' + quote(host, safe='') + f'&type={wanted}&edns_client_subnet=0.0.0.0%2F0&cd=0'
        result = _one(url, 'GET', None, deadline, 65536, _bootstrap=True)
        if result.status != 200:
            raise NetworkError('可信DoH返回非200状态；未尝试连接Fake-IP。')
        data = result.json()
        if not isinstance(data, dict) or type(data.get('Status')) is not int or data['Status'] != 0 or data.get('TC') is not False:
            raise NetworkError('可信DoH失败、DNS状态无效或响应被截断。')
        questions = data.get('Question')
        if (not isinstance(questions, list) or len(questions) != 1 or not isinstance(questions[0], dict)
                or type(questions[0].get('type')) is not int or questions[0]['type'] != wanted
                or _dns_name(questions[0].get('name')) != host):
            raise NetworkError('可信DoH返回了不匹配的查询。')
        answers = data.get('Answer', [])
        if not isinstance(answers, list) or len(answers) > 64:
            raise NetworkError('可信DoH答案条数无效。')
        aliases, addresses = {}, []
        for record in answers:
            if not isinstance(record, dict) or type(record.get('type')) is not int or type(record.get('TTL')) is not int or not 0 <= record['TTL'] <= 2 ** 31 - 1:
                raise NetworkError('可信DoH资源记录格式无效。')
            owner = _dns_name(record.get('name'))
            rtype = record['type']
            if rtype == 5:
                target = _dns_name(record.get('data'))
                if owner in aliases and aliases[owner] != target:
                    raise NetworkError('可信DoH出现冲突CNAME。')
                aliases[owner] = target
            elif rtype in (1, 28):
                raw = record.get('data')
                try:
                    value = ipaddress.ip_address(raw) if isinstance(raw, str) else None
                except ValueError:
                    value = None
                if value is None or value.version != (4 if rtype == 1 else 6) or not public_ip(str(value)):
                    raise NetworkError('可信DoH含非公网或无效IP，已拒绝。')
                addresses.append((owner, rtype, value))
        reachable, cursor = {host}, host
        for _ in range(16):
            if cursor not in aliases:
                break
            cursor = aliases[cursor]
            if cursor in reachable:
                raise NetworkError('可信DoH出现CNAME循环。')
            reachable.add(cursor)
        else:
            raise NetworkError('可信DoH的CNAME链过长。')
        for owner, rtype, value in addresses:
            if owner not in reachable:
                raise NetworkError('可信DoH返回与查询无关的IP记录。')
            if rtype == wanted:
                endpoint = (socket.AF_INET, (str(value), 443)) if value.version == 4 else (socket.AF_INET6, (str(value), 443, 0, 0))
                if endpoint not in endpoints:
                    endpoints.append(endpoint)
    if not endpoints or len(endpoints) > 32:
        raise NetworkError('可信DoH没有返回有效的公开地址。')
    remaining(deadline)
    return endpoints


def check_target(url, timeout=8):
    url = canonical_url(url)
    resolve_public(urlsplit(url).hostname, time.monotonic() + min(max(float(timeout), .2), 15))
    return url


class Response:
    def __init__(self, url, status, headers, body):
        self.url, self.status, self.headers, self.body = url, status, headers, body

    def text(self):
        content_type = self.headers.get('content-type', '')
        match = re.search(r'charset\s*=\s*["\']?([a-zA-Z0-9_-]{1,40})', content_type, re.I)
        encoding = match.group(1) if match else 'utf-8-sig'
        try:
            return self.body.decode(encoding, errors='replace')
        except LookupError:
            return self.body.decode('utf-8-sig', errors='replace')

    def json(self):
        try:
            return json.loads(self.body.decode('utf-8-sig'), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError, RecursionError):
            raise NetworkError('远端没有返回有效 JSON。') from None


class _CappedFile:
    def __init__(self, stream):
        self.stream, self.line_bytes = stream, 0

    def readline(self, limit=-1):
        value = self.stream.readline(min(limit, 8193) if limit >= 0 else 8193)
        self.line_bytes += len(value)
        if len(value) > 8192 or self.line_bytes > 32768:
            raise NetworkError('响应头或分块元数据超限。')
        return value

    def __getattr__(self, key):
        return getattr(self.stream, key)


class _HeaderSocket:
    def __init__(self, sock):
        self.sock = sock

    def makefile(self, mode):
        return _CappedFile(self.sock.makefile(mode))


def _one(url, method, data, deadline, byte_limit, _bootstrap=False):
    parsed = urlsplit(url)
    if _bootstrap:
        # This internal-only exception is not an arbitrary caller-supplied pin.
        if parsed.scheme != 'https' or parsed.hostname != 'dns.google' or parsed.path != '/resolve' or parsed.port is not None or method != 'GET' or data is not None or byte_limit > 65536:
            raise NetworkError('无效的可信DoH引导请求。')
        addresses = [(socket.AF_INET, ('8.8.8.8', 443))]
    else:
        addresses = resolve_public(parsed.hostname, deadline)
    context = ssl.create_default_context()
    context.set_alpn_protocols(['http/1.1'])
    connection, timer, holder = None, None, {}
    try:
        for family, endpoint in addresses[:4]:
            raw = socket.socket(family, socket.SOCK_STREAM)
            raw.settimeout(min(remaining(deadline), 4))
            try:
                # endpoint contains the validated numeric address. No DNS call
                # is made between this point and TLS certificate validation.
                raw.connect(endpoint)
                if ipaddress.ip_address(raw.getpeername()[0]) != ipaddress.ip_address(endpoint[0]):
                    raise NetworkError('连接的实际地址与已验证 IP 不一致。')
                raw.settimeout(min(remaining(deadline), 4))
                connection = context.wrap_socket(raw, server_hostname=parsed.hostname)
                break
            except ssl.SSLCertVerificationError:
                raw.close()
                raise NetworkError('TLS 证书或主机名验证失败。') from None
            except NetworkError:
                raw.close()
                raise
            except (OSError, TimeoutError):
                raw.close()
                remaining(deadline)
        if connection is None:
            raise NetworkError('无法连接公开 HTTPS 服务。')
        holder['socket'] = connection

        def expire():
            sock = holder.get('socket')
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass

        timer = threading.Timer(remaining(deadline), expire)
        timer.daemon = True
        timer.start()
        host_header = '[' + parsed.hostname + ']' if ':' in parsed.hostname else parsed.hostname
        target = parsed.path + ('?' + parsed.query if parsed.query else '')
        headers = [f'{method} {target} HTTP/1.1', f'Host: {host_header}', 'User-Agent: YukinoCore/0.5.0 (market2; https://yukino.bond)', 'Accept: application/json, application/rss+xml, application/atom+xml, text/html, */*;q=0.5', 'Accept-Encoding: gzip, deflate, identity', 'Connection: close']
        if data is not None:
            headers += ['Content-Type: application/json; charset=utf-8', f'Content-Length: {len(data)}']
        request = ('\r\n'.join(headers) + '\r\n\r\n').encode('ascii') + (data or b'')
        connection.settimeout(remaining(deadline))
        connection.sendall(request)
        response = http.client.HTTPResponse(_HeaderSocket(connection),method=method)
        try:
            response.begin()
            response_headers = {key.lower(): value for key, value in response.getheaders()}
            if method=='HEAD':return Response(url,response.status,response_headers,b'')
            encoding = response.getheader('Content-Encoding', '').strip().lower()
            if encoding not in ('', 'identity', 'gzip', 'deflate'):
                raise NetworkError('不支持该响应压缩编码；仅允许有界gzip/deflate。')
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == 'gzip' else zlib.MAX_WBITS) if encoding in ('gzip', 'deflate') else None
            raw_length = response.getheader('Content-Length')
            transfer = response.getheader('Transfer-Encoding', '').strip().lower()
            if transfer not in ('', 'chunked') or (transfer and raw_length is not None):
                raise NetworkError('响应传输长度格式不明确。')
            if raw_length is not None:
                try:
                    size = int(raw_length)
                except ValueError:
                    raise NetworkError('响应长度无效。') from None
                if size < 0 or size > byte_limit:
                    raise NetworkError('响应超过最大允许大小。')
            # Redirect bodies are irrelevant. Closing rather than consuming an
            # arbitrarily large redirect stream keeps the same global budget.
            if response.status in (301, 302, 303, 307, 308):
                return Response(url, response.status, response_headers, b'')
            if not 200 <= response.status < 300:
                raise NetworkError(f'远端返回 HTTP {response.status}。')
            chunks, total, decoded_total = [], 0, 0
            while True:
                connection.settimeout(remaining(deadline))
                chunk = response.read(min(65536, byte_limit - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > byte_limit:
                    raise NetworkError('响应超过最大允许大小。')
                if decoder:
                    try:
                        chunk = decoder.decompress(chunk, byte_limit - decoded_total + 1)
                    except zlib.error:
                        raise NetworkError('压缩响应无效或不完整。') from None
                    if decoder.unused_data:
                        raise NetworkError('拒绝多成员或含尾随数据的压缩响应。')
                decoded_total += len(chunk)
                if decoded_total > byte_limit or (decoder is not None and decoder.unconsumed_tail):
                    raise NetworkError('响应解压后超过最大允许大小。')
                chunks.append(chunk)
            if raw_length is not None and total != size:
                raise NetworkError('响应正文长度不完整。')
            if decoder and not decoder.eof:
                raise NetworkError('压缩响应未完整结束。')
            remaining(deadline)
            return Response(url, response.status, response_headers, b''.join(chunks))
        finally:
            response.close()
    except (ssl.SSLError, OSError, http.client.HTTPException, TimeoutError):
        raise NetworkError('HTTPS 连接失败、响应不完整或请求超时。') from None
    finally:
        if timer:
            timer.cancel()
        if connection:
            connection.close()


def request(url, method='GET', json_body=None, timeout=8, max_bytes=MAX_BYTES, max_redirects=3, allowed_hosts=None):
    if method not in ('GET', 'POST','HEAD'):
        raise NetworkError('仅允许 GET/POST/HEAD。')
    limit = min(MAX_BYTES, max(1024, int(max_bytes)))
    deadline = time.monotonic() + min(max(float(timeout), .2), 15)
    current = canonical_url(url)
    allowed = set(allowed_hosts) if allowed_hosts else None
    data = json.dumps(json_body, ensure_ascii=False, allow_nan=False).encode('utf-8') if json_body is not None else None
    if data is not None and len(data) > 16384:
        raise NetworkError('请求内容过长。')
    visited = set()
    for hop in range(min(max(0, int(max_redirects)), 3) + 1):
        remaining(deadline)
        if current in visited:
            raise NetworkError('检测到重定向循环。')
        visited.add(current)
        if allowed is not None and urlsplit(current).hostname not in allowed:
            raise NetworkError('公开 API 重定向到了未授权的主机。')
        result = _one(current, method, data, deadline, limit)
        if result.status not in (301, 302, 303, 307, 308):
            return result
        location = result.headers.get('location')
        if not location or hop >= min(max(0, int(max_redirects)), 3):
            raise NetworkError('重定向次数超限或缺少目标。')
        next_url = canonical_url(urljoin(current, location))
        if method == 'POST' and result.status in (301, 302, 303):
            method, data = 'GET', None
        current = next_url
    raise NetworkError('重定向次数超限。')
