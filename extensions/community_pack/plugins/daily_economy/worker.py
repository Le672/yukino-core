"""Independent command-only entertainment economy. No real-world value."""
import json
import os
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timedelta, timezone

SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")

def _now():
    return datetime.now(SHANGHAI)

def _integer(settings, key, default, low, high):
    value = settings.get(key, default)
    return max(low, min(high, value)) if type(value) is int else default

def _open(context):
    root = Path(context.get("data_dir", ""))
    if not context.get("data_dir") or not root.is_absolute():
        raise ValueError("插件需要内核分配独立数据目录")
    root.mkdir(parents=True, exist_ok=True)
    path = root / "economy.sqlite3"
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.execute("PRAGMA busy_timeout=10000")
    if os.name != "nt": path.chmod(0o600)
    db.executescript("""
      CREATE TABLE IF NOT EXISTS accounts(scope TEXT,user TEXT,balance INTEGER NOT NULL DEFAULT 0,
        streak INTEGER NOT NULL DEFAULT 0,lastdate TEXT NOT NULL DEFAULT '',PRIMARY KEY(scope,user));
      CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY,scope TEXT,user TEXT,delta INTEGER,
        reason TEXT,stamp TEXT);
      CREATE INDEX IF NOT EXISTS ledger_lookup ON ledger(scope,user,id);
      CREATE TABLE IF NOT EXISTS bag(scope TEXT,user TEXT,item TEXT,count INTEGER NOT NULL,
        PRIMARY KEY(scope,user,item));
    """)
    return db

def _account(db, scope, user):
    db.execute("INSERT OR IGNORE INTO accounts(scope,user) VALUES(?,?)", (scope,user))
    return db.execute("SELECT balance,streak,lastdate FROM accounts WHERE scope=? AND user=?", (scope,user)).fetchone()

def _record(db, scope, user, delta, reason, stamp):
    db.execute("INSERT INTO ledger(scope,user,delta,reason,stamp) VALUES(?,?,?,?,?)", (scope,user,delta,reason,stamp))

def _shop(settings):
    raw = settings.get("shop_json", '[{"id":"flower","name":"小花","price":30},{"id":"badge","name":"纪念徽章","price":150}]')
    try:
        data = json.loads(raw)
        if not isinstance(data,list) or not 1 <= len(data) <= 20: raise ValueError()
        result = {}
        for item in data:
            if (not isinstance(item,dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,24}",str(item.get("id","")))
                or not isinstance(item.get("name"),str) or not 1 <= len(item["name"]) <= 30
                or any(ord(ch)<32 for ch in item["name"])
                or type(item.get("price")) is not int or not 1 <= item["price"] <= 1000000
                or item["id"] in result): raise ValueError()
            result[item["id"]] = (item["name"],item["price"])
        return result
    except (ValueError,TypeError):
        raise ValueError("商店配置无效，请管理员检查商品 JSON。") from None

HELP = "娱乐积分，无现金价值，按会话独立。\n/签到 /余额 /金币榜 /流水 /商店 /背包\n/转账 QQ号 正整数金额\n/购买 商品ID [数量1-99]"

