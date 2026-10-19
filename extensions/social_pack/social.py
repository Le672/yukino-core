"""Four original text-only social tools. No network or proactive messages."""
from datetime import datetime, timedelta, timezone
from common import add, execute, need, owned, words

TZ = timezone(timedelta(hours=8))
HELPS = {
    's_bottle': ('/漂流瓶', '/漂流瓶 投递 正文（本群公开、匿名展示）｜捞一个｜我的｜撤回 编号｜清空（主人，仅本群）。捞取不删除；禁止跨群。'),
    's_capsule': ('/时光信', '仅本人私聊：/时光信 存 2030-01-02 09:00 | 正文（北京时间、未来365天内）｜列表｜取 编号（到期才可取，取后删除）｜撤销 编号。不会自动发信。'),
    's_mood': ('/心情', '仅本人私聊：/心情 记 1-5 | 可选备注｜列表｜统计｜删除 编号｜清空。分数为你的自评，统计不作诊断。'),
    's_sleep': ('/睡眠', '仅本人私聊：/睡眠 记 2030-01-01 23:00 | 2030-01-02 07:00（北京时间）｜列表｜统计｜删除 编号｜清空。手动时段最多24小时，不代表实际睡着。'),
}


def date(value):
    try:
        dt = datetime.strptime(value.strip(), '%Y-%m-%d %H:%M')
    except (ValueError, TypeError):
        need(False, '时间格式为 YYYY-MM-DD HH:MM（北京时间）。')
    need(dt.strftime('%Y-%m-%d %H:%M') == value.strip(), '时间格式为 YYYY-MM-DD HH:MM（北京时间）。')
    return dt.replace(tzinfo=TZ).timestamp()


def stamp(value):
    return datetime.fromtimestamp(value, TZ).strftime('%Y-%m-%d %H:%M')


def rows(db, pid, sid, uid):
    return db.execute('SELECT * FROM entries WHERE kind=? AND scope=? AND uid=? ORDER BY id DESC LIMIT 20', (pid, sid, uid)).fetchall()


