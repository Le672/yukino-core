"""Original stdlib-only, bounded developer tools for independent Yukino plugins.

Each public plugin archive embeds this file as _engine.py. No network, eval,
shell command or host configuration access is used. Regex execution alone uses
a fixed Python child process with an independent wall-clock timeout.
"""
from __future__ import annotations

import ast
import colorsys
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import difflib
from fractions import Fraction
from http import HTTPStatus
import io
import ipaddress
import json
import math
import mimetypes
import re
import subprocess
import sys
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid


class InputError(ValueError):
    pass


def setting(ctx, key, default, low, high):
    value = ctx.get('settings', {}).get(key, default)
    return max(low, min(high, value)) if type(value) is int else default


def need(condition, message):
    if not condition:
        raise InputError(message)


def number(value):
    value = str(value).strip()
    need(len(value) <= 200 and re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?', value), '请输入有限十进制数字。')
    result = Decimal(value)
    need(result.is_finite() and (not result or abs(result.adjusted()) <= 100), '数字超出支持范围（指数 ±100）。')
    return result


def fmt(value, digits=12):
    if isinstance(value, Fraction):
        return str(value.numerator) if value.denominator == 1 else f'{value.numerator}/{value.denominator}'
    if isinstance(value, Decimal):
        if not value:
            return '0'
        result = format(value, f'.{digits}g')
        return result.rstrip('0').rstrip('.') if '.' in result and 'e' not in result.lower() else result
    return str(value)


def strict_json(text, decimal_numbers=False):
    def bad_constant(value):
        raise InputError('JSON 不支持 NaN/Infinity。')
    value = json.loads(text, parse_constant=bad_constant, parse_float=Decimal if decimal_numbers else float)
    stack = [(value, 0)]
    visited = 0
    while stack:
        node, depth = stack.pop()
        visited += 1
        need(depth <= 40 and visited <= 10000, 'JSON 层级或条目过多。')
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)
    return value


def json_tool(command, arg, ctx):
    if command == '/JSON格式':
        need(arg, '用法：/JSON格式 {"name":"雪乃","items":[1,2]}')
        value = strict_json(arg)
        return 'JSON 格式化\n' + json.dumps(value, ensure_ascii=False, indent=setting(ctx, 'indent', 2, 1, 4), allow_nan=False)
    need('|' in arg, '用法：/JSON取值 user.name|{"user":{"name":"雪乃"}}；也支持 JSON Pointer /user/name。')
    path, raw = arg.split('|', 1)
    current = strict_json(raw)
    path = path.strip()
    if path.startswith('/'):
        tokens = [part.replace('~1', '/').replace('~0', '~') for part in path[1:].split('/')]
    elif path == '':
        tokens = []
    else:
        need(re.fullmatch(r'[^.\[\]]+(?:\[\d+\])*(?:\.[^.\[\]]+(?:\[\d+\])*)*', path), '路径格式错误；使用 a.b[0] 或 JSON Pointer。')
        tokens = re.findall(r'[^.\[\]]+', path)
    for token in tokens:
        if isinstance(current, dict):
            need(token in current, f'路径不存在：{token}')
            current = current[token]
        elif isinstance(current, list):
            need(token.isdigit() and int(token) < len(current), '数组下标越界或不是非负整数。')
            current = current[int(token)]
        else:
            raise InputError('路径经过了非容器值。')
    return 'JSON 取值\n' + json.dumps(current, ensure_ascii=False, indent=2, allow_nan=False)


def csv_rows(arg, ctx):
    delimiter = ctx.get('settings', {}).get('delimiter', ',')
    need(delimiter in (',', ';', '\t'), '分隔符配置只支持逗号、分号或制表符。')
    rows = []
    for row in csv.reader(io.StringIO(arg), delimiter=delimiter, strict=True):
        need(len(rows) < 1000 and len(row) <= 50, '最多 1000 行、50 列。')
        rows.append(row)
    need(rows and any(rows), '请提供 CSV 内容。')
    return rows


def csv_tool(command, arg, ctx):
    need(arg, '用法：/CSV统计 姓名,分数\n小明,90\n小红,80')
    rows = csv_rows(arg, ctx)
    header = ctx.get('settings', {}).get('has_header', True) is not False
    width = max(map(len, rows))
    names = rows[0] if header else [str(i + 1) for i in range(width)]
    values = rows[1:] if header else rows
    result = [f'CSV 统计：数据 {len(values)} 行，最大 {width} 列', f'列数不一致：{sum(len(row) != width for row in values)} 行']
    for col in range(width):
        cells = [row[col] if col < len(row) else '' for row in values]
        present = [c for c in cells if c.strip()]
        label = names[col] if col < len(names) and names[col] else f'列{col + 1}'
        detail = f'{label}：非空 {len(present)}，缺失 {len(cells) - len(present)}，不同值 {len(set(present))}'
        try:
            nums = [number(cell) for cell in present]
            if nums:
                detail += f'；最小 {fmt(min(nums))}，最大 {fmt(max(nums))}，平均 {fmt(sum(nums) / len(nums))}'
        except (ValueError, InvalidOperation):
            pass
        result.append(detail)
    return '\n'.join(result)


