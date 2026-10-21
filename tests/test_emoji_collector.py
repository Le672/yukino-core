"""Sticker collection through real event routing, SQLite and resource storage."""
import asyncio
import io
import json
import socket
import unittest
from unittest import mock

from PIL import Image

import test_core as harness
from yukino_core.emoji_collector import CollectionError, EmojiCollector, PublicResolver, image_segments, media_url
from yukino_core.transport import normalize_event


class EmojiCollectorTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = harness.KernelTests.asyncSetUp
    asyncTearDown = harness.KernelTests.asyncTearDown
    message = harness.KernelTests.message

    def image(self, *, sticker=True, url='https://gchat.qpic.cn/test/image', **data):
        return {'type': 'image', 'data': {'url': url, 'file': 'abc123.png',
                'sub_type': 1 if sticker else 0, 'summary': '[开心]', **data}}

    def event(self, segments, *, user='42', group='777', reply_to=''):
        self.counter += 1
        if reply_to:
            segments = [{'type': 'reply', 'data': {'id': reply_to}}, *segments]
        raw = dict(post_type='message', message_type='group' if group else 'private',
                   self_id='900', user_id=user, message_id=str(self.counter), time=100,
                   sender={'user_id':user}, message=segments)
        if group: raw['group_id'] = group
        return normalize_event(raw)

    def picture_bytes(self, color='red', format='PNG'):
        buffer = io.BytesIO()
        Image.new('RGB', (2, 2), color).save(buffer, format=format)
        return buffer.getvalue()

    async def test_group_collects_without_at_and_reuses_the_real_resource(self):
        payload = self.picture_bytes()
        with mock.patch.object(self.kernel.collector, '_download', new=mock.AsyncMock(return_value=payload)) as download:
            event = self.event([self.image()])
            await self.kernel.on_event(event)
            await self.kernel.collector.queue.join()
        self.assertEqual(download.await_count, 1)
        self.assertEqual(self.provider.prompts, [])
        self.assertEqual(self.transport.sent, [])
        assets = await self.resources.list_items('images', q='聊天收藏')
        self.assertEqual(assets['total'], 1)
        path = await self.resources.image_file(assets['items'][0]['id'])
        self.assertEqual(path.read_bytes(), payload)
        # The normal /表情 command resolves the collected item immediately.
        await self.kernel.on_event(self.message('/表情 开心', group='777'))
        self.assertEqual(self.transport.sent[-1][2][0]['type'], 'image')

    async def test_private_sticker_is_silent_and_does_not_send_an_image_placeholder_to_model(self):
        with mock.patch.object(self.kernel.collector, '_download', new=mock.AsyncMock(return_value=self.picture_bytes())):
            await self.kernel.on_event(self.event([self.image()], group=None))
            await self.kernel.collector.queue.join()
        self.assertEqual(self.provider.prompts, [])
        self.assertEqual(self.transport.sent, [])

    async def test_ordinary_photos_flash_and_blocked_sessions_are_not_auto_collected(self):
        with mock.patch.object(self.kernel.collector, '_download', new=mock.AsyncMock()) as download:
            await self.kernel.on_event(self.event([self.image(sticker=False)]))
            await self.kernel.on_event(self.event([self.image(type='flash')]))
            self.config.data['group_allowlist'] = ['888']
            await self.kernel.on_event(self.event([self.image()]))
            self.config.data['group_allowlist'] = []
            self.config.data['user_blocklist'] = ['42']
            await self.kernel.on_event(self.event([self.image()]))
            await asyncio.sleep(0)
            download.assert_not_awaited()
        self.assertIsNone(self.kernel.collector.worker)

    async def test_receive_disabled_plugin_disabled_and_group_option_prevent_collection(self):
        events = []
        self.config.receive_enabled = False
        events.append(self.kernel.collector.observe(self.event([self.image()])))
        self.config.receive_enabled = True
        self.config.plugin_flags['emoji_collector'] = False
        events.append(self.kernel.collector.observe(self.event([self.image()])))
        self.config.plugin_flags['emoji_collector'] = True
        self.plugins.update_settings('emoji_collector', {'in_groups':False})
        events.append(self.kernel.collector.observe(self.event([self.image()])))
        self.assertEqual(events, [False]*3)

    async def test_inline_manual_collection_accepts_ordinary_image_and_custom_label(self):
        segments = [{'type':'text','data':{'text':'/偷表情 委屈猫'}}, self.image(sticker=False)]
        with mock.patch.object(self.kernel.collector, '_download', new=mock.AsyncMock(return_value=self.picture_bytes())):
            await self.kernel.on_event(self.event(segments))
        self.assertIn('偷到 1 张', self.transport.sent[-1][2][0]['data']['text'])
        self.assertEqual((await self.resources.list_items('images', q='委屈猫'))['total'], 1)
        self.assertEqual(self.provider.prompts, [])

    async def test_manual_quote_uses_only_observed_message_in_same_conversation(self):
        original = self.event([self.image(sticker=False)])
        await self.kernel.on_event(original)
        with mock.patch.object(self.kernel.collector, '_download', new=mock.AsyncMock(return_value=self.picture_bytes())) as download:
            quoted = self.event([{'type':'text','data':{'text':'/偷表情'}}], reply_to=original['message_id'])
            await self.kernel.on_event(quoted)
            self.assertEqual(download.await_count, 1)
            other = self.event([{'type':'text','data':{'text':'/偷表情'}}], group='888', reply_to=original['message_id'])
            await self.kernel.on_event(other)
            self.assertEqual(download.await_count, 1)
            self.assertIn('引用一条', self.transport.sent[-1][2][0]['data']['text'])

    async def test_duplicate_content_and_media_keys_persist_across_collector_restart(self):
        collector = self.kernel.collector
        with mock.patch.object(collector, '_download', new=mock.AsyncMock(return_value=self.picture_bytes())):
            first = await collector.collect([self.image()])
            second = await collector.collect([self.image(url='https://gchat.qpic.cn/other')])
        self.assertEqual(first['saved'], 1)
        self.assertEqual(second['duplicates'], 1)
        await collector.close()
        self.kernel.collector = EmojiCollector(self.kernel)
        with mock.patch.object(self.kernel.collector, '_download', new=mock.AsyncMock()) as download:
            result = await self.kernel.collector.collect([self.image()])
            download.assert_not_awaited()
        self.assertEqual(result['duplicates'], 1)
        self.assertEqual((await self.resources.list_items('images'))['total'], 1)
        record = json.loads(self.kernel.collector.path.read_text(encoding='utf-8'))
        self.assertNotIn('https://', json.dumps(record))

    async def test_daily_limit_stops_further_downloads(self):
        self.plugins.update_settings('emoji_collector', {'daily_limit':1})
        with mock.patch.object(self.kernel.collector, '_download', new=mock.AsyncMock(return_value=self.picture_bytes())) as download:
            first = await self.kernel.collector.collect([self.image()])
            second = await self.kernel.collector.collect([self.image(url='https://gchat.qpic.cn/other')])
        self.assertEqual(first['saved'], 1)
        self.assertTrue(second['limited'])
        self.assertEqual(download.await_count, 1)

    async def test_manual_name_can_be_added_after_automatic_collection(self):
        with mock.patch.object(self.kernel.collector, '_download', new=mock.AsyncMock(return_value=self.picture_bytes())) as download:
            await self.kernel.collector.collect([self.image()])
            await self.kernel.collector.collect([self.image()], '偷来的开心猫')
        self.assertEqual(download.await_count, 1)
        self.assertEqual((await self.resources.list_items('images', q='偷来的开心猫'))['total'], 1)

    async def test_cdn_fileid_is_identity_but_expiring_rkey_is_not(self):
        first = self.image(url='https://multimedia.nt.qq.com.cn/download?fileid=one&rkey=old')
        refreshed = self.image(url='https://multimedia.nt.qq.com.cn/download?fileid=one&rkey=new')
        other = self.image(url='https://multimedia.nt.qq.com.cn/download?fileid=two&rkey=new')
        self.assertEqual(self.kernel.collector._key(first), self.kernel.collector._key(refreshed))
        self.assertNotEqual(self.kernel.collector._key(first), self.kernel.collector._key(other))

    async def test_bad_content_is_not_registered_or_reserved_as_a_resource(self):
        with mock.patch.object(self.kernel.collector, '_download', new=mock.AsyncMock(return_value=b'not-a-picture')):
            result = await self.kernel.collector.collect([self.image()])
        self.assertEqual(result['failed'], 1)
        self.assertEqual(self.kernel.collector.state['items'], {})
        self.assertEqual((await self.resources.list_items('images'))['total'], 0)

    async def test_only_owner_can_change_auto_mode_and_manual_still_works_when_off(self):
        await self.kernel.on_event(self.message('/偷表情 关闭', user='49', group='777'))
        self.assertTrue(self.kernel.collector.settings()['auto_collect'])
        await self.kernel.on_event(self.message('/偷表情 关闭', group='777'))
        self.assertFalse(self.kernel.collector.settings()['auto_collect'])
        with mock.patch.object(self.kernel.collector, '_download', new=mock.AsyncMock(return_value=self.picture_bytes())):
            await self.kernel.on_event(self.event([{'type':'text','data':{'text':'/偷表情包'}},self.image()]))
        self.assertIn('偷到 1 张', self.transport.sent[-1][2][0]['data']['text'])

    async def test_oversize_metadata_rejected_before_download_or_adapter_call(self):
        with mock.patch.object(self.kernel.collector, '_read_url', new=mock.AsyncMock()) as fetch:
            with self.assertRaises(CollectionError):
                await self.kernel.collector._download(self.image(file_size=6_000_000), 5_000_000)
            fetch.assert_not_awaited()

    async def test_market_sticker_without_subtype_is_recognized(self):
        item = self.image(sticker=False, emoji_id='a'*32)
        self.assertEqual(image_segments([item], stickers_only=True), [item])
        with mock.patch.object(self.kernel.collector, '_read_url', new=mock.AsyncMock(return_value=self.picture_bytes())) as fetch:
            await self.kernel.collector._download({'type':'mface','data':{'emoji_id':'a'*32}}, 5_000_000)
        self.assertIn('/aa/'+'a'*32+'/raw300.gif', fetch.call_args.args[0])

    async def test_unsafe_url_does_not_fall_back_to_adapter_open(self):
        self.transport.call = mock.AsyncMock()
        with self.assertRaises(CollectionError):
            await self.kernel.collector._download(self.image(url='http://127.0.0.1/secret'), 5_000_000)
        self.transport.call.assert_not_awaited()

    async def test_expired_cdn_url_can_refresh_through_get_image(self):
        self.transport.call = mock.AsyncMock(return_value={'url':'https://gchat.qpic.cn/refreshed'})
        payload = self.picture_bytes()
        with mock.patch.object(self.kernel.collector, '_read_url', new=mock.AsyncMock(side_effect=[CollectionError('expired'),payload])):
            result = await self.kernel.collector._download(self.image(),5_000_000)
        self.assertEqual(result,payload)
        self.transport.call.assert_awaited_once_with('get_image', {'file':'abc123.png'}, timeout=10)


class MediaBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_expected_qq_https_hosts(self):
        self.assertEqual(media_url('http://gchat.qpic.cn/a?rkey=abc'), 'https://gchat.qpic.cn/a?rkey=abc')
        for value in ('https://qpic.cn.evil.example/a','http://127.0.0.1/a','file:///etc/passwd',
                      'https://gchat.qpic.cn:443/a','https://user:pw@gchat.qpic.cn/a',
                      'https://gchat.qpic.cn/a#fragment','https://gchat.qpic.cn/a\nHeader:x'):
            with self.subTest(value=value), self.assertRaises(CollectionError): media_url(value)

    async def test_dns_resolution_rejects_private_or_mixed_answers(self):
        loop = asyncio.get_running_loop()
        for address in ('127.0.0.1','10.0.0.1','169.254.169.254','::1'):
            answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address,443))]
            with mock.patch.object(loop,'getaddrinfo',new=mock.AsyncMock(return_value=answer)):
                with self.assertRaises(OSError): await PublicResolver().resolve('gchat.qpic.cn',443)
        answer = [(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))]
        with mock.patch.object(loop,'getaddrinfo',new=mock.AsyncMock(return_value=answer)):
            self.assertEqual((await PublicResolver().resolve('gchat.qpic.cn',443))[0]['host'],'8.8.8.8')


if __name__ == '__main__':
    unittest.main()
