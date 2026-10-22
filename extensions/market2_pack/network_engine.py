"""Original public-data plugins; bounded HTTPS and per-user RSS SQLite state."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import quote, urljoin, urlsplit
import xml.etree.ElementTree as ET

_network_path = Path(__file__).with_name('_safe_net.py')
if not _network_path.exists():
    _network_path = Path(__file__).with_name('safe_net.py')
_spec = importlib.util.spec_from_file_location('yukino_market2_safe_net', _network_path)
NET = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(NET)

COMMANDS = {'m_rss': '/订阅源', 'm_bangumi': '/番剧', 'm_abbr': '/缩写', 'm_webtext': '/网页正文', 'm_bing': '/每日壁纸'}
HELP = {
    'm_rss': '/订阅源 添加 名称|https://公开RSS地址；列表；查看 编号；删除 编号；刷新 [编号]。个人订阅按当前会话和QQ隔离，只在手动刷新时返回新条目；不后台轮询、不主动推送。首次刷新展示最新条目并记录本次已获取条目，后续按标识去重。',
    'm_bangumi': '/番剧 搜索 名称；详情 条目ID；放送 [1～7]。查询 Bangumi 官方公开 API；默认放送日期按配置UTC偏移。不登录、不使用私人收藏或令牌。',
    'm_abbr': '/缩写 yyds；可用逗号分隔最多5个英文数字缩写。查询神奇海螺“能不能好好说话”公开接口；结果来自社区贡献，可能有多义、缺失或不准确。',
    'm_webtext': '/网页正文 https://公开网址。只读取公开HTTPS网页的标题与可见文本摘录，忽略脚本、样式、导航、页脚等；不执行JavaScript、不绕过登录，动态网页可能没有正文。',
    'm_bing': '/每日壁纸 [0～7]；0为今天、1为前一天。发送Bing每日原图、标题和版权说明。区域由面板配置。',
}


def need(condition, message):
    if not condition:
        raise ValueError(message)


def option(ctx, key, default, low, high):
    settings = ctx.get('settings', {})
    value = settings.get(key, default) if isinstance(settings, dict) else default
    return max(low, min(high, value)) if type(value) is int else default


def clean(value, maximum=300):
    return re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', ' ', '' if value is None else str(value)).strip()[:maximum]


def timeout(ctx):
    return option(ctx, 'network_timeout_seconds', 12, 2, 15)


def api(url, ctx, body=None):
    return NET.request(url, method='POST' if body is not None else 'GET', json_body=body, timeout=timeout(ctx), allowed_hosts={urlsplit(url).hostname}).json()


def output_url(value, base=None):
    try:
        return NET.canonical_url(urljoin(base, str(value)) if base else str(value))
    except NET.NetworkError:
        return ''


class VisibleText(HTMLParser):
    BLOCKS = {'p', 'div', 'section', 'main', 'article', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'br', 'pre', 'tr', 'td', 'blockquote'}
    IGNORED = {'script', 'style', 'noscript', 'template', 'svg', 'canvas', 'nav', 'footer', 'header', 'aside', 'form', 'button'}

    def __init__(self, maximum=20000):
        super().__init__(convert_charrefs=True)
        self.maximum, self.parts, self.title_parts = maximum, [], []
        self.skipped, self.in_title, self.collected, self.nodes = [], False, 0, 0

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        need(self.nodes <= 50000, '网页结构节点过多。')
        if self.skipped:
            if tag in self.IGNORED:
                self.skipped.append(tag)
            return
        if tag in self.IGNORED:
            self.skipped.append(tag)
        elif tag == 'title':
            self.in_title = True
        elif tag in self.BLOCKS:
            self._append('\n')

    def handle_endtag(self, tag):
        if self.skipped:
            if tag in self.skipped:
                index = len(self.skipped) - 1 - self.skipped[::-1].index(tag)
                self.skipped = self.skipped[:index]
            return
        if tag == 'title':
            self.in_title = False
        elif tag in self.BLOCKS:
            self._append('\n')

    def _append(self, value):
        if self.collected < self.maximum:
            value = value[:self.maximum - self.collected]
            self.parts.append(value)
            self.collected += len(value)

    def handle_data(self, data):
        if self.skipped:
            return
        if self.in_title:
            if sum(map(len, self.title_parts)) < 300:
                self.title_parts.append(data[:300])
        else:
            self._append(data)

    def result(self):
        title = clean(' '.join(''.join(self.title_parts).split()), 300)
        lines = [' '.join(line.split()) for line in ''.join(self.parts).splitlines()]
        return title, '\n'.join(line for line in lines if line)


def plain(value, maximum=300):
    parser = VisibleText(maximum=maximum)
    parser.feed(str(value or '')[:10000])
    parser.close()
    title, body = parser.result()
    return clean(body or title, maximum)


def identity(ctx):
    scope, user = str(ctx.get('session_id', '')), str(ctx.get('user_id', ''))
    need(re.fullmatch(r'[pg]:[0-9]{1,30}', scope) and re.fullmatch(r'[0-9]{1,30}', user), '缺少有效会话／QQ标识。')
    return scope, user


def database(ctx):
    root_value = ctx.get('plugin_data_dir')
    need(isinstance(root_value, str) and root_value and Path(root_value).is_absolute(), '需要宿主分配的独立数据目录。')
    root = Path(root_value)
    need(not root.is_symlink() and not getattr(root, 'is_junction', lambda: False)(), '插件数据目录不能是链接。')
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'market2-rss.sqlite3'
    need(not path.is_symlink() and path.resolve().parent == root.resolve(), '插件数据文件路径无效。')
    db = sqlite3.connect(path, timeout=2)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''CREATE TABLE IF NOT EXISTS feeds(id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, who TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL, created REAL NOT NULL, UNIQUE(scope,who,url));
CREATE INDEX IF NOT EXISTS feeds_lookup ON feeds(scope,who,id);
CREATE TABLE IF NOT EXISTS seen(id INTEGER PRIMARY KEY AUTOINCREMENT, feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE, fingerprint TEXT NOT NULL, stamp REAL NOT NULL, UNIQUE(feed_id,fingerprint));''')
    return db


def feed_entries(raw, base):
    need(len(raw) <= NET.MAX_BYTES, 'RSS响应超限。')
    need(b'\x00' not in raw, '不支持含NUL字节的XML或UTF-16/32订阅；请使用常规UTF-8源。')
    need(not re.search(br'<!\s*(?:DOCTYPE|ENTITY)\b', raw, re.I), '不支持含DTD或实体声明的订阅源。')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ValueError('远端不是有效的RSS/Atom XML。') from None
    tag = lambda node: node.tag.rsplit('}', 1)[-1].lower() if isinstance(node.tag, str) else ''
    need(tag(root) in ('rss', 'feed', 'rdf'), '不是受支持的RSS/Atom订阅源。')
    entries, nodes = [], 0
    for node in root.iter():
        nodes += 1
        need(nodes <= 10000, 'RSS节点过多。')
        if tag(node) not in ('item', 'entry'):
            continue
        values, link = {}, ''
        for child in list(node)[:100]:
            name = tag(child)
            if name == 'link':
                if child.attrib.get('href') and child.attrib.get('rel', 'alternate') == 'alternate':
                    link = child.attrib['href']
                elif child.text and not link:
                    link = child.text.strip()
            elif name in ('title', 'guid', 'id', 'pubdate', 'published', 'updated', 'summary', 'description', 'content', 'encoded'):
                values[name] = ''.join(child.itertext())[:2000]
        title = plain(values.get('title', ''), 250) or '未命名条目'
        link = output_url(link, base) if link else ''
        published = clean(values.get('published') or values.get('pubdate') or values.get('updated') or '', 100)
        key = values.get('guid') or values.get('id') or link or title + '|' + published
        fingerprint = hashlib.sha256(key.encode('utf-8')).hexdigest()
        if not any(entry['fingerprint'] == fingerprint for entry in entries):
            summary=plain(values.get('summary') or values.get('description') or values.get('content') or values.get('encoded') or '',500)
            entries.append(dict(title=title, url=link, published=published, fingerprint=fingerprint,summary=summary))
        if len(entries) >= 100:
            break
    return entries


def rss(arg, ctx):
    scope, who = identity(ctx)
    action, _, rest = arg.partition(' ')
    rest = rest.strip()
    if not action:
        action = '列表'
    if action == '添加':
        need('|' in rest, '用法：/订阅源 添加 名称|https://RSS地址')
        title, url = (value.strip() for value in rest.split('|', 1))
        need(1 <= len(title) <= 80 and not any(ord(char) < 32 for char in title), '订阅名称须为1～80字。')
        url = NET.check_target(url, timeout=timeout(ctx))
        db = database(ctx)
        try:
            db.execute('BEGIN IMMEDIATE')
            need(db.execute('SELECT COUNT(*) FROM feeds WHERE scope=? AND who=?', (scope, who)).fetchone()[0] < option(ctx, 'max_subscriptions', 10, 1, 30), '已达到个人订阅数量上限。')
            need(not db.execute('SELECT id FROM feeds WHERE scope=? AND who=? AND url=?', (scope, who, url)).fetchone(), '当前会话已订阅这个地址。')
            cursor = db.execute('INSERT INTO feeds(scope,who,title,url,created) VALUES(?,?,?,?,?)', (scope, who, title, url, time.time()))
            identifier = cursor.lastrowid
            db.commit()
        finally:
            db.close()
        return f'订阅已添加 #{identifier}：{title}\n用 /订阅源 刷新 {identifier} 手动获取；不自动推送。'
    db = database(ctx)
    try:
        if action == '列表':
            need(not rest, '列表不需要额外参数。')
            rows = db.execute('SELECT id,title FROM feeds WHERE scope=? AND who=? ORDER BY id', (scope, who)).fetchall()
            return '个人RSS订阅（仅当前会话）\n' + ('\n'.join(f'#{i} {title}' for i, title in rows) if rows else '暂无订阅。')
        need(action in ('查看', '删除', '刷新'), HELP['m_rss'])
        if rest:
            need(rest.isascii() and rest.isdigit() and len(rest) <= 10, '订阅编号应为数字。')
            rows = db.execute('SELECT id,title,url FROM feeds WHERE id=? AND scope=? AND who=?', (int(rest), scope, who)).fetchall()
            need(rows, '订阅不存在或不属于当前用户／会话。')
        else:
            need(action == '刷新', '请提供订阅编号。')
            rows = db.execute('SELECT id,title,url FROM feeds WHERE scope=? AND who=? ORDER BY id LIMIT ?', (scope, who, option(ctx, 'refresh_feed_limit', 3, 1, 10))).fetchall()
            need(rows, '暂无订阅。')
        if action == '查看':
            return f'#{rows[0][0]} {rows[0][1]}\n{rows[0][2]}\n只提供手动刷新，不自动推送。'
        if action == '删除':
            db.execute('DELETE FROM feeds WHERE id=? AND scope=? AND who=?', (rows[0][0], scope, who))
            db.commit()
            return f'订阅 #{rows[0][0]} 已删除；去重记录同时清理。'
    finally:
        db.close()
    deadline = time.monotonic() + timeout(ctx)
    result = ['手动刷新RSS（本次最多读取100条/源；已获取条目均记录去重）']
    limit = option(ctx, 'result_limit', 3, 1, 10)
    for identifier, title, url in rows:
        try:
            budget = NET.remaining(deadline)
            need(budget >= .25, '本次刷新已到总预算，请指定编号单独刷新剩余订阅。')
            response = NET.request(url, timeout=budget)
            entries = feed_entries(response.body, response.url)
            db = database(ctx)
            try:
                db.execute('BEGIN IMMEDIATE')
                need(db.execute('SELECT id FROM feeds WHERE id=? AND scope=? AND who=?', (identifier, scope, who)).fetchone(), '订阅已删除。')
                fresh = []
                for entry in entries:
                    cursor = db.execute('INSERT OR IGNORE INTO seen(feed_id,fingerprint,stamp) VALUES(?,?,?)', (identifier, entry['fingerprint'], time.time()))
                    if cursor.rowcount:
                        fresh.append(entry)
                db.execute('DELETE FROM seen WHERE feed_id=? AND id NOT IN (SELECT id FROM seen WHERE feed_id=? ORDER BY id DESC LIMIT 500)', (identifier, identifier))
                db.commit()
            finally:
                db.close()
            result.append(f'#{identifier} {title}：本次新条目 {len(fresh)} 条')
            for entry in fresh[:limit]:
                result.append('• ' + entry['title'] + ('\n' + entry['published'] if entry['published'] else '') + ('\n' + entry['summary'] if entry['summary'] else '\n源站未提供正文摘要。') + ('\n' + entry['url'] if entry['url'] else ''))
            if len(fresh) > limit:
                result.append(f'仅展示前{limit}条，其余本次条目已记为获取。')
        except (sqlite3.Error, OSError):
            result.append(f'#{identifier} {title}：刷新失败：插件数据存储暂不可用。')
        except ValueError as exc:
            result.append(f'#{identifier} {title}：刷新失败：{clean(exc, 120)}')
    return '\n'.join(result)


def bangumi(arg, ctx):
    action, _, rest = arg.partition(' ')
    rest = rest.strip()
    limit = option(ctx, 'result_limit', 5, 1, 10)
    if action == '搜索':
        need(1 <= len(rest) <= 80, '用法：/番剧 搜索 动画名称（最多80字）')
        result = api(f'https://api.bgm.tv/v0/search/subjects?limit={limit}&offset=0', ctx, {'keyword': rest, 'sort': 'match', 'filter': {'type': [2], 'nsfw': False}})
        need(isinstance(result, dict) and isinstance(result.get('data'), list), 'Bangumi搜索响应格式异常。')
        lines = [f'Bangumi 动画搜索：{rest}'];media=[]
        for row in result['data'][:limit]:
            if not isinstance(row, dict):
                continue
            identifier = row.get('id')
            if type(identifier) is not int or identifier <= 0:
                continue
            rating = row.get('rating', {}) if isinstance(row.get('rating'), dict) else {}
            lines += [f"#{identifier} {plain(row.get('name_cn') or row.get('name'), 100)}；评分 {clean(rating.get('score', '未知'), 20)}", plain(row.get('summary',''),240), f'https://bgm.tv/subject/{identifier}']
            images=row.get('images',{})
            cover=output_url(images.get('common') or images.get('large')) if isinstance(images,dict) else ''
            if cover and len(media)<3:media.append({'type':'image','url':cover})
        result='\n'.join(lines) + ('\n没有匹配条目。' if len(lines) == 1 else '\n详情：/番剧 详情 条目ID')
        return {'version':1,'text':result,'media':media} if ctx.get('rich_result_version') else result
    if action == '详情':
        need(rest.isascii() and rest.isdigit() and 0 < int(rest) < 1000000000, '用法：/番剧 详情 正整数ID')
        identifier = int(rest)
        row = api(f'https://api.bgm.tv/v0/subjects/{identifier}', ctx)
        need(isinstance(row, dict) and row.get('id') == identifier, 'Bangumi详情响应格式异常。')
        rating = row.get('rating', {}) if isinstance(row.get('rating'), dict) else {}
        summary = plain(row.get('summary', ''), option(ctx, 'summary_max_chars', 500, 100, 1500)) or '暂无公开简介。'
        result='\n'.join([f"Bangumi #{identifier} {plain(row.get('name_cn') or row.get('name'), 120)}", f"原名：{plain(row.get('name'), 120)}", f"日期：{clean(row.get('date'), 40) or '未知'}；集数：{clean(row.get('eps'), 20) or '未知'}", f"评分：{clean(rating.get('score', '未知'), 20)}；参与人数：{clean(rating.get('total', '未知'), 20)}", summary, f'https://bgm.tv/subject/{identifier}'])
        images=row.get('images',{});cover=output_url(images.get('large') or images.get('common')) if isinstance(images,dict) else ''
        return {'version':1,'text':result,'media':[{'type':'image','url':cover}] if cover else []} if ctx.get('rich_result_version') else result
    if action in ('放送', '每日放送'):
        tz = timezone(timedelta(hours=option(ctx, 'utc_offset_hours', 8, -12, 14)))
        need(not rest or rest in tuple('1234567'), '放送星期请填1～7，1为周一，7为周日。')
        weekday = int(rest) if rest else datetime.now(tz).isoweekday()
        rows = api('https://api.bgm.tv/calendar', ctx)
        need(isinstance(rows, list), 'Bangumi放送响应格式异常。')
        items = []
        for day in rows:
            if isinstance(day, dict) and isinstance(day.get('weekday'), dict) and day['weekday'].get('id') == weekday:
                items = day.get('items') if isinstance(day.get('items'), list) else []
        lines = [f'Bangumi 每周放送：星期{weekday}（来源排期，未验证实际更新）']
        for row in items[:limit]:
            if isinstance(row, dict) and type(row.get('id')) is int:
                lines.append(f"#{row['id']} {plain(row.get('name_cn') or row.get('name'), 120)}\nhttps://bgm.tv/subject/{row['id']}")
        return '\n'.join(lines) + ('\n暂无条目。' if len(lines) == 1 else f'\n展示{len(lines)-1}/{len(items)}项。')
    raise ValueError(HELP['m_bangumi'])


def abbreviations(arg, ctx):
    words = [word for word in re.split(r'[,，\s]+', arg.lower()) if word]
    need(1 <= len(words) <= 5 and all(re.fullmatch(r'[a-z0-9]{2,20}', word) for word in words), '缩写须为2～20位英文数字，最多5个，用逗号分隔。')
    words = list(dict.fromkeys(words))
    rows = api('https://lab.magiconch.com/api/nbnhhsh/guess', ctx, {'text': ','.join(words)})
    need(isinstance(rows, list), '缩写接口响应格式异常。')
    matches = {row.get('name'): row for row in rows if isinstance(row, dict) and row.get('name') in words}
    lines = ['能不能好好说话（社区释义，仅供参考）']
    for word in words:
        row = matches.get(word, {})
        translations = row.get('trans') or row.get('inputting') or []
        values = [plain(value, 80) for value in translations[:option(ctx, 'explanation_limit', 8, 1, 20)] if isinstance(value, str)] if isinstance(translations, list) else []
        lines.append(word + '：' + ('；'.join(values) if values else '没有查到公开释义。'))
    lines.append('来源：https://lab.magiconch.com/nbnhhsh/')
    return '\n'.join(lines)


def webtext(arg, ctx):
    need(arg and not any(char.isspace() for char in arg), '用法：/网页正文 https://公开网页地址')
    response = NET.request(arg, timeout=timeout(ctx))
    mime = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    need(mime in ('', 'text/html', 'application/xhtml+xml', 'text/plain'), '只支持HTML或纯文本网页，不解析文件、图片、PDF。')
    source = response.text()
    if 'charset' not in response.headers.get('content-type', '').lower():
        match = re.search(br'charset\s*=\s*["\']?([a-zA-Z0-9_-]{1,30})', response.body[:4096], re.I)
        if match:
            try:
                source = response.body.decode(match.group(1).decode('ascii'), errors='replace')
            except LookupError:
                pass
    if mime == 'text/plain':
        title, content = '纯文本网页', clean(source, 20000)
    else:
        parser = VisibleText()
        parser.feed(source)
        parser.close()
        title, content = parser.result()
    maximum = option(ctx, 'text_max_chars', 2400, 200, 6000)
    excerpt = content[:maximum]
    return f"网页标题：{title or '未提供标题'}\n来源：{response.url}\n" + (excerpt if excerpt else '未提取到可见正文；可能是登录页或依赖JavaScript。') + ('\n（正文摘录达到配置上限。）' if len(content) > maximum else '')


def bing(arg, ctx):
    need(not arg or arg in tuple('01234567'), '用法：/每日壁纸 [0～7]，0为今天。')
    settings = ctx.get('settings', {})
    market = settings.get('market', 'zh-CN') if isinstance(settings, dict) else 'zh-CN'
    need(market in ('zh-CN', 'en-US', 'ja-JP', 'de-DE'), '壁纸区域配置无效。')
    data = api(f'https://www.bing.com/HPImageArchive.aspx?format=js&idx={arg or 0}&n=1&mkt={market}', ctx)
    need(isinstance(data, dict) and isinstance(data.get('images'), list) and data['images'], 'Bing没有返回每日图片。')
    image = data['images'][0]
    need(isinstance(image, dict), 'Bing响应格式异常。')
    path = str(image.get('urlbase', '')) + '_UHD.jpg' if image.get('urlbase') else image.get('url', '')
    url = output_url(path, 'https://www.bing.com')
    need(url, 'Bing未提供可用的公开HTTPS图片链接。')
    result='\n'.join([f"Bing每日一图：{plain(image.get('title'), 180) or '无标题'}", f"日期：{clean(image.get('startdate'), 20) or '未知'}；区域：{market}", f"版权：{plain(image.get('copyright'), 400) or '接口未提供'}"])
    if ctx.get('rich_result_version'):
        return {'version':1,'text':result,'media':[{'type':'image','url':url,'name':str(image.get('startdate','bing'))+'-wallpaper.jpg'}],'layout':'text'}
    return result+'\n'+url+'\n旧版内核仅展示原图地址，未下载。'


FUNCTIONS = {'m_rss': rss, 'm_bangumi': bangumi, 'm_abbr': abbreviations, 'm_webtext': webtext, 'm_bing': bing}


def handle(kind, text, context):
    if kind not in COMMANDS or not isinstance(text, str) or not text.strip():
        return None
    pieces = text.strip().split(maxsplit=1)
    if pieces[0] != COMMANDS[kind]:
        return None
    arg = pieces[1].strip() if len(pieces) > 1 else ''
    if arg in ('帮助', 'help', '?'):
        return HELP[kind]
    if len(text) > 6000:
        return '操作未完成：命令长度超过6000字符。'
    ctx = context if isinstance(context, dict) else {}
    try:
        result = FUNCTIONS[kind](arg, ctx)
        if isinstance(result,dict):return result
        return result[:7500] + ('\n（回复达到插件上限。）' if len(result) > 7500 else '')
    except (OSError, sqlite3.Error):
        return '操作未完成：插件数据存储暂不可用，请稍后重试。'
    except (ValueError, RecursionError, LookupError, TypeError, OverflowError) as exc:
        return '操作未完成：' + clean(exc, 250)
