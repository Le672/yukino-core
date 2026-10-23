"""Shared bounded helpers; this pack never receives model credentials."""
from __future__ import annotations
import base64,contextlib,hashlib,io,json,re,sqlite3,time
from datetime import datetime,timezone
from pathlib import Path
from PIL import Image
from yukino_core import public_net

class UserError(ValueError):pass

def need(condition,message):
    if not condition:raise UserError(message)

def clean(value,limit=300):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]','',str(value or ''))[:limit].strip()

def argument(text):return text.strip().partition(' ')[2].strip()

def settings(context):return context.get('settings',{})

def deadline(context):return time.monotonic()+min(20,max(2,int(settings(context).get('network_seconds',12))))

def get_json(url,end,hosts=None):
    response=public_net.request(url,timeout=public_net.remaining(end),max_bytes=1_500_000,allowed_hosts=hosts)
    try:return json.loads(response.body)
    except (ValueError,UnicodeError):raise UserError('数据源没有返回有效JSON，请稍后重试。') from None

def cached(context,key,load):
    need(settings(context).get('online_enabled',True),'本插件联网已关闭，可在插件管理中开启。')
    root=Path(context['data_dir']);root.mkdir(parents=True,exist_ok=True);path=root/'discovery-cache.sqlite3'
    need(not path.is_symlink(),'缓存目录不可用。');age=min(3600,max(0,int(settings(context).get('cache_seconds',300))))
    key=hashlib.sha256(key.encode()).hexdigest();now=time.time()
    with contextlib.closing(sqlite3.connect(path,timeout=3)) as db:
        path.chmod(0o600)
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY,body TEXT,at REAL)')
            row=db.execute('SELECT body,at FROM cache WHERE key=?',(key,)).fetchone()
            if age and row and 0<=now-row[1]<age:return json.loads(row[0]),datetime.fromtimestamp(row[1],timezone.utc).isoformat(),True
        value=load();body=json.dumps(value,ensure_ascii=False,allow_nan=False)
        need(len(body.encode())<=500000,'查询结果过大，请缩小范围。')
        fetched=time.time()
        if age:
            with db:
                db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)',(key,body,fetched))
                db.execute('DELETE FROM cache WHERE at<?',(now-86400,))
                db.execute('DELETE FROM cache WHERE rowid IN (SELECT rowid FROM cache ORDER BY at DESC LIMIT -1 OFFSET 40)')
        return value,datetime.fromtimestamp(fetched,timezone.utc).isoformat(),False

def image_item(image,name='result.png'):
    if isinstance(image,bytes):raw=image
    else:
        buffer=io.BytesIO();image.save(buffer,'PNG',optimize=True);raw=buffer.getvalue()
        if len(raw)>35000:
            buffer=io.BytesIO();image.convert('RGB').quantize(colors=96).save(buffer,'PNG',optimize=True);raw=buffer.getvalue()
    encoded=base64.b64encode(raw).decode('ascii');need(len(encoded)<49000,'生成图片过大，请减少文字或帧数。')
    return dict(type='image',data=encoded,name=name)

def result(text='',media=None):return dict(version=1,text=text,media=media or [],layout='text')

def guard(run,*args):
    try:return run(*args)
    except UserError as error:return str(error)
    except public_net.NetworkError as error:return '公开数据源暂时不可用：'+str(error)
    except (ValueError,KeyError,IndexError,TypeError,OverflowError,AttributeError):return '数据源格式暂不支持；未编造结果，请稍后重试。'
