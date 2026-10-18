"""Seven original offline solo puzzles; JSON state transitions only.

The host owns storage, access scope, and transactions. Rejected actions never
mutate the supplied state. No network, model, assets, or messages are used.
"""
from collections import deque
from copy import deepcopy
import random

RNG = random.SystemRandom()
DIRECTIONS = {'上': (-1, 0), '下': (1, 0), '左': (0, -1), '右': (0, 1)}


def need(condition, message):
    if not condition:
        raise ValueError(message)


def setting(ctx, key, default, lo, hi):
    settings = ctx.get('settings', {})
    value = settings.get(key, default) if isinstance(settings, dict) else default
    return value if type(value) is int and lo <= value <= hi else default


def integer(raw, lo, hi):
    try:
        value = int(raw)
    except (ValueError, TypeError):
        raise ValueError('请输入有效整数。')
    need(str(value) == str(raw), '请输入普通十进制整数。')
    need(lo <= value <= hi, f'范围应为{lo}～{hi}。')
    return value


def at(args, size):
    need(len(args) == 2, '请提供 行 列，编号从1开始。')
    return (integer(args[0], 1, size)-1)*size + integer(args[1], 1, size)-1


def neighbours(index, size):
    row, col = divmod(index, size)
    return [r*size+c for r in range(max(0, row-1), min(size, row+2))
            for c in range(max(0, col-1), min(size, col+2)) if (r, c) != (row, col)]


def grid(cells, size):
    return '    ' + ' '.join(str(i+1) for i in range(size)) + '\n' + '\n'.join(
        f'{r+1:>2}  ' + ' '.join(str(x) for x in cells[r*size:(r+1)*size]) for r in range(size))


def mine_render(s, reveal=False):
    mines = set(s['mines'] or [])
    opened, flags = set(s['opened']), set(s['flags'])
    cells = []
    for i in range(s['size']**2):
        if reveal and i in mines: cells.append('✹')
        elif i in flags: cells.append('⚑')
        elif i in opened: cells.append(str(sum(n in mines for n in neighbours(i, s['size']))))
        else: cells.append('□')
    return f"安全格已开{len(opened)}/{s['size']**2-s['bombs']} · 插旗{len(flags)}\n" + grid(cells, s['size'])


def minesweeper(arg, s, ctx):
    parts = arg.split(); action = parts[0] if parts else '状态'
    if action == '开始':
        need(len(parts) in (1, 3), '开始 或 开始 边长 雷数；边长5～9。')
        size = integer(parts[1], 5, 9) if len(parts) == 3 else setting(ctx, 'board_size', 6, 5, 9)
        bombs = integer(parts[2], 1, size**2-1) if len(parts) == 3 else min(setting(ctx, 'mine_count', 6, 1, 40), size**2-1)
        s = dict(size=size, bombs=bombs, mines=None, opened=[], flags=[], done=False, won=False)
        return s, '扫雷已开始；首个实际翻开的格子安全。开 行 列；旗 行 列。\n' + mine_render(s)
    need(s is not None, '请先发送 /扫雷 开始。')
    if action == '状态': return s, mine_render(s, s['done'])
    if action in ('投降', '结束'):
        s['done'] = True; return s, '扫雷已结束。\n' + mine_render(s, True)
    need(not s['done'], '本局已经结束，请重新开始。')
    need(action in ('开', '翻开', '旗', '插旗'), '用法：开 行 列；旗 行 列；状态；投降。')
    index = at(parts[1:], s['size'])
    need(index not in s['opened'], '此格已经翻开。')
    if action in ('旗', '插旗'):
        if index in s['flags']: s['flags'].remove(index); result = '已取消插旗。'
        else:
            need(len(s['flags']) < s['bombs'], '插旗数不能超过雷数。')
            s['flags'].append(index); result = '已插旗；旗帜不计为成功翻开。'
        return s, result + '\n' + mine_render(s)
    need(index not in s['flags'], '请先取消此格的旗帜再翻开。')
    if s['mines'] is None:
        s['mines'] = sorted(RNG.sample([i for i in range(s['size']**2) if i != index], s['bombs']))
    mines = set(s['mines'])
    if index in mines:
        s['done'] = True; return s, '踩雷了，本局结束。\n' + mine_render(s, True)
    opened = set(s['opened']); flags = set(s['flags']); todo = [index]
    while todo:
        current = todo.pop()
        if current in opened or current in flags or current in mines: continue
        opened.add(current)
        adjacent = neighbours(current, s['size'])
        if not any(n in mines for n in adjacent): todo.extend(n for n in adjacent if n not in opened)
    s['opened'] = sorted(opened)
    s['won'] = len(opened) == s['size']**2-s['bombs']; s['done'] = s['won']
    return s, ('全部安全格已翻开，扫雷获胜！' if s['won'] else '已翻开。') + '\n' + mine_render(s, s['done'])


