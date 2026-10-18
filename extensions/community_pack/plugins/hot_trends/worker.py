"""Read-only Bilibili popular videos and Tieba public topic list."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location('hot_trends_net', Path(__file__).with_name('_net.py'))
net = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(net)

def handle(text, context):
    net.begin()
    try:
        source = net.argument(text, {'/热榜'}, 12)
        if source is None:
            return None
        cfg = net.settings(context)
        source = source or str(cfg.get('default_source', 'b站'))
        limit = net.number(cfg.get('result_limit'), 5, 1, 10)
        lines = []
        if source.lower() in ('b站', 'bilibili', '哔哩哔哩'):
            payload = net.fetch('https://api.bilibili.com/x/web-interface/popular', {'pn':1, 'ps':20}, ttl=120)
            if payload.get('code') != 0:
                raise net.LookupFailure('B站限制了公开热门接口')
            items = payload['data']['list']
            for item in items[:limit]:
                bvid = str(item.get('bvid', ''))
                if len(bvid) < 6 or not bvid.startswith('BV') or not bvid.isalnum():
                    continue
                lines.append(net.clean(item['title'])+'\nhttps://www.bilibili.com/video/'+bvid+'/')
            heading = 'B站热门视频（公开热门接口顺序，并非热搜榜）'
        elif source == '贴吧':
            payload = net.fetch('https://tieba.baidu.com/hottopic/browse/topicList', ttl=120)
            data = payload.get('data', {})
            items = data.get('bang_topic', {}).get('topic_list', [])
            if not isinstance(items, list):
                raise ValueError()
            for item in items[:limit]:
                topicid = str(item.get('topic_id', ''))
                if not topicid.isdigit():
                    continue
                name = net.clean(item.get('topic_name', ''), 120)
                if name:
                    lines.append(name+'\nhttps://tieba.baidu.com/hottopic/browse/hottopic?topic_id='+topicid)
            heading = '贴吧热议话题（公开话题榜顺序）'
        else:
            return '用法：/热榜 b站 或 /热榜 贴吧'
        if not lines:
            raise net.LookupFailure('公开榜单暂无可解析条目或接口格式已变化')
        return '\n'.join([heading]+[f'{n}. {line}' for n,line in enumerate(lines,1)]+['查询时间：'+net.local_time()+'；缓存最长2分钟'])
    except Exception as exc:
        return net.failure('热榜', exc)
