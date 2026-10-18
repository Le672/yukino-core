"""Bilibili public video metadata search and live room status."""
import importlib.util
import re
from pathlib import Path

_spec = importlib.util.spec_from_file_location('bili_query_net', Path(__file__).with_name('_net.py'))
net = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(net)

def handle(text, context):
    net.begin()
    try:
        query = net.argument(text, {'/B站搜索','/b站搜索','/直播'}, 80)
        if query is None:
            return None
        command = text.strip().split(maxsplit=1)[0]
        if command == '/直播':
            if not re.fullmatch('[0-9]{1,12}', query):
                return '用法：/直播 直播间号码（只接受数字，不接受URL）'
            payload = net.fetch('https://api.live.bilibili.com/room/v1/Room/get_info', {'room_id':int(query)}, ttl=30)
            if payload.get('code') != 0:
                raise net.LookupFailure('直播间不存在或原站暂不可用')
            room = payload['data']
            roomid = str(room.get('room_id', query))
            if not roomid.isdigit():
                raise ValueError()
            status = {0:'未开播', 1:'正在直播', 2:'轮播中'}.get(room.get('live_status'), '状态未知')
            lines = ['B站直播｜'+net.clean(room.get('title','未提供标题')), '状态：'+status,
                '分区：'+net.clean(room.get('parent_area_name',''))+' / '+net.clean(room.get('area_name','')),
                'https://live.bilibili.com/'+roomid, '来源：B站公开直播间信息；状态缓存30秒。']
            return '\n'.join(lines)
        if not query:
            return '用法：/B站搜索 关键词'
        limit = net.number(net.settings(context).get('result_limit'), 5, 1, 10)
        payload = net.fetch('https://api.bilibili.com/x/web-interface/search/type',
            {'search_type':'video', 'keyword':query, 'page':1, 'page_size':20}, ttl=120)
        if payload.get('code') != 0:
            raise net.LookupFailure('B站搜索接口受限或暂不可用')
        videos = payload.get('data', {}).get('result', [])
        if not isinstance(videos, list):
            raise ValueError()
        if not videos:
            return 'B站未找到匹配视频。'
        lines = ['B站搜索（原站排序）']
        for item in videos[:limit]:
            bvid = str(item.get('bvid',''))
            if not re.fullmatch('BV[A-Za-z0-9]{6,20}', bvid):
                continue
            lines.extend([net.clean(item.get('title','未知标题'))+'｜UP：'+net.clean(item.get('author','未知')),
                'https://www.bilibili.com/video/'+bvid+'/'])
        if len(lines) == 1:
            raise ValueError()
        lines.append('来源：B站公开搜索；仅返回公开信息与原站链接，不下载视频。')
        return '\n'.join(lines)
    except Exception as exc:
        return net.failure('B站', exc)
