"""Original offline life and collaboration utilities for the Yukino worker protocol.

Only the host-assigned data directory is used. No network, subprocess, scheduled
delivery, or implicit QQ action is performed by this module.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit


TZ = timezone(timedelta(hours=8))
SHARED = {'x_split_bill', 'x_signup', 'x_round_robin', 'x_queue', 'x_agenda'}


class UserError(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise UserError(message)


def text(value, maximum=200):
    value = str(value).replace('\r\n', '\n').replace('\r', '\n').strip()
    need(bool(value) and len(value) <= maximum and not re.search(r'[\x00-\x08\x0b-\x1f\x7f]', value), f'内容不能为空且不能超过{maximum}字。')
    return value


def integer(value, lo=1, hi=1000000):
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise UserError('请输入有效整数。')
    need(str(result) == str(value).strip() or str(value).strip() == '+' + str(result), '请输入整数，不接受小数。')
    need(lo <= result <= hi, f'整数范围为{lo}～{hi}。')
    return result


def number(value, lo=Decimal('0'), hi=Decimal('1000000000')):
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise UserError('请输入有效数字。')
    need(result.is_finite() and lo <= result <= hi and result.as_tuple().exponent >= -4, '数字超出范围，最多4位小数。')
    return result


def fmt(value):
    return format(Decimal(str(value)).normalize(), 'f')


def money(value):
    result = number(value, Decimal('0.01'), Decimal('10000000'))
    need(result.as_tuple().exponent >= -2, '金额最多2位小数。')
    return int(result * 100)


def cash(cents):
    return f'{cents / 100:.2f}'


def today():
    return datetime.now(TZ).date()


def fields(value, count=None, maximum=600):
    parts = [text(x, maximum) for x in value.split('|')]
    if count is not None:
        need(len(parts) == count, f'需要{count}段内容，用 | 分隔。')
    return parts


def split_action(arg):
    parts = arg.strip().split(maxsplit=1)
    return (parts[0], parts[1] if len(parts) > 1 else '') if parts else ('列表', '')


def with_id(rest):
    parts = rest.split(maxsplit=1)
    need(bool(parts), '请提供记录编号。')
    return integer(parts[0]), parts[1] if len(parts) > 1 else ''


class Store:
    def __init__(self, kind, ctx):
        self.kind = kind
        self.scope = text(ctx.get('session_id', ''), 100)
        self.user = text(ctx.get('user_id', ''), 40)
        settings = ctx.get('settings', {})
        configured_limit = settings.get('max_active_records', 200) if isinstance(settings, dict) else 200
        self.max_active_records = configured_limit if type(configured_limit) is int and 10 <= configured_limit <= 200 else 200
        owners = ctx.get('owner_ids', [])
        self.owner = self.user in [str(x) for x in owners] if isinstance(owners, (list, tuple)) else False
        self.shared = kind in SHARED or (kind == 'x_wiki' and self.scope.startswith('g:'))
        if kind in SHARED:
            need(self.scope.startswith('g:'), '此协作功能只用于当前群聊。')
        self.key = '*' if self.shared else self.user
        root = Path(str(ctx.get('data_dir', '')))
        need(bool(ctx.get('data_dir')) and root.is_absolute(), '需要内核分配的独立绝对数据目录。')
        root.mkdir(parents=True, exist_ok=True)
        path = root / 'life.sqlite3'
        self.db = sqlite3.connect(path, timeout=3)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        try:
            self.db.execute('CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, scope TEXT NOT NULL, user TEXT NOT NULL, creator TEXT NOT NULL, body TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, updated REAL NOT NULL)')
            self.db.execute('CREATE INDEX IF NOT EXISTS life_lookup ON records(kind,scope,user,active)')
            self.db.commit()
            self.db.execute('BEGIN IMMEDIATE')
        except Exception:
            self.db.close()
            raise

    def rows(self):
        rows = self.db.execute('SELECT id, creator, body FROM records WHERE kind=? AND scope=? AND user=? AND active=1 ORDER BY id', (self.kind, self.scope, self.key)).fetchall()
        return [dict(json.loads(body), _id=i, _creator=c) for i, c, body in rows]

    def get(self, identifier):
        row = self.db.execute('SELECT creator,body FROM records WHERE id=? AND kind=? AND scope=? AND user=? AND active=1', (identifier, self.kind, self.scope, self.key)).fetchone()
        need(row is not None, '记录不存在、已归档，或不属于当前用户／会话。')
        return dict(json.loads(row[1]), _id=identifier, _creator=row[0])

    def manage(self, row):
        need(row['_creator'] == self.user or self.owner, '仅创建者或机器人主人可以修改此共享项目。')

    def add(self, body):
        count = self.db.execute('SELECT COUNT(*),COALESCE(SUM(active),0) FROM records WHERE kind=? AND scope=? AND user=?', (self.kind, self.scope, self.key)).fetchone()
        need(count[0] < 2000 and count[1] < self.max_active_records, f'已达记录上限：活跃{self.max_active_records}条／累计2000条。请保留或导出已有数据。')
        stamp = time.time()
        result = self.db.execute('INSERT INTO records(kind,scope,user,creator,body,created,updated) VALUES(?,?,?,?,?,?,?)', (self.kind, self.scope, self.key, self.user, json.dumps(body, ensure_ascii=False), stamp, stamp))
        return result.lastrowid

    def save(self, row):
        body = {k: v for k, v in row.items() if not k.startswith('_')}
        need(len(json.dumps(body, ensure_ascii=False)) <= 100000, '记录内容超过存储上限。')
        self.db.execute('UPDATE records SET body=?,updated=? WHERE id=?', (json.dumps(body, ensure_ascii=False), time.time(), row['_id']))

    def archive(self, row):
        if self.shared:
            self.manage(row)
        self.db.execute('UPDATE records SET active=0,updated=? WHERE id=?', (time.time(), row['_id']))


def listing(rows, render=None):
    if not rows:
        return '暂无记录。'
    render = render or (lambda r: r.get('title', ''))
    result = '\n'.join(f"#{r['_id']} {render(r)}" for r in rows[:50])
    if len(rows) > 50:
        result += f'\n共{len(rows)}条，仅显示前50条。'
    return result


def archive_action(s, action, rest):
    if action in ('归档', '删除', '撤销'):
        identifier, tail = with_id(rest)
        need(not tail, '归档只需要编号。')
        row = s.get(identifier)
        s.archive(row)
        return f'已归档 #{identifier}，历史数据保留。'
    return None


def habits(s, action, rest):
    if action == '添加':
        i = s.add({'title': text(rest, 80), 'days': []})
        return f'习惯已添加 #{i}。'
    if action in ('打卡', '取消打卡'):
        i, tail = with_id(rest); need(not tail, '只需要习惯编号。'); row = s.get(i); day = today().isoformat()
        if action == '打卡':
            need(day not in row['days'], '今天已经打卡。'); need(len(row['days']) < 3660, '打卡累计已达3660天上限。'); row['days'].append(day)
        else:
            need(day in row['days'], '今天尚未打卡。'); row['days'].remove(day)
        s.save(row); return f"{action}成功：{row['title']}，累计{len(row['days'])}天。"
    if action in ('列表', '今日', '统计'):
        return listing(s.rows(), lambda r: f"{r['title']} · 累计{len(r['days'])}天 · 今日{'已打卡' if today().isoformat() in r['days'] else '未打卡'}")
    return archive_action(s, action, rest)


def ledger(s, action, rest):
    if action == '添加':
        p = rest.split(maxsplit=3); need(len(p) >= 3 and p[0] in ('收入', '支出'), '用法：添加 收入|支出 金额 分类 [备注]')
        i = s.add({'type': p[0], 'cents': money(p[1]), 'category': text(p[2], 40), 'note': text(p[3], 200) if len(p) == 4 else '', 'day': today().isoformat()})
        return f'流水已记录 #{i}：{p[0]} {cash(money(p[1]))}。'
    rows = s.rows()
    if action == '统计':
        month = rest or today().isoformat()[:7]; need(bool(re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month)), '月份格式为 YYYY-MM。')
        chosen = [r for r in rows if r['day'].startswith(month)]; income = sum(r['cents'] for r in chosen if r['type'] == '收入'); spend = sum(r['cents'] for r in chosen if r['type'] == '支出')
        return f'{month} · {len(chosen)}笔\n收入 {cash(income)}\n支出 {cash(spend)}\n结余 {cash(income-spend)}'
    if action == '列表':
        return listing(list(reversed(rows)), lambda r: f"{r['day']} {r['type']} {cash(r['cents'])} {r['category']} {r['note']}")
    return archive_action(s, action, rest)


def reading(s, action, rest):
    if action == '添加':
        p = fields(rest, 2, 100); total = integer(p[1], 1, 1000000); i = s.add({'title': p[0], 'total': total, 'current': 0}); return f'阅读记录已创建 #{i}，总计{total}页。'
    if action == '进度':
        i, tail = with_id(rest); row = s.get(i); row['current'] = integer(tail, 0, row['total']); s.save(row); return f"进度已更新：{row['current']}/{row['total']}页（{row['current']/row['total']:.1%}）。"
    if action == '列表':
        return listing(s.rows(), lambda r: f"{r['title']} {r['current']}/{r['total']}页（{r['current']/r['total']:.1%}）")
    return archive_action(s, action, rest)


def watchlist(s, action, rest):
    if action == '添加':
        i = s.add({'title': text(rest, 100), 'watched': False, 'score': None, 'note': ''}); return f'观影条目已添加 #{i}。'
    if action in ('看完', '评分'):
        i, tail = with_id(rest); row = s.get(i)
        if action == '看完':
            need(not tail, '看完只需要编号。'); row['watched'] = True
        else:
            p = tail.split(maxsplit=1); need(bool(p), '评分需要0～10的分数。'); row['score'] = fmt(number(p[0], Decimal('0'), Decimal('10'))); row['note'] = text(p[1], 200) if len(p) > 1 else ''
        s.save(row); return f"已更新：{row['title']}。"
    if action == '列表':
        return listing(s.rows(), lambda r: f"{r['title']} · {'已看' if r['watched'] else '待看'} · 评分{r['score'] or '未评'} {r['note']}")
    return archive_action(s, action, rest)


def spaced_cards(s, action, rest):
    if action == '添加':
        q, a = fields(rest, 2, 500); i = s.add({'title': q, 'answer': a, 'level': 0, 'due': today().isoformat(), 'reviews': 0}); return f'复习卡已创建 #{i}，今天到期。'
    if action in ('回答', '评估'):
        i, tail = with_id(rest); row = s.get(i)
        if action == '回答':
            need(not tail, '回答只需要卡片编号。'); return f"#{i} {row['title']}\n答案：{row['answer']}\n请用 /复习卡 评估 {i} 0～5 自评。"
        grade = integer(tail, 0, 5); row['level'] = min(5, row['level'] + 1) if grade >= 3 else 0
        interval = (1, 3, 7, 14, 30, 60)[row['level']]; row['due'] = (today() + timedelta(days=interval)).isoformat(); row['reviews'] += 1; s.save(row)
        return f"已记录自评，{interval}天后复习：{row['due']}。"
    if action in ('列表', '到期'):
        rows = s.rows(); rows = [r for r in rows if r['due'] <= today().isoformat()] if action == '到期' else rows
        return listing(rows, lambda r: f"{r['title']} · 到期{r['due']} · 复习{r['reviews']}次")
    return archive_action(s, action, rest)


def pomodoro(s, action, rest):
    rows = s.rows(); running = next((r for r in rows if r.get('running')), None)
    if action == '开始':
        need(running is None, '已有进行中的番茄钟，先完成或取消。'); p = rest.split(maxsplit=1); need(bool(p), '用法：开始 分钟 [任务]'); minutes = integer(p[0], 1, 180)
        i = s.add({'title': text(p[1], 100) if len(p) > 1 else '专注', 'minutes': minutes, 'start': time.time(), 'running': True, 'elapsed': 0}); return f'番茄钟已开始 #{i}，{minutes}分钟；不会主动推送。'
    if action in ('状态', '列表'):
        if not running: return '当前没有进行中的番茄钟。'
        elapsed = max(0, int(time.time() - running['start'])); remaining = max(0, running['minutes'] * 60 - elapsed)
        return f"#{running['_id']} {running['title']} · 已过{elapsed}秒 · 剩余{remaining}秒{'，已到时' if remaining == 0 else ''}。不会主动推送。"
    if action in ('完成', '取消'):
        need(running is not None, '当前没有进行中的番茄钟。'); running['elapsed'] = max(0, int(time.time() - running['start'])); running['running'] = False; running['cancelled'] = action == '取消'; s.save(running)
        return f"番茄钟已{action}，实际{running['elapsed']}秒{'（提前结束）' if running['elapsed'] < running['minutes']*60 and action == '完成' else ''}。"
    if action == '历史':
        return listing([r for r in rows if not r.get('running')], lambda r: f"{r['title']} {r['elapsed']}秒 · {'取消' if r.get('cancelled') else '完成'}")
    if action in ('归档', '删除'):
        i, _ = with_id(rest); need(not s.get(i).get('running'), '进行中的番茄钟请先完成或取消。')
        return archive_action(s, action, rest)
    return None


def goals(s, action, rest):
    if action == '添加':
        p = fields(rest, 2, 100); target = number(p[1], Decimal('0.0001')); i = s.add({'title': p[0], 'target': fmt(target), 'current': '0'}); return f'目标已添加 #{i}，目标值{fmt(target)}。'
    if action in ('进度', '增加'):
        i, tail = with_id(rest); row = s.get(i); value = number(tail)
        if action == '增加': value += Decimal(row['current'])
        need(value <= Decimal('1000000000'), '累计值超出上限。'); row['current'] = fmt(value); s.save(row)
        return f"进度已更新：{row['current']}/{row['target']}（{value/Decimal(row['target']):.1%}）。"
    if action == '列表': return listing(s.rows(), lambda r: f"{r['title']} {r['current']}/{r['target']}")
    return archive_action(s, action, rest)


def inventory(s, action, rest):
    if action == '添加':
        p = rest.split(maxsplit=1); need(bool(p), '用法：添加 物品 [单位]'); i = s.add({'title': text(p[0], 80), 'unit': text(p[1], 10) if len(p) > 1 else '件', 'quantity': 0, 'moves': []}); return f'库存条目已添加 #{i}，初始数量0。'
    if action in ('变动', '流水'):
        i, tail = with_id(rest); row = s.get(i)
        if action == '流水': return '\n'.join(f"{m['day']} {m['delta']:+d} → {m['quantity']} {m['note']}" for m in row['moves'][-30:]) or '暂无数量流水。'
        p = tail.split(maxsplit=1); need(bool(p), '变动需要带正负号的整数。'); delta = integer(p[0], -1000000, 1000000); need(delta != 0, '变动不能为0。'); new = row['quantity'] + delta
        need(0 <= new <= 1000000000, '库存不能为负数或超过10亿。'); need(len(row['moves']) < 1000, '本条数量流水已达1000条上限，旧流水保留。')
        row['quantity'] = new; row['moves'].append({'day': datetime.now(TZ).isoformat(timespec='seconds'), 'delta': delta, 'quantity': new, 'note': text(p[1], 100) if len(p) > 1 else ''}); s.save(row); return f"库存已更新：{row['title']} {new}{row['unit']}。"
    if action == '列表': return listing(s.rows(), lambda r: f"{r['title']} {r['quantity']}{r['unit']}")
    return archive_action(s, action, rest)


def split_bill(s, action, rest):
    if action == '创建':
        p = fields(rest, maximum=60); need(2 <= len(p) <= 31 and len(set(p[1:])) == len(p)-1, '格式：创建 标题|成员A|成员B；成员1～30个且不重复。')
        i = s.add({'title': p[0], 'members': p[1:], 'expenses': []}); return f'AA账本已创建 #{i}；仅记录和计算，不转账。'
    if action in ('成员', '支出', '结算', '查看'):
        i, tail = with_id(rest); row = s.get(i)
        if action == '成员':
            s.manage(row); name = text(tail, 60); need(name not in row['members'] and len(row['members']) < 30, '成员已存在或已达30人。'); row['members'].append(name); s.save(row); return '成员已添加。'
        if action == '支出':
            s.manage(row); p = fields(tail, maximum=100); need(len(p) in (2, 3), '格式：支出 编号 成员|金额|可选备注'); need(p[0] in row['members'], '成员不在当前账本。'); need(len(row['expenses']) < 1000, '支出已达1000笔上限。')
            row['expenses'].append({'member': p[0], 'cents': money(p[1]), 'note': p[2] if len(p) == 3 else ''}); s.save(row); return f'支出已记录：{p[0]} {cash(money(p[1]))}。'
        total = sum(e['cents'] for e in row['expenses']); n = len(row['members']); base, remainder = divmod(total, n); paid = {m: sum(e['cents'] for e in row['expenses'] if e['member'] == m) for m in row['members']}
        balances = {m: paid[m] - base - (j < remainder) for j, m in enumerate(row['members'])}
        result = [f"{row['title']} · 总支出{cash(total)} · {n}人；余分按成员顺序分配。"]
        result.extend(f"{m} 已付{cash(paid[m])} · {'应收' if balances[m] >= 0 else '应付'}{cash(abs(balances[m]))}" for m in row['members'])
        if action == '结算':
            debt = [[m, -b] for m, b in balances.items() if b < 0]; credit = [[m, b] for m, b in balances.items() if b > 0]
            for debtor in debt:
                for creditor in credit:
                    amount = min(debtor[1], creditor[1])
                    if amount: result.append(f'建议 {debtor[0]} → {creditor[0]}：{cash(amount)}'); debtor[1] -= amount; creditor[1] -= amount
            result.append('以上仅计算建议，不会转账。')
        return '\n'.join(result)
    if action == '列表': return listing(s.rows())
    return archive_action(s, action, rest)


def signup(s, action, rest):
    if action == '创建':
        p = fields(rest, 2, 100); cap = integer(p[1], 1, 100); i = s.add({'title': p[0], 'capacity': cap, 'people': [], 'closed': False}); return f'报名活动已创建 #{i}，名额{cap}人。'
    if action in ('加入', '报名', '退出', '名单', '关闭'):
        i, tail = with_id(rest); row = s.get(i)
        if action == '名单': return f"{row['title']} · {'已关闭' if row['closed'] else '开放'} · {len(row['people'])}/{row['capacity']}\n" + ('\n'.join(f"{j+1}. {p['name']}" for j, p in enumerate(row['people'])) or '暂无报名。')
        if action == '关闭': s.manage(row); row['closed'] = True
        else:
            need(not row['closed'], '活动已关闭。'); existing = next((p for p in row['people'] if p['user'] == s.user), None)
            if action == '退出': need(existing is not None, '你尚未报名。'); row['people'].remove(existing)
            else: need(existing is None, '你已经报名。'); need(len(row['people']) < row['capacity'], '名额已满。'); row['people'].append({'user': s.user, 'name': text(tail, 40) if tail else s.user})
        s.save(row); return f'{action}成功：{row["title"]}。'
    if action == '列表': return listing(s.rows(), lambda r: f"{r['title']} {len(r['people'])}/{r['capacity']} {'已关闭' if r['closed'] else '开放'}")
    return archive_action(s, action, rest)


def round_robin(s, action, rest):
    if action == '创建':
        p = fields(rest, maximum=60); need(2 <= len(p) <= 51 and len(set(p[1:])) == len(p)-1, '格式：创建 标题|A|B，名单1～50人且不重复。'); i = s.add({'title': p[0], 'people': p[1:], 'next': 0, 'turns': 0}); return f'轮值名单已创建 #{i}。'
    if action in ('下一个', '名单', '设置'):
        i, tail = with_id(rest); row = s.get(i)
        if action == '名单': return f"{row['title']} · 已轮值{row['turns']}次 · 下一位{row['people'][row['next']]}\n" + '、'.join(row['people'])
        s.manage(row)
        if action == '设置':
            people = fields(tail, maximum=60); need(len(people) <= 50 and len(set(people)) == len(people), '名单最多50人且不能重复。'); row['people'] = people; row['next'] = 0; s.save(row); return '名单已设置，下次从第一位开始。'
        name = row['people'][row['next']]; row['next'] = (row['next'] + 1) % len(row['people']); row['turns'] += 1; s.save(row); return f"本次轮值：{name}。下一位：{row['people'][row['next']]}。"
    if action == '列表': return listing(s.rows())
    return archive_action(s, action, rest)


def queue(s, action, rest):
    if action in ('创建', '开始'):
        i = s.add({'title': text(rest, 100), 'people': [], 'served': 0, 'closed': False}); return f'队列已创建 #{i}。'
    if action in ('入列', '退出', '状态', '推进', '关闭'):
        i, tail = with_id(rest); row = s.get(i)
        if action == '状态': return f"{row['title']} · {'已关闭' if row['closed'] else '开放'} · 已推进{row['served']}人\n" + ('\n'.join(f"{j+1}. {p['name']}" for j, p in enumerate(row['people'])) or '当前无人排队。')
        if action == '关闭': s.manage(row); row['closed'] = True; result = '队列已关闭。'
        else:
            need(not row['closed'], '队列已关闭。')
            if action == '推进': s.manage(row); need(bool(row['people']), '当前无人排队。'); person = row['people'].pop(0); row['served'] += 1; result = f"已推进：{person['name']}。"
            elif action == '退出':
                own = next((p for p in row['people'] if p['user'] == s.user), None); need(own is not None, '你尚未入列。'); row['people'].remove(own); result = '已退出队列。'
            else:
                need(not any(p['user'] == s.user for p in row['people']), '你已经入列。'); need(len(row['people']) < 100, '队列已满100人。'); row['people'].append({'user': s.user, 'name': text(tail, 40) if tail else s.user}); result = f"入列成功，排在第{len(row['people'])}位。"
        s.save(row); return result
    if action == '列表': return listing(s.rows())
    return archive_action(s, action, rest)


def wishlist(s, action, rest):
    if action == '添加':
        p = fields(rest, 2, 100); priority = integer(p[1], 1, 5); i = s.add({'title': p[0], 'priority': priority, 'done': False}); return f'愿望已添加 #{i}，优先级{priority}（1最高）。'
    if action in ('优先', '完成'):
        i, tail = with_id(rest); row = s.get(i)
        if action == '优先': row['priority'] = integer(tail, 1, 5)
        else: need(not tail, '完成只需要编号。'); row['done'] = True
        s.save(row); return f"愿望已更新：{row['title']}。"
    if action == '列表': return listing(sorted(s.rows(), key=lambda r: (r['done'], r['priority'], r['_id'])), lambda r: f"P{r['priority']} {'已完成' if r['done'] else '未完成'} {r['title']}")
    return archive_action(s, action, rest)


def recipes(s, action, rest):
    if action in ('添加', '修改'):
        row = None
        if action == '修改': i, rest = with_id(rest); row = s.get(i)
        p = fields(rest, 3, 600); body = {'title': text(p[0], 100), 'ingredients': p[1], 'steps': p[2]}
        if row: row.update(body); s.save(row); return f'食谱已修改 #{i}。'
        i = s.add(body); return f'食谱已收录 #{i}（用户自录）。'
    if action == '查看':
        i, tail = with_id(rest); need(not tail, '查看只需要编号。'); row = s.get(i); return f"{row['title']}\n食材：{row['ingredients']}\n步骤：{row['steps']}"
    if action in ('列表', '搜索'):
        rows = s.rows()
        if action == '搜索': key = text(rest, 80).casefold(); rows = [r for r in rows if key in (r['title'] + r['ingredients']).casefold()]
        return listing(rows)
    return archive_action(s, action, rest)


def bookmark_body(rest):
    p = fields(rest, 3, 1000); title = text(p[0], 100); url = p[1]
    try: parsed = urlsplit(url); port = parsed.port
    except ValueError: raise UserError('URL格式无效。')
    need(parsed.scheme in ('http', 'https') and bool(parsed.hostname) and not parsed.username and not parsed.password and not any(c.isspace() for c in url), '只保存有效 http/https URL，不接受用户名密码。')
    return {'title': title, 'url': url, 'tags': text(p[2], 100)}


def bookmarks(s, action, rest):
    if action in ('添加', '修改'):
        row = None
        if action == '修改': i, rest = with_id(rest); row = s.get(i)
        body = bookmark_body(rest)
        if row: row.update(body); s.save(row); return f'书签已修改 #{i}。'
        i = s.add(body); return f'书签已添加 #{i}；只存储，不请求该网站。'
    if action == '查看':
        i, tail = with_id(rest); need(not tail, '查看只需要编号。'); row = s.get(i); return f"{row['title']} [{row['tags']}]\n{row['url']}"
    if action in ('列表', '搜索'):
        rows = s.rows()
        if action == '搜索': key = text(rest, 100).casefold(); rows = [r for r in rows if key in (r['title'] + r['tags'] + r['url']).casefold()]
        return listing(rows, lambda r: f"{r['title']} [{r['tags']}] {r['url']}")
    return archive_action(s, action, rest)


def next_anniversary(month_day):
    need(bool(re.fullmatch(r'\d{2}-\d{2}', month_day)), '日期格式为 MM-DD。')
    try: date(2000, int(month_day[:2]), int(month_day[3:]))
    except ValueError: raise UserError('日期无效。')
    current = today()
    for year in range(current.year, current.year + 9):
        try: candidate = date(year, int(month_day[:2]), int(month_day[3:]))
        except ValueError: continue
        if candidate >= current: return candidate
    raise UserError('无法计算下一次日期。')


def anniversary(s, action, rest):
    if action in ('添加', '修改'):
        row = None
        if action == '修改': i, rest = with_id(rest); row = s.get(i)
        p = fields(rest, 2, 100); next_anniversary(p[1]); body = {'title': p[0], 'date': p[1]}
        if row: row.update(body); s.save(row); return f'纪念日已修改 #{i}。'
        i = s.add(body); return f'纪念日已添加 #{i}。'
    if action in ('列表', '查看'):
        rows = sorted(s.rows(), key=lambda r: next_anniversary(r['date']))
        return listing(rows, lambda r: f"{r['title']} {r['date']} → {next_anniversary(r['date']).isoformat()}，还有{(next_anniversary(r['date'])-today()).days}天")
    return archive_action(s, action, rest)


PACKING_TEMPLATES = {'短途': ('证件', '手机与充电器', '换洗衣物', '常用药品'), '出差': ('证件', '电脑与电源', '工作资料', '换洗衣物', '洗漱用品')}


def packing(s, action, rest):
    if action in ('创建', '模板'):
        if action == '模板':
            need(rest in PACKING_TEMPLATES, '模板可选：短途、出差。'); p = [rest + '行李', *PACKING_TEMPLATES[rest]]
        else: p = fields(rest, maximum=100); need(2 <= len(p) <= 101, '创建 标题|物品A|物品B，最多100项。')
        i = s.add({'title': p[0], 'items': [{'name': x, 'done': False} for x in p[1:]]}); return f'行李清单已创建 #{i}，{len(p)-1}项。'
    if action in ('勾选', '取消', '添加', '状态'):
        i, tail = with_id(rest); row = s.get(i)
        if action == '状态': return row['title'] + '\n' + '\n'.join(f"{j+1}. {'☑' if item['done'] else '☐'} {item['name']}" for j, item in enumerate(row['items']))
        if action == '添加': need(len(row['items']) < 100, '清单已达100项。'); row['items'].append({'name': text(tail, 100), 'done': False})
        else: index = integer(tail, 1, len(row['items'])) - 1; row['items'][index]['done'] = action == '勾选'
        s.save(row); return f'清单已更新，完成{sum(x["done"] for x in row["items"])}/{len(row["items"])}项。'
    if action == '列表': return listing(s.rows(), lambda r: f"{r['title']} {sum(x['done'] for x in r['items'])}/{len(r['items'])}项")
    return archive_action(s, action, rest)


def agenda(s, action, rest):
    if action == '创建':
        i = s.add({'title': text(rest, 100), 'topics': [], 'closed': False, 'serial': 0}); return f'议程已创建 #{i}。'
    if action in ('添加', '排序', '状态', '推进', '关闭'):
        i, tail = with_id(rest); row = s.get(i)
        if action == '状态': return f"{row['title']} · {'已关闭' if row['closed'] else '进行中'}\n" + ('\n'.join(f"{topic['id']}. {'☑' if topic['done'] else '☐'} {topic['title']}" for topic in row['topics']) or '暂无议题。')
        need(not row['closed'], '议程已关闭。')
        if action == '添加':
            need(len(row['topics']) < 100, '议程已达100项。'); row['serial'] += 1; row['topics'].append({'id': row['serial'], 'title': text(tail, 200), 'user': s.user, 'done': False}); result = f"议题已添加 #{row['serial']}。"
        else:
            s.manage(row)
            if action == '关闭': row['closed'] = True; result = '议程已关闭。'
            elif action == '排序':
                ids = [integer(x) for x in tail.replace('，', ',').split(',')]; existing = {x['id']: x for x in row['topics']}; need(len(ids) == len(existing) and set(ids) == set(existing), '排序必须列出全部议题编号且每项一次。'); row['topics'] = [existing[x] for x in ids]; result = '议程顺序已更新。'
            else:
                topic = next((x for x in row['topics'] if not x['done']), None); need(topic is not None, '没有未完成议题。'); topic['done'] = True; result = f"已完成议题：{topic['title']}。"
        s.save(row); return result
    if action == '列表': return listing(s.rows())
    return archive_action(s, action, rest)


def time_budget(s, action, rest):
    rows = s.rows(); running = next((r for r in rows if r.get('running')), None)
    if action == '开始':
        need(running is None, '已有计时中的项目，先结束。'); i = s.add({'title': text(rest, 100), 'start': time.time(), 'running': True, 'elapsed': 0}); return f'工时计时已开始 #{i}。'
    if action == '结束':
        need(running is not None, '当前没有计时中的项目。'); running['elapsed'] = max(0, int(time.time() - running['start'])); running['running'] = False; s.save(running); return f"已结束 {running['title']}，用时{running['elapsed']}秒。"
    if action in ('状态', '列表'):
        return f"正在计时 {running['title']}：{max(0,int(time.time()-running['start']))}秒。" if running else '当前没有计时中的项目。'
    if action == '统计':
        totals = {}
        for r in rows:
            if not r.get('running'): totals[r['title']] = totals.get(r['title'], 0) + r['elapsed']
        return '\n'.join(f'{title}：{seconds}秒（{seconds/3600:.2f}小时）' for title, seconds in sorted(totals.items())) or '暂无已结束工时。'
    if action == '历史': return listing([r for r in rows if not r.get('running')], lambda r: f"{r['title']} {r['elapsed']}秒")
    if action in ('归档', '删除'):
        i, _ = with_id(rest); need(not s.get(i).get('running'), '计时中的项目请先结束。')
        return archive_action(s, action, rest)
    return None


def criteria(rest):
    parts = fields(rest, maximum=100); need(1 <= len(parts) <= 15, '需要1～15个指标。'); result = []
    for p in parts:
        pair = p.rsplit(':', 1); need(len(pair) == 2, '指标格式：名称:正权重'); result.append({'name': text(pair[0], 50), 'weight': fmt(number(pair[1], Decimal('0.0001'), Decimal('1000000')))})
    need(len({x['name'] for x in result}) == len(result), '指标名称不能重复。'); return result


def decision_matrix(s, action, rest):
    if action == '创建':
        parts = rest.split('|', 1); need(len(parts) == 2, '创建 标题|指标:权重|指标:权重'); cs = criteria(parts[1]); i = s.add({'title': text(parts[0], 100), 'criteria': cs, 'options': []}); return f'决策表已创建 #{i}，{len(cs)}项指标。'
    if action in ('评分', '结果', '移除', '权重'):
        i, tail = with_id(rest); row = s.get(i)
        if action == '评分':
            p = fields(tail, maximum=100); need(len(p) == len(row['criteria']) + 1, '按指标顺序提供每项0～10分：选项|分数|分数。'); scores = [fmt(number(x, Decimal('0'), Decimal('10'))) for x in p[1:]]; name = text(p[0], 100); option = next((o for o in row['options'] if o['name'] == name), None)
            if option: option['scores'] = scores
            else: need(len(row['options']) < 30, '选项已达30个。'); row['options'].append({'name': name, 'scores': scores})
            s.save(row); return f'评分已保存：{name}。'
        if action == '移除':
            name = text(tail, 100); found = [o for o in row['options'] if o['name'] == name]; need(bool(found), '选项不存在。'); row.setdefault('archived_options', []).extend(found); need(len(row['archived_options']) <= 100, '归档选项已达100项上限。'); row['options'] = [o for o in row['options'] if o['name'] != name]; s.save(row); return '选项已归档，原评分保留。'
        if action == '权重':
            cs = criteria(tail); need([x['name'] for x in cs] == [x['name'] for x in row['criteria']], '修改权重时指标名称与顺序必须保持一致。'); row['criteria'] = cs; s.save(row); return '权重已更新。'
        weights = [Decimal(c['weight']) for c in row['criteria']]; result = []
        for o in row['options']: result.append((sum(Decimal(v)*w for v, w in zip(o['scores'], weights))/sum(weights), o['name']))
        result.sort(key=lambda p: (-p[0], p[1])); return row['title'] + '\n指标：' + '、'.join(f"{c['name']}×{c['weight']}" for c in row['criteria']) + '\n' + ('\n'.join(f'{name}：{value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)} / 10' for value, name in result) or '暂无评分。') + '\n结果仅反映输入权重和分数。'
    if action == '列表': return listing(s.rows())
    return archive_action(s, action, rest)


def wiki_content(value):
    value = text(value.replace('\\n', '\n'), 1500)
    need(not any(re.match(r'^#{7,}\s', line) for line in value.splitlines()), '标题最多6级，使用 #、## 等 Markdown 标题。')
    return value


def wiki(s, action, rest):
    if action == '创建':
        p = rest.split('|', 1); need(len(p) == 2, '创建 标题|正文，可包含 # 一级、## 二级标题。'); body = wiki_content(p[1]); i = s.add({'title': text(p[0], 100), 'versions': [{'content': body, 'user': s.user, 'stamp': datetime.now(TZ).isoformat(timespec='seconds')}], 'current': 1}); return f'知识页已创建 #{i}，版本1。'
    if action in ('更新', '查看', '历史', '恢复', '版本'):
        i, tail = with_id(rest); row = s.get(i)
        if action == '查看': return f"{row['title']} · 版本{row['current']}\n{row['versions'][row['current']-1]['content']}"
        if action == '历史': return f"{row['title']} · 当前版本{row['current']}\n" + '\n'.join(f"v{j+1} {v['stamp']} {v['content'][:60].replace(chr(10),' / ')}" for j, v in enumerate(row['versions']))
        if action == '版本':
            version = integer(tail, 1, len(row['versions'])); return f"{row['title']} · 历史版本{version}\n{row['versions'][version-1]['content']}"
        if s.shared: s.manage(row)
        need(len(row['versions']) < 50, '历史版本已达50个上限，旧版本保留。')
        content = wiki_content(tail) if action == '更新' else row['versions'][integer(tail, 1, len(row['versions']))-1]['content']
        row['versions'].append({'content': content, 'user': s.user, 'stamp': datetime.now(TZ).isoformat(timespec='seconds')}); row['current'] = len(row['versions']); s.save(row); return f"知识页已{action}，新增版本{row['current']}，旧版本保留。"
    if action == '列表': return listing(s.rows(), lambda r: f"{r['title']} v{r['current']}")
    return archive_action(s, action, rest)


FUNCTIONS = { 'x_habits': habits, 'x_ledger': ledger, 'x_reading': reading, 'x_watchlist': watchlist, 'x_spaced_cards': spaced_cards, 'x_pomodoro': pomodoro, 'x_goals': goals, 'x_inventory': inventory, 'x_split_bill': split_bill, 'x_signup': signup, 'x_round_robin': round_robin, 'x_queue': queue, 'x_wishlist': wishlist, 'x_recipes': recipes, 'x_bookmarks': bookmarks, 'x_anniversary': anniversary, 'x_packing': packing, 'x_agenda': agenda, 'x_time_budget': time_budget, 'x_decision_matrix': decision_matrix, 'x_wiki': wiki }


def handle(kind, command, context):
    if kind not in FUNCTIONS: return None
    if not isinstance(command, str) or len(command) > 2200: return '命令过长，最多2200字。'
    parts = command.strip().split(maxsplit=1); argument = parts[1] if len(parts) > 1 else ''
    if argument in ('帮助', 'help', '?'):
        return HELP.get(kind, '请查看本插件README。')
    store = None
    try:
        action, rest = split_action(argument)
        store = Store(kind, context)
        result = FUNCTIONS[kind](store, action, rest)
        if result is None:
            store.db.rollback(); return '未知子命令。\n' + HELP.get(kind, '')
        store.db.commit()
        return result[:15000]
    except UserError as error:
        if store: store.db.rollback()
        return '操作未完成：' + str(error)
    except (sqlite3.Error, OSError, json.JSONDecodeError):
        if store: store.db.rollback()
        return '存储暂不可用，操作未完成。请稍后重试或由管理员检查插件数据目录。'
    finally:
        if store: store.db.close()


HELP = {
 'x_habits': '/习惯 添加 名称；打卡 编号；取消打卡 编号；列表；统计；归档 编号',
 'x_ledger': '/记账 添加 支出 12.50 餐饮 午饭；列表；统计 YYYY-MM；撤销 编号。只记录，不转账。',
 'x_reading': '/阅读进度 添加 书名|总页数；进度 编号 已读页数；列表；归档 编号',
 'x_watchlist': '/观影单 添加 片名；看完 编号；评分 编号 8.5 备注；列表；归档 编号',
 'x_spaced_cards': '/复习卡 添加 问题|答案；到期；回答 编号；评估 编号 0～5；列表；归档 编号。不主动提醒。',
 'x_pomodoro': '/番茄钟 开始 25 任务；状态；完成；取消；历史；归档 编号。计时持久化，不主动推送。',
 'x_goals': '/目标 添加 名称|目标值；进度 编号 当前值；增加 编号 增量；列表；归档 编号',
 'x_inventory': '/物品库存 添加 物品 单位；变动 编号 +5 入库；流水 编号；列表；归档 编号',
 'x_split_bill': '/AA账本 创建 标题|成员A|成员B；成员 编号 名称；支出 编号 成员|金额|备注；结算 编号；查看 编号；归档 编号。群共享，创建者维护，不转账。',
 'x_signup': '/活动报名 创建 名称|名额；加入 编号 显示名；退出 编号；名单 编号；关闭 编号；列表。限当前群。',
 'x_round_robin': '/轮值抽取 创建 标题|A|B；下一个 编号；名单 编号；设置 编号 A|B；列表；归档 编号。推进需创建者或主人。',
 'x_queue': '/排队 创建 名称；入列 编号 名称；退出 编号；状态 编号；推进 编号；关闭 编号；列表。推进需创建者或主人。',
 'x_wishlist': '/愿望单 添加 名称|优先级1～5；优先 编号 优先级；完成 编号；列表；归档 编号',
 'x_recipes': '/食谱本 添加 名称|食材|步骤；查看 编号；搜索 关键词；修改 编号 名称|食材|步骤；归档 编号。只保存用户录入。',
 'x_bookmarks': '/书签 添加 名称|https://网址|标签；搜索 关键词；查看 编号；修改 编号 名称|网址|标签；归档 编号',
 'x_anniversary': '/纪念日 添加 名称|MM-DD；列表；修改 编号 名称|MM-DD；归档 编号。2月29日按下一闰年计算。',
 'x_packing': '/行李清单 创建 名称|物品A|物品B；模板 短途；勾选 编号 项号；取消 编号 项号；添加 编号 物品；状态 编号；归档 编号',
 'x_agenda': '/议程 创建 名称；添加 编号 议题；排序 编号 2,1；状态 编号；推进 编号；关闭 编号；列表。当前群共享，主持操作需创建者或主人。',
 'x_time_budget': '/工时 开始 项目；状态；结束；统计；历史；归档 编号。累计实际经过秒数，不主动推送。',
 'x_decision_matrix': '/决策表 创建 名称|指标A:3|指标B:2；评分 编号 选项|8|9；结果 编号；权重 编号 指标A:1|指标B:1；移除 编号 选项；归档 编号',
 'x_wiki': '/知识页 创建 标题|# 一级\\n## 二级\\n正文；更新 编号 正文；查看 编号；历史 编号；版本 编号 版本号；恢复 编号 版本号；归档 编号。群内共享，编辑需创建者或主人。',
}
