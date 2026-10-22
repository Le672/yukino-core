"""Actual song playback, isolated per-user selection, and bounded public sources."""
import importlib.util
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit,parse_qs

_spec=importlib.util.spec_from_file_location('music_search_net',Path(__file__).with_name('_net.py'))
net=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(net)
net.ALLOWED=net.ALLOWED|{'music-api.gdstudio.xyz'}
HELP='用法：/搜歌 歌名 或 /点歌 歌名，默认直接播放可用结果。切换：/选歌 序号；也支持发送网易云单曲链接。音频文件为原音频，长歌曲另附语音片段。'


def pending(context,rows=None):
    root=Path(context['data_dir']);root.mkdir(parents=True,exist_ok=True)
    scope=str(context.get('session_id',''));user=str(context.get('user_id',''))
    if not re.fullmatch(r'[pg]:[0-9]{1,30}',scope) or not user.isdigit():raise ValueError('Missing identity')
    path=root/'music-selection.sqlite3'
    if path.is_symlink():raise ValueError('Invalid state path')
    with closing(sqlite3.connect(path,timeout=2)) as db,db:
        db.execute('CREATE TABLE IF NOT EXISTS selection(scope TEXT,user TEXT,body TEXT,stamp REAL,PRIMARY KEY(scope,user))')
        if rows is not None:
            db.execute('INSERT OR REPLACE INTO selection VALUES(?,?,?,?)',(scope,user,json.dumps(rows,ensure_ascii=False),time.time()))
            db.execute('DELETE FROM selection WHERE stamp<?',(time.time()-900,));return rows
        found=db.execute('SELECT body,stamp FROM selection WHERE scope=? AND user=?',(scope,user)).fetchone()
        return json.loads(found[0]) if found and time.time()-found[1]<900 else []


def stream(song,settings):
    sid=song['id']
    try:result=net.fetch('https://music.163.com/api/song/enhance/player/url',{'ids':json.dumps([sid]),'br':128000},ttl=60)
    except net.LookupFailure:result={}
    rows=result.get('data') or []
    info=rows[0] if rows and isinstance(rows[0],dict) else {}
    url=info.get('url')
    source='网易云官方播放接口'
    if not url and settings.get('public_audio_fallback',True):
        info=net.fetch('https://music-api.gdstudio.xyz/api.php',{'types':'url','source':'netease','id':sid,'br':128},ttl=60)
        url=info.get('url');source='GD Studio 公开音乐 API（网易云）'
    if not isinstance(url,str) or not url.startswith(('https://','http://')):return None
    location=urlsplit(url)
    if location.username or location.password or location.port is not None:return None
    return {'url':url.replace('http://','https://',1),'source':source,'preview':bool(info.get('freeTrialInfo'))}


def handle(text,context):
    if not isinstance(text,str) or not text.strip():return None
    net.begin()
    try:
        query=net.argument(text,{'/搜歌','/点歌','/选歌'},120)
        command=text.strip().split(maxsplit=1)[0]
        song_id=None
        if query is None:
            for value in re.findall(r'https?://[^\s]+',text):
                p=urlsplit(value)
                if p.hostname not in ('music.163.com','y.music.163.com'):continue
                values=parse_qs(p.query or p.fragment.partition('?')[2]);candidate=values.get('id',[''])[0]
                if candidate.isdigit():song_id=int(candidate);break
            if song_id is None:return None
        if query in ('帮助','help','?',''):return HELP
        cfg=net.settings(context);limit=net.number(cfg.get('result_limit'),5,1,10)
        if command=='/选歌':
            songs=pending(context)
            if not query.isdigit() or not 1<=int(query)<=len(songs):return '没有这个选歌序号，先搜一首歌。选歌结果仅对本人有效，保留15分钟。'
            choices=[(int(query)-1,songs[int(query)-1])]
        else:
            if song_id is not None:
                payload=net.fetch('https://music.163.com/api/song/detail',{'ids':json.dumps([song_id])},ttl=120)
                rows=payload.get('songs',[])
            else:
                payload=net.fetch('https://music.163.com/api/search/get',data={'s':query,'type':1,'offset':0,'limit':limit},ttl=120)
                if payload.get('code')!=200:raise net.LookupFailure('网易云搜索要求登录或暂不可用')
                rows=payload.get('result',{}).get('songs',[]) if isinstance(payload.get('result'),dict) else []
            songs=[]
            for row in rows[:limit]:
                if not isinstance(row,dict) or not str(row.get('id','')).isdigit():continue
                artists=row.get('artists',row.get('ar',[]));album=row.get('album',row.get('al',{}))
                songs.append({'id':int(row['id']),'name':net.clean(row.get('name','未知歌曲'),100),
                    'artist':' / '.join(net.clean(a.get('name',''),50) for a in artists[:5] if isinstance(a,dict)),
                    'duration':max(0,min(7200,int(row.get('duration',row.get('dt',0)))/1000)),
                    'cover':album.get('picUrl','') if isinstance(album,dict) else ''})
            if not songs:return '未找到这首歌，换个歌名或加上歌手试试。'
            pending(context,songs)
            if not cfg.get('auto_play_first',True):return '\n'.join(f'{i+1}. {s["name"]} — {s["artist"]}' for i,s in enumerate(songs))+'\n用 /选歌 序号 直接播放。'
            choices=list(enumerate(songs[:3]))
        for index,song in choices:
            audio=stream(song,cfg)
            if not audio:continue
            title=song['name']+' — '+(song['artist'] or '未知歌手')
            lines=[title,('本次为原站提供的试听音频。' if audio['preview'] else '发送音频文件；长歌曲另附语音片段。'),'音源：'+audio['source']]
            if len(songs)>1:lines+=['其它结果：'+'；'.join(f'{i+1}. {s["name"]} — {s["artist"]}' for i,s in enumerate(songs)),f'切换可发 /选歌 序号（当前第{index+1}首）。']
            media=[]
            cover=song['cover']
            if isinstance(cover,str) and cover.startswith(('https://','http://')):media.append({'type':'image','url':cover.replace('http://','https://',1),'description':title})
            media.append({'type':'audio','url':audio['url'],'name':title+'.mp3','duration':song['duration']})
            if not context.get('rich_result_version'):return '\n'.join(lines)+'\n需要 Yukino Core 0.7.0 发送实际音频。'
            return {'version':1,'text':'\n'.join(lines),'media':media,'layout':'text'}
        return '找到了歌曲信息，但当前音源没有返回可用音频；本次没有用链接冒充播放。可试试其它版本或稍后重试。'
    except Exception as exc:return net.failure('歌曲',exc)