REGEX_CHILD = '''import json,re,sys
req=json.loads(sys.stdin.read())
try:
 p=re.compile(req['pattern'], re.IGNORECASE if req['ignore_case'] else 0)
 out=[]
 for m in p.finditer(req['text']):
  out.append({'start':m.start(),'end':m.end(),'text':m.group(0),'groups':m.groups()})
  if len(out)>=req['limit']: break
 print(json.dumps({'matches':out},ensure_ascii=False))
except re.error as exc:
 print(json.dumps({'error':str(exc)},ensure_ascii=False))
'''


def regex_tool(command, arg, ctx):
    if arg.lstrip().startswith('{'):
        obj = strict_json(arg)
        need(isinstance(obj, dict) and set(obj) == {'pattern', 'text'} and all(isinstance(v, str) for v in obj.values()), '正则 JSON 输入应为 {"pattern":"模式","text":"文本"}。')
        pattern, text = obj['pattern'], obj['text']
    else:
        need('|' in arg, '用法：/正则测试 模式|文本；含竖线的模式可用 {"pattern":"a|b","text":"abc"}。')
        pattern, text = arg.split('|', 1)
    need(0 < len(pattern) <= 300 and len(text) <= 4000, '模式或文本长度超限。')
    limit = setting(ctx, 'match_limit', 10, 1, 30)
    request = {'pattern': pattern, 'text': text, 'limit': limit, 'ignore_case': ctx.get('settings', {}).get('ignore_case', False) is True}
    try:
        process = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', REGEX_CHILD], input=json.dumps(request), text=True, encoding='utf-8', capture_output=True, timeout=setting(ctx, 'timeout_ms', 500, 200, 1500) / 1000, check=False)
    except subprocess.TimeoutExpired:
        return '正则执行超时，子进程已停止；请简化模式或缩短文本。'
    need(process.returncode == 0, '正则子进程无法完成。')
    result = json.loads(process.stdout)
    need('error' not in result, '正则语法错误：' + result.get('error', ''))
    matches = result['matches']
    if not matches:
        return '正则匹配：0 项。'
    lines = [f'正则匹配：{len(matches)} 项（最多 {limit} 项；下标从 0 起，末尾不含）']
    lines.extend(f"[{m['start']}:{m['end']}] {json.dumps(m['text'], ensure_ascii=False)}；分组 {json.dumps(m['groups'], ensure_ascii=False)}" for m in matches)
    return '\n'.join(lines)


CRON_LIMITS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
CRON_NAMES = ('分钟', '小时', '日', '月', '星期（0/7=周日）')


def cron_field(token, low, high):
    need(0 < len(token) <= 120, 'Cron 字段过长。')
    values = set()
    for part in token.split(','):
        need(part, 'Cron 列表包含空项。')
        pieces = part.split('/')
        need(len(pieces) <= 2 and pieces[0], 'Cron 步长格式错误。')
        step = int(pieces[1]) if len(pieces) == 2 and pieces[1].isdigit() else 1
        need(len(pieces) == 1 or (pieces[1].isdigit() and 1 <= step <= high - low + 1), 'Cron 步长超出范围。')
        base = pieces[0]
        if base == '*':
            first, last = low, high
        elif '-' in base:
            pair = base.split('-')
            need(len(pair) == 2 and all(x.isdigit() for x in pair), 'Cron 区间格式错误。')
            first, last = map(int, pair)
        else:
            need(base.isdigit(), '仅支持数字、*、逗号、区间和步长的标准 5 字段 Cron。')
            first = int(base)
            last = high if len(pieces) == 2 else first
        need(low <= first <= last <= high, 'Cron 数值超出字段范围。')
        values.update(range(first, last + 1, step))
    return values


def cron_parse(expression):
    tokens = expression.split()
    need(len(tokens) == 5, 'Cron 必须为 5 个字段：分钟 小时 日 月 星期。')
    fields = [cron_field(token, *bounds) for token, bounds in zip(tokens, CRON_LIMITS)]
    if 7 in fields[4]:
        fields[4].discard(7)
        fields[4].add(0)
    return tokens, fields


def cron_tool(command, arg, ctx):
    expression, _, start_text = arg.partition('|')
    tokens, fields = cron_parse(expression.strip())
    if command == '/Cron说明':
        descriptions = [f'{name}：' + ','.join(map(str, sorted(values))) for name, values in zip(CRON_NAMES, fields)]
        return 'Cron 规则\n' + '\n'.join(descriptions) + '\n日与星期均受限时采用标准 OR；不含秒字段，不负责执行任务。'
    tz = timezone(timedelta(hours=setting(ctx, 'utc_offset_hours', 8, -12, 14)))
    start = datetime.fromisoformat(start_text.strip()) if start_text.strip() else datetime.now(tz)
    start = start.replace(tzinfo=tz) if start.tzinfo is None else start.astimezone(tz)
    start = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    count = setting(ctx, 'result_count', 3, 1, 5)
    horizon = setting(ctx, 'lookahead_days', 1096, 1, 1096)
    result = []
    for offset in range(horizon):
        day = (start + timedelta(days=offset)).date()
        if day.month not in fields[3]:
            continue
        dom, dow = day.day in fields[2], ((day.weekday() + 1) % 7) in fields[4]
        # Vixie cron treats a field beginning with '*' (including */step) as
        # wildcard syntax, so DOM/DOW use AND if either field has that flag.
        matches = dom and dow if tokens[2].startswith('*') or tokens[4].startswith('*') else dom or dow
        if not matches:
            continue
        for hour in sorted(fields[1]):
            for minute in sorted(fields[0]):
                candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
                if candidate >= start:
                    result.append(candidate.isoformat(timespec='minutes'))
                    if len(result) == count:
                        return 'Cron 下次触发（严格晚于起点）\n' + '\n'.join(result)
    return 'Cron 下次触发\n' + ('\n'.join(result) + '\n' if result else '') + f'扫描 {horizon} 天后未找到足够的日期；不会无限扫描。'