def sliding_neighbours(blank):
    row, col = divmod(blank, 3)
    return [r*3+c for dr, dc in DIRECTIONS.values() for r, c in [(row+dr, col+dc)] if 0 <= r < 3 and 0 <= c < 3]


def sliding_render(s):
    return '步数：' + str(s['steps']) + '\n' + grid(['□' if x == 0 else str(x) for x in s['board']], 3)


def sliding(arg, s, ctx):
    parts = arg.split(); action = parts[0] if parts else '状态'
    if action == '开始':
        need(len(parts) == 1, '使用 开始 创建3×3拼图。')
        board = list(range(1, 9)) + [0]; blank, previous = 8, None
        for _ in range(setting(ctx, 'shuffle_steps', 80, 10, 200)):
            options = [x for x in sliding_neighbours(blank) if x != previous] or sliding_neighbours(blank)
            choice = RNG.choice(options); board[blank], board[choice] = board[choice], board[blank]; previous, blank = blank, choice
        if board == list(range(1, 9)) + [0]: board[7], board[8] = board[8], board[7]
        s = dict(board=board, steps=0, done=False, won=False)
        return s, '滑块拼图已开始；通过合法移动打乱，保证可解。移动 数字；上/下/左/右移动空格。\n' + sliding_render(s)
    need(s is not None, '请先发送 /滑块拼图 开始。')
    if action == '状态': return s, sliding_render(s)
    if action in ('投降', '结束'): s['done'] = True; return s, '滑块拼图已结束。\n' + sliding_render(s)
    need(not s['done'], '本局已经结束，请重新开始。')
    blank = s['board'].index(0)
    if action == '移动':
        need(len(parts) == 2, '使用 移动 数字，或直接发送空格移动方向。')
        tile = integer(parts[1], 1, 8); chosen = s['board'].index(tile)
    else:
        need(action in DIRECTIONS and len(parts) == 1, '使用 移动 数字，或 上/下/左/右 移动空格。')
        row, col = divmod(blank, 3); dr, dc = DIRECTIONS[action]; row, col = row+dr, col+dc
        need(0 <= row < 3 and 0 <= col < 3, '空格不能移出棋盘。'); chosen = row*3+col
    need(chosen in sliding_neighbours(blank), '只能移动与空格上下左右相邻的数字。')
    s['board'][blank], s['board'][chosen] = s['board'][chosen], s['board'][blank]; s['steps'] += 1
    s['won'] = s['board'] == list(range(1, 9)) + [0]; s['done'] = s['won']
    return s, ('拼图复原成功！' if s['won'] else '已移动。') + '\n' + sliding_render(s)


def merge_line(line):
    values = [x for x in line if x]; result = []; score = 0; index = 0
    while index < len(values):
        if index+1 < len(values) and values[index] == values[index+1]:
            merged = values[index]*2; result.append(merged); score += merged; index += 2
        else: result.append(values[index]); index += 1
    return result + [0]*(len(line)-len(result)), score


def shift_2048(board, direction):
    result = list(board); gain = 0
    for axis in range(4):
        indices = [axis*4+i for i in range(4)] if direction in ('左', '右') else [i*4+axis for i in range(4)]
        if direction in ('右', '下'): indices.reverse()
        merged, added = merge_line([board[i] for i in indices]); gain += added
        for i, value in zip(indices, merged): result[i] = value
    return result, gain


def spawn_2048(board):
    empty = [i for i, v in enumerate(board) if v == 0]
    if empty: board[RNG.choice(empty)] = 4 if RNG.random() < .1 else 2


