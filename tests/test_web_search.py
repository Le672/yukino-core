import asyncio
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from yukino_core.config import DEFAULTS
from yukino_core.public_net import NetworkError, Response
from yukino_core.web_search import WebSearch, _DuckResults, clean_sources, extract_page, query_for


def response(text,url='https://example.com/',kind='text/html'):
    return Response(url,200,{'content-type':kind},text.encode())


class QueryTests(unittest.TestCase):
    def test_public_questions_selected_and_casual_private_messages_skipped(self):
        for text in ('Python现在最新的稳定版本是什么？','上海明天天气怎么样','查一下空间站最近新闻','Ubuntu 24.04支持Docker吗'):
            self.assertIsNotNone(query_for(text),text)
        for text in ('你好','我今天累死了','你现在在干嘛','你是什么模型','我是谁','我好无聊','今天总算忙完了，想找点好玩的'):
            self.assertIsNone(query_for(text),text)
        self.assertIsNone(query_for('Python最新稳定版本是什么',automatic=False))
        self.assertEqual(query_for('帮我查一下 Python 最新版本',automatic=False),'Python 最新版本')

    def test_credentials_contacts_paths_urls_and_identifiers_are_not_searched(self):
        for text in ('查一下邮箱 alice@example.com','查一下 QQ 12345678','查一下 token=private',
                     '查询 Cookie: session=secret','查询 C:\\Users\\me\\notes.txt','查询 /home/user/notes'):
            self.assertIsNone(query_for(text),text)
        query=query_for('查一下 Python https://example.com/?token=secret')
        self.assertNotIn('secret',query or '')
        self.assertNotIn('example.com',query or '')

    def test_followup_uses_only_latest_question_from_same_sender(self):
        previous=[dict(role='user',user_id='one',content='Steam Deck是什么'),dict(role='assistant',content='PRIVATE_ANSWER')]
        self.assertEqual(query_for('那它现在多少钱',previous,'one'),'Steam Deck是什么 那它现在多少钱')
        self.assertEqual(query_for('那它现在多少钱',previous,'two'),'那它现在多少钱')
        previous.append(dict(role='user',user_id='two',content='PERSONAL_GROUP_HISTORY'))
        self.assertEqual(query_for('那它现在多少钱',previous,'one'),'那它现在多少钱')

    def test_html_and_sources_cannot_introduce_internal_targets_or_active_content(self):
        rows=[dict(title='<b>Title</b>',url='https://example.com/a#top',snippet='<script>BAD</script>Good'),
              dict(title='duplicate',url='https://example.com/a'),dict(title='Local',url='https://127.0.0.1/a'),
              dict(title='Credentials',url='https://user:pass@example.com'),dict(title='Token',url='https://example.com/?access_token=secret')]
        self.assertEqual(clean_sources(rows,5),[dict(title='Title',url='https://example.com/a',snippet='Good')])
        parser=_DuckResults();parser.feed('<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">A <b>title</b></a><a class="result__snippet">A snippet</a>')
        self.assertEqual(clean_sources(parser.rows,2),[dict(title='A title',url='https://example.com/a',snippet='A snippet')])
        page=extract_page('<nav>REMOVE_NAVIGATION</nav><script>REMOVE_SCRIPT</script><p>Python stable version is 3.15.1</p>','Python stable',200)
        self.assertIn('3.15.1',page);self.assertNotIn('REMOVE',page)


class SearchTests(unittest.IsolatedAsyncioTestCase):
    def make(self,**patch):
        config=SimpleNamespace(data={**copy.deepcopy(DEFAULTS),**patch})
        return WebSearch(config)

    async def test_missing_disabled_and_casual_never_make_network_calls(self):
        for config in ({},DEFAULTS,dict(DEFAULTS,web_search_enabled=True)):
            client=WebSearch(SimpleNamespace(data=copy.deepcopy(config)))
            with patch('yukino_core.web_search.request',side_effect=AssertionError('unexpected network')):
                result=await client.lookup('你好' if config.get('web_search_enabled') else 'Python最新版本是什么')
                self.assertIsNone(result)

    async def test_retrieval_cache_bounds_and_no_personal_context_in_network(self):
        client=self.make(web_search_enabled=True)
        calls=[]
        html='<a class="result__a" href="https://example.com/release">Release</a><a class="result__snippet">Latest release</a>'
        def fetch(url,**kwargs):
            calls.append((url,kwargs))
            return response(html if 'duckduckgo' in url else '<p>Current stable version is 3.15.1 as of October 21.</p>',url)
        with patch('yukino_core.web_search.request',side_effect=fetch):
            first=await client.lookup('Python 最新稳定版本',history=[dict(role='user',content='PRIVATE_CHAT',user_id='QQ_ID')],user_id='QQ_ID')
            again=await client.lookup('Python 最新稳定版本')
        self.assertEqual(len(calls),2)
        self.assertFalse(first['cached']);self.assertTrue(again['cached'])
        self.assertEqual(first['retrieved_at'],again['retrieved_at'])
        self.assertIn('3.15.1',first['sources'][0]['content'])
        self.assertIn('max_bytes',calls[0][1]);self.assertLessEqual(calls[0][1]['timeout'],4)
        for private in ('PRIVATE_CHAT','QQ_ID'):
            self.assertNotIn(private,json.dumps(calls));self.assertNotIn(private,json.dumps(client.snapshot()))
        first['sources'][0]['title']='MUTATED'
        self.assertEqual(again['sources'][0]['title'],'Release')

    async def test_failure_is_honest_safe_and_not_reported_as_success(self):
        client=self.make(web_search_enabled=True)
        with patch('yukino_core.web_search.request',side_effect=NetworkError('REMOTE_SECRET')):
            result=await client.lookup('Python latest release')
        self.assertEqual(result['status'],'unavailable');self.assertEqual(result['sources'],[])
        self.assertNotIn('REMOTE_SECRET',json.dumps(result));self.assertEqual(client.snapshot()['failures'],1)

    async def test_bad_primary_falls_back_and_page_read_is_optional(self):
        client=self.make(web_search_enabled=True,web_search_read_pages=0)
        rss='<rss><channel><item><title>Fallback</title><link>https://example.com/r</link><description>Summary</description></item></channel></rss>'
        calls=[]
        def fetch(url,**kwargs):
            calls.append(url)
            return response('<form>challenge</form>' if 'duckduckgo' in url else rss,url)
        with patch('yukino_core.web_search.request',side_effect=fetch):result=await client.lookup('Python 最新版本')
        self.assertEqual(result['engine'],'bing');self.assertEqual(len(calls),2)
        self.assertNotIn('content',result['sources'][0])

    async def test_json_backend_bad_shapes_and_xml_entities_do_not_become_evidence(self):
        client=self.make(web_search_enabled=True,web_search_searxng_url='https://example.com/search')
        def fetch(url,**kwargs):
            if url.startswith('https://example.com/search'):return response('{"results":"invalid"}',url,'application/json')
            if 'duckduckgo' in url:return response('<form>challenge</form>',url)
            return response('<!DOCTYPE rss [<!ENTITY x "bad">]><rss/>',url)
        with patch('yukino_core.web_search.request',side_effect=fetch):result=await client.lookup('Python latest release')
        self.assertEqual(result['status'],'unavailable')
