"""Opt-in, bounded web evidence. Disabled until an administrator enables it."""
from __future__ import annotations

import asyncio
import copy
import datetime as dt
from collections import OrderedDict
from html.parser import HTMLParser
import re
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
import xml.etree.ElementTree as ET

from .public_net import NetworkError, canonical_url, remaining, request

_EXPLICIT = re.compile(r'联网|上网查|搜索|查一下|查一查|查查|帮.{0,3}查|查询|搜一下|搜搜|查找|百度一下|google\b|search\s+(?:for|the)', re.I)
_FRESH = re.compile(r'最新|最近|近期|目前|当前|现在|今天|今日|明天|本周|这周|今年|实时|新闻|热搜|天气|气温|汇率|股价|多少钱|价格|票价|赛程|比分|发售|发布时间|稳定版|版本号|\b(?:latest|current|today|weather|price|news)\b', re.I)
_FACT = re.compile(r'是什么|什么是|是谁|有哪些|怎么样|哪个好|推荐.{1,30}|哪一年|什么时候|哪里买|哪里下载|官网|官方文档|如何安装|怎么安装|支持.{0,20}吗|\b(?:who is|what is|recommend)\b', re.I)
_QUESTION = re.compile(r'[?？]|什么|哪|谁|多少|怎么|如何|是否|有没有|吗|么|\b(?:what|which|who|how|is|are)\b',re.I)
_TOPIC = re.compile(r'天气|气温|新闻|热搜|汇率|股价|价格|票价|赛程|比分|版本|发售|发布|\b(?:weather|news|price|release|version)\b',re.I)
_PERSONAL = re.compile(r'你是谁|你是什么|你叫什么|你在干嘛|你在做什么|你今天|你现在|你最近|你目前|你觉得我|我是谁|我叫什么|我现在|我今天|我最近|我目前|心情|好无聊|想你|喜欢我|爱我|你喜欢|记得我|我好累')
_FOLLOWUP = re.compile(r'^(?:那|那么|它|这个|那个|这款|那款|这家|这台|还有|what about|and (?:its|the))', re.I)
_PRIVATE = re.compile(r'(?:cookie|authorization|password|token|api[_-]?key|密码|令牌|密钥)\s*[:=：]|[\w.+-]+@[\w.-]+\.[a-z]{2,}|\b\d{7,}\b|(?:[A-Z]:\\|/home/|/Users/)', re.I)
_URL = re.compile(r'https?://[^\s<>"\]\u3002]+', re.I)


def query_for(text, history=(), user_id=None, *, automatic=True, followups=True):
    """No IDs, display names, memories or transcript are accepted as input."""
    text = str(text).strip()
    explicit = bool(_EXPLICIT.search(text))
    if not text or len(text)>2000 or _PRIVATE.search(text): return None
    fresh_question = _FRESH.search(text) and (_QUESTION.search(text) or _TOPIC.search(text))
    if not explicit and (not automatic or _PERSONAL.search(text) or not (fresh_question or _FACT.search(text))): return None
    query = re.sub(r'\[CQ:[^\]]*\]|\[图片\]|\[视频\]|\[语音\]', ' ', text)
    # URLs may contain signed credentials; don't pass them to a search engine.
    query = _URL.sub('', query)
    query = re.sub(r'^(?:请|麻烦|能不能|可以)?(?:帮我|给我)?(?:联网|上网)?(?:搜索|查一下|查一查|查查|搜一下|搜搜|查找|查询)[下\s：:，,]*', '', query)
    query = re.sub(r'\s+', ' ', query).strip(' ，,。.!！?？:：')
    if len(query)<2: return None
    if followups and user_id is not None and _FOLLOWUP.search(query):
        latest=next((row for row in reversed(history) if row.get('role')=='user'),None)
        if latest and str(latest.get('user_id',''))==str(user_id):
            previous=query_for(latest.get('content',''),automatic=True,followups=False)
            if previous: query=previous[:140]+' '+query
    return query[:320]


class _Text(HTMLParser):
    hidden_tags={'script','style','nav','header','footer','noscript','form','svg','template'}
    blocks={'p','div','li','tr','h1','h2','h3','article','main','section','br'}
    def __init__(self):
        super().__init__(convert_charrefs=True); self.parts=[]; self.hidden=[]
    def handle_starttag(self,tag,attrs):
        if tag in self.hidden_tags:self.hidden.append(tag)
        if not self.hidden and tag in self.blocks:self.parts.append('\n')
    def handle_endtag(self,tag):
        if self.hidden:
            if tag==self.hidden[-1]:self.hidden.pop()
        elif tag in self.blocks:self.parts.append('\n')
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)


def plain(value,limit=800):
    parser=_Text();parser.feed(str(value)[:200000])
    return re.sub(r'\s+',' ',''.join(parser.parts)).strip()[:limit]


class _DuckResults(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.rows=[];self.current=None;self.capture=None
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs);classes=attrs.get('class','').split()
        if tag=='a' and 'result__a' in classes:
            raw=urljoin('https://duckduckgo.com/',attrs.get('href',''));parsed=urlsplit(raw)
            if parsed.hostname in ('duckduckgo.com','www.duckduckgo.com'):raw=parse_qs(parsed.query).get('uddg',[''])[0]
            self.current=dict(title='',url=raw,snippet='');self.rows.append(self.current);self.capture='title'
        elif self.current is not None and 'result__snippet' in classes:self.capture='snippet'
    def handle_endtag(self,tag):
        if tag=='a':self.capture=None
    def handle_data(self,data):
        if self.capture and self.current is not None:self.current[self.capture]+=data


