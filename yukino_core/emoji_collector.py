"""Collect received QQ stickers into the existing editable resource library."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import ipaddress
import json
import re
import socket
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import aiohttp

from .config import atomic_json
from .resources import MAX_IMAGE_BYTES

PLUGIN_ID = 'emoji_collector'
_FILE = re.compile(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,159}')
_MARKET_ID = re.compile(r'[a-fA-F0-9]{32}')


class CollectionError(ValueError):
    pass


def media_url(value):
    """Only QQ image CDNs, HTTPS, no redirects or local-file references."""
    if not isinstance(value, str) or not 1 <= len(value) <= 8192:
        raise CollectionError('图片地址不可用')
    try:
        url = urlsplit(value)
        host = (url.hostname or '').lower()
        allowed = (host == 'qpic.cn' or host.endswith('.qpic.cn')
                   or host in {'multimedia.nt.qq.com.cn', 'gxh.vip.qq.com'})
        if (not allowed or url.scheme not in {'http', 'https'} or url.username or url.password
                or url.port is not None or url.fragment or '\\' in value
                or any(ord(c) < 33 for c in value)):
            raise ValueError()
        return urlunsplit(('https', host, url.path or '/', url.query, ''))
    except ValueError:
        raise CollectionError('只支持QQ图片服务器的有效地址') from None


class PublicResolver(aiohttp.abc.AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        answers = await asyncio.get_running_loop().getaddrinfo(
            host, port, family=family, type=socket.SOCK_STREAM)
        result = []
        for kind, _, proto, _, address in answers:
            ip = address[0]
            if not ipaddress.ip_address(ip).is_global:
                raise OSError('QQ图片地址解析到了非公网网络')
            result.append(dict(hostname=host, host=ip, port=port, family=kind,
                               proto=proto, flags=socket.AI_NUMERICHOST))
        if not result:
            raise OSError('QQ图片地址无法解析')
        return result

    async def close(self):
        pass


def image_segments(segments, stickers_only=False, limit=3):
    result = []
    for segment in segments[:100] if isinstance(segments, list) else []:
        if not isinstance(segment, dict) or segment.get('type') not in {'image', 'mface'}:
            continue
        data = segment.get('data', {})
        if not isinstance(data, dict) or data.get('type') == 'flash':
            continue
        sticker = (segment['type'] == 'mface' or str(data.get('sub_type')) == '1'
                   or bool(_MARKET_ID.fullmatch(str(data.get('emoji_id', '')))))
        if stickers_only and not sticker:
            continue
        result.append({'type': segment['type'], 'data': dict(data)})
        if len(result) >= limit:
            break
    return result


class EmojiCollector:
    def __init__(self, kernel):
        self.kernel = kernel
        self.queue = asyncio.Queue(maxsize=12)
        self.worker = None
        self.http = None
        self.lock = asyncio.Lock()
        self.state = None
        self.path = None
        self.last_queued = {}
        self.metrics = dict(saved=0, duplicates=0, failures=0, skipped=0)

    def settings(self):
        return self.kernel.plugins.settings(PLUGIN_ID)

    def allowed(self, event):
        cfg = self.kernel.config.data
        if (not self.kernel.plugins.enabled(PLUGIN_ID)
                or not getattr(self.kernel.config, 'receive_enabled', True)
                or event.get('post_type') == 'notice'
                or event.get('user_id') == self.kernel.config.self_id
                or event.get('user_id') in cfg['user_blocklist']
                or (cfg['ignore_bots'] and event.get('is_bot'))):
            return False
        opts = self.settings()
        if event.get('message_type') == 'private':
            return (cfg['private_enabled'] and opts['in_private']
                    and (not cfg['private_allowlist'] or event['user_id'] in cfg['private_allowlist']))
        return (event.get('message_type') == 'group' and cfg['group_enabled'] and opts['in_groups']
                and event['group_id'] not in cfg['group_blocklist']
                and (not cfg['group_allowlist'] or event['group_id'] in cfg['group_allowlist']))

    def observe(self, event):
        if not self.allowed(event) or not self.settings()['auto_collect']:
            return False
        item, _ = self.kernel.plugins.match(event['text'])
        if item and item['id'] == PLUGIN_ID:
            return False
        opts = self.settings()
        images = image_segments(event.get('segments', []), opts['stickers_only'], opts['per_message_limit'])
        if not images:
            return False
        now = time.monotonic()
        sid = event['session_id']
        if (self.queue.full() or now - self.last_queued.get(sid, -float('inf')) < opts['cooldown_seconds']):
            self.metrics['skipped'] += 1
            return False
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self._run(), name='emoji-collector')
        self.last_queued[sid] = now
        # Bound the cooldown cache even when a public instance sees many peers.
        if len(self.last_queued) > 2000:
            self.last_queued = {sid: now}
        self.queue.put_nowait((event, images))
        return True

    async def _run(self):
        while True:
            event, images = await self.queue.get()
            try:
                if self.allowed(event) and self.settings()['auto_collect']:
                    result = await self.collect(images)
                    if result['failed']:
                        self.kernel.plugins.errors[PLUGIN_ID] = '部分表情下载失败，可重新发送后手动收藏'
                    else:
                        self.kernel.plugins.errors.pop(PLUGIN_ID, None)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.metrics['failures'] += 1
                self.kernel.plugins.errors[PLUGIN_ID] = '表情收集失败，可检查收集上限、图片有效期与网络'
            finally:
                self.queue.task_done()

    async def _load(self):
        if self.state is not None:
            return
        directory = self.kernel.plugins._state_directory(PLUGIN_ID)
        self.path = directory / 'collection.json'
        if self.path.is_symlink():
            raise CollectionError('收集记录路径无效')
        def read():
            if not self.path.exists():
                return {'day': '', 'attempts': 0, 'items': {}, 'seen': {}}
            if self.path.stat().st_size > 2_000_000:
                raise CollectionError('收集记录过大')
            return json.loads(self.path.read_text(encoding='utf-8'))
        state = await asyncio.to_thread(read)
        if (not isinstance(state, dict) or not isinstance(state.get('day'), str)
                or type(state.get('attempts')) is not int or not 0 <= state['attempts'] <= 10000
                or not isinstance(state.get('items'), dict) or len(state['items']) > 10000
                or any(not re.fullmatch(r'img_[a-f0-9]{24}', key) or type(size) is not int
                       or not 0 < size <= MAX_IMAGE_BYTES for key, size in state['items'].items())
                or not isinstance(state.get('seen'), dict) or len(state['seen']) > 5000
                or any(not re.fullmatch(r'[a-f0-9]{64}', key)
                       or not isinstance(value, str) or not re.fullmatch(r'img_[a-f0-9]{24}', value)
                       for key, value in state['seen'].items())):
            raise CollectionError('收集记录无效，请在维护时检查')
        self.state = state

    async def _save(self):
        await asyncio.to_thread(atomic_json, self.path, self.state)

    async def _read_url(self, url, maximum):
        url = media_url(url)
        if self.http is None or self.http.closed:
            self.http = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(resolver=PublicResolver(), limit=2),
                timeout=aiohttp.ClientTimeout(total=15, sock_connect=5, sock_read=8), trust_env=False)
        async with self.http.get(url, allow_redirects=False) as response:
            if response.status != 200:
                raise CollectionError('QQ图片下载暂时不可用')
            if response.content_length is not None and response.content_length > maximum:
                raise CollectionError('图片超过收集大小上限')
            payload = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                payload.extend(chunk)
                if len(payload) > maximum:
                    raise CollectionError('图片超过收集大小上限')
            return bytes(payload)

    async def _download(self, segment, maximum):
        data = segment['data']
        try:
            size = int(data.get('file_size') or 0)
        except (TypeError, ValueError):
            raise CollectionError('图片大小无效') from None
        if size > maximum or size < 0:
            raise CollectionError('图片超过收集大小上限')
        url = data.get('url') or (data.get('file') if str(data.get('file', '')).startswith(('http://', 'https://')) else '')
        market = str(data.get('emoji_id', ''))
        if not url and _MARKET_ID.fullmatch(market):
            url = f'https://gxh.vip.qq.com/club/item/parcel/item/{market[:2]}/{market}/raw300.gif'
        if url:
            # Validate before the try block: an unsafe URL is never a reason to
            # ask the adapter to open a caller-controlled file or URL instead.
            url = media_url(url)
            try:
                return await self._read_url(url, maximum)
            except (aiohttp.ClientError, TimeoutError, CollectionError):
                pass
        token = data.get('file', '')
        if not isinstance(token, str) or not _FILE.fullmatch(token) or '..' in token:
            raise CollectionError('无法获取图片，请重新发送原图或表情')
        info = await self.kernel.transport.call('get_image', {'file': token}, timeout=10)
        if not isinstance(info, dict):
            raise CollectionError('图片信息无效')
        if info.get('base64'):
            encoded = info['base64']
            if not isinstance(encoded, str) or len(encoded) > ((maximum + 2) // 3) * 4:
                raise CollectionError('图片超过收集大小上限')
            try:
                result = base64.b64decode(encoded, validate=True)
            except ValueError:
                raise CollectionError('图片内容无效') from None
            if not 0 < len(result) <= maximum:
                raise CollectionError('图片大小无效')
            return result
        return await self._read_url(media_url(info.get('url')), maximum)

    @staticmethod
    def _key(segment):
        data = segment['data']
        raw_url = str(data.get('url', ''))[:8192]
        try:
            parsed = urlsplit(raw_url)
            query = sorted((k, v) for k, v in parse_qsl(parsed.query, max_num_fields=50)
                           if k.lower() not in {'rkey', 'expires', 'expire', 't', 'ts', 'token', 'authkey', 'auth_key'})
            url = [parsed.scheme, parsed.netloc, parsed.path, query]
        except ValueError:
            url = raw_url
        value = [str(data.get(key, ''))[:200] for key in ('file', 'emoji_id')]
        value.append(url)
        return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()

    async def collect(self, images, label=''):
        result = dict(saved=0, duplicates=0, failed=0, limited=False)
        async with self.lock:
            await self._load()
            opts = self.settings()
            today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
            if self.state['day'] != today:
                self.state.update(day=today, attempts=0)
            for image in images[:opts['per_message_limit']]:
                key = self._key(image)
                if key in self.state['seen']:
                    if label and self.state['seen'][key] in self.state['items']:
                        await self.kernel.resources.update_item('images', self.state['seen'][key], {'description': label[:120]})
                    result['duplicates'] += 1
                    self.metrics['duplicates'] += 1
                    continue
                if (self.state['attempts'] >= opts['daily_limit']
                        or len(self.state['items']) >= opts['max_collected']
                        or sum(self.state['items'].values()) >= opts['max_total_mb'] * 1_000_000):
                    result['limited'] = True
                    break
                self.state['attempts'] += 1
                await self._save()
                try:
                    payload = await self._download(image, min(opts['max_image_mb'] * 1_000_000, MAX_IMAGE_BYTES))
                    await asyncio.to_thread(self.kernel.resources._upload_format, 'qq-sticker', payload)
                    item_id = 'img_' + hashlib.sha256(payload).hexdigest()[:24]
                    try:
                        await self.kernel.resources.get_item('images', item_id)
                        duplicate = True
                    except KeyError:
                        duplicate = False
                    if duplicate:
                        if label and item_id in self.state['items']:
                            await self.kernel.resources.update_item('images', item_id, {'description': label[:120]})
                        result['duplicates'] += 1
                        self.metrics['duplicates'] += 1
                    else:
                        if sum(self.state['items'].values()) + len(payload) > opts['max_total_mb'] * 1_000_000:
                            result['limited'] = True
                            break
                        summary = str(image['data'].get('summary', '')).strip(' []【】')[:80]
                        description = (label or summary or '聊天收藏表情')[:120]
                        # Reserve storage before committing to the resource
                        # catalogue, so a process interruption cannot reset it.
                        self.state['items'][item_id] = len(payload)
                        await self._save()
                        await self.kernel.resources.upload_image('qq-sticker', payload, description, ['聊天收藏', '表情包'])
                        result['saved'] += 1
                        self.metrics['saved'] += 1
                    self.state['seen'][key] = item_id
                    self.state['seen'] = dict(list(self.state['seen'].items())[-5000:])
                    await self._save()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    result['failed'] += 1
                    self.metrics['failures'] += 1
        return result

    async def handle(self, event):
        if not self.allowed(event):
            return '当前会话没有启用表情收集。'
        text = ''.join(str(s.get('data', {}).get('text', '')) for s in event.get('segments', []) if s.get('type') == 'text').strip()
        arg = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ''
        if arg in {'开启', '关闭'}:
            owners = getattr(self.kernel.config, 'owner_ids', [self.kernel.config.owner_id])
            if event['user_id'] not in owners:
                return '自动收集的开关让主人来改吧。'
            self.kernel.plugins.update_settings(PLUGIN_ID, {'auto_collect': arg == '开启'})
            return '自动偷表情已开启，收集时不会刷屏。' if arg == '开启' else '自动偷表情已关闭，仍可引用图片手动收藏。'
        if arg == '状态':
            async with self.lock:
                await self._load()
                opts = self.settings()
                return (f'自动偷表情：{"开启" if opts["auto_collect"] else "关闭"}\n'
                        f'累计收集/预留：{len(self.state["items"])} 张\n'
                        '引用图片发送 /偷表情，可在命令后加名称；/表情 名称 可以取用。')
        images = image_segments(event.get('segments', []), limit=self.settings()['per_message_limit'])
        if not images and event.get('reply_to'):
            # Only locally observed messages from this conversation can be
            # referenced; a fabricated ID cannot read another private chat.
            quoted = await self.kernel.store.message_event(event['session_id'], event['reply_to'])
            if quoted is not None:
                images = image_segments(quoted.get('segments', []), limit=self.settings()['per_message_limit'])
        if not images:
            return '引用一条图片或表情消息，发送 /偷表情；也可以把图片与命令一起发。加名称可用 /偷表情 开心。'
        result = await self.collect(images, arg[:120])
        if result['limited']:
            return f'已收藏 {result["saved"]} 张；达到收集上限，可在插件面板调整。'
        if result['saved']:
            return f'偷到 {result["saved"]} 张表情，已放进资源库。' + (f'另有 {result["duplicates"]} 张已经收过。' if result['duplicates'] else '')
        if result['duplicates'] and not result['failed']:
            return '这些表情已经在资源库里啦，不会重复存。'
        return '这次没能获取有效图片，请重新发送原图或表情后再试。'

    def snapshot(self):
        return {**self.metrics, 'pending': self.queue.qsize(),
                'auto_collect': self.settings()['auto_collect'],
                'collected': len(self.state['items']) if self.state is not None else None}

    async def close(self):
        if self.worker is not None:
            self.worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.worker
        if self.http is not None:
            await self.http.close()