def handle(text, context):
    parts = text.strip().split()
    if not parts: return HELP
    command = parts[0]
    if command == "/经济帮助": return HELP
    scope, user = str(context.get("session_id", "")), str(context.get("user_id", ""))
    if not scope or not user: return "缺少会话或用户标识。"
    if len(scope)>200 or len(user)>100: return "会话或用户标识无效。"
    settings = context.get("settings", {})
    cap = _integer(settings,"max_balance",1000000000,1000,1000000000)
    now = _now().astimezone(SHANGHAI)
    stamp, today = now.isoformat(timespec="seconds"), now.date().isoformat()
    if command in ("/商店", "/购买"):
        try: shop = _shop(settings)
        except ValueError as exc: return str(exc)
        if command == "/商店":
            return "娱乐积分商店（虚拟物品）：\n" + "\n".join(f"{key}｜{name}｜{price} 金币" for key,(name,price) in shop.items()) + "\n购买：/购买 商品ID [数量]"
    try:
        db = _open(context)
    except (ValueError,OSError,sqlite3.Error): return "积分数据目录不可用，请管理员检查。"
    try:
        if command == "/余额":
            row = db.execute("SELECT balance FROM accounts WHERE scope=? AND user=?", (scope,user)).fetchone()
            return f"本会话余额：{row[0] if row else 0} 金币（娱乐积分）。"
        if command == "/金币榜":
            rows = db.execute("SELECT user,balance FROM accounts WHERE scope=? ORDER BY balance DESC,user LIMIT 10", (scope,)).fetchall()
            return "本会话金币榜：\n" + ("\n".join(f"{i}. {uid}：{balance}" for i,(uid,balance) in enumerate(rows,1)) or "暂无记录。")
        if command == "/流水":
            rows = db.execute("SELECT delta,reason,stamp FROM ledger WHERE scope=? AND user=? ORDER BY id DESC LIMIT 10", (scope,user)).fetchall()
            return "最近10笔：\n" + ("\n".join(f"{stamp} {delta:+d} {reason}" for delta,reason,stamp in rows) or "暂无流水。")
        if command == "/背包":
            rows = db.execute("SELECT item,count FROM bag WHERE scope=? AND user=? ORDER BY item LIMIT 20", (scope,user)).fetchall()
            return "本会话背包：\n" + ("\n".join(f"{item} × {count}" for item,count in rows) or "空空如也。")
        if command not in ("/签到", "/转账", "/购买"): return HELP
        db.execute("BEGIN IMMEDIATE")
        balance, streak, previous = _account(db,scope,user)
        if command == "/签到":
            if previous == today:
                db.rollback()
                return f"今天已签到（上海时间），余额 {balance} 金币。"
            yesterday = (now.date()-timedelta(days=1)).isoformat()
            streak = streak+1 if previous == yesterday else 1
            reward = _integer(settings,"daily_reward",50,1,100000) + min(streak-1,6)*_integer(settings,"streak_bonus",10,0,10000)
            reward = max(0,min(reward,cap-balance))
            db.execute("UPDATE accounts SET balance=balance+?,streak=?,lastdate=? WHERE scope=? AND user=?", (reward,streak,today,scope,user))
            _record(db,scope,user,reward,f"签到连续{streak}天",stamp)
            db.commit()
            return f"签到成功！连续 {streak} 天，+{reward} 金币，余额 {balance+reward}。" + ("已达余额上限。" if balance+reward>=cap else "")
        if command == "/转账":
            if (len(parts)!=3 or not re.fullmatch(r"[0-9]{5,15}",parts[1])
                    or not re.fullmatch(r"[1-9][0-9]{0,8}",parts[2])):
                db.rollback(); return "用法：/转账 QQ号 正整数金额（不支持负数、小数、科学计数）。"
            target, amount = parts[1],int(parts[2])
            if target == user: db.rollback(); return "不能给自己转账。"
            if amount>_integer(settings,"max_transfer",1000000,1,100000000):
                db.rollback(); return "超过单笔转账上限。"
            other = _account(db,scope,target)[0]
            if balance<amount: db.rollback(); return "余额不足。"
            if other+amount>cap: db.rollback(); return "对方余额将超过上限。"
            db.execute("UPDATE accounts SET balance=balance-? WHERE scope=? AND user=?", (amount,scope,user))
            db.execute("UPDATE accounts SET balance=balance+? WHERE scope=? AND user=?", (amount,scope,target))
            _record(db,scope,user,-amount,"转给"+target,stamp)
            _record(db,scope,target,amount,"来自"+user,stamp)
            db.commit()
            return f"已向 {target} 转账 {amount} 金币，余额 {balance-amount}。"
        if len(parts) not in (2,3) or parts[1] not in shop or (len(parts)==3 and not re.fullmatch(r"[1-9][0-9]?",parts[2])):
            db.rollback(); return "用法：/购买 商品ID [数量1-99]，商品见 /商店。"
        item, quantity = parts[1], int(parts[2]) if len(parts)==3 else 1
        name, price = shop[item]
        cost = quantity*price
        count = db.execute("SELECT count FROM bag WHERE scope=? AND user=? AND item=?", (scope,user,item)).fetchone()
        if count and count[0]+quantity>9999: db.rollback(); return "该物品已达背包上限9999。"
        if balance<cost: db.rollback(); return "余额不足。"
        db.execute("UPDATE accounts SET balance=balance-? WHERE scope=? AND user=?", (cost,scope,user))
        db.execute("INSERT INTO bag(scope,user,item,count) VALUES(?,?,?,?) ON CONFLICT(scope,user,item) DO UPDATE SET count=count+excluded.count", (scope,user,item,quantity))
        _record(db,scope,user,-cost,f"购买{name}×{quantity}",stamp)
        db.commit()
        return f"购买成功：{name} × {quantity}，花费 {cost} 金币，余额 {balance-cost}。"
    except sqlite3.Error:
        if db.in_transaction: db.rollback()
        return "积分数据库暂时繁忙，请稍后再试。"
    finally:
        db.close()