def uuid_tool(command, arg, ctx):
    if command == '/UUID解析':
        need(arg, '用法：/UUID解析 550e8400-e29b-41d4-a716-446655440000')
        value = uuid.UUID(arg.strip())
        return f'UUID 解析\n标准形式：{value}\n版本：{value.version}\n变体：{value.variant}\n整数：{value.int}\nURN：{value.urn}'
    if arg.startswith('5 '):
        need('|' in arg, '用法：/UUID 5 dns|example.com；命名空间 dns/url/oid/x500 或 UUID。')
        raw_namespace, name = arg[2:].split('|', 1)
        presets = {'dns': uuid.NAMESPACE_DNS, 'url': uuid.NAMESPACE_URL, 'oid': uuid.NAMESPACE_OID, 'x500': uuid.NAMESPACE_X500}
        namespace = presets.get(raw_namespace.strip().lower())
        if namespace is None:
            namespace = uuid.UUID(raw_namespace.strip())
        need(0 < len(name) <= 1000, 'UUID 名称应为 1～1000 字符。')
        return 'UUID v5（同命名空间和名称得到同值）\n' + str(uuid.uuid5(namespace, name))
    pieces = arg.split()
    need(not pieces or pieces[0] == '4', '支持 /UUID、/UUID 4 数量、/UUID 5 dns|名称。')
    need(len(pieces) <= 2 and (len(pieces) < 2 or pieces[1].isdigit()), 'UUID 数量格式错误。')
    count = int(pieces[1]) if len(pieces) == 2 else setting(ctx, 'count', 1, 1, 10)
    need(1 <= count <= 10, '一次最多生成 10 个 UUID。')
    return 'UUID v4\n' + '\n'.join(str(uuid.uuid4()) for _ in range(count))


def ip_tool(command, arg, ctx):
    need(arg and len(arg) < 160, '用法：/IP信息 192.168.1.10/24 或 2001:db8::1/64；仅离线地址分析。')
    interface = ipaddress.ip_interface(arg.strip())
    address, network = interface.ip, interface.network
    usable = network.num_addresses
    if address.version == 4 and network.prefixlen < 31:
        usable -= 2
    return '\n'.join(['IP 离线分析', f'版本：IPv{address.version}', f'地址：{address.compressed}', f'网络：{network.with_prefixlen}', f'掩码：{network.netmask}', f'首地址：{network.network_address}', f'末地址：{network.broadcast_address}', f'地址总数：{network.num_addresses}；常规可用：{usable}', f'私有/非全局：{address.is_private}；全局可路由：{address.is_global}', f'回环：{address.is_loopback}；链路本地：{address.is_link_local}；组播：{address.is_multicast}', f'反向 DNS 名称：{address.reverse_pointer}', '不查询归属地、运营商、风险或连通性。'])


def url_tool(command, arg, ctx):
    need(arg and len(arg) <= 8000, '用法：/链接信息 https://example.com/path?a=1；或 /清理链接 URL')
    need(not re.search(r'[\x00-\x20\x7f]', arg), '链接不得包含控制符或未编码空格。')
    parts = urlsplit(arg)
    need(parts.scheme in ('http', 'https') and parts.hostname, '只支持带主机名的 http/https 链接。')
    port = parts.port
    need(parts.username is None and parts.password is None, '不接受含用户名或密码的 URL。')
    params = parse_qsl(parts.query, keep_blank_values=True, max_num_fields=100)
    if command == '/清理链接':
        trackers = {'fbclid', 'gclid', 'dclid', 'msclkid', 'spm', 'spm_id_from', 'vd_source', 'from_spmid', 'from', 'share_source', 'share_medium'}
        filtered = [(key, value) for key, value in params if not key.lower().startswith('utm_') and key.lower() not in trackers]
        result = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(filtered), parts.fragment))
        return f'清理链接（移除 {len(params) - len(filtered)} 个已知跟踪参数）\n{result}'
    return '\n'.join(['链接信息（未访问）', f'协议：{parts.scheme}', f'主机：{parts.hostname}', f'端口：{port or (443 if parts.scheme == "https" else 80)}', f'路径：{parts.path or "/"}', f'查询参数：{len(params)} 个；名称：' + ', '.join(key for key, _ in params), f'片段：{parts.fragment or "无"}'])


def color_parse(raw):
    raw = raw.strip()
    if re.fullmatch(r'#[0-9a-fA-F]{3}', raw):
        return tuple(int(c * 2, 16) for c in raw[1:])
    if re.fullmatch(r'#?[0-9a-fA-F]{6}', raw):
        raw = raw.lstrip('#')
        return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4))
    if raw.lower().startswith('rgb('):
        need(raw.endswith(')'), 'rgb() 缺少闭合括号。')
        raw = raw[4:-1]
    match = re.fullmatch(r'\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*', raw)
    need(match, '颜色支持 #RGB、#RRGGBB 或 rgb(255,0,0)。')
    rgb = tuple(map(int, match.groups()))
    need(all(0 <= x <= 255 for x in rgb), 'RGB 分量必须为 0～255。')
    return rgb


