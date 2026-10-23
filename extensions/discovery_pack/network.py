"""Live public lookups. Query inputs only; no QQ identity/history transmission."""
from __future__ import annotations
import html,json,math,re,time
from datetime import datetime,timezone
from urllib.parse import parse_qs,quote,unquote,urlencode,urlsplit
from PIL import Image,ImageDraw
from common import UserError,argument,cached,clean,deadline,get_json,guard,image_item,need,result,settings
from yukino_core import public_net
from yukino_core.result_cards import font

def stamp(at,hit):return '\n检索：'+at.replace('T',' ').split('.')[0]+' UTC'+('（缓存）' if hit else '')

def dt(value):
    value=datetime.fromisoformat(value.replace('Z','+00:00'));need(value.tzinfo is not None,'来源时间缺少时区。');return value

def approved(url,hosts):
    try:
        url=public_net.canonical_url(url);host=urlsplit(url).hostname
        return url if any(host==h or host.endswith('.'+h) for h in hosts) else None
    except (ValueError,TypeError):return None

def epic_items(data,now):
    offers=data['data']['Catalog']['searchStore']['elements'];need(isinstance(offers,list),'商城返回结构无效。');rows=[];seen=set()
    for item in offers[:150]:
        if not isinstance(item,dict) or item.get('id') in seen:continue
        price=item.get('price',{}).get('totalPrice',{});original=price.get('originalPrice')
        if type(original) not in (int,float) or not math.isfinite(original) or original<=0:continue  # Exclude permanent-free products.
        promos=item.get('promotions') or {};events=[]
        for key in ('promotionalOffers','upcomingPromotionalOffers'):
            for group in promos.get(key,[])[:10]:events.extend(group.get('promotionalOffers',[])[:20])
        for offer in events:
            if offer.get('discountSetting',{}).get('discountPercentage')!=0:continue
            try:start,end=dt(offer['startDate']),dt(offer['endDate'])
            except (ValueError,KeyError,UserError):continue
            if not start<end or now>=end:continue
            current=start<=now<end
            if current and price.get('discountPrice')!=0:continue
            slug=next((m.get('pageSlug') for m in item.get('catalogNs',{}).get('mappings',[]) if m.get('pageType')=='productHome'),None) or item.get('productSlug') or item.get('urlSlug')
            slug=slug.removesuffix('/home') if isinstance(slug,str) else ''
            page='https://store.epicgames.com/zh-CN/p/'+quote(slug,safe='/') if slug and re.fullmatch(r'[A-Za-z0-9_/-]+',slug) else 'https://store.epicgames.com/zh-CN/browse?'+urlencode({'keywords':item.get('title','')})
            pics=item.get('keyImages',[]);cover=next((approved(p.get('url'),('epicgames.com','unrealengine.com')) for p in pics if p.get('type') in ('OfferImageWide','DieselStoreFrontWide','Thumbnail') and approved(p.get('url'),('epicgames.com','unrealengine.com'))),None)
            rows.append(dict(title=clean(item.get('title'),120),description=clean(item.get('description'),280),current=current,start=start.isoformat(),end=end.isoformat(),page=page,cover=cover));seen.add(item.get('id'));break
    return sorted(rows,key=lambda r:(not r['current'],r['start'],r['title']))

def epic(text,context):
    country=settings(context).get('country','CN');need(country in ('CN','US','HK','JP','SG'),'商城地区不支持。')
    end=deadline(context)
    def load():
        failure=None
        for host in ('store-site-backend-static.ak.epicgames.com','store-site-backend-static-ipv4.ak.epicgames.com'):
            try:return get_json('https://'+host+'/freeGamesPromotions?'+urlencode(dict(locale='zh-CN',country=country,allowCountries=country)),end,[host])
            except public_net.NetworkError as error:failure=error
        raise failure
    data,at,hit=cached(context,'epic:'+country,load);rows=epic_items(data,datetime.now(timezone.utc));limit=min(4,max(1,int(settings(context).get('max_results',3))))
    lines=['Epic 喜加一 · '+country+'商店'];media=[]
    for row in rows[:limit]:
        label='正在免费' if row['current'] else '即将免费'
        lines+=['\n'+label+'：'+row['title'],row['description'],'开始：'+row['start'],'截止：'+row['end'],row['page']]
        if row['cover']:media.append(dict(type='image',url=row['cover'],name='Epic-'+str(len(media)+1)+'.jpg'))
    if not rows:lines.append('当前未查到有效的限时免费活动；没有把永久免费游戏当成赠送。')
    lines.append('来源：Epic Games 商城；时间带UTC偏移，地区以所选商店为准。')
    return result('\n'.join(lines)+stamp(at,hit),media)

