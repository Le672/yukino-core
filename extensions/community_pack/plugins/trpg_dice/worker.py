"""Bounded independent TRPG dice; no eval or copied rule text."""
import random
import re

_rng=random.SystemRandom()

def _bound(settings,key,default,low,high):
    value=settings.get(key,default)
    return max(low,min(high,value)) if type(value) is int else default

def _roll(expression,settings):
    match=re.fullmatch(r"([1-9][0-9]{0,2})?[dD]([1-9][0-9]{0,6})([+-][0-9]{1,7})?",expression)
    if not match: raise ValueError("骰式仅支持 nDm±k，例如 2d6+3、d100；不支持代码、括号或多个骰组。")
    count,faces,extra=int(match[1] or 1),int(match[2]),int(match[3] or 0)
    if count>_bound(settings,"max_dice",100,1,100) or faces>_bound(settings,"max_faces",1000000,2,1000000) or abs(extra)>1000000:
        raise ValueError("骰子数量、面数或修正值超过上限。")
    values=[_rng.randint(1,faces) for _ in range(count)]
    return values,extra

def _check(roll,skill):
    if roll==1: return "大成功"
    if roll==100 or (skill<50 and roll>=96): return "大失败"
    if roll<=skill//5: return "极难成功"
    if roll<=skill//2: return "困难成功"
    if roll<=skill: return "成功"
    return "失败"

HELP="/r 2d6+3\n/ra 技能值 [技能名]：CoC7默认 d100，1大成功，技能<50时96-100大失败，否则100；极难/困难阈值向下取整。不含奖励骰/惩罚骰。\n/coc [1-5]：随机生成CoC7基础属性（非完整人物卡）\n/先攻 [角色:修正 ...]：逐人1d20+修正，同点按输入顺序；默认自己。"

def handle(text,context):
    parts=text.strip().split()
    if not parts: return HELP
    command=parts[0]
    settings=context.get("settings",{})
    if command=="/r":
        if len(parts)!=2: return "用法：/r 2d6+3（骰式与指令用空格分隔）。"
        try: values,extra=_roll(parts[1],settings)
        except ValueError as exc: return str(exc)
        return f"{parts[1]} = "+" + ".join(map(str,values))+(f" {extra:+d}" if extra else "")+f" = {sum(values)+extra}"
    if command=="/ra":
        if len(parts) not in (2,3) or not re.fullmatch(r"[0-9]{1,3}",parts[1]) or not 0<=int(parts[1])<=100:
            return "用法：/ra 技能值0-100 [技能名]。"
        skill=int(parts[1]); roll=_rng.randint(1,100)
        label=parts[2] if len(parts)==3 else "检定"
        if len(label)>30: return "技能名称最多30字。"
        return f"CoC7默认｜{label}：d100={roll} / {skill}，{_check(roll,skill)}。"
    if command=="/coc":
        if len(parts)>2 or (len(parts)==2 and not re.fullmatch(r"[1-5]",parts[1])): return "用法：/coc [1-5]。"
        cards=[]
        for i in range(int(parts[1]) if len(parts)==2 else 1):
            values={name:sum(_rng.randint(1,6) for _ in range(3))*5 for name in ("STR","CON","DEX","APP","POW")}
            values.update({name:(sum(_rng.randint(1,6) for _ in range(2))+6)*5 for name in ("SIZ","INT","EDU")})
            values["LUCK"]=sum(_rng.randint(1,6) for _ in range(3))*5
            cards.append(f"#{i+1} "+" ".join(f"{name}:{value}" for name,value in values.items()))
        return "CoC7基础随机属性，未应用年龄/职业调整，LUCK单独计算；非完整人物卡：\n"+"\n".join(cards)
    if command=="/先攻":
        specs=parts[1:] or ["自己:0"]
        if len(specs)>12: return "先攻最多12个角色。"
        entries=[]; names=set()
        for spec in specs:
            match=re.fullmatch(r"([^:\s]{1,20}):([+-]?[0-9]{1,3})",spec)
            if not match or abs(int(match[2]))>100 or match[1] in names:
                return "用法：/先攻 角色:修正 ...；名称不重复，修正范围-100至100。"
            names.add(match[1]); entries.append((match[1],int(match[2])))
        rows=[]
        for index,(name,modifier) in enumerate(entries):
            roll=_rng.randint(1,20)
            rows.append((roll+modifier,index,name,roll,modifier))
        rows.sort(key=lambda row:(-row[0],row[1]))
        return "先攻（1d20+修正，同分按输入顺序）：\n"+"\n".join(f"{i}. {name}: {roll}{modifier:+d}={total}" for i,(total,_,name,roll,modifier) in enumerate(rows,1))
    return HELP