def can_2048_move(board):
    return any(not value for value in board) or any(shift_2048(board, d)[0] != board for d in DIRECTIONS)


def render_2048(s):
    return f"分数：{s['score']} · 步数：{s['steps']} · 目标：{s['target']}\n" + grid([str(v) if v else '·' for v in s['board']], 4)


def game_2048(arg, s, ctx):
    parts = arg.split(); action = parts[0] if parts else '状态'
    if action == '开始':
        need(len(parts) == 1, '使用 开始 创建4×4棋盘。')
        board = [0]*16; spawn_2048(board); spawn_2048(board)
        target = setting(ctx, 'target', 2048, 128, 65536)
        if target & (target-1): target = 2048
        s = dict(board=board, score=0, steps=0, target=target, reached=False, done=False)
        return s, '2048已开始；发送 上/下/左/右，每格每步只合并一次。\n' + render_2048(s)
    need(s is not None, '请先发送 /2048 开始。')
    if action == '状态': return s, render_2048(s)
    if action in ('投降', '结束'): s['done'] = True; return s, '2048已结束。\n' + render_2048(s)
    need(not s['done'], '本局已经结束，请重新开始。')
    need(action in DIRECTIONS and len(parts) == 1, '发送 上/下/左/右，或状态、投降。')
    board, gain = shift_2048(s['board'], action)
    need(board != s['board'], '这个方向没有有效移动，不增加步数，也不会生成新块。')
    s['board'] = board; s['score'] += gain; s['steps'] += 1; spawn_2048(s['board'])
    reached = max(s['board']) >= s['target']; first = reached and not s['reached']; s['reached'] = s['reached'] or reached
    s['done'] = not can_2048_move(s['board'])
    result = '目标已达成，可以继续挑战！' if first else '已移动。'
    if s['done']: result += '没有可行移动，本局结束。'
    return s, result + '\n' + render_2048(s)


def toggle_lights(board, size, index):
    row, col = divmod(index, size)
    for dr, dc in [(0, 0), *DIRECTIONS.values()]:
        r, c = row+dr, col+dc
        if 0 <= r < size and 0 <= c < size: board[r*size+c] ^= 1


def render_lights(s):
    return f"步数：{s['steps']} · 亮灯{sum(s['board'])}盏\n" + grid(['■' if x else '·' for x in s['board']], s['size'])


def lights(arg, s, ctx):
    parts = arg.split(); action = parts[0] if parts else '状态'
    if action == '开始':
        need(len(parts) == 1, '使用 开始 创建熄灯棋盘。')
        size = setting(ctx, 'board_size', 4, 3, 5); board = [0]*(size**2)
        for _ in range(setting(ctx, 'shuffle_steps', 12, 1, 100)): toggle_lights(board, size, RNG.randrange(size**2))
        if not any(board): toggle_lights(board, size, 0)
        s = dict(board=board, size=size, steps=0, done=False, won=False)
        return s, '熄灯游戏已开始；从全灭状态经合法开关生成，保证可解。按 行 列；十字相邻和自身一起切换。\n' + render_lights(s)
    need(s is not None, '请先发送 /熄灯游戏 开始。')
    if action == '状态': return s, render_lights(s)
    if action in ('投降', '结束'): s['done'] = True; return s, '熄灯游戏已结束。\n' + render_lights(s)
    need(not s['done'], '本局已经结束，请重新开始。')
    need(action in ('按', '切换'), '使用 按 行 列。')
    index = at(parts[1:], s['size']); toggle_lights(s['board'], s['size'], index); s['steps'] += 1
    s['won'] = not any(s['board']); s['done'] = s['won']
    return s, ('全部熄灭，获胜！' if s['won'] else '开关已切换。') + '\n' + render_lights(s)


# Original compact layout. Both boxes have unobstructed solutions and targets.
SOKOBAN_MAP = ('#######', '#  .  #', '#  $  #', '#  @  #', '#  $ .#', '#     #', '#######')


