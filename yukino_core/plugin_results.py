"""Versioned, bounded plugin results; old text workers stay compatible."""
import json
import re

MAX_RESULT_BYTES = 120_000


def validate_game_result(value,text_limit):
    if set(value)!={'version','text','game_ai'} or value.get('version')!=2 or not isinstance(value['text'],str):raise ValueError('Invalid game envelope')
    game=value['game_ai']
    if not isinstance(game,dict) or set(game)-{'version','task','tracking','error','stale','consumed'} or game.get('version')!=1:raise ValueError('Invalid game protocol')
    if len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode())>MAX_RESULT_BYTES:raise ValueError('Game envelope too large')
    for key in ('error','stale','consumed'):
        if key in game and not isinstance(game[key],bool):raise ValueError('Invalid game flag')
    tracking=game.get('tracking')
    if tracking is not None:
        if not isinstance(tracking,dict) or set(tracking)!={'active','owner_id','members','ambient'} or not isinstance(tracking['active'],bool) or not isinstance(tracking['ambient'],bool):raise ValueError('Invalid game tracking')
        if not isinstance(tracking['owner_id'],str) or not re.fullmatch('[1-9][0-9]{0,19}',tracking['owner_id']):raise ValueError('Invalid game owner')
        if not isinstance(tracking['members'],list) or len(tracking['members'])>12 or any(not isinstance(u,str) or not re.fullmatch('[1-9][0-9]{0,19}',u) for u in tracking['members']):raise ValueError('Invalid game players')
    task=game.get('task')
    if task is not None:
        if not isinstance(task,dict) or set(task)!={'nonce','mode','instruction','observation','choices'}:raise ValueError('Invalid game task')
        if not isinstance(task['nonce'],str) or not re.fullmatch('[a-f0-9]{64}',task['nonce']) or task['mode'] not in ('choice','text'):raise ValueError('Invalid game turn')
        if not isinstance(task['instruction'],str) or len(task['instruction'])>1500 or not isinstance(task['observation'],dict) or len(json.dumps(task['observation'],ensure_ascii=False).encode())>20000:raise ValueError('Invalid game observation')
        if not isinstance(task['choices'],list) or len(task['choices'])>512 or any(not isinstance(c,dict) or set(c)!={'id','label'} or not isinstance(c['id'],str) or not re.fullmatch('[1-9][0-9]{0,3}',c['id']) or not isinstance(c['label'],str) or len(c['label'])>180 for c in task['choices']):raise ValueError('Invalid game choices')
        if [c['id'] for c in task['choices']]!=[str(i+1) for i in range(len(task['choices']))]:raise ValueError('Invalid game choice order')
    return dict(version=2,text=value['text'][:text_limit],game_ai=game)


def validate_result(value, text_limit=8000):
    if value is None or isinstance(value, str):
        return value[:text_limit] if isinstance(value, str) else None
    if isinstance(value,dict) and value.get('version')==2:return validate_game_result(value,text_limit)
    if not isinstance(value, dict) or set(value)-{'version','text','media','layout'} or value.get('version') != 1:
        raise ValueError('Invalid rich plugin result')
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > MAX_RESULT_BYTES:
        raise ValueError('Plugin result is too large')
    text = value.get('text', '')
    if not isinstance(text, str) or value.get('layout', 'auto') not in ('auto','text','card','board'):
        raise ValueError('Invalid result text or layout')
    media = value.get('media', [])
    if not isinstance(media, list) or len(media) > 6:
        raise ValueError('Too many media items')
    cleaned = []
    for item in media:
        if not isinstance(item, dict) or set(item)-{'type','url','text','data','name','description','duration'}:
            raise ValueError('Invalid media fields')
        if item.get('type') not in ('image','audio','file'):
            raise ValueError('Invalid media type')
        sources = [key for key in ('url','text','data') if key in item]
        if len(sources) != 1 or not isinstance(item[sources[0]], str):
            raise ValueError('Exactly one media source is required')
        source = sources[0]
        if source == 'url' and (not item[source].startswith('https://') or len(item[source])>4096):
            raise ValueError('Media URLs must use HTTPS')
        if source == 'text' and (item['type']!='file' or len(item[source])>40000):
            raise ValueError('Invalid inline document')
        if source == 'data' and (item['type']!='image' or len(item[source])>50000):
            raise ValueError('Inline data is only for small images')
        for key, maximum in (('name',120),('description',500)):
            if key in item and (not isinstance(item[key],str) or len(item[key])>maximum or '\x00' in item[key]):
                raise ValueError('Invalid media label')
        if 'duration' in item and (type(item['duration']) not in (int,float) or not 0<=item['duration']<=7200):
            raise ValueError('Invalid audio duration')
        cleaned.append(dict(item))
    if not text and not cleaned:
        raise ValueError('Empty rich result')
    return {'version':1,'text':text[:text_limit],'media':cleaned,'layout':value.get('layout','auto')}


def music_intent(text):
    """Explicit play requests, not discussion about music or arbitrary digits."""
    match = re.fullmatch(r'(?:请|帮我|给我)?\s*(?:我想听|想听|播放|放一首|来一首|来首|点一首|点歌|搜歌)\s*[:：]?\s*(.{1,100})',text.strip())
    if not match or re.search(r'怎么|如何|是什么|为什么|不要|别放|不想听',match[1]):
        return None
    return '/点歌 '+match[1]