def luminance(rgb):
    values = [x / 255 for x in rgb]
    linear = [x / 12.92 if x <= 0.04045 else ((x + .055) / 1.055) ** 2.4 for x in values]
    return sum(weight * channel for weight, channel in zip((.2126, .7152, .0722), linear))


def color_tool(command, arg, ctx):
    if command == '/对比度':
        need('|' in arg, '用法：/对比度 #000000|#ffffff')
        first, second = map(color_parse, arg.split('|', 1))
        light, dark = sorted((luminance(first), luminance(second)), reverse=True)
        ratio = (light + .05) / (dark + .05)
        return f'颜色对比度：{ratio:.2f}:1\nWCAG AA 普通文字（4.5）：{"通过" if ratio >= 4.5 else "未通过"}\nAAA 普通文字（7）：{"通过" if ratio >= 7 else "未通过"}\nAA 大号文字（3）：{"通过" if ratio >= 3 else "未通过"}'
    rgb = color_parse(arg)
    hue, light, saturation = colorsys.rgb_to_hls(*(x / 255 for x in rgb))
    recommended = '#000000' if (luminance(rgb) + .05) / .05 >= 1.05 / (luminance(rgb) + .05) else '#FFFFFF'
    return f'颜色转换\nHEX：#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}\nRGB：{rgb}\nHSL：{hue * 360:.2f}°, {saturation * 100:.2f}%, {light * 100:.2f}%\n相对亮度：{luminance(rgb):.6f}\n黑白文字中对比度更高：{recommended}'


UNIT_GROUPS = {
    '长度': {'m': '1', 'km': '1000', 'cm': '.01', 'mm': '.001', 'in': '.0254', 'ft': '.3048', 'yd': '.9144', 'mi': '1609.344'},
    '质量': {'kg': '1', 'g': '.001', 'mg': '.000001', 't': '1000', 'lb': '.45359237', 'oz': '.028349523125'},
    '速度': {'m/s': '1', 'km/h': '.2777777777777777777777777777777777777778', 'mph': '.44704', 'knot': '.5144444444444444444444444444444444444444'},
    '面积': {'m2': '1', 'km2': '1000000', 'cm2': '.0001', 'ha': '10000'},
    '体积': {'m3': '1', 'l': '.001', 'ml': '.000001', 'gal': '.003785411784'},
    '数据': {'b': '1', 'kb': '1000', 'mb': '1000000', 'gb': '1000000000', 'kib': '1024', 'mib': '1048576', 'gib': '1073741824'},
    '时间间隔': {'s': '1', 'min': '60', 'h': '3600', 'day': '86400'},
}


def units_tool(command, arg, ctx):
    pieces = arg.split()
    need(len(pieces) == 3, '用法：/单位换算 100 km m；温度 C/F/K；长度/质量/速度/面积/体积/数据/时间。')
    value = number(pieces[0])
    source, target = pieces[1].lower().replace('°', ''), pieces[2].lower().replace('°', '')
    if source in ('c', 'f', 'k') and target in ('c', 'f', 'k'):
        kelvin = value + Decimal('273.15') if source == 'c' else (value - 32) * 5 / 9 + Decimal('273.15') if source == 'f' else value
        need(kelvin >= 0, '温度不能低于绝对零度。')
        result = kelvin - Decimal('273.15') if target == 'c' else (kelvin - Decimal('273.15')) * 9 / 5 + 32 if target == 'f' else kelvin
        dimension = '温度'
    else:
        dimension = next((name for name, units in UNIT_GROUPS.items() if source in units and target in units), None)
        need(dimension, '单位未知或量纲不一致；数据单位 b 表示字节，gal 为美制液体加仑。')
        factors = UNIT_GROUPS[dimension]
        result = value * Decimal(factors[source]) / Decimal(factors[target])
    return f'单位换算（{dimension}）\n{fmt(value)} {pieces[1]} = {fmt(result, setting(ctx, "precision", 14, 4, 28))} {pieces[2]}'


def radix_tool(command, arg, ctx):
    pieces = arg.split()
    need(len(pieces) == 3 and pieces[1].isdigit() and pieces[2].isdigit(), '用法：/进制转换 FF 16 2；进制范围 2～36。')
    raw, source, target = pieces[0], int(pieces[1]), int(pieces[2])
    need(2 <= source <= 36 and 2 <= target <= 36 and len(raw) <= 600, '进制或数字长度超限。')
    need(re.fullmatch(r'[+-]?[0-9A-Za-z]+', raw), '数字只能含该进制的数字/字母及开头符号。')
    value = int(raw, source)
    need(abs(value).bit_length() <= 2048, '数字最多 2048 位二进制。')
    remaining, digits = abs(value), ''
    while remaining:
        remaining, digit = divmod(remaining, target)
        digits = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'[digit] + digits
    return f'进制转换：{source} → {target}\n' + ('-' if value < 0 else '') + (digits or '0')


