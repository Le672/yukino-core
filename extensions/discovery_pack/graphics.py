"""Original Chinese meme cards and locally encoded QR images."""
from __future__ import annotations
import re
from datetime import datetime,timedelta,timezone
from PIL import Image,ImageDraw
from common import UserError,argument,guard,image_item,need,result
from yukino_core.result_cards import font,wrap

def meme(text,context):
    cmd=text.split(maxsplit=1)[0];body=argument(text)
    need(body,'发送 /举牌 内容、/记仇 内容 或 /摸鱼牌 内容；会直接返回图片。')
    need(len(body)<=160,'文字请控制在160字内。');body=body.replace('[CQ:','［CQ：')
    canvas=Image.new('RGB',(620,480),'#f9f5e9');d=ImageDraw.Draw(canvas);ink='#263d38'
    if cmd=='/记仇':
        d.rounded_rectangle((40,35,580,445),radius=15,fill='#fffdf4',outline=ink,width=3)
        for y in range(140,420,42):d.line((65,y,555,y),fill='#d7dcd2',width=2)
        d.text((70,55),'今日记仇小本本',font=font(35),fill=ink);top=145
        d.text((70,108),datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')+' · 娱乐便签',font=font(18),fill='#778377')
    else:
        # An original round bear and placard, without third-party meme images.
        d.ellipse((72,51,134,113),fill='#d7b894',outline=ink,width=3);d.ellipse((184,51,246,113),fill='#d7b894',outline=ink,width=3)
        d.ellipse((91,66,230,205),fill='#e8cfaa',outline=ink,width=3)
        d.ellipse((123,115,132,124),fill=ink);d.ellipse((185,115,194,124),fill=ink);d.arc((141,134,181,164),0,180,fill=ink,width=3)
        d.rounded_rectangle((40,185,580,435),radius=14,fill='#fffdf4',outline=ink,width=4)
        title='我有话要说' if cmd=='/举牌' else '摸鱼许可牌';d.text((270,75),title,font=font(30),fill=ink);top=213
    face=font(30);lines=wrap(d,body,face,470);need(len(lines)<=6,'文字换行太多，请缩短内容。')
    for i,line in enumerate(lines):d.text((75,top+36*i),line,font=face,fill=ink)
    d.text((390,449),'YUKINO · 原创小画板',font=font(16),fill='#849486')
    return result(media=[image_item(canvas,cmd.lstrip('/')+'.png')])

def qr(text,context):
    from qrcodegen import QrCode,QrSegment
    payload=argument(text);need(payload,'用法：/二维码 文本或网址。全部在本地生成，不上传内容。')
    need(len(payload.encode('utf-8'))<=700,'二维码内容请控制在700字节内。')
    try:
        segments=QrSegment.make_segments(payload) if payload.isascii() else [QrSegment.make_eci(26),QrSegment.make_bytes(payload.encode('utf-8'))]
        code=QrCode.encode_segments(segments,QrCode.Ecc.MEDIUM,maxversion=24)
    except ValueError:raise UserError('内容超过二维码容量，请缩短。') from None
    border=4;scale=max(4,min(9,600//(code.get_size()+8)));side=(code.get_size()+border*2)*scale
    image=Image.new('RGB',(side,side),'white');draw=ImageDraw.Draw(image)
    for y in range(code.get_size()):
        for x in range(code.get_size()):
            if code.get_module(x,y):draw.rectangle(((x+border)*scale,(y+border)*scale,(x+border+1)*scale-1,(y+border+1)*scale-1),fill='black')
    return result('二维码已生成（本地处理）。',[image_item(image,'二维码.png')])

def handle(pid,text,context):return guard(qr if pid=='d_qrcode' else meme,text,context)