def render_sokoban(s):
    walls, boxes, goals = set(s['walls']), set(s['boxes']), set(s['goals']); cells = []
    for i in range(s['size']**2):
        if i in walls: value = '#'
        elif i == s['player']: value = '+' if i in goals else '@'
        elif i in boxes: value = '*' if i in goals else '$'
        elif i in goals: value = '.'
        else: value = '·'
        cells.append(value)
    return f"步数：{s['steps']} · 箱子{len(boxes & goals)}/{len(boxes)}到位\n" + grid(cells, s['size'])


def sokoban(arg, s, ctx):
    parts = arg.split(); action = parts[0] if parts else '状态'
    if action == '开始':
        need(len(parts) == 1, '使用 开始 创建原创小关卡。')
        cells = ''.join(SOKOBAN_MAP)
        s = dict(size=7, player=cells.index('@'), walls=[i for i, c in enumerate(cells) if c == '#'],
                 boxes=[i for i, c in enumerate(cells) if c == '$'], goals=[i for i, c in enumerate(cells) if c == '.'],
                 steps=0, history=[], undo_limit=setting(ctx, 'undo_limit', 50, 1, 100), done=False, won=False)
        return s, '推箱子已开始；@是你，$是箱子，.是目标，*为到位箱子。发送方向；撤销可回退。\n' + render_sokoban(s)
    need(s is not None, '请先发送 /推箱子 开始。')
    if action == '状态': return s, render_sokoban(s)
    if action in ('投降', '结束'): s['done'] = True; s['surrendered'] = True; return s, '推箱子已结束。\n' + render_sokoban(s)
    if action == '撤销':
        need(not s.get('surrendered'), '投降后请重新开始。'); need(bool(s['history']), '暂无可撤销的步骤。')
        old = s['history'].pop(); s.update(old); s['done'] = False; s['won'] = False
        return s, '已撤销一步。\n' + render_sokoban(s)
    need(not s['done'], '本局已经结束，请重新开始。')
    need(action in DIRECTIONS and len(parts) == 1, '发送 上/下/左/右，或撤销、状态、投降。')
    row, col = divmod(s['player'], s['size']); dr, dc = DIRECTIONS[action]; r, c = row+dr, col+dc
    need(0 <= r < s['size'] and 0 <= c < s['size'], '不能移出地图。'); target = r*s['size']+c
    need(target not in s['walls'], '前方是墙，无法移动。')
    boxes = list(s['boxes'])
    if target in boxes:
        rr, cc = r+dr, c+dc; need(0 <= rr < s['size'] and 0 <= cc < s['size'], '箱子不能移出地图。'); beyond = rr*s['size']+cc
        need(beyond not in s['walls'] and beyond not in boxes, '箱子前方被阻挡，不能推动。'); boxes[boxes.index(target)] = beyond
    s['history'].append({'player': s['player'], 'boxes': s['boxes'][:], 'steps': s['steps']})
    s['history'] = s['history'][-s['undo_limit']:]; s['player'] = target; s['boxes'] = boxes; s['steps'] += 1
    s['won'] = set(boxes) == set(s['goals']); s['done'] = s['won']
    return s, ('全部箱子到位，推箱子获胜！' if s['won'] else '已移动。') + '\n' + render_sokoban(s)


def make_maze(size):
    paths = {size+1}; stack = [size+1]
    while stack:
        current = stack[-1]; row, col = divmod(current, size)
        options = [(row+dr*2, col+dc*2, dr, dc) for dr, dc in DIRECTIONS.values()
                   if 0 < row+dr*2 < size-1 and 0 < col+dc*2 < size-1 and (row+dr*2)*size+col+dc*2 not in paths]
        if not options: stack.pop(); continue
        r, c, dr, dc = RNG.choice(options); target = r*size+c; paths.add((row+dr)*size+col+dc); paths.add(target); stack.append(target)
    return sorted(paths)


def render_maze(s):
    paths = set(s['paths']); visited = set(s['visited']); cells = []
    for i in range(s['size']**2):
        cells.append('@' if i == s['player'] else 'E' if i == s['exit'] else '#' if i not in paths else '·' if i in visited else ' ')
    return f"步数：{s['steps']} · @当前位置，E出口，·已走路径\n" + grid(cells, s['size'])


