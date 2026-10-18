"""Original bounded ICS course importer. No files/URLs, polling, or delivery."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import time as clock

LOCAL = timezone(timedelta(hours=8))
DAYS = {'MO':0,'TU':1,'WE':2,'TH':3,'FR':4,'SA':5,'SU':6}
USAGE = ('/课表 导入 后粘贴 BEGIN:VCALENDAR…END:VCALENDAR；/课表 今天；明天；YYYY-MM-DD；历史；恢复 编号；清空。'
         '仅私聊、纯文本，总命令最多4000字；支持UTC、Asia/Shanghai或无TZID的固定UTC+8。'
         '仅支持每周RRULE的INTERVAL/COUNT/UNTIL/BYDAY及EXDATE；不接收文件/网址，不主动提醒。')
EXAMPLE = ('BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:example-course\nSUMMARY:示例课程\n'
           'DTSTART;TZID=Asia/Shanghai:20261019T090000\nDTEND;TZID=Asia/Shanghai:20261019T100000\n'
           'RRULE:FREQ=WEEKLY;COUNT=4;BYDAY=MO\nEND:VEVENT\nEND:VCALENDAR')


def need(value, message):
    if not value: raise ValueError(message)


def setting(ctx, key, default, low, high):
    settings = ctx.get('settings', {})
    value = settings.get(key, default) if isinstance(settings, dict) else default
    return value if type(value) is int and low <= value <= high else default


def identity(ctx):
    scope, user = str(ctx.get('session_id','')), str(ctx.get('user_id',''))
    need(scope.startswith('p:') and 2 < len(scope) <= 100 and 0 < len(user) <= 40 and not any(ord(c)<32 for c in scope+user), '课表仅支持私聊，并需要有效的当前用户和会话。')
    return scope, user


def db_open(ctx):
    value = ctx.get('data_dir'); root = Path(str(value or ''))
    need(value and root.is_absolute(), '需要内核分配的独立绝对数据目录。')
    root.mkdir(parents=True, exist_ok=True); path = root/'courses.sqlite3'; db = sqlite3.connect(path, timeout=3)
    try:
        path.chmod(0o600)
        db.execute('CREATE TABLE IF NOT EXISTS calendars(id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT NOT NULL,user TEXT NOT NULL,body TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created REAL NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS calendars_lookup ON calendars(scope,user,active)'); db.commit(); db.execute('BEGIN IMMEDIATE')
    except Exception:
        db.close(); raise
    return db


def unfold(raw):
    need(isinstance(raw,str) and 0 < len(raw) <= 4000, 'ICS文本不能为空且最多4000字；整条命令也不得超过4000字。')
    raw = raw.lstrip('\ufeff').replace('\r\n','\n')
    need('\r' not in raw and not any(ord(c)<32 and c not in '\n\t' for c in raw), 'ICS含不支持的控制字符或单独CR换行。')
    lines = []
    for line in raw.split('\n'):
        if line.startswith((' ','\t')):
            need(bool(lines), '首行不能是折行续行。'); lines[-1] += line[1:]
        elif line:
            lines.append(line)
    need(len(lines) <= 500 and all(len(line) <= 2000 for line in lines), 'ICS行数或单行过长。')
    return lines


def split_quoted(value, separator):
    result, start, quoted = [], 0, False
    for i, c in enumerate(value):
        if c == '"': quoted = not quoted
        elif c == separator and not quoted: result.append(value[start:i]); start = i+1
    need(not quoted, 'ICS参数引号未闭合。'); result.append(value[start:]); return result


def property_line(line):
    quoted = False; colon = None
    for i, char in enumerate(line):
        if char == '"': quoted = not quoted
        elif char == ':' and not quoted: colon = i; break
    need(colon is not None, 'ICS属性缺少冒号。')
    head, value = line[:colon], line[colon+1:]
    parts = split_quoted(head,';'); key = parts[0].upper(); params = {}
    need(bool(re.fullmatch('[A-Z][A-Z0-9-]*',key)), 'ICS属性名无效。')
    for part in parts[1:]:
        pair = part.split('=',1); need(len(pair)==2 and pair[0], 'ICS参数格式无效。')
        name, content = pair[0].upper(), pair[1]
        if content.startswith('"') and content.endswith('"'): content = content[1:-1]
        need(name not in params and len(content)<=200, 'ICS参数重复或过长。'); params[name] = content
    return key, params, value


def unescape_text(value, maximum):
    result = []; i = 0
    while i < len(value):
        if value[i] != '\\': result.append(value[i]); i += 1; continue
        need(i+1<len(value), 'ICS文本转义未完成。'); code = value[i+1]
        need(code in ('\\',';',',','n','N'), 'ICS文本含不支持的转义。')
        result.append('\n' if code in ('n','N') else code); i += 2
    text = ''.join(result); need(len(text)<=maximum, f'ICS文字属性最多{maximum}字。'); return text


def parse_time(value, params, default_zone=None):
    need(set(params) <= {'TZID','VALUE'} and params.get('VALUE','DATE-TIME').upper()=='DATE-TIME', '仅支持DATE-TIME时间，不支持全天VALUE=DATE或其它时间参数。')
    need(bool(re.fullmatch(r'\d{8}T\d{6}Z?',value)), '时间应为YYYYMMDDTHHMMSS，可带UTC后缀Z。')
    raw = datetime.strptime(value.rstrip('Z'),'%Y%m%dT%H%M%S')
    need(1992 <= raw.year <= 2099, '课程时间仅支持1992～2099年，以避免未支持的历史时区规则。')
    tzid = params.get('TZID')
    if value.endswith('Z'):
        need(tzid is None, 'UTC后缀Z不能再同时指定TZID。'); zone = timezone.utc
    elif tzid is None:
        zone = default_zone or LOCAL
    else:
        zones = {'Asia/Shanghai':LOCAL,'UTC':timezone.utc,'Etc/UTC':timezone.utc,'UTC+08:00':LOCAL,'UTC+8':LOCAL,'Etc/GMT-8':LOCAL}
        need(tzid in zones, '不支持TZID='+tzid+'；只支持UTC或固定UTC+8（含Asia/Shanghai）。'); zone = zones[tzid]
    return raw.replace(tzinfo=zone)


def parse_rule(value, start, explicit_timezone):
    rule = {}
    for part in value.split(';'):
        pair = part.split('=',1); need(len(pair)==2 and pair[0] and pair[1], 'RRULE字段格式错误。')
        key = pair[0].upper(); need(key not in rule, 'RRULE字段不能重复。'); rule[key] = pair[1].upper()
    need(set(rule) <= {'FREQ','INTERVAL','COUNT','UNTIL','BYDAY'}, 'RRULE仅支持FREQ/INTERVAL/COUNT/UNTIL/BYDAY；其它规则会拒绝整份导入。')
    need(rule.get('FREQ')=='WEEKLY', '仅支持FREQ=WEEKLY，不支持其它重复频率。')
    need(not ('COUNT' in rule and 'UNTIL' in rule), 'RRULE的COUNT和UNTIL不能同时出现。')
    interval = rule.get('INTERVAL','1'); need(interval.isascii() and interval.isdigit() and 1<=int(interval)<=52, 'INTERVAL须为1～52的整数。')
    count = rule.get('COUNT')
    if count is not None: need(count.isascii() and count.isdigit() and 1<=int(count)<=10000, 'COUNT须为1～10000的整数。')
    days = rule.get('BYDAY', next(k for k,v in DAYS.items() if v==start.weekday())).split(',')
    need(0 < len(days) <= 7 and len(days)==len(set(days)) and all(d in DAYS for d in days), 'BYDAY只支持不重复的MO/TU/WE/TH/FR/SA/SU，不支持序号。')
    weekdays = sorted(DAYS[d] for d in days)
    need(start.weekday() in weekdays, 'DTSTART星期必须包含在BYDAY中，避免未定义的首个重复事件。')
    until = None
    if 'UNTIL' in rule:
        raw = rule['UNTIL']; need(not explicit_timezone or raw.endswith('Z'), '带TZID或Z的DTSTART要求UNTIL使用UTC后缀Z。')
        until_time = parse_time(raw,{}, start.tzinfo)
        need(until_time >= start, 'UNTIL不能早于DTSTART。'); until = until_time.isoformat()
    return {'interval':int(interval),'count':int(count) if count is not None else None,'until':until,'days':weekdays}


def event_from_properties(properties):
    allowed = {'UID','SUMMARY','LOCATION','DESCRIPTION','DTSTART','DTEND','RRULE','EXDATE','DTSTAMP','CREATED','LAST-MODIFIED','SEQUENCE','TRANSP','STATUS','CLASS','URL','ORGANIZER','ATTENDEE'}
    found = {}
    for key, params, value in properties:
        need(key in allowed, '不支持VEVENT属性 '+key+'，整份导入未保存。')
        if key != 'EXDATE' and key != 'ATTENDEE': need(key not in found, 'VEVENT属性重复：'+key)
        found.setdefault(key,[]).append((params,value))
    need(all(k in found for k in ('UID','DTSTART','DTEND')), '每条课程必须包含UID、DTSTART和DTEND。')
    for key in ('UID','RRULE'):
        if key in found: need(not found[key][0][0], key+'不支持参数。')
    uid = unescape_text(found['UID'][0][1],200); need(bool(uid), 'UID不能为空。')
    if 'STATUS' in found: need(not found['STATUS'][0][0] and found['STATUS'][0][1] in ('CONFIRMED','TENTATIVE'), '不支持已取消课程或未知STATUS；请先清理日历。')
    start_params, start_raw = found['DTSTART'][0]; start = parse_time(start_raw,start_params)
    end = parse_time(found['DTEND'][0][1],found['DTEND'][0][0])
    need(timedelta(0) < end-start <= timedelta(hours=48), 'DTEND必须晚于DTSTART，单次课程最多48小时。')
    def text_property(key,maximum,default=''):
        if key not in found: return default
        params,value = found[key][0]
        need(set(params) <= {'LANGUAGE'} and (not params or bool(re.fullmatch('[A-Za-z-]{1,30}',params['LANGUAGE']))), key+'含不支持的文字参数。')
        return unescape_text(value,maximum)
    result = {'uid':uid,'title':text_property('SUMMARY',200,'未提供标题'),'location':text_property('LOCATION',200),
              'description':text_property('DESCRIPTION',600),'start':start.isoformat(),'end':end.isoformat(),'rule':None,'excluded':[],
              'status':found.get('STATUS',[({},'CONFIRMED')])[0][1]}
    if 'RRULE' in found: result['rule'] = parse_rule(found['RRULE'][0][1],start,bool(start_params.get('TZID')) or start_raw.endswith('Z'))
    exclusions = []
    for params,value in found.get('EXDATE',[]):
        for raw in value.split(','):
            exclusion = parse_time(raw,params,start.tzinfo); exclusions.append(exclusion.astimezone(timezone.utc).isoformat())
    need(len(exclusions)<=100, '每条课程最多100个EXDATE排除时间。')
    result['excluded'] = sorted(set(exclusions)); return result


def parse_ics(raw,max_events=50):
    lines = unfold(raw)
    need(lines and lines[0].upper()=='BEGIN:VCALENDAR' and lines[-1].upper()=='END:VCALENDAR', '必须提供完整的BEGIN:VCALENDAR…END:VCALENDAR。')
    events, current, seen_calendar = [], None, {}
    for line in lines[1:-1]:
        key, params, value = property_line(line)
        if key=='BEGIN':
            need(not params and value.upper()=='VEVENT' and current is None, '只支持VEVENT，不支持VTIMEZONE、VALARM或其它嵌套组件。'); current=[]; continue
        if key=='END':
            need(not params and value.upper()=='VEVENT' and current is not None, '日历组件结束位置无效。')
            events.append(event_from_properties(current)); current=None
            need(len(events)<=max_events, f'课程数量超过面板上限{max_events}条，整份未保存。'); continue
        if current is not None: current.append((key,params,value)); continue
        need(key in ('VERSION','PRODID','CALSCALE','METHOD','X-WR-CALNAME','X-WR-TIMEZONE','X-PUBLISHED-TTL'), '不支持日历属性 '+key)
        need(not params and key not in seen_calendar, '日历属性重复或含不支持参数：'+key); seen_calendar[key]=value
        if key=='VERSION': need(value=='2.0', '只支持VERSION:2.0。')
        if key=='CALSCALE': need(value.upper()=='GREGORIAN', '只支持公历。')
        if key=='METHOD': need(value.upper()=='PUBLISH', '只支持METHOD:PUBLISH，不处理邀请或取消。')
        if key=='X-WR-TIMEZONE': need(value in ('Asia/Shanghai','UTC','Etc/UTC','UTC+08:00','UTC+8','Etc/GMT-8'), '日历声明了不支持的时区。')
    need(current is None and seen_calendar.get('VERSION')=='2.0' and events, '日历必须有VERSION:2.0与至少一条完整VEVENT。')
    need(len({e['uid'] for e in events})==len(events), 'UID重复；不支持RECURRENCE-ID覆盖事件，整份未保存。')
    return events


def occurrence_rank(start,candidate,rule):
    if candidate < start or candidate.weekday() not in rule['days']: return None
    first_monday = start.date()-timedelta(days=start.weekday())
    monday = candidate.date()-timedelta(days=candidate.weekday())
    weeks = (monday-first_monday).days//7
    if weeks < 0 or weeks % rule['interval']: return None
    first_days = [d for d in rule['days'] if d>=start.weekday()]
    if weeks==0: return sum(d<=candidate.weekday() for d in first_days)
    cycles = weeks//rule['interval']
    return len(first_days)+(cycles-1)*len(rule['days'])+sum(d<=candidate.weekday() for d in rule['days'])


def occurrences_on(events,day):
    start_of_day = datetime.combine(day,time(),LOCAL); end_of_day = start_of_day+timedelta(days=1); result=[]
    for event in events:
        start,end = datetime.fromisoformat(event['start']),datetime.fromisoformat(event['end']); duration=end-start; rule=event['rule']
        if not rule: candidates=[start]
        else:
            lower=(start_of_day-duration).astimezone(start.tzinfo).date(); upper=end_of_day.astimezone(start.tzinfo).date()
            candidates=[]
            for offset in range((upper-lower).days+1):
                candidate=datetime.combine(lower+timedelta(days=offset),start.timetz()); rank=occurrence_rank(start,candidate,rule)
                if rank is None or (rule['count'] is not None and rank>rule['count']): continue
                if rule['until'] and candidate>datetime.fromisoformat(rule['until']): continue
                candidates.append(candidate)
        for candidate in candidates:
            if candidate.astimezone(timezone.utc).isoformat() in event['excluded']: continue
            finish=candidate+duration
            if candidate < end_of_day and finish > start_of_day: result.append((candidate.astimezone(LOCAL),finish.astimezone(LOCAL),event))
    return sorted(result,key=lambda item:(item[0],item[1],item[2]['uid']))


def parse_day(raw):
    day=date.fromisoformat(raw); need(1992<=day.year<=2099, '查询日期仅支持1992～2099年。'); return day


def handle(text,context):
    if not isinstance(text,str) or not text.strip() or text.strip().split()[0]!='/课表': return None
    db=None
    try:
        need(len(text)<=4000, '整条课表命令最多4000字，请减少单次导入课程。')
        arg=text.strip()[len('/课表'):].lstrip(); scope,user=identity(context)
        if arg in ('帮助','help','?'): return USAGE
        if arg=='示例': return '/课表 导入\n'+EXAMPLE
        action,_,tail=arg.partition(' ')
        if arg.startswith('导入') and (len(arg)==2 or arg[2].isspace()): action,tail='导入',arg[2:].lstrip()
        parsed=parse_ics(tail,setting(context,'max_events',50,5,100)) if action=='导入' else None
        db=db_open(context)
        if action=='导入':
            body=json.dumps(parsed,ensure_ascii=False,separators=(',',':'))
            current=db.execute('SELECT body FROM calendars WHERE scope=? AND user=? AND active=1',(scope,user)).fetchone()
            if current and current[0]==body: db.rollback(); return '课表内容相同，未新增版本。'
            count=db.execute('SELECT COUNT(*) FROM calendars WHERE scope=? AND user=?',(scope,user)).fetchone()[0]
            cap=setting(context,'max_versions',10,1,30); need(count<cap,f'导入历史已达{cap}版上限，旧版本保留，未替换课表。')
            db.execute('UPDATE calendars SET active=0 WHERE scope=? AND user=?',(scope,user))
            row=db.execute('INSERT INTO calendars(scope,user,body,created) VALUES(?,?,?,?)',(scope,user,body,clock.time()))
            db.commit(); return f'课表已导入：{len(parsed)}条，版本#{row.lastrowid}。查询按UTC+8；不会主动提醒。'
        if action=='历史':
            need(not tail,'历史不接受其他用户或编号。')
            rows=db.execute('SELECT id,body,active FROM calendars WHERE scope=? AND user=? ORDER BY id DESC LIMIT 30',(scope,user)).fetchall(); db.rollback()
            return '课表历史\n'+('\n'.join(f"#{i} {len(json.loads(body))}条 · {'当前' if active else '已归档'}" for i,body,active in rows) or '暂无历史。')
        if action=='恢复':
            need(tail.isascii() and tail.isdigit(),'恢复需要本人的历史版本编号。'); identifier=int(tail)
            row=db.execute('SELECT body FROM calendars WHERE id=? AND scope=? AND user=?',(identifier,scope,user)).fetchone(); need(row is not None,'版本不存在或不属于当前用户／会话。')
            db.execute('UPDATE calendars SET active=0 WHERE scope=? AND user=?',(scope,user)); db.execute('UPDATE calendars SET active=1 WHERE id=?',(identifier,)); db.commit(); return f'课表已恢复至版本#{identifier}，旧版本保留。'
        if action=='清空':
            need(not tail,'清空只作用于本人的当前课表，不接受其他参数。'); db.execute('UPDATE calendars SET active=0 WHERE scope=? AND user=? AND active=1',(scope,user)); db.commit(); return '当前课表已归档，历史版本保留；不会主动提醒。'
        need(not tail,'用法：/课表 今天、明天或YYYY-MM-DD；其它操作见帮助。')
        now=datetime.now(LOCAL).date(); day=now if action in ('','今天') else now+timedelta(days=1) if action=='明天' else parse_day(action)
        row=db.execute('SELECT body FROM calendars WHERE scope=? AND user=? AND active=1',(scope,user)).fetchone(); db.rollback()
        if not row: return '尚未导入本会话的个人课表。发送 /课表 示例 查看纯文本导入格式。'
        events=occurrences_on(json.loads(row[0]),day); cap=setting(context,'result_limit',20,1,50)
        lines=[day.isoformat()+' 课表 · UTC+8']
        for begin,end,event in events[:cap]:
            stamp=begin.strftime('%H:%M')+'–'+end.strftime('%H:%M') if begin.date()==end.date()==day else begin.strftime('%m-%d %H:%M')+'–'+end.strftime('%m-%d %H:%M')
            lines.append(stamp+' '+('（暂定）' if event.get('status')=='TENTATIVE' else '')+event['title']+(' @'+event['location'] if event['location'] else ''))
        if not events: lines.append('当天没有课程。')
        if len(events)>cap: lines.append(f'共{len(events)}条，仅显示前{cap}条；可在面板提高显示上限。')
        return '\n'.join(lines)
    except (ValueError,TypeError,OverflowError) as exc:
        if db: db.rollback()
        return '课表操作未完成：'+str(exc)[:250]
    except (sqlite3.Error,OSError,json.JSONDecodeError):
        if db: db.rollback()
        return '课表存储暂不可用，原数据未被替换。'
    finally:
        if db: db.close()