def unicode_tool(command, arg, ctx):
    if command == '/文本规范化':
        need('|' in arg, '用法：/文本规范化 NFKC|ＡＢＣ；可选 NFC/NFD/NFKC/NFKD。')
        form, value = arg.split('|', 1)
        form = form.strip().upper()
        need(form in ('NFC', 'NFD', 'NFKC', 'NFKD'), '规范化形式必须为 NFC/NFD/NFKC/NFKD。')
        result = unicodedata.normalize(form, value)
        return f'Unicode {form} 规范化\n{result}\n码点数：{len(value)} → {len(result)}'
    need(arg and len(arg) <= setting(ctx, 'character_limit', 40, 1, 80), '请提供不超过配置上限的文字（默认 40 码点）。')
    lines = ['Unicode 字符信息（按码点；组合字形可含多个码点）']
    for char in arg:
        display = json.dumps(char, ensure_ascii=False)
        lines.append(f'{display} U+{ord(char):04X} {unicodedata.name(char, "UNNAMED")}；类别 {unicodedata.category(char)}；UTF-8 {char.encode("utf-8").hex(" ")}')
    return '\n'.join(lines)


def markdown_tool(command, arg, ctx):
    need(arg, '请在命令后提供 Markdown 文本或 CSV 表格内容。')
    if command == '/Markdown表格':
        rows = csv_rows(arg, ctx)
        width = max(map(len, rows))
        need(width <= 20, 'Markdown 表格最多 20 列。')
        escaped = [[cell.replace('\\', '\\\\').replace('|', '\\|').replace('\n', '<br>') for cell in row] + [''] * (width - len(row)) for row in rows]
        output = ['| ' + ' | '.join(escaped[0]) + ' |', '| ' + ' | '.join(['---'] * width) + ' |']
        output.extend('| ' + ' | '.join(row) + ' |' for row in escaped[1:])
        return 'Markdown 表格\n' + '\n'.join(output)
    headings, slugs = [], {}
    fence_char, fence_length = None, 0
    for line in arg.splitlines():
        fence = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if fence:
            mark = fence.group(1)
            if fence_char is None:
                fence_char, fence_length = mark[0], len(mark)
            elif mark[0] == fence_char and len(mark) >= fence_length:
                fence_char = None
            continue
        if fence_char:
            continue
        match = re.match(r'^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$', line)
        if match:
            level, title = len(match.group(1)), match.group(2)
            slug = re.sub(r'[^\w\-\s]', '', title.lower()).strip().replace(' ', '-')
            index = slugs.get(slug, 0)
            slugs[slug] = index + 1
            anchor = slug + (f'-{index}' if index else '')
            headings.append('  ' * (level - 1) + f'- [{title}](#{anchor})')
    return 'Markdown 目录（ATX 标题；通用锚点规则，站点渲染器可能不同）\n' + ('\n'.join(headings) if headings else '未找到标题。')


def diff_tool(command, arg, ctx):
    if arg.lstrip().startswith('{'):
        obj = strict_json(arg)
        need(isinstance(obj, dict) and set(obj) == {'a', 'b'} and all(isinstance(v, str) for v in obj.values()), 'JSON 应为 {"a":"旧文本","b":"新文本"}。')
        left, right = obj['a'], obj['b']
    else:
        need('|' in arg, '用法：/文本差异 旧文本|新文本；多行/含竖线可用 {"a":"…","b":"…"}。')
        left, right = arg.split('|', 1)
    a, b = left.splitlines(), right.splitlines()
    need(len(a) <= 200 and len(b) <= 200 and len(left) + len(right) <= 16000, '每段最多 200 行，总长 16000。')
    if left == right:
        return '文本差异：内容完全相同。'
    lines = list(difflib.unified_diff(a, b, fromfile='旧文本', tofile='新文本', n=setting(ctx, 'context_lines', 2, 0, 5), lineterm=''))
    similarity = difflib.SequenceMatcher(None, left, right, autojunk=True).ratio()
    if not lines:
        lines = ['正文行相同，差异仅在末尾换行符。']
    return f'文本差异；字符相似度 {similarity:.2%}\n' + '\n'.join(lines)


SEMVER = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$')


def semver_parse(raw):
    need(len(raw) <= 160, '版本号过长。')
    match = SEMVER.fullmatch(raw)
    need(match, '请使用严格 SemVer：主.次.补丁[-预发行][+构建]。')
    prerelease = match.group(4).split('.') if match.group(4) else []
    need(all(not (part.isdigit() and len(part) > 1 and part.startswith('0')) for part in prerelease), '数字预发行标识不能有前导零。')
    return tuple(map(int, match.group(1, 2, 3))), prerelease


def semver_compare(left, right):
    first, a = semver_parse(left)
    second, b = semver_parse(right)
    if first != second:
        return (first > second) - (first < second)
    if not a or not b:
        return (bool(b) > bool(a)) - (bool(b) < bool(a))
    for x, y in zip(a, b):
        if x == y:
            continue
        if x.isdigit() and y.isdigit():
            return (int(x) > int(y)) - (int(x) < int(y))
        if x.isdigit() != y.isdigit():
            return -1 if x.isdigit() else 1
        return (x > y) - (x < y)
    return (len(a) > len(b)) - (len(a) < len(b))


