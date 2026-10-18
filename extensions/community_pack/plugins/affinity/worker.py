"""Explicit-command affinity, not a listener for all messages."""
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timedelta, timezone

SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")

def _now(): return datetime.now(SHANGHAI)

def _number(settings,key,default,low,high):
    value=settings.get(key,default)
    return max(low,min(high,value)) if type(value) is int else default

def _open(context):
    root=Path(context.get("data_dir",""))
    if not context.get("data_dir") or not root.is_absolute(): raise ValueError()
    root.mkdir(parents=True,exist_ok=True)
    path=root/"affinity.sqlite3"
    db=sqlite3.connect(path,timeout=10,isolation_level=None)
    db.execute("PRAGMA busy_timeout=10000")
    if os.name!="nt": path.chmod(0o600)
    db.execute("CREATE TABLE IF NOT EXISTS affinity(scope TEXT,user TEXT,points INTEGER NOT NULL DEFAULT 0,day TEXT NOT NULL DEFAULT '',gain INTEGER NOT NULL DEFAULT 0,last REAL NOT NULL DEFAULT 0,PRIMARY KEY(scope,user))")
    return db

def handle(text,context):
    parts=text.strip().split(maxsplit=1)
    if not parts or parts[0] not in ("/好感","/互动","/好感榜"):
        return "仅指令累积好感：/好感 /互动 [打招呼|夸夸|击掌] /好感榜。普通消息不会自动计分。"
    scope,user=str(context.get("session_id","")),str(context.get("user_id",""))
    if not scope or not user or len(scope)>200 or len(user)>100: return "缺少有效会话或用户标识。"
    settings=context.get("settings",{})
    maximum=_number(settings,"max_points",1000,10,1000000)
    owner_boost=settings.get("owner_max",False) is True and user in {str(x) for x in context.get("owner_ids",[])}
    command=parts[0]
    if command=="/好感" and owner_boost: return f"本会话好感：{maximum}/{maximum}（主人满级配置）。仅指令累积。"
    if command=="/互动" and len(parts)==2 and parts[1] not in ("打招呼","夸夸","击掌"):
        return "可用互动：/互动 打招呼、/互动 夸夸、/互动 击掌。"
    try: db=_open(context)
    except (ValueError,OSError,sqlite3.Error): return "好感数据目录不可用。"
    try:
        if command=="/好感榜":
            rows=db.execute("SELECT user,points FROM affinity WHERE scope=? ORDER BY points DESC,user LIMIT 10",(scope,)).fetchall()
            return "本会话好感榜（已累积记录）：\n"+("\n".join(f"{i}. {uid}：{point}" for i,(uid,point) in enumerate(rows,1)) or "暂无记录。")
        if command=="/好感":
            row=db.execute("SELECT points FROM affinity WHERE scope=? AND user=?",(scope,user)).fetchone()
            return f"本会话好感：{row[0] if row else 0}/{maximum}。仅 /互动 指令累积。"
        now=_now().astimezone(SHANGHAI)
        today,stamp=now.date().isoformat(),now.timestamp()
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT OR IGNORE INTO affinity(scope,user) VALUES(?,?)",(scope,user))
        points,day,gain,last=db.execute("SELECT points,day,gain,last FROM affinity WHERE scope=? AND user=?",(scope,user)).fetchone()
        gain=gain if day==today else 0
        cooldown=_number(settings,"cooldown_seconds",60,0,86400)
        daily=_number(settings,"daily_limit",30,1,10000)
        increment=_number(settings,"points_per_interaction",5,1,1000)
        if owner_boost:
            db.execute("UPDATE affinity SET points=?,day=? WHERE scope=? AND user=?",(maximum,today,scope,user))
            db.commit(); return f"击掌！好感 {maximum}/{maximum}（主人满级配置）。"
        if last and stamp-last<cooldown:
            db.rollback(); return f"互动冷却中，请 {max(1,int(cooldown-(stamp-last)+0.999))} 秒后再来。"
        if gain>=daily: db.rollback(); return "今天的互动积分已达上限（上海时间）。"
        if points>=maximum: db.rollback(); return f"好感已满：{points}/{maximum}。"
        actual=min(increment,daily-gain,maximum-points)
        db.execute("UPDATE affinity SET points=points+?,day=?,gain=?,last=? WHERE scope=? AND user=?",(actual,today,gain+actual,stamp,scope,user))
        db.commit()
        action=parts[1] if len(parts)>1 else "打招呼"
        return f"{action}收到啦，好感 +{actual}，当前 {points+actual}/{maximum}；今日获得 {gain+actual}/{daily}。"
    except sqlite3.Error:
        if db.in_transaction: db.rollback()
        return "好感数据库暂时繁忙。"
    finally: db.close()
