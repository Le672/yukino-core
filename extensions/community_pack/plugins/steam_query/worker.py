"""Official Steam store search and application details."""
import importlib.util
import re
from pathlib import Path

_spec = importlib.util.spec_from_file_location('steam_query_net', Path(__file__).with_name('_net.py'))
net = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(net)

def handle(text, context):
    net.begin()
    try:
        query = net.argument(text, {'/steam','/Steam'}, 100)
        if query is None:
            return None
        if not query:
            return '用法：/steam 游戏名 或 /steam 620'
        cfg = net.settings(context)
        country = str(cfg.get('country_code', 'cn')).lower()
        if not re.fullmatch('[a-z]{2}', country):
            return 'Steam 国家代码设置无效，应为 cn、us 等两位代码。'
        limit = net.number(cfg.get('result_limit'), 3, 1, 5)
        if re.fullmatch('[0-9]{1,10}', query):
            ids = [int(query)]
        else:
            search = net.fetch('https://store.steampowered.com/api/storesearch/',
                {'term':query, 'l':'schinese', 'cc':country}, ttl=120)
            items = search.get('items')
            if not isinstance(items, list):
                raise ValueError()
            ids = [int(x['id']) for x in items[:limit] if isinstance(x, dict) and str(x.get('id','')).isdigit()]
        if not ids:
            return 'Steam 未找到匹配游戏。'
        lines = ['Steam 商店查询｜地区 '+country.upper()+'（实际售价以商店结算为准）']; media=[]
        for appid in ids:
            record = net.fetch('https://store.steampowered.com/api/appdetails',
                {'appids':appid, 'l':'schinese', 'cc':country}, ttl=120).get(str(appid), {})
            if not record.get('success'):
                lines.append(f'App {appid}：该地区无可用详情/可能已下架。')
                continue
            detail = record['data']
            price = detail.get('price_overview')
            if detail.get('is_free') is True:
                pricing = '免费游戏'
            elif isinstance(price, dict):
                currency = net.clean(price.get('currency', '未知币种'), 10)
                final = net.clean(price.get('final_formatted', ''), 40)
                if not final and isinstance(price.get('final'), (int,float)):
                    final = f"{price['final']/100:.2f}"
                pricing = (final or '未提供售价')+' '+currency
                discount = net.number(price.get('discount_percent'), 0, 0, 100)
                if discount:
                    pricing += f'，优惠 {discount}%'
            else:
                pricing = '未提供该地区售价（预售、下架或套餐等情况）'
            lines.extend([net.clean(detail.get('name', f'App {appid}')), pricing,
                net.clean(detail.get('short_description',''),500),
                f'https://store.steampowered.com/app/{appid}/'])
            cover=detail.get('header_image','')
            if isinstance(cover,str) and cover.startswith('https://') and len(media)<3:
                media.append({'type':'image','url':cover,'description':net.clean(detail.get('name','Steam游戏'))})
        lines.append('查询时间：'+net.local_time()+'；来源：Steam 官方商店')
        result='\n'.join(lines)
        return {'version':1,'text':result,'media':media} if context.get('rich_result_version') else result
    except Exception as exc:
        return net.failure('Steam', exc)
