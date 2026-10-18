"""Small transparent random meal picker; no network or state."""
import json
import random

_rng=random.SystemRandom()
DEFAULT=["番茄鸡蛋面","黄焖鸡米饭","饺子","麻辣烫","炒饭","牛肉面","清蒸鱼配米饭","盖浇饭","馄饨","鸡肉沙拉"]

def _list(raw,label):
    try:
        data=json.loads(raw)
        if not isinstance(data,list) or len(data)>100: raise ValueError()
        if any(not isinstance(x,str) or not 1<=len(x)<=40 or any(ord(ch)<32 for ch in x) for x in data): raise ValueError()
        return list(dict.fromkeys(data))
    except (TypeError,ValueError): raise ValueError(label+"须为最多100个短文本组成的 JSON 数组。") from None

def handle(text,context):
    parts=text.strip().split(maxsplit=1)
    if not parts or parts[0] not in ("/吃什么","/吃什么列表"): return "用法：/吃什么 [排除:饺子,炒饭]；/吃什么列表。"
    settings=context.get("settings",{})
    try:
        candidates=_list(settings.get("candidates_json",json.dumps(DEFAULT,ensure_ascii=False)),"候选菜单")
        excluded=_list(settings.get("excluded_json","[]"),"排除菜单")
    except ValueError as exc: return str(exc)
    if len(parts)>1:
        if not parts[1].startswith("排除:"): return "可选参数写法：/吃什么 排除:饺子,炒饭（按完整菜名排除）。"
        excluded += [x.strip() for x in parts[1][3:].replace("，",",").split(",") if x.strip()]
    candidates=[x for x in candidates if x not in set(excluded)]
    if not candidates: return "排除后没有可选菜单，请调整插件设置或排除项。"
    if parts[0]=="/吃什么列表": return "当前可选菜单：\n"+"、".join(candidates)
    return "今天可以吃："+_rng.choice(candidates)+"。菜单随机推荐，请按口味、过敏和实际供应自行选择。"
