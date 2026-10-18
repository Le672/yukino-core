"""Small fixed-origin HTTP client. No credentials, redirects, retries or disk writes."""
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 2 * 1024 * 1024
CACHE_BUDGET = 2 * 1024 * 1024
ALLOWED = frozenset({'geocoding-api.open-meteo.com', 'api.open-meteo.com',
    'store.steampowered.com', 'api.bilibili.com', 'tieba.baidu.com',
    'music.163.com', 'api.mcsrvstat.us', 'api.live.bilibili.com'})
_CACHE = {}
_DEADLINE = None

def begin():
    global _DEADLINE
    _DEADLINE = time.monotonic() + 14

def store(key, expires, success, value, size):
    # A byte budget complements the item limit; large JSON lists cannot fill 64 slots.
    while _CACHE and (len(_CACHE) >= 64 or sum(item[3] for item in _CACHE.values()) + size > CACHE_BUDGET):
        _CACHE.pop(next(iter(_CACHE)))
    if size <= CACHE_BUDGET:
        _CACHE[key] = (expires, success, value, size)

class LookupFailure(Exception):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LookupFailure('数据源要求跳转，已停止查询')

def clean(value, maximum=160):
    value = html.unescape(re.sub(r'<[^>]*>', '', str(value)))
    return ' '.join(re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', value).split())[:maximum]

def argument(text, commands, maximum=100):
    if not isinstance(text, str):
        return None
    parts = text.strip().split(maxsplit=1)
    if not parts or parts[0] not in commands:
        return None
    value = parts[1].strip() if len(parts) > 1 else ''
    if len(value) > maximum or re.search(r'[\x00-\x1f\x7f-\x9f]', value):
        raise LookupFailure('查询内容过长或包含控制字符')
    return value

def settings(context):
    value = context.get('settings', {}) if isinstance(context, dict) else {}
    return value if isinstance(value, dict) else {}

def number(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (ValueError, TypeError, OverflowError):
        return default

def fetch(url, params=None, data=None, ttl=120):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in ALLOWED or parsed.port is not None
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise LookupFailure('数据源地址无效')
    query = urllib.parse.urlencode(params or {})
    target = url + ('?' + query if query else '')
    body = urllib.parse.urlencode(data).encode('utf-8') if data is not None else None
    key = (target, body)
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and cached[0] > now:
        if cached[1] is False:
            raise LookupFailure(cached[2])
        return cached[2]
    headers = {'User-Agent': 'YukinoCore-CommunityPlugin/1.0 (public read-only lookup)',
        'Accept': 'application/json', 'Accept-Encoding': 'identity'}
    if parsed.hostname == 'music.163.com':
        headers['Referer'] = 'https://music.163.com/'
    if body is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    try:
        remaining = 5 if _DEADLINE is None else min(5, _DEADLINE - time.monotonic())
        if remaining <= 0:
            raise LookupFailure('单次查询时间预算已用尽')
        request = urllib.request.Request(target, data=body, headers=headers)
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=remaining) as response:
            length = response.headers.get('Content-Length')
            if length and int(length) > MAX_BYTES:
                raise LookupFailure('数据源返回内容过大')
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise LookupFailure('数据源返回内容过大')
            value = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(value, dict):
                raise LookupFailure('数据源返回格式异常')
    except urllib.error.HTTPError as exc:
        error = '数据源暂不可用（HTTP %s）' % exc.code
        exc.close()
    except LookupFailure as exc:
        error = str(exc)
    except (OSError, ValueError, TypeError, TimeoutError, urllib.error.URLError):
        error = '数据源连接超时、网络不可用或返回格式异常'
    else:
        store(key, now + number(ttl, 120, 30, 300), True, value, len(raw))
        return value
    store(key, now + 30, False, error, len(error.encode('utf-8')))
    raise LookupFailure(error)

def failure(name, exc):
    # Never reflect exception URLs, request headers or upstream error bodies.
    return name + '查询失败：' + (str(exc) if isinstance(exc, LookupFailure) else '数据源返回格式异常') + '。请稍后再试；本次不会转交模型编造结果。'

def local_time():
    return time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())
