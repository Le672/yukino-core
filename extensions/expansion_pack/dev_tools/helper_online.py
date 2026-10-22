"""Actual public IP/DNS/site lookups; no LAN access or inferred risk scores."""
import importlib.util
import ipaddress
from pathlib import Path
import re
import time
from datetime import datetime,timezone,timedelta
from urllib.parse import quote,urlencode,urlsplit

_spec=importlib.util.spec_from_file_location('yukino_dev_public_net',Path(__file__).with_name('helper_net.py'))
NET=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(NET)
_CACHE={}

def setting(ctx,key,default,low,high):
    value=ctx.get('settings',{}).get(key,default)
    return max(low,min(high,value)) if type(value) is int else default

def clean(value,limit=160):
    return re.sub(r'[\x00-\x1f\x7f]',' ',str(value if value is not None else '未提供')).strip()[:limit]

def stamp():return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S UTC+8')

def online(ctx):return ctx.get('settings',{}).get('online_enabled',True) is not False

def json_get(url,ctx):
    now=time.monotonic();cache=_CACHE.get(url)
    if cache and cache[0]>now:return cache[1]
    result=NET.request(url,timeout=setting(ctx,'network_timeout_seconds',8,2,15),max_bytes=262144,allowed_hosts={urlsplit(url).hostname})
    if result.status!=200:raise NET.NetworkError('数据源返回 HTTP '+str(result.status))
    data=result.json()
    if not isinstance(data,dict):raise NET.NetworkError('数据源格式变化')
    if len(_CACHE)>=64:_CACHE.pop(next(iter(_CACHE)))
    _CACHE[url]=(now+setting(ctx,'cache_seconds',600,0,3600),data)
    return data

def geo(value,ctx):
    if not online(ctx):return '联网归属查询已在面板关闭。'
    if value is not None and not NET.public_ip(str(value)):return '非公网地址，无公网归属信息；未发送给外部服务。'
    target=str(value) if value is not None else ''
    settings=ctx.get('settings',{});preferred=settings.get('ip_provider','ipwhois')
    providers=['ipapi','ipwhois'] if preferred=='ipapi' else ['ipwhois','ipapi']
    for provider in providers:
        try:
            if provider=='ipwhois':
                data=json_get('https://ipwho.is/'+quote(target,safe=':'),ctx)
                if data.get('success') is not True:raise NET.NetworkError('归属接口未提供结果')
                result_ip=str(data.get('ip',''))
                if value is not None and ipaddress.ip_address(result_ip)!=value:raise NET.NetworkError('归属接口返回了不匹配的IP')
                conn=data.get('connection') or {};zone=data.get('timezone') or {}
                lines=['IP 联网查询'+('（机器人服务器出口）' if value is None else ''),
                    '地址：'+clean(result_ip),'归属地：'+' / '.join(clean(data.get(k)) for k in ('country','region','city')),
                    '运营商 ISP：'+clean(conn.get('isp')),'组织：'+clean(conn.get('org')),
                    'ASN：'+clean(conn.get('asn')),'时区：'+clean(zone.get('id')),'来源：ipwho.is']
            else:
                data=json_get('https://ipapi.co/'+(quote(target,safe=':')+'/' if target else '')+'json/',ctx)
                if data.get('error'):raise NET.NetworkError('备用归属接口未提供结果')
                result_ip=str(data.get('ip',''))
                if value is not None and ipaddress.ip_address(result_ip)!=value:raise NET.NetworkError('归属接口返回了不匹配的IP')
                lines=['IP 联网查询'+('（机器人服务器出口）' if value is None else ''),'地址：'+clean(result_ip),
                    '归属地：'+' / '.join(clean(data.get(k)) for k in ('country_name','region','city')),
                    '组织/运营商：'+clean(data.get('org')),'ASN：'+clean(data.get('asn')),'时区：'+clean(data.get('timezone')),'来源：ipapi.co']
            return '\n'.join(lines+['查询：'+stamp()+'；归属为数据库估计，成功结果最多缓存'+str(setting(ctx,'cache_seconds',600,0,3600))+'秒。'])
        except (NET.NetworkError,ValueError,TypeError,KeyError):continue
    return '公网归属查询暂不可用（源站限流、连接失败或未收录）；已尝试备用源。未编造归属、运营商或风险结论。'

def domain(value):
    if len(value)>253 or re.search(r'[/:?@\s\\]',value):raise ValueError('域名格式无效；不要填URL、端口或路径。')
    canonical=NET.canonical_url('https://'+value+'/')
    host=urlsplit(canonical).hostname
    if re.fullmatch(r'[0-9.]+',host):raise ValueError('IPv4地址格式无效。')
    return host

def dns(arg,ctx):
    pieces=arg.split()
    if not 1<=len(pieces)<=2:raise ValueError('用法：/DNS example.com [A/AAAA/MX/TXT/NS/CNAME/SOA]')
    host=domain(pieces[0]);kind=pieces[1].upper() if len(pieces)==2 else 'A'
    types={'A':1,'AAAA':28,'CNAME':5,'MX':15,'TXT':16,'NS':2,'SOA':6,'CAA':257}
    if kind not in types:raise ValueError('不支持该DNS记录类型。')
    if not online(ctx):return 'DNS联网查询已在面板关闭。'
    try:
        data=json_get('https://dns.google/resolve?'+urlencode({'name':host,'type':kind,'edns_client_subnet':'0.0.0.0/0'}),ctx)
        status=data.get('Status');rows=data.get('Answer') or []
        if status==3:return 'DNS：'+host+' 不存在（NXDOMAIN）。'
        if status!=0:raise NET.NetworkError('DNS查询状态 '+clean(status))
        lines=['DNS 联网查询｜'+host+'｜'+kind]
        type_names={number:label for label,number in types.items()}
        for row in rows[:16]:lines.append(clean(row.get('name'))+' '+type_names.get(row.get('type'),str(row.get('type')))+' '+clean(row.get('data'),400)+'（TTL '+clean(row.get('TTL'))+'秒）')
        if not rows:lines.append('该类型没有记录。')
        lines+=['查询：'+stamp()+'；来源：Google Public DNS']
        return '\n'.join(lines)
    except NET.NetworkError:return 'DNS查询暂不可用，未使用猜测结果。'

def website(url,ctx):
    if not online(ctx):return '网址联网检测已在面板关闭。'
    upgraded=url.startswith('http://')
    if upgraded:url='https://'+url[7:]
    try:
        began=time.monotonic();response=NET.request(url,method='HEAD',timeout=setting(ctx,'network_timeout_seconds',8,2,15),max_bytes=16384)
        lines=['网站联网检测'+('（使用HTTPS）' if upgraded else ''),'最终地址：'+response.url,
            'HTTP状态：'+str(response.status),'类型：'+clean(response.headers.get('content-type')),
            '响应长度头：'+clean(response.headers.get('content-length')),'服务器头：'+clean(response.headers.get('server')),
            '检测耗时：'+str(round(time.monotonic()-began,2))+'秒（从机器人服务器出发）','查询：'+stamp()]
        if response.status in (405,501):lines.append('站点不接受HEAD检测；这不代表网页无法打开，可用/网页正文读取公开页面。')
        return '\n'.join(lines)
    except NET.NetworkError as e:return '网址联网检测未完成：'+clean(e)+ '；未尝试访问内网或绕过证书验证。'
