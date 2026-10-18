"""Search song metadata and link to official pages; never download audio."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location('music_search_net', Path(__file__).with_name('_net.py'))
net = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(net)

def handle(text, context):
    net.begin()
    try:
        query = net.argument(text, {'/搜歌'}, 80)
        if query is None:
            return None
        if not query:
            return '用法：/搜歌 歌名 或 歌手'
        limit = net.number(net.settings(context).get('result_limit'), 5, 1, 10)
        payload = net.fetch('https://music.163.com/api/search/get', data={
            's':query, 'type':1, 'offset':0, 'limit':limit}, ttl=120)
        if payload.get('code') != 200:
            raise net.LookupFailure('网易云公开搜索暂不可用或要求登录')
        songs = payload.get('result', {}).get('songs', [])
        if not isinstance(songs, list):
            raise ValueError()
        if not songs:
            return '网易云音乐未找到匹配歌曲。'
        lines = ['网易云音乐搜索（歌曲信息与官方页面）']
        for song in songs[:limit]:
            songid = str(song.get('id', ''))
            if not songid.isdigit():
                continue
            artists = song.get('artists', song.get('ar', []))
            artist = ' / '.join(net.clean(x.get('name',''), 50) for x in artists[:5] if isinstance(x, dict))
            lines.append(net.clean(song.get('name','未知曲名'))+' — '+(artist or '未知歌手')+
                '\nID：'+songid+'\nhttps://music.163.com/#/song?id='+songid)
        if len(lines) == 1:
            raise ValueError()
        lines.append('来源：网易云公开搜索；播放、会员与版权限制以原站为准，不提供音频下载。')
        return '\n'.join(lines)
    except Exception as exc:
        return net.failure('歌曲', exc)
