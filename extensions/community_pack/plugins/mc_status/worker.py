"""Minecraft Java status through fixed mcsrvstat.us origin, never direct sockets."""
import importlib.util
import ipaddress
import re
import urllib.parse
from pathlib import Path

_spec = importlib.util.spec_from_file_location('mc_status_net', Path(__file__).with_name('_net.py'))
net = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(net)

def validate_host(value):
    if not value or len(value) > 260 or any(x in value for x in '/\\@?#%'):
        raise net.LookupFailure('请输入公网主机名或公网IP，可附加 :端口；不接受URL或内网地址')
    if value.startswith('['):
        match = re.fullmatch(r'\[([0-9a-fA-F:]+)\](?::([0-9]{1,5}))?', value)
        if not match:
            raise net.LookupFailure('IPv6 应使用 [公网IPv6]:端口 格式')
        host, port = match.groups()
    else:
        if value.count(':') > 1:
            raise net.LookupFailure('IPv6 应使用 [公网IPv6]:端口 格式')
        host, sep, port = value.partition(':')
        port = port if sep else None
    if port is not None and (not port.isdigit() or not 1 <= int(port) <= 65535):
        raise net.LookupFailure('端口范围应为1～65535')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        host = host.lower()
        # Require a real multi-label DNS name. Block local names and alternative numeric IP forms.
        if (host.endswith(('.localhost','.local','.internal','.lan','.home','.invalid','.test','.example'))
                or host in ('localhost','localhost.localdomain') or len(host) > 253
                or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', host)):
            raise net.LookupFailure('仅支持公网域名或公网IP，不接受本地、内网、测试地址')
    else:
        if not address.is_global or address.is_multicast:
            raise net.LookupFailure('不允许查询本地、内网或保留IP地址')
        host = '['+address.compressed+']' if address.version == 6 else address.compressed
    return host + (':'+str(int(port)) if port is not None else '')

def handle(text, context):
    net.begin()
    try:
        query = net.argument(text, {'/mc'}, 260)
        if query is None:
            return None
        if not query:
            return '用法：/mc mc.hypixel.net 或 /mc 公网主机:25565（Java版）'
        target = validate_host(query)
        data = net.fetch('https://api.mcsrvstat.us/3/'+urllib.parse.quote(target, safe=''), ttl=300)
        if not isinstance(data.get('online'), bool):
            raise ValueError()
        if not data['online']:
            return 'Minecraft Java｜'+target+'\n状态：离线或第三方探测不可达。\n来源：mcsrvstat.us；结果缓存最长5分钟，不等同于本机网络测试。'
        players = data.get('players', {})
        lines = ['Minecraft Java｜'+target, '状态：在线',
            '版本：'+net.clean(data.get('version','未知')),
            '玩家：'+net.clean(players.get('online','未知'))+'/'+net.clean(players.get('max','未知'))]
        motd = data.get('motd', {}).get('clean', [])
        if isinstance(motd, list) and motd:
            lines.append('简介：'+' / '.join(net.clean(x, 120) for x in motd[:3]))
        lines.append('来源：mcsrvstat.us 第三方探测；结果缓存最长5分钟，不提供本机延迟或RCON操作。')
        result='\n'.join(lines);icon=data.get('icon','')
        if context.get('rich_result_version') and isinstance(icon,str) and icon.startswith('data:image/png;base64,') and len(icon)<50000:
            return {'version':1,'text':result,'media':[{'type':'image','data':icon,'name':'server-icon.png'}]}
        return result
    except Exception as exc:
        return net.failure('Minecraft', exc)