def maze(arg, s, ctx):
    parts = arg.split(); action = parts[0] if parts else '状态'
    if action == '开始':
        need(len(parts) == 1, '使用 开始 创建随机迷宫。')
        size = setting(ctx, 'board_size', 9, 7, 15)
        if size % 2 == 0: size -= 1
        paths = make_maze(size)
        s = dict(size=size, paths=paths, player=size+1, exit=(size-2)*size+size-2, visited=[size+1], steps=0, done=False, won=False)
        return s, '迷宫已开始；随机通路保证起点可达出口。发送 上/下/左/右。\n' + render_maze(s)
    need(s is not None, '请先发送 /迷宫 开始。')
    if action == '状态': return s, render_maze(s)
    if action in ('投降', '结束'): s['done'] = True; return s, '迷宫已结束。\n' + render_maze(s)
    need(not s['done'], '本局已经结束，请重新开始。')
    need(action in DIRECTIONS and len(parts) == 1, '发送 上/下/左/右，或状态、投降。')
    row, col = divmod(s['player'], s['size']); dr, dc = DIRECTIONS[action]; r, c = row+dr, col+dc
    need(0 <= r < s['size'] and 0 <= c < s['size'] and r*s['size']+c in s['paths'], '前方是墙，不能穿越。')
    s['player'] = r*s['size']+c; s['steps'] += 1
    if s['player'] not in s['visited']: s['visited'].append(s['player'])
    s['won'] = s['player'] == s['exit']; s['done'] = s['won']
    return s, ('已经走到出口，迷宫获胜！' if s['won'] else '已移动。') + '\n' + render_maze(s)


MEMORY_SYMBOLS = ('🍎', '🍋', '🍇', '🍊', '🍉', '🍒', '🍓', '🥝', '🍍', '🥥', '🍑', '🍐')


def render_memory(s):
    visible = set(s['matched']) | set(s['faceup'])
    return f"配对{len(s['matched'])//2}/{len(s['cards'])//2} · 回合{s['turns']}\n" + '  '.join(f"{i+1}:{card if i in visible else '□'}" for i, card in enumerate(s['cards']))


def memory(arg, s, ctx):
    parts = arg.split(); action = parts[0] if parts else '状态'
    if action == '开始':
        need(len(parts) == 1, '使用 开始 创建翻牌局。')
        pairs = setting(ctx, 'pairs', 6, 4, 12); cards = list(MEMORY_SYMBOLS[:pairs])*2; RNG.shuffle(cards)
        s = dict(cards=cards, matched=[], faceup=[], turns=0, done=False, won=False)
        return s, '翻牌记忆已开始；翻 编号 或 翻 编号A 编号B。只显示本次翻开的牌与已配对的牌。\n' + render_memory(s)
    need(s is not None, '请先发送 /翻牌记忆 开始。')
    if action == '状态': return s, render_memory(s)
    if action in ('投降', '结束'):
        s['done'] = True; s['faceup'] = []
        return s, '翻牌记忆已结束，未翻开的答案仍然隐藏。\n' + render_memory(s)
    need(not s['done'], '本局已经结束，请重新开始。')
    need(action in ('翻', '翻开') and len(parts) in (2, 3), '使用 翻 编号，或 翻 编号A 编号B。')
    chosen = [integer(x, 1, len(s['cards']))-1 for x in parts[1:]]
    need(len(set(chosen)) == len(chosen), '同一回合不能重复翻同一张牌。')
    need(not any(x in s['matched'] or x in s['faceup'] for x in chosen), '这张牌已配对或正面朝上，请选择其他牌。')
    need(len(s['faceup'])+len(chosen) <= 2, '已经翻开一张，请只补翻一张。')
    s['faceup'].extend(chosen)
    shown = '，'.join(f"{x+1}={s['cards'][x]}" for x in s['faceup'])
    if len(s['faceup']) == 1: return s, '翻开 ' + shown + '；请再翻一张。\n' + render_memory(s)
    first, second = s['faceup']; s['turns'] += 1; matched = s['cards'][first] == s['cards'][second]
    if matched: s['matched'].extend(s['faceup'])
    s['faceup'] = []; s['won'] = len(s['matched']) == len(s['cards']); s['done'] = s['won']
    return s, '翻开 ' + shown + ('；配对成功！' if matched else '；未配对，已盖回。') + ('全部配对，获胜！' if s['won'] else '') + '\n' + render_memory(s)


