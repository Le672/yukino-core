"""Read public Steam game news without a key; no installed-client control."""
import importlib.util
from pathlib import Path
import re
from urllib.parse import urlsplit
_spec=importlib.util.spec_from_file_location('yukino_game_news_net',Path(__file__).with_name('_net.py'))
net=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(net)

def handle(text,context):
    net.begin()
    try:
        app=net.argument(text,{'/游戏更新'},12)
        if app is None:return None
        if not re.fullmatch(r'[1-9][0-9]{0,9}',app):return '用法：/游戏更新 SteamAppID，例如 /游戏更新 730；AppID可用 /steam 查询。'
        count=net.number(net.settings(context).get('result_limit'),3,1,5)
        data=net.fetch('https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/',{'appid':app,'count':count,'maxlength':250,'format':'json'},ttl=300)
        rows=data.get('appnews',{}).get('newsitems',[])
        if not isinstance(rows,list):raise ValueError()
        if not rows:return '该游戏没有可用的公开新闻/更新记录。'
        lines=['Steam 游戏新闻与更新｜App '+app]
        for row in rows[:count]:
            link=str(row.get('url',''));u=urlsplit(link)
            if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:link='https://store.steampowered.com/app/'+app+'/'
            lines.extend([net.clean(row.get('title',''),120),net.clean(row.get('feedlabel',''),40)+'｜'+net.clean(row.get('author',''),40),net.clean(row.get('contents',''),230),link])
        return '\n'.join(lines)+'\n来源：Steam公开新闻接口；新闻可能包含社区媒体，标题不等同于版本已安装。'
    except Exception as exc:return net.failure('游戏更新',exc)
