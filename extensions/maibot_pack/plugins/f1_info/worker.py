"""Independent Jolpica/Ergast-compatible F1 queries. No FastF1 model or telemetry downloads."""
import importlib.util
import re
from pathlib import Path
from datetime import datetime,timedelta,timezone

_spec=importlib.util.spec_from_file_location('yukino_f1_net',Path(__file__).with_name('_net.py'))
net=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(net)
ROOT='https://api.jolpi.ca/ergast/f1/'
HELP='/f1 下一站\n/f1 赛历 [赛季]\n/f1 积分 [赛季]\n/f1 车队 [赛季]\n/f1 成绩 [赛季] [轮次]\n/f1 排位 [赛季] [轮次]\n默认当前赛季、最近一站；轮次可用 last。'

def _year(value):
    if value=='current':return value
    if not re.fullmatch(r'(?:19|20)\d{2}',value) or not 1950<=int(value)<=datetime.now(timezone.utc).year+1:raise net.LookupFailure('赛季须为1950至明年的年份')
    return value

def _load(path):
    result=net.fetch(ROOT+path+'.json',{'limit':100},ttl=300)
    data=result.get('MRData')
    if not isinstance(data,dict):raise ValueError()
    return data

def _time(item,zone):
    date=item.get('date','')
    clock=item.get('time')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(date)):return '时间未公布'
    if not clock:return str(date)+'（具体时间未公布）'
    try:return datetime.fromisoformat(date+'T'+clock.replace('Z','+00:00')).astimezone(zone).strftime('%m-%d %H:%M')
    except ValueError:return str(date)+'（时间格式暂不可用）'

def handle(text,context):
    net.begin()
    try:
        arg=net.argument(text,{'/f1','/F1'},100)
        if arg is None:return None
        parts=arg.split();action=parts.pop(0) if parts else '下一站'
        if action in ('帮助','help'):return HELP
        cfg=net.settings(context);zone=timezone(timedelta(hours=net.number(cfg.get('utc_offset_hours'),8,-12,14)))
        label='UTC'+datetime.now(zone).strftime('%z')
        limit=net.number(cfg.get('result_limit'),20,1,30)
        season=_year(parts[0]) if parts else 'current'
        if len(parts)>2:return HELP
        if action in ('下一站','周末','next') and parts:return '下一站使用当前赛季；历史年份可用 /f1 赛历 年份 查询。'
        if action not in ('成绩','结果','排位','results') and len(parts)>1:return HELP
        if action in ('下一站','周末','next'):
            data=_load('current/next');races=data.get('RaceTable',{}).get('Races',[])
            if not races:return 'Jolpica 尚未公布下一站赛程，或赛季已经结束。'
            race=races[0];circuit=race.get('Circuit',{})
            lines=[f"F1 下一站｜{race.get('season','')} R{race.get('round','')} {net.clean(race.get('raceName',''))}",
                   net.clean(circuit.get('circuitName',''))+'｜'+net.clean(circuit.get('Location',{}).get('country','')),
                   '时间：'+label]
            for key,title in [('FirstPractice','一练'),('SecondPractice','二练'),('ThirdPractice','三练'),('SprintQualifying','冲刺排位'),('SprintShootout','冲刺排位'),('Sprint','冲刺赛'),('Qualifying','排位赛')]:
                if isinstance(race.get(key),dict):lines.append(title+'：'+_time(race[key],zone))
            lines.append('正赛：'+_time(race,zone))
        elif action in ('赛历','赛程','calendar'):
            data=_load(season);table=data.get('RaceTable',{});races=table.get('Races',[])
            if not races:return '该赛季赛历尚未发布或没有数据。'
            lines=['F1 '+str(table.get('season',season))+' 赛历｜'+label]
            for row in races[:30]:lines.append(f"R{row.get('round','?')} {_time(row,zone)} {net.clean(row.get('raceName',''),70)}")
        elif action in ('积分','车手','车队','standings'):
            constructor=action=='车队';endpoint='constructorstandings' if constructor else 'driverstandings'
            data=_load(season+'/'+endpoint);rows=data.get('StandingsTable',{}).get('StandingsLists',[])
            if not rows:return '该赛季积分榜尚未发布。'
            table=rows[0];entries=table.get('ConstructorStandings' if constructor else 'DriverStandings',[])
            lines=[f"F1 {table.get('season',season)} {'车队' if constructor else '车手'}积分｜R{table.get('round','?')}后"]
            for row in entries[:limit]:
                person=row.get('Constructor' if constructor else 'Driver',{})
                name=person.get('name') if constructor else person.get('code') or (str(person.get('givenName',''))+' '+str(person.get('familyName','')))
                lines.append(f"{row.get('position','?')}. {net.clean(name,60)}｜{row.get('points','?')}分｜{row.get('wins','?')}胜")
        elif action in ('成绩','结果','排位','results'):
            rd=parts[1] if len(parts)>1 else 'last'
            if rd!='last' and not re.fullmatch(r'(?:[1-9]|[12]\d|30)',rd):return '轮次为1—30，或 last；例如 /f1 成绩 2024 5。'
            qualifying=action=='排位';data=_load(season+'/'+rd+('/qualifying' if qualifying else '/results'))
            races=data.get('RaceTable',{}).get('Races',[])
            if not races:return '该站结果尚未发布或没有数据。'
            race=races[0];lines=[f"F1 {race.get('season',season)} R{race.get('round',rd)} {net.clean(race.get('raceName',''))}｜{'排位' if qualifying else '正赛'}"]
            for row in race.get('QualifyingResults' if qualifying else 'Results',[])[:limit]:
                driver=row.get('Driver',{});name=driver.get('code') or str(driver.get('givenName',''))+' '+str(driver.get('familyName',''))
                details=' / '.join(str(row[k]) for k in ('Q1','Q2','Q3') if k in row) if qualifying else str(row.get('points','?'))+'分｜'+net.clean(row.get('status',''))
                lines.append(f"{row.get('position','?')}. {net.clean(name,45)}｜{net.clean(row.get('Constructor',{}).get('name',''),45)}｜{details}")
        else:return HELP
        return '\n'.join(lines)+f'\n数据查询：{net.local_time()}\n来源：Jolpica F1（Ergast兼容数据，结果以官方赛会最终公告为准）。'
    except Exception as exc:return net.failure('F1',exc)
