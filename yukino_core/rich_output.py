"""Resolve real media, render results, and retain authenticated panel previews."""
from __future__ import annotations
import asyncio
import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
import threading
import uuid
from PIL import Image
from . import public_net
from .plugin_results import validate_result
from .result_cards import render

MAX_ASSET = 24*1024*1024
BOARD_IDS = frozenset('x_connect4 x_tictactoe x_gomoku x_reversi x_battleship x_knight x_minesweeper x_sliding x_2048 x_lights x_sokoban x_maze x_memory m_mahjong m_uno i_dots i_set i_nim s_sudoku s_nonogram wordle'.split())
CARD_IDS = BOARD_IDS | frozenset('tarot daily_luck weather_query steam_query bili_query mc_status f1_info game_news daily_economy affinity polls m_ics m_bangumi s_expedition s_mood s_sleep'.split())
FILE_IDS = frozenset('x_json x_csv x_diff x_markdown x_regex x_matrix x_stats m_ics m_webtext'.split())


def safe_name(value, default='result.txt'):
    value=re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]','_',str(value or default)).strip(' .')[:110]
    if not value or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',value):return default
    return value


class AssetStore:
    def __init__(self,root):self.root=Path(root);self.lock=threading.Lock()

    def save(self,data,kind,name,mime):
        with self.lock:return self._save(data,kind,name,mime)

    def _save(self,data,kind,name,mime):
        if not isinstance(data,bytes) or not 0<len(data)<=MAX_ASSET:raise ValueError('结果附件大小超限')
        if self.root.is_symlink() or getattr(self.root,'is_junction',lambda:False)():raise ValueError('Invalid media root')
        self.root.mkdir(parents=True,exist_ok=True)
        key='media_'+hashlib.sha256(kind.encode()+data).hexdigest()[:32]
        path=self.root/(key+'.bin');meta=self.root/(key+'.json')
        if path.is_symlink() or meta.is_symlink():raise ValueError('Invalid media path')
        if path.exists():path.touch()
        if not path.exists():
            # Only this cache's own hash-named files are managed; no user paths.
            entries=sorted(self.root.glob('media_*.bin'),key=lambda p:p.stat().st_mtime)
            total=sum(p.stat().st_size for p in entries)
            for old in entries:
                if total+len(data)<=512*1024*1024:break
                if old.is_symlink() or not re.fullmatch(r'media_[a-f0-9]{32}\.bin',old.name):continue
                total-=old.stat().st_size;old.unlink();old.with_suffix('.json').unlink(missing_ok=True)
            temporary=self.root/(key+'.'+uuid.uuid4().hex+'.tmp');temporary.write_bytes(data);temporary.chmod(0o600);temporary.replace(path)
        info={'type':kind,'resource_id':key,'filename':safe_name(name),'mime':mime,'bytes':len(data),'description':safe_name(name)}
        temporary=meta.with_name(meta.name+'.tmp');temporary.write_text(json.dumps(info,ensure_ascii=False),encoding='utf-8');temporary.chmod(0o600);temporary.replace(meta)
        return info

    def get(self,key):
        if not re.fullmatch(r'media_[a-f0-9]{32}',key):return None
        path=self.root/(key+'.bin');meta=self.root/(key+'.json')
        if self.root.is_symlink() or path.is_symlink() or meta.is_symlink() or not path.is_file() or not meta.is_file():return None
        if path.resolve().parent!=self.root.resolve() or meta.stat().st_size>3000:return None
        info=json.loads(meta.read_text(encoding='utf-8'))
        return path,info


def image_format(data):
    if len(data)>5_000_000:raise ValueError('图片超过5MB')
    with Image.open(BytesIO(data)) as image:
        if image.width*image.height>20_000_000 or image.format not in ('PNG','JPEG','WEBP','GIF'):raise ValueError('图片格式或尺寸无效')
        fmt=image.format;image.verify()
    return {'PNG':('png','image/png'),'JPEG':('jpg','image/jpeg'),'WEBP':('webp','image/webp'),'GIF':('gif','image/gif')}[fmt]


