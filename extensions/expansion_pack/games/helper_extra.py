"""Seven original single-player text games; JSON state only, no I/O.

The games engine owns persistence and user/session isolation. Every call clones
the supplied state before validating or changing it, including error paths.
"""
from __future__ import annotations

import copy
import random
import re
import time

RNG = random.SystemRandom()

WORDS = (
    ('apple', '常见水果，名字也被一家科技公司使用'),
    ('beach', '沙子、海浪和日光常在这里相遇'),
    ('bread', '面粉经过发酵和烘烤后的常见食物'),
    ('bridge', '连接河流或道路两侧的结构'),
    ('camera', '用来把光影保存成照片的设备'),
    ('castle', '带有防御设施的古老建筑'),
    ('cloud', '天空中悬浮的小水滴或冰晶'),
    ('coffee', '常见的烘焙豆饮品'),
    ('forest', '许多树木共同生长的地方'),
    ('garden', '有人照料花草的小天地'),
    ('guitar', '通常有六根弦的拨弦乐器'),
    ('island', '四周被水包围的陆地'),
    ('jungle', '植被浓密、藤蔓交错的林地'),
    ('lemon', '味道偏酸的黄色柑橘类水果'),
    ('mirror', '能让你看到自己影像的物品'),
    ('orange', '既可以表示一种水果，也是一种颜色'),
    ('pencil', '常用石墨书写、可以擦除的文具'),
    ('planet', '围绕恒星运行的一类天体'),
    ('puzzle', '需要动脑解开的谜题或拼图'),
    ('rabbit', '有长耳朵、常被画成爱吃胡萝卜的小动物'),
    ('rocket', '通过喷出物质获得推力的飞行器'),
    ('silver', '一种常见贵金属，也是一种颜色'),
    ('spring', '既是季节，也可指弹簧或泉水'),
    ('window', '让光线进入房间、也能向外看的建筑构件'),
)

TRIVIA = (
    ('一小时有多少分钟？', ('60', '24', '100', '3600'), 0, '一小时等于60分钟。'),
    ('十进制数字10转换为二进制是？', ('1010', '1001', '1110', '1100'), 0, '10=8+2，所以写作1010。'),
    ('以下哪一种不是哺乳动物？', ('企鹅', '海豚', '鲸', '蝙蝠'), 0, '企鹅属于鸟类。'),
    ('地球的天然卫星是？', ('月球', '火星', '金星', '木星'), 0, '月球是地球的天然卫星。'),
    ('标准大气压下，纯水的沸点约为？', ('100℃', '0℃', '50℃', '200℃'), 0, '在标准大气压下，纯水约在100℃沸腾。'),
    ('以下哪一个是质数？', ('29', '21', '27', '33'), 0, '29只有1和29两个正因数。'),
    ('一个直角是多少度？', ('90度', '45度', '180度', '360度'), 0, '直角为90度。'),
    ('光在真空中的传播速度约为每秒？', ('30万千米', '340米', '3万米', '300千米'), 0, '真空光速约为每秒30万千米。'),
    ('Python中不可变的序列类型是？', ('tuple', 'list', 'dict', 'set'), 0, 'tuple是不可变序列；其元素仍可能引用可变对象。'),
    ('IPv4地址包含多少个二进制位？', ('32', '16', '64', '128'), 0, 'IPv4地址长度是32位。'),
    ('以下哪个是HTTP成功状态码？', ('200', '301', '404', '500'), 0, '200表示请求成功。'),
    ('中国标准时间通常采用哪个UTC偏移？', ('UTC+8', 'UTC+0', 'UTC-5', 'UTC+12'), 0, '中国标准时间采用UTC+8。'),
    ('十六进制的F等于十进制的？', ('15', '10', '16', '14'), 0, '十六进制A到F对应10到15。'),
    ('声音通常不能在哪种环境中传播？', ('真空', '空气', '水', '钢铁'), 0, '声音是机械波，需要介质传播。'),
    ('植物进行光合作用主要需要哪种气体？', ('二氧化碳', '氦气', '氖气', '氢气'), 0, '光合作用利用二氧化碳和水制造有机物。'),
    ('以下哪个不是标准国际象棋棋子？', ('炮', '后', '马', '象'), 0, '炮属于中国象棋棋子，标准国际象棋没有炮。'),
    ('π通常近似写为？', ('3.14159', '2.71828', '1.41421', '1.61803'), 0, 'π约为3.14159。'),
    ('元素符号O表示？', ('氧', '金', '铁', '银'), 0, 'O是氧的元素符号。'),
    ('标准公历中，以下哪一年是闰年？', ('2000年', '1900年', '2100年', '2023年'), 0, '整百年必须能被400整除才是闰年。'),
    ('正方形有多少条对称轴？', ('4条', '2条', '1条', '8条'), 0, '两条对角线和两条对边中垂线都是对称轴。'),
    ('计算机中一个字节通常包含多少位？', ('8位', '4位', '16位', '32位'), 0, '现代计算机中一个字节通常为8个二进制位。'),
    ('以下哪一个协议通常用于域名查询？', ('DNS', 'SMTP', 'SSH', 'IMAP'), 0, 'DNS负责域名解析。'),
    ('三角形在欧氏平面中的内角和为？', ('180度', '90度', '270度', '360度'), 0, '欧氏平面三角形的内角和是180度。'),
    ('汉字“众”由几个“人”组成？', ('3个', '2个', '4个', '5个'), 0, '“众”的常见字形包含三个“人”。'),
)