def clean_sources(rows,limit):
    output=[];seen=set()
    for row in rows[:50]:
        if not isinstance(row,dict):continue
        try:
            url=canonical_url(row.get('url',''));parsed=urlsplit(url)
            if any(k.lower() in ('token','key','signature','auth','password','access_token') for k in parse_qs(parsed.query)):continue
        except (NetworkError,ValueError):continue
        title=plain(row.get('title',''),200)
        if not title or url in seen:continue
        seen.add(url);output.append(dict(title=title,url=url,snippet=plain(row.get('snippet',''),700)))
        if len(output)==limit:break
    return output


def extract_page(html,query,limit):
    parser=_Text();parser.feed(html[:800000])
    lines=[re.sub(r'\s+',' ',line).strip() for line in ''.join(parser.parts).splitlines()]
    lines=list(dict.fromkeys(line for line in lines if len(line)>12))[:2000]
    tokens=set(re.findall(r'[a-z0-9]{3,}|[\u4e00-\u9fff]{2}',query.lower()))
    scores=sorted(range(len(lines)),key=lambda i:-sum(token in lines[i].lower() for token in tokens))
    chosen=sorted(set(range(min(3,len(lines))))|set(scores[:12]))
    return '\n'.join(lines[i] for i in chosen)[:limit]


class WebSearch:
    def __init__(self,config):
        self.config=config;self.cache=OrderedDict()
        self.metrics=dict(requests=0,successes=0,failures=0,cache_hits=0,skipped=0)
        self.last=dict(state='not_used',latency_ms=0,source_count=0)
    def snapshot(self):
        return dict(enabled=self.config.data.get('web_search_enabled',False),**self.metrics,last=dict(self.last))
    async def lookup(self,text,history=(),user_id=None):
        cfg=dict(self.config.data)
        # This is a strict opt-in boundary. Missing settings also mean disabled.
        if not cfg.get('web_search_enabled',False):return None
        query=query_for(text,history,user_id,automatic=cfg.get('web_search_auto',True),followups=cfg.get('web_search_followups',True))
        if not query:self.metrics['skipped']+=1;return None
        key=(query.casefold(),*(cfg.get(k) for k in ('web_search_searxng_url','web_search_results','web_search_read_pages','web_search_page_chars')))
        started=time.monotonic();ttl=cfg.get('web_search_cache_seconds',180);cached=self.cache.get(key);self.metrics['requests']+=1
        if cached and ttl>0 and started-cached[0]<min(ttl,15 if cached[1]['status']!='ok' else ttl):
            self.cache.move_to_end(key);data=copy.deepcopy(cached[1]);data['cached']=True;self.metrics['cache_hits']+=1
        else:
            try:data=await asyncio.wait_for(asyncio.to_thread(self._search,query,cfg),cfg.get('web_search_timeout_seconds',10)+.5)
            except (TimeoutError,NetworkError,ValueError,ET.ParseError):data=dict(query=query,status='unavailable',sources=[],retrieved_at=dt.datetime.now(dt.timezone.utc).isoformat())
            data['cached']=False
            if ttl:
                self.cache[key]=(time.monotonic(),copy.deepcopy(data));self.cache.move_to_end(key)
                while len(self.cache)>128:self.cache.popitem(last=False)
        self.metrics['successes' if data['status']=='ok' else 'failures']+=1
        # Do not log queries, account identifiers, credentials or error bodies.
        self.last=dict(state=data['status'],latency_ms=round((time.monotonic()-started)*1000),source_count=len(data['sources']),cached=data['cached'],retrieved_at=data['retrieved_at'])
        return data
    @staticmethod
    def _search(query,cfg):
        deadline=time.monotonic()+cfg.get('web_search_timeout_seconds',10);count=cfg.get('web_search_results',5)
        endpoint=cfg.get('web_search_searxng_url','');sources=[];engine=''
        for engine in (['searxng','duckduckgo','bing'] if endpoint else ['duckduckgo','bing']):
            try:
                budget=min(4,remaining(deadline))
                if engine=='searxng':
                    response=request(endpoint+'?'+urlencode(dict(q=query,format='json')),timeout=budget,max_bytes=300000);payload=response.json()
                    rows=[dict(title=r.get('title'),url=r.get('url'),snippet=r.get('content')) for r in payload.get('results',[])[:50] if isinstance(r,dict)] if isinstance(payload,dict) else []
                elif engine=='duckduckgo':
                    response=request('https://html.duckduckgo.com/html/?'+urlencode(dict(q=query)),timeout=budget,max_bytes=300000)
                    parser=_DuckResults();parser.feed(response.text());rows=parser.rows
                else:
                    response=request('https://www.bing.com/search?'+urlencode(dict(q=query,format='rss')),timeout=budget,max_bytes=200000);body=response.text()
                    if re.search(r'<!DOCTYPE|<!ENTITY',body,re.I):raise NetworkError('不支持XML实体')
                    rows=[dict(title=r.findtext('title',''),url=r.findtext('link',''),snippet=r.findtext('description','')) for r in ET.fromstring(body).findall('./channel/item')][:30]
                sources=clean_sources(rows,count)
                if sources:break
            except (NetworkError,ValueError,ET.ParseError,TypeError,AttributeError):continue
        checked=dt.datetime.now(dt.timezone.utc).isoformat()
        for source in sources[:cfg.get('web_search_read_pages',2)]:
            try:
                response=request(source['url'],timeout=min(3,remaining(deadline)),max_bytes=800000,max_redirects=2)
                if 'text/html' not in response.headers.get('content-type','').lower():continue
                content=extract_page(response.text(),query,cfg.get('web_search_page_chars',2400))
                if content:source.update(content=content,page_url=response.url,read_at=dt.datetime.now(dt.timezone.utc).isoformat())
            except (NetworkError,ValueError):continue
        return dict(query=query,status='ok' if sources else 'unavailable',engine=engine,retrieved_at=checked,sources=sources)