def dispatch(pid, cmd, tail, sid, uid, ctx, db, now, limits):
    if pid == 's_bottle':
        if cmd == '投递':
            rid = add(db, pid, sid, uid, words(tail, limits['text']), now, limits)
            return f'漂流瓶 #{rid} 已投递：正文在本群公开，捞取时匿名展示；这不是秘密信箱。可用 /漂流瓶 撤回 {rid}。'
        if cmd == '捞一个':
            need(not tail, '用法：/漂流瓶 捞一个')
            row = db.execute('SELECT id,body FROM entries WHERE kind=? AND scope=? ORDER BY random() LIMIT 1', (pid, sid)).fetchone()
            return f'本群匿名漂流瓶 #{row["id"]}：\n{row["body"]}' if row else '本群还没有漂流瓶。'
        if cmd == '我的':
            own = rows(db, pid, sid, uid)
            return '\n'.join(f'#{r["id"]} {r["body"][:80]}{"…" if len(r["body"]) > 80 else ""}' for r in own) if own else '你在本群还没有漂流瓶。'
        if cmd == '撤回':
            row = owned(db, pid, sid, uid, tail)
            db.execute('DELETE FROM entries WHERE id=?', (row['id'],))
            return f'漂流瓶 #{row["id"]} 已撤回。'
        if cmd == '清空':
            owners = ctx.get('owner_ids', [])
            need(isinstance(owners, list) and uid in owners, '仅机器人主人可清空本群漂流瓶。')
            count = db.execute('DELETE FROM entries WHERE kind=? AND scope=?', (pid, sid)).rowcount
            return f'已清空本群 {count} 个漂流瓶。'
    elif pid == 's_capsule':
        if cmd == '存':
            parts = tail.split('|', 1)
            need(len(parts) == 2, '用法：/时光信 存 YYYY-MM-DD HH:MM | 正文')
            due = date(parts[0])
            need(now < due <= now + 365 * 86400, '取信时间须晚于现在，且在未来365天内。')
            rid = add(db, pid, sid, uid, words(parts[1], limits['text']), now, limits, due=due)
            return f'时光信 #{rid} 已封存，{stamp(due)}（北京时间）后由你私聊取信；不会自动发送。'
        if cmd == '列表':
            own = rows(db, pid, sid, uid)
            return '\n'.join(f'#{r["id"]} {stamp(r["due"])}（{"已到期" if r["due"] <= now else "未到期"}）' for r in own) if own else '你还没有时光信。'
        if cmd in ('取', '撤销'):
            row = owned(db, pid, sid, uid, tail)
            if cmd == '取':
                need(row['due'] <= now, f'尚未到期，请在 {stamp(row["due"])}（北京时间）后取信。')
            db.execute('DELETE FROM entries WHERE id=?', (row['id'],))
            return f'时光信 #{row["id"]} 已取出（记录已删除）：\n{row["body"]}' if cmd == '取' else f'时光信 #{row["id"]} 已撤销。'
    elif pid == 's_mood':
        if cmd == '记':
            parts = tail.split('|', 1)
            score = parts[0].strip()
            need(score in ('1', '2', '3', '4', '5'), '请用 1—5 整数自评，例如 /心情 记 4 | 今天完成了一件小事。')
            body = words(parts[1], limits['text']) if len(parts) == 2 else ''
            rid = add(db, pid, sid, uid, body, now, limits, score=int(score))
            return f'心情 #{rid} 已记录，自评 {score}/5；仅保存你的自评，不作诊断。'
        if cmd == '列表':
            own = rows(db, pid, sid, uid)
            return '\n'.join(f'#{r["id"]} {stamp(r["created"])} {r["score"]}/5 {r["body"][:80]}{"…" if len(r["body"]) > 80 else ""}' for r in own) if own else '你还没有心情记录。'
        if cmd == '统计':
            row = db.execute('SELECT count(*),avg(score),min(score),max(score) FROM entries WHERE kind=? AND scope=? AND uid=?', (pid, sid, uid)).fetchone()
            return f'共 {row[0]} 次自评，平均 {row[1]:.2f}/5，最低 {row[2]}，最高 {row[3]}。仅描述你的记录，不作诊断。' if row[0] else '你还没有心情记录。'
    elif pid == 's_sleep':
        if cmd == '记':
            parts = tail.split('|')
            need(len(parts) == 2, '用法：/睡眠 记 YYYY-MM-DD HH:MM | YYYY-MM-DD HH:MM')
            start, finish = date(parts[0]), date(parts[1])
            need(0 < finish - start <= 86400, '结束须晚于开始，手动记录时段不能超过24小时。')
            need(finish <= now, '结束时间不能晚于现在。')
            rid = add(db, pid, sid, uid, '', now, limits, start=start, finish=finish)
            return f'睡眠时段 #{rid} 已记录：{(finish-start)/3600:.2f} 小时。这是手动时段，不代表实际睡着时长。'
        if cmd == '列表':
            own = rows(db, pid, sid, uid)
            return '\n'.join(f'#{r["id"]} {stamp(r["start"])} → {stamp(r["finish"])}，{(r["finish"]-r["start"])/3600:.2f} 小时' for r in own) if own else '你还没有睡眠时段记录。'
        if cmd == '统计':
            row = db.execute('SELECT count(*),avg(finish-start),sum(finish-start) FROM entries WHERE kind=? AND scope=? AND uid=?', (pid, sid, uid)).fetchone()
            return f'共 {row[0]} 个手动时段，平均 {row[1]/3600:.2f} 小时，合计 {row[2]/3600:.2f} 小时；时段可能重叠，合计不是实际睡眠时长，不作健康判断。' if row[0] else '你还没有睡眠时段记录。'
    if pid in ('s_mood', 's_sleep') and cmd == '删除':
        row = owned(db, pid, sid, uid, tail)
        db.execute('DELETE FROM entries WHERE id=?', (row['id'],))
        return f'记录 #{row["id"]} 已删除。'
    if pid in ('s_mood', 's_sleep') and cmd == '清空':
        count = db.execute('DELETE FROM entries WHERE kind=? AND scope=? AND uid=?', (pid, sid, uid)).rowcount
        return f'已清空你的 {count} 条记录。'
    return HELPS[pid][1]


def handle(pid, text, context):
    return execute(pid, text, context, dispatch, HELPS)