USAGE = {
 'x_minesweeper': '/扫雷 开始 [边长 雷数]；开 行 列；旗 行 列（再次取消）；状态；投降。首个实际翻开格安全，插旗不算翻开。',
 'x_sliding': '/滑块拼图 开始；移动 数字；或 上/下/左/右（移动空格）；状态；投降。目标为1～8按行排列，空格右下。',
 'x_2048': '/2048 开始；上/下/左/右；状态；投降。每格每步最多合并一次；无效移动不会产生新块。达标后可继续。',
 'x_lights': '/熄灯游戏 开始；按 行 列；状态；投降。切换自身及上下左右，目标全部熄灭。',
 'x_sokoban': '/推箱子 开始；上/下/左/右；撤销；状态；投降。@人物，$箱子，.目标，*到位。只能推不能拉，原创单关。',
 'x_maze': '/迷宫 开始；上/下/左/右；状态；投降。@人物，E出口，#墙，·走过路径；随机地图保证可达。',
 'x_memory': '/翻牌记忆 开始；翻 编号；或 翻 编号A 编号B；状态；投降。不匹配的两张在回执显示后盖回，隐藏其他牌。',
}


def integer_schema(key, label, default, lo, hi, options=None):
    result = dict(key=key, type='integer', label=label, default=default, min=lo, max=hi)
    if options: result['options'] = options
    return result


CATALOG = [
 dict(id='x_minesweeper', name='首步安全扫雷', commands=['/扫雷'], smoke_command='/扫雷 开始', expected_contains='扫雷已开始', config_schema=[integer_schema('board_size','默认棋盘边长',6,5,9), integer_schema('mine_count','默认雷数（至多总格数减一）',6,1,40)]),
 dict(id='x_sliding', name='可解滑块拼图', commands=['/滑块拼图'], smoke_command='/滑块拼图 开始', expected_contains='滑块拼图已开始', config_schema=[integer_schema('shuffle_steps','合法随机打乱步数',80,10,200)]),
 dict(id='x_2048', name='2048合并挑战', commands=['/2048'], smoke_command='/2048 开始', expected_contains='2048已开始', config_schema=[integer_schema('target','达标方块',2048,128,65536,[128,256,512,1024,2048,4096,8192,16384,32768,65536])]),
 dict(id='x_lights', name='可解熄灯游戏', commands=['/熄灯游戏'], smoke_command='/熄灯游戏 开始', expected_contains='熄灯游戏已开始', config_schema=[integer_schema('board_size','棋盘边长',4,3,5), integer_schema('shuffle_steps','生成局面时开关次数',12,1,100)]),
 dict(id='x_sokoban', name='原创推箱子', commands=['/推箱子'], smoke_command='/推箱子 开始', expected_contains='推箱子已开始', config_schema=[integer_schema('undo_limit','最多保留撤销步数',50,1,100)]),
 dict(id='x_maze', name='随机可达迷宫', commands=['/迷宫'], smoke_command='/迷宫 开始', expected_contains='迷宫已开始', config_schema=[integer_schema('board_size','地图边长（单数）',9,7,15,[7,9,11,13,15])]),
 dict(id='x_memory', name='翻牌配对记忆', commands=['/翻牌记忆'], smoke_command='/翻牌记忆 开始', expected_contains='翻牌记忆已开始', config_schema=[integer_schema('pairs','配对数量',6,4,12)]),
]
for _item in CATALOG: _item['usage'] = USAGE[_item['id']]
FUNCTIONS = {'x_minesweeper': minesweeper, 'x_sliding': sliding, 'x_2048': game_2048, 'x_lights': lights, 'x_sokoban': sokoban, 'x_maze': maze, 'x_memory': memory}


def run(kind, arg, state, context):
    need(kind in FUNCTIONS, '未知益智游戏。')
    need(isinstance(arg, str) and len(arg) <= 500, '参数须为最多500字符的文字。')
    state = deepcopy(state)
    if arg.strip() in ('帮助', 'help', '?'): return state, USAGE[kind]
    return FUNCTIONS[kind](arg.strip(), state, context or {})