def semver_tool(command, arg, ctx):
    pieces = arg.split('|') if '|' in arg else arg.split()
    need(len(pieces) == 2, '用法：/版本比较 1.2.3-alpha.2 1.2.3-alpha.10')
    left, right = (piece.strip() for piece in pieces)
    result = semver_compare(left, right)
    return f'SemVer 比较：{left} {"<" if result < 0 else ">" if result > 0 else "="} {right}\n构建元数据不参与优先级；数字预发行标识按数值比较。'


def http_tool(command, arg, ctx):
    if command == '/MIME':
        need(arg and len(arg) <= 200, '用法：/MIME image.png 或 /MIME application/json')
        if '/' in arg and not arg.startswith(('http:', 'https:')):
            extensions = mimetypes.guess_all_extensions(arg.lower(), strict=False)
            need(extensions, '本机标准库 MIME 表中没有该类型。')
            return f'MIME 类型：{arg}\n常见扩展名：' + ', '.join(sorted(set(extensions)))
        filename = 'file' + arg if arg.startswith('.') else arg
        mime, encoding = mimetypes.guess_type(filename, strict=False)
        need(mime, '无法从扩展名确定 MIME。')
        return f'MIME 查询\n类型：{mime}\n压缩编码：{encoding or "无"}\n仅按扩展名推断，未读取文件。'
    need(arg.isdigit() and 100 <= int(arg) <= 599, '用法：/HTTP状态 404；范围 100～599。')
    status = int(arg)
    try:
        item = HTTPStatus(status)
    except ValueError:
        return f'HTTP {status}：标准库没有注册该状态；类别：{status // 100}xx。'
    return f'HTTP {status} {item.phrase}\n{item.description or "标准状态码"}\n类别：' + {1: '信息响应', 2: '成功', 3: '重定向', 4: '客户端错误', 5: '服务器错误'}[status // 100]


def epoch_tool(command, arg, ctx):
    need(arg, '用法：/时间戳转换 1700000000；支持 ms:1700000000000、s:1700000000 或 ISO 日期时间。')
    tz = timezone(timedelta(hours=setting(ctx, 'utc_offset_hours', 8, -12, 14)))
    prefix, _, raw = arg.partition(':')
    if prefix in ('s', 'ms') or re.fullmatch(r'[+-]?\d+(?:\.\d+)?', arg):
        numeric = number(raw if prefix in ('s', 'ms') else arg)
        is_ms = prefix == 'ms' or (prefix not in ('s', 'ms') and abs(numeric) >= Decimal('100000000000'))
        seconds = numeric / 1000 if is_ms else numeric
        need(Decimal('-62135596800') <= seconds < Decimal('253402300799'), '时间戳超出 0001～9999 年。')
        value = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=int(seconds * 1000000))
        source = '毫秒' if is_ms else '秒'
    else:
        value = datetime.fromisoformat(arg.replace('Z', '+00:00'))
        value = value.replace(tzinfo=tz) if value.tzinfo is None else value
        delta = value.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        seconds = Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 1000000
        source = 'ISO 日期（无偏移时使用配置时区）'
    return f'时间戳转换；输入解释：{source}\nUnix 秒：{fmt(seconds, 20)}\nUnix 毫秒：{fmt(seconds * 1000, 23)}\nUTC：{value.astimezone(timezone.utc).isoformat()}\n本地：{value.astimezone(tz).isoformat()}'


def calculate(expression, fractional=False, precision=28):
    need(0 < len(expression) <= 600, '表达式长度应为 1～600。')
    tree = ast.parse(expression, mode='eval')
    need(sum(1 for _ in ast.walk(tree)) <= 80, '表达式节点过多。')
    constructor = Fraction if fractional else Decimal

    def visit(node, depth=0):
        need(depth <= 12, '表达式嵌套过深。')
        if isinstance(node, ast.Expression):
            return visit(node.body, depth + 1)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            raw = ast.get_source_segment(expression, node)
            return constructor(str(number(raw)))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand, depth + 1)
            return -value if isinstance(node.op, ast.USub) else value
        need(isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)), '仅支持数字、括号及 + - * / // % **，禁止函数、变量和代码。')
        left, right = visit(node.left, depth + 1), visit(node.right, depth + 1)
        if isinstance(node.op, ast.Pow):
            need(right == int(right) and -20 <= right <= 20, '幂指数必须为 -20～20 的整数。')
            result = left ** int(right)
        elif isinstance(node.op, ast.Add): result = left + right
        elif isinstance(node.op, ast.Sub): result = left - right
        elif isinstance(node.op, ast.Mult): result = left * right
        elif isinstance(node.op, ast.Div): result = left / right
        elif isinstance(node.op, ast.FloorDiv): result = left // right
        else: result = left % right
        if fractional:
            result = Fraction(result)
            need(max(result.numerator.bit_length(), result.denominator.bit_length()) <= 8192, '分数计算结果超出容量。')
        else:
            need(result.is_finite() and (not result or abs(result.adjusted()) <= 1000), '计算结果超出范围。')
        return result

    with localcontext() as context:
        context.prec, context.Emax, context.Emin = precision, 1100, -1100
        return visit(tree)


def decimal_tool(command, arg, ctx):
    fractional = arg.startswith('分数 ')
    expression = arg[3:].strip() if fractional else arg
    precision = setting(ctx, 'precision', 28, 10, 80)
    value = calculate(expression, fractional=fractional, precision=precision)
    return f'{"有理数精确计算" if fractional else f"Decimal 计算（有效位数 {precision}）"}\n{expression} = {fmt(value, precision)}'


