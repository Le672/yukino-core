"""Scope-local display aliases, never authorization identities."""
import os
from pathlib import Path
import re
import sqlite3

def _open(context):
    root=Path(context.get("data_dir",""))
    if not context.get("data_dir") or not root.is_absolute(): raise ValueError()
    root.mkdir(parents=True,exist_ok=True)
    path=root/"aliases.sqlite3"
    db=sqlite3.connect(path,timeout=10)
    db.execute("PRAGMA busy_timeout=10000")
    if os.name!="nt": path.chmod(0o600)
    db.execute("CREATE TABLE IF NOT EXISTS aliases(scope TEXT,user TEXT,alias TEXT NOT NULL,PRIMARY KEY(scope,user))")
    return db

HELP="/外号 设置 外号（只改自己）\n/外号 查 [QQ号]\n/外号 清除（只清自己）\n主人管理：/外号 管理 QQ号 设置 外号 或 /外号 管理 QQ号 清除\n外号仅显示，不作为权限或真实身份依据，按会话隔离。"

def handle(text,context):
    parts=text.strip().split(maxsplit=4)
    if len(parts)<2 or parts[0]!="/外号": return HELP
    scope,user=str(context.get("session_id","")),str(context.get("user_id",""))
    if not scope or not user or len(scope)>200 or len(user)>100: return "缺少有效会话或用户标识。"
    target=user
    action=parts[1]
    alias=""
    if action=="管理":
        if user not in {str(x) for x in context.get("owner_ids",[])}: return "只有内核配置的主人能管理其他人的外号。"
        if len(parts)<4 or not re.fullmatch(r"[0-9]{5,15}",parts[2]): return HELP
        target,action=parts[2],parts[3]
        if action=="设置" and len(parts)==5: alias=parts[4]
        elif action=="清除" and len(parts)==4: pass
        else: return HELP
    elif action=="设置":
        # Keep spaces inside an alias, never parse an apparent QQ as a target.
        raw=text.strip().split(maxsplit=2)
        if len(raw)!=3: return HELP
        alias=raw[2]
    elif action=="查":
        if len(parts)==3 and re.fullmatch(r"[0-9]{5,15}",parts[2]): target=parts[2]
        elif len(parts)!=2: return HELP
    elif action=="清除":
        if len(parts)!=2: return "只能清除自己的外号；主人管理请用 /外号 管理。"
    else: return HELP
    maximum=context.get("settings",{}).get("max_alias_chars",24)
    maximum=max(1,min(60,maximum)) if type(maximum) is int else 24
    if action=="设置" and (not alias or len(alias)>maximum or any(ord(ch)<32 or ord(ch)==127 for ch in alias)
            or any(char in alias for char in ("[", "]", "<", ">"))):
        return f"外号须为1-{maximum}字，不含控制字符、方括号或尖括号。"
    try: db=_open(context)
    except (ValueError,OSError,sqlite3.Error): return "外号数据目录不可用。"
    try:
        if action=="查":
            row=db.execute("SELECT alias FROM aliases WHERE scope=? AND user=?",(scope,target)).fetchone()
            return f"{target} 的会话外号：{row[0]}（仅显示名）" if row else f"{target} 在本会话没有设置外号。"
        if action=="设置":
            with db: db.execute("INSERT INTO aliases(scope,user,alias) VALUES(?,?,?) ON CONFLICT(scope,user) DO UPDATE SET alias=excluded.alias",(scope,target,alias))
            return f"已设置 {target} 的会话外号：{alias}。外号不影响权限。"
        with db: db.execute("DELETE FROM aliases WHERE scope=? AND user=?",(scope,target))
        return f"已清除 {target} 在本会话的外号。"
    except sqlite3.Error: return "外号数据库暂时繁忙。"
    finally: db.close()
