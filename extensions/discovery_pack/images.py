"""Original small image effects, using explicitly supplied images only."""
from __future__ import annotations
import io,math,re
from urllib.parse import urlsplit
from PIL import Image,ImageDraw,ImageOps,UnidentifiedImageError
from common import UserError,argument,deadline,guard,image_item,need,result,settings
from yukino_core import public_net

HELP='图片工坊：引用或同一条消息附图发送 /摸头、/转圈、/像素化、/图片灰度、/图片镜像；GIF可用 /动图倒放、/动图加速 2。也可在指令后附一个公开HTTPS图片网址。不查询QQ头像，不上传原图到第三方处理服务。'
COMMANDS={'/摸头':'pet','/转圈':'spin','/像素化':'pixel','/图片灰度':'gray','/图片镜像':'mirror','/动图倒放':'reverse','/动图加速':'speed'}

def get_source(text,context):
    urls=context.get('image_urls',[])
    explicit=re.search(r'https://[^\s]+',argument(text))
    url=explicit[0] if explicit else urls[0] if urls else ''
    need(url,HELP);need(isinstance(url,str) and len(url)<=4096,'图片地址过长。')
    host=(urlsplit(url).hostname or '').lower()
    need(not (host=='qlogo.cn' or host.endswith('.qlogo.cn')),'不查询QQ号头像接口，请直接发送图片或引用已收到的图片。')
    need(settings(context).get('online_enabled',True),'图片获取已关闭，可在插件管理中开启。')
    # A QQ number is never sent to an avatar service; only the image's original
    # requested HTTPS address is read. Host networking pins DNS/IP and TLS.
    response=public_net.request(url,timeout=public_net.remaining(deadline(context)),max_bytes=4_000_000)
    return response.body

def open_frames(raw,maximum=24):
    try:
        with Image.open(io.BytesIO(raw)) as source:
            need(source.format in ('PNG','JPEG','GIF','WEBP'),'只支持PNG/JPEG/GIF/WebP图片。')
            need(0<source.width*source.height<=4_000_000,'图片像素过大，请缩小到400万像素内。')
            need(getattr(source,'n_frames',1)<=maximum,'动图帧数过多，请先裁成'+str(maximum)+'帧内。')
            frames=[];durations=[]
            for index in range(getattr(source,'n_frames',1)):
                source.seek(index);frame=ImageOps.exif_transpose(source.convert('RGBA'));frame.thumbnail((640,640))
                frames.append(frame.copy());durations.append(min(2000,max(30,int(source.info.get('duration',100)))))
            return frames,durations
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError):raise UserError('未读到有效图片，请重新发送原图。') from None

def gif(frames,durations):
    for side,colors in ((144,96),(112,64),(88,48),(64,32)):
        converted=[]
        for frame in frames:
            canvas=Image.new('RGB',frame.size,'white')
            if frame.mode=='RGBA':canvas.paste(frame,mask=frame.getchannel('A'))
            else:canvas.paste(frame)
            canvas.thumbnail((side,side));converted.append(canvas.quantize(colors=colors))
        out=io.BytesIO();converted[0].save(out,'GIF',save_all=True,append_images=converted[1:],duration=durations,loop=0,optimize=True,disposal=2)
        if len(out.getvalue())<35000:return out.getvalue()
    raise UserError('这张动图较复杂，压缩后仍过大，请减少帧数。')

def portrait(frame,side=100):
    image=ImageOps.fit(frame.convert('RGBA'),(side,side));mask=Image.new('L',(side,side));ImageDraw.Draw(mask).ellipse((1,1,side-2,side-2),fill=255);image.putalpha(mask);return image

def pet(frame):
    original=portrait(frame);out=[]
    for offset in (0,5,12,6,0):
        canvas=Image.new('RGBA',(144,144),'white');avatar=original.resize((100+offset,100-offset),Image.Resampling.LANCZOS);canvas.alpha_composite(avatar,((144-avatar.width)//2,37+offset))
        draw=ImageDraw.Draw(canvas);skin='#f2d6bb';edge='#906f59';y=offset
        draw.rounded_rectangle((52,12+y,134,40+y),radius=12,fill=skin,outline=edge,width=2)
        for x,h in ((60,9),(74,2),(88,5),(102,12)):draw.rounded_rectangle((x,h+y,x+13,27+y),radius=6,fill=skin,outline=edge,width=1)
        draw.line((66,30+y,106,30+y),fill=edge,width=2);out.append(canvas)
    return gif(out,[100]*5)

def spin(frame):
    original=portrait(frame,106);frames=[]
    for angle in range(0,360,45):
        canvas=Image.new('RGBA',(144,144),'white');rotated=original.rotate(-angle,resample=Image.Resampling.BICUBIC,expand=False);canvas.alpha_composite(rotated,(19,19));frames.append(canvas)
    return gif(frames,[90]*8)

def static_image(frame,name):
    # Photos are much less compressible than illustrations. Keep a real image
    # result within the host's inline limit by reducing resolution as needed.
    for side in (480,384,288,224,160):
        image=frame.copy();image.thumbnail((side,side))
        try:return image_item(image,name)
        except UserError:continue
    raise UserError('图片压缩后仍过大，请换一张较小的图片。')

def run(text,context):
    command=text.split(maxsplit=1)[0];mode=COMMANDS.get(command)
    if not mode:return HELP
    raw=get_source(text,context);frames,durations=open_frames(raw,min(32,max(1,int(settings(context).get('max_frames',24)))))
    if mode=='pet':output=pet(frames[0]);name='摸头.gif'
    elif mode=='spin':output=spin(frames[0]);name='转圈.gif'
    elif mode in ('reverse','speed'):
        need(len(frames)>1,'这张是静态图，请发送GIF动图。')
        if mode=='reverse':frames.reverse();durations.reverse()
        else:
            arg=re.sub(r'https://[^\s]+','',argument(text)).replace('[图片]','').replace('[表情]','').strip();rate=float(arg or 2);need(math.isfinite(rate) and .25<=rate<=4,'速度倍率范围为0.25—4，例如 /动图加速 2。');durations=[max(25,round(d/rate)) for d in durations]
        output=gif(frames,durations);name='倒放.gif' if mode=='reverse' else '调速.gif'
    else:
        image=frames[0]
        if mode=='pixel':image=ImageOps.fit(image,(24,24)).resize((288,288),Image.Resampling.NEAREST)
        elif mode=='gray':image=ImageOps.grayscale(image)
        else:image=ImageOps.mirror(image)
        return result(media=[static_image(image,{'pixel':'像素化.png','gray':'灰度.png','mirror':'镜像.png'}[mode])])
    return result(media=[image_item(output,name)])

def handle(text,context):return guard(run,text,context)