def matrix_parse(raw):
    obj = strict_json(raw, decimal_numbers=True)
    need(isinstance(obj, list) and 1 <= len(obj) <= 6 and all(isinstance(row, list) and 1 <= len(row) <= 6 for row in obj), '矩阵必须为 1～6 行、1～6 列的二维 JSON 数组。')
    need(len({len(row) for row in obj}) == 1, '矩阵各行列数必须相同。')
    need(all(type(value) in (int, Decimal) for row in obj for value in row), '矩阵元素必须为有限数字。')
    return [[Fraction(number(str(value))) for value in row] for row in obj]


def matrix_show(matrix):
    return '\n'.join('[ ' + ', '.join(fmt(value) for value in row) + ' ]' for row in matrix)


def matrix_tool(command, arg, ctx):
    operation, _, raw = arg.partition(' ')
    need(raw and operation in ('转置', '乘法', '行列式'), '用法：/矩阵 转置 [[1,2],[3,4]]；乘法 A|B；行列式 A。最多 6×6。')
    if operation == '乘法':
        need('|' in raw, '矩阵乘法需要 A|B。')
        a, b = map(matrix_parse, raw.split('|', 1))
        need(len(a[0]) == len(b), '乘法维度不匹配。')
        result = [[sum((a[i][k] * b[k][j] for k in range(len(b))), Fraction(0)) for j in range(len(b[0]))] for i in range(len(a))]
        return '矩阵乘法\n' + matrix_show(result)
    a = matrix_parse(raw)
    if operation == '转置':
        return '矩阵转置\n' + matrix_show(list(map(list, zip(*a))))
    need(len(a) == len(a[0]), '只有方阵可以求行列式。')
    determinant = Fraction(1)
    for col in range(len(a)):
        pivot = next((i for i in range(col, len(a)) if a[i][col]), None)
        if pivot is None:
            return '矩阵行列式：0（奇异矩阵）'
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
            determinant *= -1
        factor = a[col][col]
        determinant *= factor
        for row in range(col + 1, len(a)):
            ratio = a[row][col] / factor
            for k in range(col + 1, len(a)):
                a[row][k] -= ratio * a[col][k]
    return '矩阵行列式：' + fmt(determinant)


def stats_tool(command, arg, ctx):
    tokens = [piece for piece in re.split(r'[,，;\s]+', arg.strip()) if piece]
    need(1 <= len(tokens) <= 1000, '用法：/数据统计 1,2,3,4；支持 1～1000 个有限数字。')
    values = sorted(number(piece) for piece in tokens)
    count = len(values)
    nonzero = [value for value in values if value]
    integer_digits = max(1, max((value.adjusted() + 1 for value in nonzero), default=1))
    places = max(0, -min((value.as_tuple().exponent for value in nonzero), default=0))
    # Preserve small spreads around large origins; variance is computed from
    # centered offsets instead of subtracting a rounded large mean.
    with localcontext() as context:
        context.prec = max(80, integer_digits + places + 20)
        origin = values[0]
        offsets = [value - origin for value in values]
        mean_offset = sum(offsets) / count
        mean = origin + mean_offset
        variance = sum((value - mean_offset) ** 2 for value in offsets) / count
        display_digits = max(12, integer_digits + max(4, places))

        def shown(value):
            return fmt(value, display_digits)

        def percentile(p):
            position = (count - 1) * p
            low = int(position)
            return values[low] + (values[min(low + 1, count - 1)] - values[low]) * (position - low)

        frequencies = {}
        for value in values:
            frequencies[value] = frequencies.get(value, 0) + 1
        most = max(frequencies.values())
        modes = [value for value, n in frequencies.items() if n == most]
        return '\n'.join(['数据统计（分位数采用线性插值）', f'数量：{count}；总和：{shown(sum(values))}', f'最小：{shown(values[0])}；最大：{shown(values[-1])}；极差：{shown(values[-1] - values[0])}', f'平均：{shown(mean)}；中位数：{shown(percentile(Decimal(".5")))}', f'Q1：{shown(percentile(Decimal(".25")))}；Q3：{shown(percentile(Decimal(".75")))}', f'总体方差：{fmt(variance)}；总体标准差：{fmt(variance.sqrt())}', '样本方差：' + (fmt(variance * count / (count - 1)) if count > 1 else '至少需要 2 个数据'), '众数：' + (', '.join(shown(value) for value in modes[:10]) + (f' 等 {len(modes)} 个' if len(modes) > 10 else '') if most > 1 else '无重复值')])