def audio_format(data):
    if data.startswith(b'ID3') or (len(data)>2 and data[0]==255 and data[1]&224==224):return 'mp3','audio/mpeg'
    if data.startswith(b'fLaC'):return 'flac','audio/flac'
    if data.startswith(b'OggS'):return 'ogg','audio/ogg'
    if data.startswith(b'RIFF') and data[8:12]==b'WAVE':return 'wav','audio/wav'
    if len(data)>12 and data[4:8]==b'ftyp':return 'm4a','audio/mp4'
    raise ValueError('远端未返回有效音频')


class RichOutput:
    def __init__(self,kernel):
        self.kernel=kernel
        self.assets=AssetStore(kernel.plugins.root.parent/'media-results')
        self.gate=asyncio.Semaphore(2)
        self.busy=0

    def _voice_preview(self,path,asset,seconds):
        binary=shutil.which('ffmpeg')
        if not binary:return None
        output=self.assets.root/('.preview-'+uuid.uuid4().hex+'.mp3')
        try:
            result=subprocess.run([binary,'-nostdin','-hide_banner','-loglevel','error','-y',
                '-protocol_whitelist','file,pipe','-i',str(path),'-t',str(seconds),'-vn',
                '-threads','1','-c:a','libmp3lame','-b:a','64k',str(output)],capture_output=True,timeout=30)
            if result.returncode or not output.is_file() or output.stat().st_size>2_000_000:return None
            data=output.read_bytes();audio_format(data)
            return self.assets.save(data,'audio',Path(asset['filename']).stem+'-语音片段.mp3','audio/mpeg')
        finally:output.unlink(missing_ok=True)

    async def _asset(self,item):
        if 'text' in item:data=item['text'].encode('utf-8')
        elif 'data' in item:data=base64.b64decode(item['data'].split(',',1)[-1],validate=True)
        else:
            response=await asyncio.to_thread(public_net.request,item['url'],timeout=35,max_bytes=MAX_ASSET,max_redirects=3)
            data=response.body
        kind=item['type'];name=safe_name(item.get('name'))
        if kind=='image':
            ext,mime=await asyncio.to_thread(image_format,data)
            if not item.get('name'):name='image.'+ext
        elif kind=='audio':
            ext,mime=audio_format(data)
            if not name.lower().endswith('.'+ext):name=safe_name(Path(name).stem+'.'+ext)
        else:
            suffix=Path(name).suffix.lower()
            allowed={'.txt':'text/plain; charset=utf-8','.md':'text/markdown; charset=utf-8','.json':'application/json',
                '.csv':'text/csv; charset=utf-8','.ics':'text/calendar; charset=utf-8','.diff':'text/plain; charset=utf-8',
                '.zip':'application/zip','.pdf':'application/pdf','.mp3':'audio/mpeg'}
            if suffix not in allowed:raise ValueError('该附件格式不支持自动发送')
            if suffix=='.zip' and not data.startswith(b'PK'):raise ValueError('远端未返回ZIP文件')
            if suffix=='.pdf' and not data.startswith(b'%PDF'):raise ValueError('远端未返回PDF文件')
            if suffix in ('.txt','.md','.json','.csv','.ics','.diff'):data.decode('utf-8-sig')
            mime=allowed[suffix]
        return await asyncio.to_thread(self.assets.save,data,kind,name,mime)

    async def prepare(self,item,result,context=None):
        value=validate_result(result,8000)
        if value is None:return []
        cfg=self.kernel.plugins.settings(item['id'])
        text=value if isinstance(value,str) else value['text']
        sources=[] if isinstance(value,str) else value['media']
        if not sources and item['id']=='s_github':
            for link in re.findall(r'https://github\.com/[^\s]+/releases/download/[^\s]+\.(?:zip|pdf|txt)(?=\s|$)',text,re.I)[:1]:
                sources.append({'type':'file','url':link,'name':link.rsplit('/',1)[-1]})
        layout='auto' if isinstance(value,str) else value['layout']
        mode=cfg.get('output_mode','auto')
        pid=item['id'];steps=[];images=[];warnings=[]
        async with self.gate:
            # Source failures remain visible; they never become a fake image/song.
            for source in sources:
                try:
                    asset=await self._asset(source)
                    found=self.assets.get(asset['resource_id']);payload=base64.b64encode(found[0].read_bytes()).decode()
                    if asset['type']=='image':images.append(({'type':'image','data':{'file':'base64://'+payload}},asset))
                    elif asset['type']=='audio':
                        duration=source.get('duration',0)
                        # Deliver the original before attempting QQ voice conversion.
                        if cfg.get('send_audio_file',True) or duration>180:
                            steps.append({'text':'[音频文件] '+asset['filename'],'segments':[{'type':'file','data':{'file':'base64://'+payload,'name':asset['filename']}}],'attachments':[asset]})
                        voice=asset
                        if duration>180 or (not duration and found[0].stat().st_size>3_000_000):
                            voice=await asyncio.to_thread(self._voice_preview,found[0],asset,cfg.get('voice_preview_seconds',90))
                        if cfg.get('send_voice',True) and voice:
                            voice_file=self.assets.get(voice['resource_id'])[0]
                            voice_payload=base64.b64encode(voice_file.read_bytes()).decode()
                            steps.append({'text':'[音频] '+voice['filename'],'segments':[{'type':'record','data':{'file':'base64://'+voice_payload}}],'attachments':[voice]})
                        elif cfg.get('send_voice',True):warnings.append('语音预览暂不可用，歌曲请打开音频文件播放。')
                    else:steps.append({'text':'[文件] '+asset['filename'],'segments':[{'type':'file','data':{'file':'base64://'+payload,'name':asset['filename']}}],'attachments':[asset]})
                except (ValueError,OSError,TimeoutError):warnings.append('有一项媒体未能获取，未发送替代内容。')
            # A real result already has its content. Cards improve layout rather
            # than pretending a link or an upstream error is a completed action.
            failed=bool(re.search(r'查询失败|操作未完成|暂时没响应|暂不可用|用法[：:]|缺少|不存在|权限不足',text[:100]))
            card=(mode=='card' or (mode=='auto' and layout not in ('text',) and (pid in CARD_IDS or layout in ('card','board') or len(text)>600)))
            card=card and bool(text) and not failed and pid!='hot_trends'
            if card:
                for data in await asyncio.to_thread(render,item['name'],text,board=pid in BOARD_IDS or layout=='board'):
                    asset=await asyncio.to_thread(self.assets.save,data,'image',item['id']+'-result.png','image/png')
                    images.append(({'type':'image','data':{'file':'base64://'+base64.b64encode(data).decode()}},asset))
            if text or images or warnings:
                visible=text
                if warnings:visible+=('\n' if visible else '')+'\n'.join(warnings)
                segments=([{'type':'text','data':{'text':visible}}] if visible else [])+[x[0] for x in images[:4]]
                steps.insert(0,{'text':text or visible,'segments':segments,'attachments':[x[1] for x in images[:4]]})
            if text and cfg.get('export_result',True) and not failed and not sources and (pid in FILE_IDS or len(text)>2500):
                suffix='.md' if pid=='x_markdown' else '.diff' if pid=='x_diff' else '.txt'
                if pid=='x_json':
                    try:json.loads(text);suffix='.json'
                    except ValueError:pass
                asset=await asyncio.to_thread(self.assets.save,text.encode(),'file',pid+'-result'+suffix,'text/plain; charset=utf-8')
                steps.append({'text':'[文件] '+asset['filename'],'segments':[{'type':'file','data':{'file':'base64://'+base64.b64encode(text.encode()).decode(),'name':asset['filename']}}],'attachments':[asset]})
        return steps

    async def send(self,event,item,result,context=None):
        self.busy+=1
        try:return await self._send(event,item,result,context)
        finally:self.busy-=1

    async def _send(self,event,item,result,context=None):
        epoch=self.kernel.epochs[event['session_id']]
        steps=await self.prepare(item,result,context)
        if not steps:return False
        if self.kernel.epochs[event['session_id']]!=epoch:
            await self.kernel.store.mark_event(event['event_id'],'reset_discarded');return False
        sent=0
        for index,step in enumerate(steps):
            ok=await self.kernel.reply(event,**step,epoch=epoch,delivery_key=event['event_id']+':result:'+str(index))
            if not ok:break  # An uncertain send is never automatically repeated.
            sent+=1
        if sent==len(steps):await self.kernel.store.mark_event(event['event_id'],'replied')
        elif sent:await self.kernel.store.mark_event(event['event_id'],'plugin_partial','部分附件发送未确认，未自动重试')
        return sent==len(steps)