def need(condition, message):
    if not condition:
        raise ValueError(message)


def option(context, key, default, low, high):
    settings = context.get('settings', {})
    value = settings.get(key, default) if isinstance(settings, dict) else default
    return max(low, min(high, value)) if type(value) is int else default


def show_hint(context):
    settings = context.get('settings', {})
    return not isinstance(settings, dict) or settings.get('show_hint', True) is not False


def coordinate(value, size):
    match = re.fullmatch(r'([A-Za-z])([1-9])', value.strip())
    need(match, '坐标格式为 A1、B3 等。')
    column, row = ord(match.group(1).upper()) - 65, int(match.group(2)) - 1
    need(0 <= column < size and 0 <= row < size, f'坐标范围 A1～{chr(64 + size)}{size}。')
    return row * size + column


def label(position, size):
    return chr(65 + position % size) + str(position // size + 1)


def grid(cells, size):
    return '   ' + ' '.join(chr(65 + x) for x in range(size)) + '\n' + '\n'.join(str(y + 1) + '  ' + ' '.join(str(cells[y * size + x]) for x in range(size)) for y in range(size))


def hangman_start(extra, context):
    need(not extra, '开始不需要额外参数。')
    answer, hint = RNG.choice(WORDS)
    return dict(answer=answer, hint=hint, guesses=[], mistakes=0, max_mistakes=option(context, 'max_mistakes', 8, 4, 12), show_hint=show_hint(context))


def hangman_status(state):
    board = ' '.join(char if char in state['guesses'] or state['done'] else '_' for char in state['answer'])
    return f"单词：{board}\n错误 {state['mistakes']}/{state['max_mistakes']}；已猜：{', '.join(state['guesses']) or '无'}" + (f"\n线索：{state['hint']}" if state['show_hint'] else '') + '\n发送 /吊小人 猜 a 或 /吊小人 猜 完整单词。'


def hangman_move(arg, state, context):
    guess = arg[2:].strip() if arg.startswith('猜 ') else arg
    guess = guess.lower()
    need(re.fullmatch('[a-z]{1,12}', guess), '请输入一个英文字母或完整英文单词。')
    need(guess not in state['guesses'], '这个字母或单词已经猜过。')
    state['guesses'].append(guess)
    if guess == state['answer']:
        state['done'] = True
        return '猜中了！答案：' + state['answer']
    if len(guess) > 1 or guess not in state['answer']:
        state['mistakes'] += 1
    if all(char in state['guesses'] for char in state['answer']):
        state['done'] = True
        return '全部字母猜中了！答案：' + state['answer']
    if state['mistakes'] >= state['max_mistakes']:
        state['done'] = True
        return '机会用完，答案：' + state['answer']
    return hangman_status(state)


def battleship_start(extra, context):
    need(not extra, '海战开始不需要额外参数。')
    size, ships, occupied = 5, [], set()
    for length in (3, 2, 2):
        candidates = []
        for horizontal in (True, False):
            for row in range(size):
                for col in range(size):
                    end_row, end_col = (row, col + length - 1) if horizontal else (row + length - 1, col)
                    if end_row < size and end_col < size:
                        positions = [row * size + col + step * (1 if horizontal else size) for step in range(length)]
                        if not occupied.intersection(positions):
                            candidates.append(positions)
        need(candidates, '暂时无法生成海图，请重新开始。')
        ship = RNG.choice(candidates)
        ships.append(ship)
        occupied.update(ship)
    return dict(size=size, ships=ships, shots=[], max_shots=option(context, 'max_shots', 18, 10, 25))


def battleship_status(state):
    occupied = {cell for ship in state['ships'] for cell in ship}
    shots = set(state['shots'])
    cells = [('X' if pos in occupied else '~') if pos in shots else ('S' if state['done'] and pos in occupied else '·') for pos in range(25)]
    sunk = sum(set(ship) <= shots for ship in state['ships'])
    return f"海图（X命中、~未中、S结束后显示的舰体）\n{grid(cells, 5)}\n已攻击 {len(shots)}/{state['max_shots']}；击沉 {sunk}/3\n发送 /海战 攻击 A1。共3艘船，长度3、2、2，可相邻，不重叠。"


def battleship_move(arg, state, context):
    value = arg[3:].strip() if arg.startswith('攻击 ') else arg
    position = coordinate(value, 5)
    need(position not in state['shots'], '这个位置已经攻击过。')
    state['shots'].append(position)
    occupied = {cell for ship in state['ships'] for cell in ship}
    ship = next((ship for ship in state['ships'] if position in ship), None)
    message = '未命中。' if ship is None else '命中，击沉一艘！' if set(ship) <= set(state['shots']) else '命中！'
    if occupied <= set(state['shots']):
        state['done'] = True
        message = '全部舰船已击沉，你赢了！'
    elif len(state['shots']) >= state['max_shots']:
        state['done'] = True
        message += ' 攻击次数用完，本局结束。'
    return message + '\n' + battleship_status(state)


def card_name(card):
    return card['suit'] + {11: 'J', 12: 'Q', 13: 'K', 14: 'A'}.get(card['rank'], str(card['rank']))


def highlow_start(extra, context):
    need(not extra, '高低牌开始不需要额外参数。')
    deck = [dict(rank=rank, suit=suit) for suit in ('♠', '♥', '♣', '♦') for rank in range(2, 15)]
    RNG.shuffle(deck)
    return dict(deck=deck, index=0, score=0, target=option(context, 'target_wins', 5, 3, 12))


def highlow_status(state):
    return f"当前牌：{card_name(state['deck'][state['index']])}\n猜中 {state['score']}/{state['target']}；2最小，A最大，只比点数。\n发送 /高低牌 高 或 /高低牌 低；同点数为平局，不加分也不判错。"


def highlow_move(arg, state, context):
    guess = arg[2:].strip() if arg.startswith('猜 ') else arg
    need(guess in ('高', '低'), '请猜“高”或“低”。')
    old = state['deck'][state['index']]
    state['index'] += 1
    new = state['deck'][state['index']]
    equal = old['rank'] == new['rank']
    correct = (new['rank'] > old['rank']) == (guess == '高')
    if equal:
        message = '同点数，平局。'
    elif correct:
        state['score'] += 1
        message = '猜对了！'
    else:
        state['done'] = True
        message = '猜错了，本局结束。'
    if state['score'] >= state['target']:
        state['done'] = True
        message = '达到目标，你赢了！'
    elif state['index'] == len(state['deck']) - 1:
        state['done'] = True
        message += ' 本副牌已用完。'
    return f'{card_name(old)} → {card_name(new)}：{message}\n' + highlow_status(state)


def knight_moves(state):
    size, position = state['size'], state['visited'][-1]
    row, col = divmod(position, size)
    moves = []
    for dx, dy in ((1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1)):
        x, y = col + dx, row + dy
        if 0 <= x < size and 0 <= y < size and y * size + x not in state['visited']:
            moves.append(y * size + x)
    return sorted(moves)


def knight_start(extra, context):
    size = option(context, 'board_size', 5, 5, 6)
    position = coordinate(extra or 'A1', size)
    need(size % 2 == 0 or (position // size + position % size) % 2 == 0, '5×5棋盘必须从与A1同色的格子开始，否则无法访问全部25格。')
    return dict(size=size, visited=[position])


def knight_status(state):
    size = state['size']
    visited = {position: index + 1 for index, position in enumerate(state['visited'])}
    cells = [str(visited.get(pos, '·')).rjust(2) for pos in range(size * size)]
    legal = ', '.join(label(pos, size) for pos in knight_moves(state)) or '无'
    return f"骑士棋盘 {size}×{size}\n{grid(cells, size)}\n当前 {label(state['visited'][-1], size)}；已访问 {len(state['visited'])}/{size * size}\n可走：{legal}\n发送 /骑士巡游 移动 B3；日字走法，不重复经过格子。"


def knight_move(arg, state, context):
    value = arg[3:].strip() if arg.startswith('移动 ') else arg
    position = coordinate(value, state['size'])
    need(position in knight_moves(state), '该位置已访问、越界或不符合骑士日字走法。')
    state['visited'].append(position)
    if len(state['visited']) == state['size'] ** 2:
        state['done'] = True
        return '完成全部格子的骑士巡游，你赢了！\n' + knight_status(state)
    if not knight_moves(state):
        state['done'] = True
        return '没有合法下一步，本局结束。\n' + knight_status(state)
    return knight_status(state)


def arithmetic_question(maximum):
    operation = RNG.choice(('+', '-', '×', '÷'))
    a, b = RNG.randint(1, maximum), RNG.randint(1, maximum)
    if operation == '+': answer = a + b
    elif operation == '-': answer = a - b
    elif operation == '×': answer = a * b
    else:
        a, b = a * b, b
        answer = a // b
    return dict(a=a, b=b, operation=operation, answer=answer)


def arithmetic_start(extra, context):
    need(not extra, '口算挑战开始不需要额外参数。')
    maximum = option(context, 'max_operand', 20, 5, 100)
    return dict(maximum=maximum, questions=option(context, 'question_count', 5, 3, 20), answered=0, correct=0, started=time.time(), question=arithmetic_question(maximum))


def arithmetic_status(state):
    if state['done']:
        return f"口算挑战：答对 {state['correct']}/{state['answered']}，共计划 {state['questions']} 题。"
    question = state['question']
    return f"第 {state['answered'] + 1}/{state['questions']} 题：{question['a']} {question['operation']} {question['b']} = ?\n当前答对 {state['correct']} 题；发送 /口算挑战 答 整数。每题提交一次，错题会揭晓答案。"


def arithmetic_move(arg, state, context):
    answer = arg[2:].strip() if arg.startswith('答 ') else arg
    need(re.fullmatch(r'[+-]?\d{1,6}', answer), '请输入整数答案，例如 /口算挑战 答 -3。')
    correct = int(answer) == state['question']['answer']
    message = '答对了！' if correct else f"答错了，正确答案是 {state['question']['answer']}。"
    state['answered'] += 1
    state['correct'] += int(correct)
    if state['answered'] >= state['questions']:
        state['done'] = True
        elapsed = max(0, int(time.time() - state['started']))
        return message + f'\n挑战完成，用时 {elapsed} 秒。\n' + arithmetic_status(state)
    state['question'] = arithmetic_question(state['maximum'])
    return message + '\n' + arithmetic_status(state)


def trivia_start(extra, context):
    need(not extra, '知识问答开始不需要额外参数。')
    count = option(context, 'question_count', 5, 3, 12)
    questions = []
    for prompt, options, correct, explanation in RNG.sample(TRIVIA, count):
        indices = list(range(4))
        RNG.shuffle(indices)
        questions.append(dict(prompt=prompt, options=[options[i] for i in indices], correct=indices.index(correct), explanation=explanation))
    return dict(questions=questions, answered=0, correct=0)


def trivia_status(state):
    if state['done']:
        return f"知识问答：答对 {state['correct']}/{state['answered']}，共计划 {len(state['questions'])} 题。"
    question = state['questions'][state['answered']]
    return f"第 {state['answered'] + 1}/{len(state['questions'])} 题：{question['prompt']}\n" + '\n'.join(f'{chr(65 + i)}. {value}' for i, value in enumerate(question['options'])) + f"\n当前答对 {state['correct']} 题；发送 /知识问答 A/B/C/D。"


def trivia_move(arg, state, context):
    answer = (arg[2:].strip() if arg.startswith('答 ') else arg).upper()
    need(answer in ('A', 'B', 'C', 'D'), '请输入一个选项字母 A、B、C 或 D。')
    question = state['questions'][state['answered']]
    correct = ord(answer) - 65 == question['correct']
    state['correct'] += int(correct)
    state['answered'] += 1
    result = ('答对了！' if correct else f"答错了，正确选项是 {chr(65 + question['correct'])}。") + ' ' + question['explanation']
    if state['answered'] >= len(state['questions']):
        state['done'] = True
        result += '\n本轮问答完成！'
    return result + '\n' + trivia_status(state)


def anagram_start(extra, context):
    need(not extra, '乱序猜词开始不需要额外参数。')
    answer, hint = RNG.choice(WORDS)
    scrambled = list(answer)
    for _ in range(8):
        RNG.shuffle(scrambled)
        if ''.join(scrambled) != answer:
            break
    if ''.join(scrambled) == answer:
        scrambled = list(answer[1:] + answer[:1])
    return dict(answer=answer, hint=hint, scrambled=''.join(scrambled), guesses=[], max_mistakes=option(context, 'max_mistakes', 5, 2, 10), show_hint=show_hint(context))


def anagram_status(state):
    return f"打乱字母：{state['scrambled']}\n错误 {len(state['guesses'])}/{state['max_mistakes']}；已猜：{', '.join(state['guesses']) or '无'}" + (f"\n线索：{state['hint']}" if state['show_hint'] else '') + '\n发送 /乱序猜词 猜 英文单词。'


def anagram_move(arg, state, context):
    guess = (arg[2:].strip() if arg.startswith('猜 ') else arg).lower()
    need(re.fullmatch('[a-z]{3,12}', guess), '请输入 3～12 个英文字母组成的单词。')
    need(guess not in state['guesses'], '这个单词已经猜过。')
    if guess == state['answer']:
        state['done'] = True
        return '猜中了！答案：' + state['answer']
    state['guesses'].append(guess)
    if len(state['guesses']) >= state['max_mistakes']:
        state['done'] = True
        return '机会用完，答案：' + state['answer']
    return '还不对。\n' + anagram_status(state)


STARTS = {'x_hangman': hangman_start, 'x_battleship': battleship_start, 'x_highlow': highlow_start, 'x_knight': knight_start, 'x_arithmetic': arithmetic_start, 'x_trivia': trivia_start, 'x_anagram': anagram_start}
STATUSES = {'x_hangman': hangman_status, 'x_battleship': battleship_status, 'x_highlow': highlow_status, 'x_knight': knight_status, 'x_arithmetic': arithmetic_status, 'x_trivia': trivia_status, 'x_anagram': anagram_status}
MOVES = {'x_hangman': hangman_move, 'x_battleship': battleship_move, 'x_highlow': highlow_move, 'x_knight': knight_move, 'x_arithmetic': arithmetic_move, 'x_trivia': trivia_move, 'x_anagram': anagram_move}


def run(kind, arg, state, context):
    need(kind in STARTS, '未知游戏类型。')
    need(isinstance(arg, str) and len(arg) <= 1000, '游戏命令最多 1000 字符。')
    arg = arg.strip()
    previous = copy.deepcopy(state)
    context = context if isinstance(context, dict) else {}
    parts = arg.split(maxsplit=1)
    action = parts[0] if parts else '状态'
    extra = parts[1] if len(parts) == 2 else ''
    if action in ('帮助', 'help', '?'):
        need(not extra, '帮助不需要额外参数。')
        return previous, USAGE[kind]
    if action == '开始':
        need(not previous or previous.get('done'), '已有进行中的游戏，请先投降或完成。')
        created = STARTS[kind](extra, context)
        created.update(kind=kind, version=1, done=False)
        return created, '游戏已开始！\n' + STATUSES[kind](created)
    need(previous is not None and isinstance(previous, dict) and previous.get('kind') == kind, '尚未开始，请发送对应命令加“开始”。')
    if action == '状态':
        need(not extra, '状态不需要额外参数。')
        return previous, ('本局已结束。\n' if previous['done'] else '') + STATUSES[kind](previous)
    need(not previous['done'], '本局已结束，请重新开始。')
    if action in ('投降', '结束', '取消'):
        need(not extra, '结束不需要额外参数。')
        previous['done'] = True
        previous['surrendered'] = True
        answer = '\n答案：' + previous['answer'] if 'answer' in previous else ''
        return previous, '本局已结束。' + answer + '\n' + STATUSES[kind](previous)
    reply = MOVES[kind](arg, previous, context)
    return previous, reply


def integer(key, label_text, default, low, high):
    return dict(key=key, type='integer', label=label_text, default=default, min=low, max=high)


HINT_SCHEMA = dict(key='show_hint', type='boolean', label='显示原创中文线索', default=True)
USAGE = {
    'x_hangman': '/吊小人 开始；/吊小人 猜 a（单字母）或 /吊小人 猜 apple（完整英文词）；状态；投降。错误字母和错误整词各消耗一次机会，重复输入不消耗。词库与中文线索原创整理。',
    'x_battleship': '/海战 开始；/海战 攻击 A1；状态；投降。5×5隐藏海图含长度3、2、2的三艘船，直线连续、不重叠、可以相邻。X为命中、~为未中；击沉全部舰船获胜，重复坐标不消耗攻击次数。',
    'x_highlow': '/高低牌 开始；/高低牌 高 或 /高低牌 低；状态；投降。完整52张牌洗牌不放回，2最小、A最大、不比较花色。同点数平局不加分也不判错，一次猜错结束，达到面板目标次数获胜。',
    'x_knight': '/骑士巡游 开始 [A1]；/骑士巡游 移动 B3；状态；投降。采用国际象棋骑士日字走法（横1竖2或横2竖1），每格只访问一次，覆盖全部格子获胜，无合法下一步结束。5×5只能从与A1同色格子开局，6×6可任意格。',
    'x_arithmetic': '/口算挑战 开始；/口算挑战 答 -3；状态；投降。原创随机整数加减乘除题，除法均可整除，每题提交一次；错题揭晓答案后进入下一题，完成后显示答对题数和实际经过秒数。',
    'x_trivia': '/知识问答 开始；/知识问答 A（或B/C/D）；状态；投降。原创24道基础知识小题库，每局随机抽题不重复，选项会打乱；每题提交一次并给出简短解释。完成后显示得分。',
    'x_anagram': '/乱序猜词 开始；/乱序猜词 猜 apple；状态；投降。将同一英文词的全部字母打乱，猜中完整单词获胜；重复输入不扣机会，错词消耗一次机会。可在面板设置机会数和是否显示原创中文线索。',
}
CATALOG = [
    dict(id='x_hangman', name='吊小人猜字母', commands=['/吊小人'], smoke_command='/吊小人 开始', expected_contains='游戏已开始', config_schema=[integer('max_mistakes', '允许猜错次数', 8, 4, 12), HINT_SCHEMA]),
    dict(id='x_battleship', name='单人坐标海战', commands=['/海战'], smoke_command='/海战 开始', expected_contains='游戏已开始', config_schema=[integer('max_shots', '每局攻击次数', 18, 10, 25)]),
    dict(id='x_highlow', name='高低牌挑战', commands=['/高低牌'], smoke_command='/高低牌 开始', expected_contains='游戏已开始', config_schema=[integer('target_wins', '猜中目标次数', 5, 3, 12)]),
    dict(id='x_knight', name='骑士巡游', commands=['/骑士巡游'], smoke_command='/骑士巡游 开始', expected_contains='游戏已开始', config_schema=[integer('board_size', '棋盘边长', 5, 5, 6)]),
    dict(id='x_arithmetic', name='口算挑战', commands=['/口算挑战'], smoke_command='/口算挑战 开始', expected_contains='游戏已开始', config_schema=[integer('question_count', '每局题数', 5, 3, 20), integer('max_operand', '生成算式因子上限', 20, 5, 100)]),
    dict(id='x_trivia', name='原创知识问答', commands=['/知识问答'], smoke_command='/知识问答 开始', expected_contains='游戏已开始', config_schema=[integer('question_count', '每局题数', 5, 3, 12)]),
    dict(id='x_anagram', name='乱序猜词', commands=['/乱序猜词'], smoke_command='/乱序猜词 开始', expected_contains='游戏已开始', config_schema=[integer('max_mistakes', '允许猜错次数', 5, 2, 10), HINT_SCHEMA]),
]
for _item in CATALOG:
    _item['usage'] = USAGE[_item['id']]