def repo_name(text):
    match=re.search(r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:[^A-Za-z0-9_.-]|$)',text)
    if match:return (match[1]+'/'+match[2]).removesuffix('.git')
    value=argument(text)
    return value if re.fullmatch(r'[A-Za-z0-9_.-]{1,80}/[A-Za-z0-9_.-]{1,100}',value) else None

def project_rows(data):
    need(isinstance(data,list),'仓库返回结构无效。');lines=[];export=[]
    for row in data[:5]:
        if not isinstance(row,dict) or row.get('private'):continue
        name=row.get('full_name','');need(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',name),'仓库名称结构无效。')
        license=(row.get('license') or {}).get('spdx_id') or '未声明'
        if license=='NOASSERTION':license='未明确识别'
        stars=row.get('stargazers_count',0);need(type(stars) is int and stars>=0,'星标统计格式无效。')
        entry=dict(repository=name,description=clean(row.get('description'),500),stars=stars,language=clean(row.get('language') or '未标注',50),license=clean(license,80),updated_at=clean(row.get('pushed_at'),40),url='https://github.com/'+name,archived=row.get('archived') is True)
        export.append(entry);lines+=['\n'+name+('（已归档）' if entry['archived'] else ''),entry['description'],f"★ {stars:,} · {entry['language']} · {entry['license']}",'更新：'+entry['updated_at'],entry['url']]
    return lines,export

def projects(text,context):
    name=repo_name(text);end=deadline(context)
    if text.startswith('https://') or ('github.com/' in text and not text.startswith('/')):
        if not name:return None
    if name:
        data,at,hit=cached(context,'repo:'+name.lower(),lambda:get_json('https://api.github.com/repos/'+name,end,['api.github.com']));rows=[data];heading='GitHub 项目速览'
    else:
        query=argument(text);need(1<=len(query)<=120,'用法：/找项目 关键词，/项目 owner/repo；直接发送GitHub仓库链接也会自动预览。')
        count=min(5,max(1,int(settings(context).get('max_results',3))));url='https://api.github.com/search/repositories?'+urlencode(dict(q=query+' is:public',sort='stars',per_page=count))
        data,at,hit=cached(context,'search:'+query,lambda:get_json(url,end,['api.github.com']));rows=data.get('items',[]);heading='GitHub 项目搜索：'+query
    lines,export=project_rows(rows)
    media=[dict(type='file',text=json.dumps(export,ensure_ascii=False,indent=2),name='GitHub项目.json')] if export and settings(context).get('attach_json',True) else []
    return result(heading+('\n'.join(lines) if lines else '\n没有找到公开仓库。')+'\n来源：GitHub公开API'+stamp(at,hit),media)

def pronunciation_source(value):
    """Resolve only the Commons page explicitly cited by this phonetic entry."""
    source=approved(value,('commons.wikimedia.org',))
    if not source or urlsplit(source).hostname!='commons.wikimedia.org':return None
    parsed=urlsplit(source);query=parse_qs(parsed.query)
    if parsed.path=='/w/index.php' and len(query.get('curid',[]))==1 and re.fullmatch(r'[1-9][0-9]{0,19}',query['curid'][0]):
        return source,{'pageids':query['curid'][0]}
    title=unquote(parsed.path.removeprefix('/wiki/'))
    if parsed.path.startswith('/wiki/') and title.startswith('File:') and 6<len(title)<240 and not any(c in title for c in '\n\r|'):
        return source,{'titles':title}
    return None


def commons_pronunciation(phonetic,end):
    source=pronunciation_source(phonetic.get('sourceUrl'))
    if not source:return None
    page,selector=source
    query=dict(action='query',prop='imageinfo',iiprop='url|extmetadata',format='json',**selector)
    data=get_json('https://commons.wikimedia.org/w/api.php?'+urlencode(query),end,['commons.wikimedia.org'])
    pages=data.get('query',{}).get('pages',{})
    if not isinstance(pages,dict) or len(pages)!=1:return None
    info=next(iter(pages.values())).get('imageinfo',[])
    if not info or not isinstance(info[0],dict):return None
    info=info[0];audio=approved(info.get('url'),('upload.wikimedia.org',))
    if not audio or urlsplit(audio).hostname!='upload.wikimedia.org' or not urlsplit(audio).path.startswith('/wikipedia/commons/'):return None
    suffix=urlsplit(audio).path.rsplit('.',1)[-1].lower()
    if suffix not in ('mp3','ogg','wav','flac','m4a'):return None
    metadata=info.get('extmetadata') or {}
    def plain(key):return clean(html.unescape(re.sub(r'<[^>]*>','',str((metadata.get(key) or {}).get('value','')))),300)
    artist=plain('Artist');license=plain('LicenseShortName')
    if not license or (not artist and 'public domain' not in license.lower() and 'cc0' not in license.lower()):return None
    description=approved(info.get('descriptionurl'),('commons.wikimedia.org',)) or page
    license_url=approved((metadata.get('LicenseUrl') or {}).get('value'),('creativecommons.org','commons.wikimedia.org'))
    attribution='发音：Wikimedia Commons · '+(artist or '公有领域')+' · '+license+'\n'+description
    if license_url:attribution+='\n许可：'+license_url
    return audio,suffix,attribution


def dictionary_pronunciation(phonetics,end):
    """At most one media probe and one Commons lookup within the query deadline."""
    first=next((p for p in phonetics if approved(p.get('audio'),('api.dictionaryapi.dev',))),None)
    if not first:return None
    audio=approved(first['audio'],('api.dictionaryapi.dev',))
    try:
        response=public_net.request(audio,timeout=min(3,public_net.remaining(end)),max_bytes=512000,max_redirects=0,allowed_hosts=['api.dictionaryapi.dev'])
        body=response.body
        if not (body.startswith((b'ID3',b'OggS',b'fLaC')) or (len(body)>2 and body[0]==255 and body[1]&224==224) or (len(body)>12 and (body.startswith(b'RIFF') and body[8:12]==b'WAVE' or body[4:8]==b'ftyp'))):
            raise ValueError('The pronunciation endpoint did not return audio')
        license=first.get('license') or {};source=pronunciation_source(first.get('sourceUrl'))
        attribution='发音：Free Dictionary API · '+clean(license.get('name') or '许可见来源页面',100)
        if source:attribution+='\n'+source[0]
        license_url=approved(license.get('url'),('creativecommons.org','commons.wikimedia.org'))
        if license_url:attribution+='\n许可：'+license_url
        return audio,'mp3',attribution
    except (public_net.NetworkError,ValueError):
        try:return commons_pronunciation(first,end)
        except (UserError,ValueError,KeyError,TypeError,IndexError,AttributeError):return None


def dictionary(text,context):
    word=argument(text).lower();need(re.fullmatch(r"[a-z][a-z' -]{0,39}",word),'用法：/查词 hello。支持英文单词及简短词组。')
    end=deadline(context);url='https://api.dictionaryapi.dev/api/v2/entries/en/'+quote(word,safe='')
    data,at,hit=cached(context,'dictionary:'+word,lambda:get_json(url,end,['api.dictionaryapi.dev']))
    need(isinstance(data,list) and data and isinstance(data[0],dict),'词典没有收录这个词。');entry=data[0];phonetics=[p for p in entry.get('phonetics',[]) if isinstance(p,dict)]
    ipa=entry.get('phonetic') or next((p.get('text') for p in phonetics if p.get('text')),'')
    lines=['英语词典：'+clean(entry.get('word') or word,60)+' '+clean(ipa,100)];media=[]
    for meaning in entry.get('meanings',[])[:3]:
        lines.append('\n'+clean(meaning.get('partOfSpeech'),40))
        for definition in meaning.get('definitions',[])[:2]:
            lines.append('• '+clean(definition.get('definition'),280))
            if definition.get('example'):lines.append('例：'+clean(definition['example'],180))
    if settings(context).get('send_pronunciation',True):
        audio=dictionary_pronunciation(phonetics,end)
        if audio:
            media.append(dict(type='audio',url=audio[0],name=word.replace(' ','_')+'.'+audio[1]));lines.append(audio[2])
        else:lines.append('该词条暂无可用发音，或发音来源暂时不可用；已保留释义。')
    license=entry.get('license') or {};lines.append('来源：Free Dictionary API / Wiktionary · '+clean(license.get('name'),100))
    return result('\n'.join(lines)+stamp(at,hit),media)

ALIASES=dict(zip('北京 上海 广州 深圳 杭州 南京 成都 重庆 武汉 西安 苏州 天津 长沙 厦门 福州 合肥 郑州 济南 青岛 昆明 南宁 海口 香港 澳门 台北 新加坡'.split(),'Beijing Shanghai Guangzhou Shenzhen Hangzhou Nanjing Chengdu Chongqing Wuhan Xian Suzhou Tianjin Changsha Xiamen Fuzhou Hefei Zhengzhou Jinan Qingdao Kunming Nanning Haikou HongKong Macau Taipei Singapore'.split()))
ALIASES.update({'香港':'Hong Kong','西安':"Xi'an"})

def air_place(value,end):
    if re.fullmatch(r'-?\d+(?:\.\d+)?,\s*-?\d+(?:\.\d+)?',value):
        lat,lon=map(float,value.split(','));need(-90<=lat<=90 and -180<=lon<=180,'经纬度范围无效。');return dict(name=value,latitude=lat,longitude=lon)
    need(1<=len(value)<=80,'用法：/空气质量 上海，或 /空气质量 纬度,经度。')
    data=get_json('https://geocoding-api.open-meteo.com/v1/search?'+urlencode(dict(name=ALIASES.get(value.removesuffix('市'),value),count=5,language='zh',format='json')),end,['geocoding-api.open-meteo.com'])
    found=data.get('results',[]);need(found,'没有找到这个地点，请补充城市英文名或经纬度。')
    row=next((r for r in found if r.get('country_code')=='CN'),found[0]) if value.removesuffix('市') in ALIASES and value not in ('新加坡','香港','澳门','台北') else found[0]
    return dict(name=clean(row.get('name'),80)+' · '+clean(row.get('admin1'),80)+' · '+clean(row.get('country'),80),latitude=row['latitude'],longitude=row['longitude'])

def air_chart(data):
    hourly=data.get('hourly') or {};times=hourly.get('time',[]);current=data['current']['time'];indexes=[i for i,t in enumerate(times) if t>=current][:24]
    if not indexes:return None
    series=[]
    for key in ('pm2_5','pm10'):
        values=hourly.get(key,[]);series.append([(n,values[i]) for n,i in enumerate(indexes) if i<len(values) and type(values[i]) in (int,float) and math.isfinite(values[i]) and values[i]>=0])
    all_values=[v for line in series for _,v in line]
    if not all_values:return None
    image=Image.new('RGB',(700,340),'#f8f6ef');d=ImageDraw.Draw(image);d.text((30,15),'未来24小时颗粒物 · 模型预报',font=font(24),fill='#25453c');top=max(20,math.ceil(max(all_values)/20)*20)
    for n in range(5):
        y=80+n*45;d.line((65,y,670,y),fill='#dce5db');d.text((15,y-9),str(round(top*(4-n)/4)),font=font(14),fill='#62766b')
    for line,color,label in zip(series,('#528e7e','#c99245'),('PM2.5','PM10')):
        points=[(65+n*605/max(1,len(indexes)-1),260-v/top*180) for n,v in line]
        # Do not join across missing values or treat them as zero.
        for index in range(1,len(points)):
            if line[index][0]==line[index-1][0]+1:d.line((points[index-1],points[index]),fill=color,width=3)
        for x,y in points:d.ellipse((x-2,y-2,x+2,y+2),fill=color)
        d.text((380 if label=='PM2.5' else 520,49),label,font=font(18),fill=color)
    d.text((60,280),times[indexes[0]].replace('T',' '),font=font(14),fill='#62766b');d.text((500,280),times[indexes[-1]].replace('T',' '),font=font(14),fill='#62766b');d.text((30,311),'μg/m³ · Open-Meteo / CAMS · 非监测站实测',font=font(15),fill='#62766b')
    return image_item(image,'空气质量趋势.png')

def air(text,context):
    value=argument(text) or settings(context).get('default_city','');need(value,'用法：/空气质量 上海。不会查询你的位置或IP。');end=deadline(context)
    def load():
        place=air_place(value,end);params=dict(latitude=place['latitude'],longitude=place['longitude'],current='pm2_5,pm10,us_aqi,ozone,nitrogen_dioxide,carbon_monoxide',hourly='pm2_5,pm10',forecast_days=2,timezone='auto')
        return {'place':place,'data':get_json('https://air-quality-api.open-meteo.com/v1/air-quality?'+urlencode(params),end,['air-quality-api.open-meteo.com'])}
    raw,at,hit=cached(context,'air:'+value,load);data=raw['data'];current=data.get('current');need(isinstance(current,dict) and isinstance(current.get('time'),str),'当前模型数据不可用。')
    lines=['空气质量：'+raw['place']['name'],'数据时间：'+current['time']+' '+clean(data.get('timezone'),80)]
    for key,label in (('us_aqi','AQI（美标）'),('pm2_5','PM2.5'),('pm10','PM10'),('ozone','臭氧'),('nitrogen_dioxide','二氧化氮'),('carbon_monoxide','一氧化碳')):
        number=current.get(key)
        if type(number) in (int,float) and math.isfinite(number):lines.append(label+'：'+str(number)+' '+clean(data.get('current_units',{}).get(key),20))
    lines.append('来源：Open-Meteo / CAMS模型估计；AQI为美标，非中国监测站实时观测。');chart=air_chart(data)
    return result('\n'.join(lines)+stamp(at,hit),[chart] if chart else [])

FUNCTIONS={'d_epic':epic,'d_projects':projects,'d_dictionary':dictionary,'d_air':air}
def handle(pid,text,context):return guard(FUNCTIONS[pid],text,context)
