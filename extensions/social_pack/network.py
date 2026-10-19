"""Fixed-endpoint public metadata lookups with bounded successful-result caching."""
from datetime import datetime, timedelta, timezone
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from common import RuleError, identity, need, setting
import safe_net

TZ = timezone(timedelta(hours=8))
HELP = {
    's_github': '/GitHub发行 owner/repo：查询 GitHub 最新正式 Release（排除草稿/预发行），最多展示5个附件链接，不下载或执行。',
    's_package': '/包版本 PyPI requests 或 /包版本 npm vue（支持 npm @scope/name）：查询注册表版本及元数据，不判断安全性或真实性。',
}
LIMIT = 1_048_576
PYPI_LIMIT = 8 * LIMIT


def clean(value, limit=200, fallback='未提供'):
    if not isinstance(value, str):
        return fallback
    value = ' '.join(''.join(c for c in value if ord(c) >= 32 and ord(c) != 127).split())
    return value[:limit] or fallback


def required(value, limit, label):
    need(isinstance(value, str) and 0 < len(value) <= limit
         and not any(ord(c) < 32 or ord(c) == 127 for c in value), f'远端 {label} 字段无效。')
    return value


def norm_pypi(value):
    return re.sub(r'[-_.]+', '-', value).lower()


def query(pid, text):
    need(isinstance(text, str) and len(text) <= 400, '指令长度或格式无效。')
    parts = text.strip().split()
    need(parts, HELP[pid])
    if pid == 's_github':
        need(len(parts) == 2 and parts[0] == '/GitHub发行', HELP[pid])
        repo = parts[1]
        need(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]{1,100}', repo)
             and repo.rsplit('/', 1)[1] not in ('.', '..'), '仓库格式应为 owner/repo，不接受 URL、路径或查询参数。')
        owner, name = repo.split('/')
        return 'github:' + repo.lower(), 'https://api.github.com/repos/' + quote(owner, safe='') + '/' + quote(name, safe='') + '/releases/latest', 'api.github.com', repo
    need(len(parts) == 3 and parts[0] == '/包版本', HELP[pid])
    kind, name = parts[1].lower(), parts[2]
    if kind == 'pypi':
        need(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?', name), 'PyPI 包名格式无效；不接受 URL 或查询参数。')
        name = norm_pypi(name)
        return 'pypi:' + name, 'https://pypi.org/pypi/' + quote(name, safe='') + '/json', 'pypi.org', name
    need(kind == 'npm', HELP[pid])
    need(re.fullmatch(r'(?:@[a-z0-9][a-z0-9._-]{0,63}/)?[a-z0-9][a-z0-9._-]{0,127}', name), 'npm 包名格式无效；支持小写包名和 @scope/name，不接受 URL 或查询参数。')
    return 'npm:' + name, 'https://registry.npmjs.org/' + quote(name, safe='') + '/latest', 'registry.npmjs.org', name


def github_url(value, repo, area):
    need(isinstance(value, str) and len(value) <= 512, '远端 GitHub 链接长度无效。')
    url = safe_net.canonical_url(value)
    parsed = urlsplit(url)
    need(parsed.hostname == 'github.com' and not parsed.query
         and parsed.path.lower().startswith('/' + repo.lower() + '/releases/' + area + '/'), '远端 GitHub 链接不属于本仓库发行页。')
    return url


def format_data(key, name, data):
    need(isinstance(data, dict), '远端元数据结构无效。')
    if key.startswith('github:'):
        need(data.get('draft') is False and data.get('prerelease') is False, '远端没有返回正式 Release。')
        tag = required(data.get('tag_name'), 100, 'tag')
        published = required(data.get('published_at'), 40, '发布时间')
        try:
            parsed = datetime.fromisoformat(published.replace('Z', '+00:00'))
            need(parsed.tzinfo is not None, '远端发布时间缺少时区。')
        except ValueError:
            raise RuleError('远端发布时间格式无效。') from None
        page = github_url(data.get('html_url'), name, 'tag')
        assets = data.get('assets')
        need(isinstance(assets, list), '远端附件列表无效。')
        lines = [f'GitHub 正式发行：{name}', f'版本：{tag}', f'发布时间：{published}', f'原页面：{page}']
        for asset in assets[:5]:
            need(isinstance(asset, dict), '远端附件元数据无效。')
            title = clean(asset.get('name'), 80, '附件')
            url = github_url(asset.get('browser_download_url'), name, 'download')
            lines.append(f'附件：{title}\n{url}')
        if not assets:
            lines.append('附件：此 Release 未上传附件。')
        elif len(assets) > 5:
            lines.append('附件仅列前5个，其余请查看原页面。')
        lines.append('仅列公开元数据及链接；未下载、执行或验证附件安全。')
        return '\n'.join(lines)
    version = required(data.get('info', {}).get('version') if key.startswith('pypi:') and isinstance(data.get('info'), dict) else data.get('version'), 100, '版本')
    if key.startswith('pypi:'):
        info = data.get('info')
        need(isinstance(info, dict) and isinstance(info.get('name'), str) and norm_pypi(info['name']) == name, '远端 PyPI 包名不匹配。')
        license_text = clean(info.get('license_expression'), 160, '') or clean(info.get('license'), 160)
        return '\n'.join([f'PyPI：{name}', f'版本：{version}', '简介：' + clean(info.get('summary'), 240),
                          '作者字段：' + clean(info.get('author'), 100), '许可证字段：' + license_text,
                          'Python 要求：' + clean(info.get('requires_python'), 120),
                          '官方包页：https://pypi.org/project/' + quote(name, safe='') + '/',
                          '以上为注册表发布者填写的元数据，不代表安全、正版或可信认证。'])
    need(data.get('name') == name, '远端 npm 包名不匹配。')
    license_value = data.get('license')
    if isinstance(license_value, dict):
        license_value = license_value.get('type')
    engines = data.get('engines')
    node = clean(engines.get('node'), 120) if isinstance(engines, dict) else '未提供'
    publisher = data.get('_npmUser')
    publisher = clean(publisher.get('name'), 100) if isinstance(publisher, dict) else '未提供'
    return '\n'.join([f'npm：{name}', f'版本：{version}', '简介：' + clean(data.get('description'), 240),
                      '发布者字段：' + publisher, '许可证字段：' + clean(license_value, 160), 'Node 要求：' + node,
                      '官方包页：https://www.npmjs.com/package/' + quote(name, safe='@/'),
                      '以上为注册表发布者填写的元数据，不代表安全、正版或可信认证。'])


def _plain(path):
    need(not path.is_symlink() and not getattr(path, 'is_junction', lambda: False)(), '拒绝链接缓存路径。')


def connect(ctx):
    raw = ctx.get('data_dir')
    need(isinstance(raw, str) and Path(raw).is_absolute(), '缺少内核分配的绝对数据目录。')
    root = Path(raw)
    for path in [*reversed(root.parents), root]:
        _plain(path)
    root.mkdir(parents=True, exist_ok=True)
    target = root / 'network-cache.sqlite3'
    for suffix in ('', '-journal', '-wal', '-shm'):
        path = root / ('network-cache.sqlite3' + suffix)
        _plain(path)
        if path.exists():
            need(path.is_file() and path.stat().st_nlink == 1 and path.stat().st_size <= 8 * 1024 * 1024, '缓存文件异常或超过8 MiB。')
    db = sqlite3.connect(target, timeout=2, isolation_level=None)
    try:
        target.chmod(0o600)
        db.execute('PRAGMA busy_timeout=2000')
        page_size = db.execute('PRAGMA page_size').fetchone()[0]
        db.execute(f'PRAGMA max_page_count={8 * 1024 * 1024 // page_size}')
        db.execute('CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY,body TEXT NOT NULL,fetched REAL NOT NULL)')
        return db
    except BaseException:
        db.close()
        raise


def display(body, fetched, cached, output_limit):
    when = datetime.fromtimestamp(fetched, TZ).strftime('%Y-%m-%d %H:%M:%S')
    footer = f'\n{"成功结果缓存，原查询时间" if cached else "本次查询时间"}：{when}（北京时间）。'
    footer += ' 缓存不代表实时结果。' if cached else ''
    if len(body) + len(footer) > output_limit:
        marker = '\n（输出已按设置截断；完整附件请看原页面。）'
        budget = output_limit - len(footer) - len(marker)
        kept = []
        for line in body.splitlines():
            if sum(len(x) + 1 for x in kept) + len(line) > budget:
                break
            kept.append(line)
        body = '\n'.join(kept) + marker
    return body + footer


def status_error(exc):
    match = re.fullmatch(r'远端返回 HTTP ([0-9]{3})。', str(exc))
    if match:
        code = int(match[1])
        return {404: '未找到该包、仓库或正式 Release（HTTP 404）；私有仓库也无法匿名查询。',
                403: '远端拒绝匿名请求或额度已用尽（HTTP 403），请稍后重试。',
                429: '远端请求限流（HTTP 429），请稍后重试。'}.get(code, f'远端返回 HTTP {code}，未获得当前结果。')
    return '查询未完成：' + str(exc)


def handle(pid, text, context):
    db = None
    try:
        need(pid in HELP, '未知插件。')
        need(isinstance(context, dict), '上下文无效。')
        identity(context)
        need(isinstance(text, str), '指令格式无效。')
        if text.strip() == ('/GitHub发行' if pid == 's_github' else '/包版本'):
            return HELP[pid]
        key, url, host, name = query(pid, text)
        ttl = setting(context, 'cache_seconds', 300, 0, 3600)
        timeout = setting(context, 'timeout_seconds', 15, 1, 15)
        output_limit = setting(context, 'max_output_chars', 4000, 500, 6000)
        db = connect(context)
        now = time.time()
        row = db.execute('SELECT body,fetched FROM cache WHERE key=?', (key,)).fetchone()
        if ttl and row and isinstance(row[0], str) and len(row[0]) <= 6000 and isinstance(row[1], (int, float)) and 0 <= now-row[1] < ttl:
            return display(row[0], row[1], True, output_limit)
        response = safe_net.request(url, timeout=timeout, max_bytes=PYPI_LIMIT if host == 'pypi.org' else LIMIT,
                                    max_redirects=0, allowed_hosts={host})
        if response.status != 200:
            raise safe_net.NetworkError(f'远端返回 HTTP {response.status}。')
        body = format_data(key, name, response.json())
        need(len(body) <= 6000, '远端元数据摘要过长。')
        fetched = time.time()
        if ttl:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO cache VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET body=excluded.body,fetched=excluded.fetched', (key, body, fetched))
            db.execute('DELETE FROM cache WHERE key IN (SELECT key FROM cache ORDER BY fetched DESC,key LIMIT -1 OFFSET 100)')
            db.commit()
        return display(body, fetched, False, output_limit)
    except RuleError as exc:
        if db:
            db.rollback()
        return str(exc)
    except safe_net.NetworkError as exc:
        if db:
            db.rollback()
        return status_error(exc) + ' 未使用过期缓存。'
    except (OSError, sqlite3.Error, TypeError, ValueError, AttributeError, OverflowError, UnicodeError):
        if db:
            db.rollback()
        return '查询或缓存状态不可用，未获得当前结果，也未使用过期缓存。'
    finally:
        if db:
            db.close()