def probability_tool(command, arg, ctx):
    pieces = arg.split()
    need(pieces and pieces[0] in ('组合', '排列', '二项'), '用法：/概率计算 组合 10 3；排列 10 3；二项 10 3 0.5。')
    operation = pieces[0]
    need(len(pieces) == (4 if operation == '二项' else 3) and pieces[1].isdigit() and pieces[2].isdigit(), '参数应为 n k（非负整数）；二项分布另加单次概率 p。')
    n, k = int(pieces[1]), int(pieces[2])
    need(0 <= k <= n <= (500 if operation == '二项' else 2000), '要求 0≤k≤n；组合/排列 n≤2000，二项 n≤500。')
    if operation != '二项':
        value = math.comb(n, k) if operation == '组合' else math.perm(n, k)
        need(value.bit_length() <= 10000, '结果过大；请减小参数。')
        return f'{operation}计算：{"C" if operation == "组合" else "P"}({n},{k}) = {value}'
    p = number(pieces[3])
    need(0 <= p <= 1, '概率 p 必须在 0～1。')
    def term(i):
        return Decimal(math.comb(n, i)) * (p ** i if i else Decimal(1)) * ((1 - p) ** (n - i) if n - i else Decimal(1))
    exact = term(k)
    cumulative = sum(term(i) for i in range(k + 1))
    return f'二项分布 X~B({n},{p})\nP(X={k}) = {fmt(exact, 16)}\nP(X≤{k}) = {fmt(cumulative, 16)}\n期望：{fmt(n * p)}；方差：{fmt(n * p * (1 - p))}\n适用于独立、同概率的伯努利试验。'


def polynomial_coefficients(raw):
    if raw.strip().startswith('['):
        obj = strict_json(raw, decimal_numbers=True)
        need(isinstance(obj, list) and all(type(item) in (int, Decimal) for item in obj), '系数必须是数字数组。')
        tokens = list(map(str, obj))
    else:
        tokens = [part for part in re.split(r'[,，\s]+', raw.strip()) if part]
    need(1 <= len(tokens) <= 21, '多项式最多 21 个系数，按最高次到常数排列。')
    return [number(token) for token in tokens]


def polynomial_tool(command, arg, ctx):
    operation, _, raw = arg.partition(' ')
    need(raw and operation in ('求值', '导数', '二次'), '用法：/多项式 求值 1,-3,2|3；导数 1,-3,2；二次 1 -3 2。')
    if operation == '求值':
        need('|' in raw, '求值需要 系数列表|x。')
        raw_coefficients, point = raw.split('|', 1)
        coefficients, x = polynomial_coefficients(raw_coefficients), number(point)
        value = Decimal(0)
        for coefficient in coefficients:
            value = value * x + coefficient
        return f'多项式求值（Horner 法）\nx={fmt(x)}；P(x)={fmt(value, 20)}'
    coefficients = polynomial_coefficients(raw)
    if operation == '导数':
        derivative = [coefficient * (len(coefficients) - index - 1) for index, coefficient in enumerate(coefficients[:-1])] or [Decimal(0)]
        return '多项式导数系数（最高次到常数）\n[' + ', '.join(fmt(value) for value in derivative) + ']'
    need(len(coefficients) == 3, '二次方程需要 a b c 三个系数。')
    a, b, c = coefficients
    if not a:
        if b:
            return '退化为一元一次方程：x=' + fmt(-c / b, 20)
        return '方程恒成立（任意 x）' if not c else '方程无解。'
    nonzero = [value for value in coefficients if value]
    span = max(value.adjusted() for value in nonzero) - min(value.as_tuple().exponent for value in nonzero) + 1
    with localcontext() as context:
        context.prec = max(80, 2 * span + 20)
        discriminant = b * b - 4 * a * c
        if discriminant < 0:
            real, imaginary = -b / (2 * a), (-discriminant).sqrt() / (2 * abs(a))
            return f'二次方程：判别式 {fmt(discriminant)}\nx={fmt(real, 20)} ± {fmt(imaginary, 20)}i'
        root = discriminant.sqrt()
        # Choose the sign that adds magnitudes, avoiding cancellation in the
        # small root. Vieta's product then recovers the second root accurately.
        q = -(b + (root if b >= 0 else -root)) / 2
        first = q / a
        second = c / q if q else -b / (2 * a)
        return f'二次方程：判别式 {fmt(discriminant)}\nx₁={fmt(first, 20)}\nx₂={fmt(second, 20)}'


TOOLS = {
    'x_json': json_tool, 'x_csv': csv_tool, 'x_regex': regex_tool,
    'x_cron': cron_tool, 'x_uuid': uuid_tool, 'x_ip': ip_tool,
    'x_url': url_tool, 'x_color': color_tool, 'x_units': units_tool,
    'x_radix': radix_tool, 'x_unicode': unicode_tool, 'x_markdown': markdown_tool,
    'x_diff': diff_tool, 'x_semver': semver_tool, 'x_http': http_tool,
    'x_epoch': epoch_tool, 'x_decimal': decimal_tool, 'x_matrix': matrix_tool,
    'x_stats': stats_tool, 'x_probability': probability_tool, 'x_polynomial': polynomial_tool,
}


def handle(kind, text, context):
    try:
        need(isinstance(text, str) and len(text) <= 24000, '命令总长最多 24000 字符。')
        parts = text.strip().split(maxsplit=1)
        need(parts, '请输入命令。')
        command, arg = parts[0], parts[1] if len(parts) == 2 else ''
        with localcontext() as decimal_context:
            decimal_context.prec = 50
            result = TOOLS[kind](command, arg, context or {})
        limit = setting(context or {}, 'max_output_chars', 6000, 1000, 12000)
        if len(result) > limit:
            return result[:limit] + '\n（输出已达配置上限；请缩小输入后重试。）'
        return result
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, ArithmeticError, csv.Error, RecursionError, OSError, SyntaxError) as exc:
        message = str(exc).replace('\n', ' ')[:250]
        return '输入错误：' + (message or '参数无效或数值超出范围。')
