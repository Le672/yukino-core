"""Original, bounded text games. No network, model, timers or shared chat access."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time

RNG = secrets.SystemRandom()
MAX_RECORDS = 2000
MAX_STATE_BYTES = 18000
RETENTION_SECONDS = 30 * 86400
PREFIXES = {"s_sudoku": "/数独", "s_nonogram": "/数织", "s_expedition": "/远征"}
HELP = {
    "s_sudoku": "4×4数独：/数独 开始；填 行 列 数字（1—4）；清除 行 列；检查；提示；撤销；状态；结束。每行、列、2×2宫都须有1—4。原创生成、严格唯一解，按用户和会话独立存档。",
    "s_nonogram": "5×5数织：/数织 开始；涂黑 行 列；标空 行 列；清除 行 列；检查；提示；撤销；状态；结束。行列线索表示连续黑格长度，连续组间至少一空格；0表示全空。?未知、■黑、×空。原创生成且唯一解。",
    "s_expedition": "原创口袋远征：/远征 开始；探索；休整；使用 药包；背包；状态；结束。携带邮袋抵达第8站灯塔即获胜，生命归零失败。每次探索耗2体力；休整耗1口粮，回4体力和1生命；药包回4生命。无网络、模型或真实奖品。",
}


class RuleError(ValueError):
    pass


class SearchLimit(RuleError):
    pass


def need(condition, message):
    if not condition:
        raise RuleError(message)


def identity(context):
    need(isinstance(context, dict), "缺少可信身份。")
    uid, sid = context.get("user_id"), context.get("session_id")
    need(isinstance(uid, str) and re.fullmatch(r"[1-9][0-9]{0,19}", uid), "用户身份无效。")
    need(isinstance(sid, str) and re.fullmatch(r"[pg]:[1-9][0-9]{0,19}", sid), "会话身份无效。")
    need(not sid.startswith("p:") or sid == "p:" + uid, "私聊身份不匹配。")
    return sid, uid


def linked(path):
    if path.is_symlink():
        return True
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)) or (stat.S_ISREG(info.st_mode) and info.st_nlink > 1)


def connect(context):
    raw = context.get("data_dir")
    need(isinstance(raw, str) and Path(raw).is_absolute(), "缺少内核分配的绝对数据目录。")
    root = Path(raw)
    need(not linked(root), "拒绝链接数据目录。")
    root.mkdir(parents=True, exist_ok=True)
    target = root / "puzzles.sqlite3"
    for suffix in ("", "-journal", "-wal", "-shm"):
        need(not linked(Path(str(target) + suffix)), "拒绝链接状态文件。")
    db = sqlite3.connect(target, timeout=3, isolation_level=None)
    try:
        db.execute("PRAGMA busy_timeout=3000")
        db.execute("PRAGMA max_page_count=16384")
        db.execute("CREATE TABLE IF NOT EXISTS games (pid TEXT NOT NULL,sid TEXT NOT NULL,uid TEXT NOT NULL,body TEXT NOT NULL,updated REAL NOT NULL,PRIMARY KEY(pid,sid,uid))")
        target.chmod(0o600)
        return db
    except Exception:
        db.close()
        raise


def valid_sudoku(board):
    if not isinstance(board, list) or len(board) != 16 or any(type(x) is not int or not 0 <= x <= 4 for x in board):
        return False
    groups = [[4 * r + c for c in range(4)] for r in range(4)]
    groups += [[4 * r + c for r in range(4)] for c in range(4)]
    groups += [[4 * (r + dr) + c + dc for dr in range(2) for dc in range(2)] for r in (0, 2) for c in (0, 2)]
    return all(len([board[i] for i in group if board[i]]) == len({board[i] for i in group if board[i]}) for group in groups)


def solve_sudoku(board, limit=2, max_nodes=5000):
    """Return at most two solutions by default; node budget is always enforced."""
    if not valid_sudoku(board):
        return []
    need(type(limit) is int and 1 <= limit <= 2 and type(max_nodes) is int and 1 <= max_nodes <= 100000, "求解参数无效。")
    work, solutions, nodes = list(board), [], 0

    def visit():
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes:
            raise SearchLimit("题目检查已达计算上限，请重新开始。")
        best, options = None, None
        for i, value in enumerate(work):
            if value:
                continue
            r, c = divmod(i, 4)
            occupied = set(work[r * 4:r * 4 + 4]) | {work[rr * 4 + c] for rr in range(4)} | {work[rr * 4 + cc] for rr in range(r // 2 * 2, r // 2 * 2 + 2) for cc in range(c // 2 * 2, c // 2 * 2 + 2)}
            possible = [n for n in range(1, 5) if n not in occupied]
            if not possible:
                return
            if options is None or len(possible) < len(options):
                best, options = i, possible
        if best is None:
            solutions.append(work[:])
            return
        for value in options:
            work[best] = value
            visit()
            if len(solutions) >= limit:
                break
        work[best] = 0

    visit()
    return solutions


def new_sudoku():
    digits = list(range(1, 5))
    RNG.shuffle(digits)
    bands, stacks = [0, 1], [0, 1]
    RNG.shuffle(bands)
    RNG.shuffle(stacks)
    rows, cols = [], []
    for band in bands:
        inside = [0, 1]
        RNG.shuffle(inside)
        rows.extend(band * 2 + x for x in inside)
    for stack in stacks:
        inside = [0, 1]
        RNG.shuffle(inside)
        cols.extend(stack * 2 + x for x in inside)
    solution = [digits[(r * 2 + r // 2 + c) % 4] for r in rows for c in cols]
    puzzle, positions = solution[:], list(range(16))
    RNG.shuffle(positions)
    for i in positions:
        if sum(bool(v) for v in puzzle) <= 6:
            break
        old, puzzle[i] = puzzle[i], 0
        if len(solve_sudoku(puzzle)) != 1:
            puzzle[i] = old
    return {"version": 1, "phase": "playing", "puzzle": puzzle, "solution": solution, "board": puzzle[:], "history": [], "moves": 0, "hints": 0}


def runs(values):
    result, count = [], 0
    for value in values:
        if value:
            count += 1
        elif count:
            result.append(count)
            count = 0
    if count:
        result.append(count)
    return result or [0]


def nonogram_clues(board):
    need(isinstance(board, list) and len(board) == 25 and all(type(v) is int and v in (0, 1) for v in board), "数织棋盘无效。")
    return [runs(board[r * 5:r * 5 + 5]) for r in range(5)], [runs([board[r * 5 + c] for r in range(5)]) for c in range(5)]


def solve_nonogram(row_clues, col_clues, limit=2, max_nodes=8000):
    need(type(limit) is int and 1 <= limit <= 2 and type(max_nodes) is int and 1 <= max_nodes <= 100000, "求解参数无效。")
    need(isinstance(row_clues, list) and isinstance(col_clues, list) and len(row_clues) == len(col_clues) == 5, "数织线索无效。")
    patterns = [[(n >> c) & 1 for c in range(5)] for n in range(32)]
    for clue in row_clues + col_clues:
        need(isinstance(clue, list) and 1 <= len(clue) <= 3 and all(type(n) is int and 0 <= n <= 5 for n in clue), "数织线索无效。")
    row_options = [[p for p in patterns if runs(p) == clue] for clue in row_clues]
    col_options = [[p for p in patterns if runs(p) == clue] for clue in col_clues]
    solutions, work, nodes = [], [], 0

    def visit(row):
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes:
            raise SearchLimit("题目检查已达计算上限，请重新开始。")
        if row == 5:
            solutions.append([v for line in work for v in line])
            return
        for candidate in row_options[row]:
            work.append(candidate)
            possible = all(any(all(pattern[r] == work[r][c] for r in range(row + 1)) for pattern in col_options[c]) for c in range(5))
            if possible:
                visit(row + 1)
            work.pop()
            if len(solutions) >= limit:
                return

    visit(0)
    return solutions


def new_nonogram():
    # All pictures are generated here. No third-party puzzle or image is bundled.
    solution = None
    for _ in range(30):
        candidate = [RNG.randrange(2) for _ in range(25)]
        if not 5 <= sum(candidate) <= 20:
            continue
        row_clues, col_clues = nonogram_clues(candidate)
        try:
            unique = len(solve_nonogram(row_clues, col_clues)) == 1
        except SearchLimit:
            continue
        if unique:
            solution = candidate
            break
    if solution is None:
        # An original, provably unique frame is a bounded-generation fallback.
        solution = [int(r in (0, 4) or c in (0, 4)) for r in range(5) for c in range(5)]
    row_clues, col_clues = nonogram_clues(solution)
    need(len(solve_nonogram(row_clues, col_clues)) == 1, "暂时无法生成唯一题目，请重试。")
    return {"version": 1, "phase": "playing", "solution": solution, "board": [-1] * 25, "row_clues": row_clues, "col_clues": col_clues, "history": [], "moves": 0, "hints": 0}


def new_expedition():
    return {"version": 1, "phase": "playing", "distance": 0, "health": 12, "energy": 8, "food": 4, "medicine": 1, "mail": 0, "moves": 0, "log": ["你从雾港出发，准备沿旧邮路把邮袋送到第8站灯塔。"]}


def integer(value, low, high):
    need(isinstance(value, str) and re.fullmatch(r"[0-9]{1,3}", value), "请输入有效整数。")
    result = int(value)
    need(low <= result <= high, f"数字范围为{low}—{high}。")
    return result


def coordinates(args, size, count=2):
    need(len(args) == count, f"请填写{'行 列 数字' if count == 3 else '行 列'}。")
    r, c = (integer(args[i], 1, size) - 1 for i in range(2))
    return r * size + c


def active(game):
    need(game is not None, "还没有存档，请先发送 开始。")
    need(game["phase"] == "playing", "本局已结束，可查看状态或重新开始。")


def validate_game(pid, game):
    need(isinstance(game, dict) and game.get("version") == 1 and game.get("phase") in ("playing", "won", "lost", "quit"), "状态数据异常。")
    need(type(game.get("moves")) is int and 0 <= game["moves"] <= 10000, "状态数据异常。")
    if pid == "s_expedition":
        for key, low, high in (("distance", 0, 8), ("health", 0, 12), ("energy", 0, 8), ("food", 0, 8), ("medicine", 0, 1), ("mail", 0, 8)):
            need(type(game.get(key)) is int and low <= game[key] <= high, "状态数据异常。")
        need(isinstance(game.get("log"), list) and len(game["log"]) <= 8 and all(isinstance(x, str) and len(x) <= 300 for x in game["log"]), "状态数据异常。")
        return
    size, values = (16, (0, 1, 2, 3, 4)) if pid == "s_sudoku" else (25, (-1, 0, 1))
    need(isinstance(game.get("board"), list) and len(game["board"]) == size and all(type(v) is int and v in values for v in game["board"]), "状态数据异常。")
    need(isinstance(game.get("history"), list) and len(game["history"]) <= 80 and all(isinstance(b, list) and len(b) == size and all(type(v) is int and v in values for v in b) for b in game["history"]), "状态数据异常。")
    need(type(game.get("hints")) is int and 0 <= game["hints"] <= 10000, "状态数据异常。")
    solution = game.get("solution")
    if pid == "s_sudoku":
        need(valid_sudoku(game["board"]) and valid_sudoku(solution) and all(solution), "状态数据异常。")
        need(valid_sudoku(game.get("puzzle")) and all(not v or v == solution[i] for i, v in enumerate(game["puzzle"])) and all(not v or game["board"][i] == v for i, v in enumerate(game["puzzle"])), "状态数据异常。")
    else:
        rows, cols = nonogram_clues(solution)
        need(rows == game.get("row_clues") and cols == game.get("col_clues"), "状态数据异常。")


def show(pid, game):
    need(game is not None, "还没有存档，请先发送 开始。")
    phase = {"playing": "进行中", "won": "胜利", "lost": "失败", "quit": "已结束"}[game["phase"]]
    if pid == "s_expedition":
        return f"雾港邮路｜{phase}\n路程 {game['distance']}/8站｜邮袋 {game['mail']}/3\n生命 {game['health']}/12｜体力 {game['energy']}/8\n口粮 {game['food']}｜药包 {game['medicine']}｜行动 {game['moves']}\n最近：" + "\n".join(game["log"][-3:])
    if pid == "s_sudoku":
        rows = [" ".join(str(v) if v else "·" for v in game["board"][r * 4:r * 4 + 4]) for r in range(4)]
        return f"4×4数独｜{phase}｜步数 {game['moves']}｜提示 {game['hints']}\n" + "\n".join(f"{i + 1}  {row}" for i, row in enumerate(rows)) + "\n列 1 2 3 4；每个2×2宫也不得重复。"
    rows = [" ".join({-1: "?", 0: "×", 1: "■"}[v] for v in game["board"][r * 5:r * 5 + 5]) + "  | " + ",".join(map(str, game["row_clues"][r])) for r in range(5)]
    return f"5×5数织｜{phase}｜步数 {game['moves']}｜提示 {game['hints']}\n" + "\n".join(f"{i + 1}  {row}" for i, row in enumerate(rows)) + "\n列线索：" + "；".join(f"{c + 1}:" + ",".join(map(str, clue)) for c, clue in enumerate(game["col_clues"]))


def remember(game):
    game["history"].append(game["board"][:])
    game["history"] = game["history"][-80:]


def board_command(pid, command, args, game):
    active(game)
    if command == "检查":
        need(not args, "检查不接受参数。")
        empty = 0 if pid == "s_sudoku" else -1
        errors = [f"{i // (4 if pid == 's_sudoku' else 5) + 1},{i % (4 if pid == 's_sudoku' else 5) + 1}" for i, v in enumerate(game["board"]) if v != empty and v != game["solution"][i]]
        return ("有误的格子：" + "、".join(errors) if errors else "已填格子均正确；继续补齐所有未知格。"), False
    if command == "撤销":
        need(not args and game["history"], "没有可撤销操作，撤销也不接受参数。")
        game["board"] = game["history"].pop()
    elif command == "提示":
        need(not args, "提示不接受参数。")
        if pid == "s_sudoku":
            need(all(not v or v == game["solution"][i] for i, v in enumerate(game["board"])), "已有填错格，请先检查并清除，再请求提示。")
        empty = 0 if pid == "s_sudoku" else -1
        i = next((i for i, v in enumerate(game["board"]) if v == empty or v != game["solution"][i]), None)
        need(i is not None, "棋盘已完成。")
        remember(game)
        game["board"][i] = game["solution"][i]
        game["hints"] += 1
    else:
        allowed = ("填", "清除") if pid == "s_sudoku" else ("涂黑", "标空", "清除")
        need(command in allowed, "未知操作，请发送不带参数的插件指令查看帮助。")
        size = 4 if pid == "s_sudoku" else 5
        i = coordinates(args, size, 3 if command == "填" else 2)
        if pid == "s_sudoku":
            need(not game["puzzle"][i], "初始题目格不能修改。")
            value = integer(args[2], 1, 4) if command == "填" else 0
        else:
            value = {"涂黑": 1, "标空": 0, "清除": -1}[command]
        need(game["board"][i] != value, "这一格已经是该值，本次未修改。")
        board = game["board"][:]
        board[i] = value
        need(pid != "s_sudoku" or valid_sudoku(board), "与已有行、列或宫的数字冲突，本次未修改。")
        remember(game)
        game["board"] = board
    game["moves"] += 1
    if game["board"] == game["solution"]:
        game["phase"] = "won"
    return show(pid, game), True


EXPEDITION_EVENTS = ("clear", "supply", "rough", "breeze", "stairs")


def expedition_command(command, args, game):
    active(game)
    need(game["moves"] < 10000, "行动次数已达上限，请结束本局。")
    if command == "探索":
        need(not args, "探索不接受参数。")
        need(game["energy"] >= 2, "体力不足2，请休整补充；若没有口粮只能结束本次远征。")
        game["energy"] -= 2
        game["distance"] += 1
        if game["distance"] in (2, 4, 6):
            game["mail"] += 1
            checkpoint = "你在路边邮亭接到一份待送邮袋。"
        else:
            checkpoint = ""
        event = RNG.choice(EXPEDITION_EVENTS)
        if event == "rough":
            game["health"] = max(0, game["health"] - 2)
            message = "海雾遮住碎石坡，你擦伤了膝盖，生命-2。"
        elif event == "stairs":
            game["health"] = max(0, game["health"] - 3)
            message = "旧台阶突然松动，你跌进草丛，生命-3。"
        elif event == "supply":
            game["food"] = min(8, game["food"] + 1)
            message = "废弃驿站留着一份完好的口粮，你收进背包。"
        elif event == "breeze":
            game["energy"] = min(8, game["energy"] + 1)
            message = "顺风推着你穿过长堤，少费了些力气，体力+1。"
        else:
            message = "你沿海岸石标顺利前行，远处的灯光更清楚了。"
        game["log"].append(f"第{game['distance']}站：{checkpoint}{message}")
        if game["health"] == 0:
            game["phase"] = "lost"
            game["log"].append("你已无力继续，被路过的巡堤人接回雾港。本次远征失败，可以重新开始。")
        elif game["distance"] == 8:
            game["phase"] = "won" if game["mail"] >= 3 else "lost"
            game["log"].append("灯塔管理员签收了邮袋，你完成了雾港邮路，远征胜利！" if game["phase"] == "won" else "到达终点却没有足够邮袋，本次远征失败。")
    elif command == "休整":
        need(not args, "休整不接受参数。")
        need(game["food"] > 0, "没有口粮，无法休整。")
        need(game["energy"] < 8 or game["health"] < 12, "状态已满，无需消耗口粮。")
        game["food"] -= 1
        game["energy"] = min(8, game["energy"] + 4)
        game["health"] = min(12, game["health"] + 1)
        game["log"].append("你在挡风墙边吃了一份口粮，体力恢复4、生命恢复1（不超过上限）。")
    elif command == "使用":
        need(args == ["药包"], "可使用的道具：药包。口粮通过 休整 使用。")
        need(game["medicine"] > 0, "背包中没有药包。")
        need(game["health"] < 12, "生命已满，无需使用药包。")
        game["medicine"] -= 1
        game["health"] = min(12, game["health"] + 4)
        game["log"].append("你用掉药包包扎伤口，生命恢复4（不超过12）。")
    else:
        raise RuleError("未知操作，请发送 /远征 查看帮助。")
    game["moves"] += 1
    game["log"] = game["log"][-8:]
    return show("s_expedition", game), True


def handle(pid, text, context):
    db = None
    try:
        need(pid in PREFIXES, "未知游戏。")
        sid, uid = identity(context)
        need(isinstance(text, str) and 0 < len(text) <= 180 and not any(ord(c) < 32 or ord(c) == 127 for c in text), "指令过长或含控制字符。")
        words = text.strip().split()
        need(words and words[0] == PREFIXES[pid], "指令前缀不匹配。")
        command = words[1] if len(words) > 1 else "帮助"
        args = words[2:]
        if command in ("帮助", "help"):
            need(not args, "帮助不接受参数。")
            return HELP[pid]
        db = connect(context)
        db.execute("BEGIN IMMEDIATE")
        now = time.time()
        row = db.execute("SELECT body,updated FROM games WHERE pid=? AND sid=? AND uid=?", (pid, sid, uid)).fetchone()
        game = None
        if row and row[1] >= now - RETENTION_SECONDS:
            need(len(row[0].encode("utf-8")) <= MAX_STATE_BYTES, "状态数据异常。")
            game = json.loads(row[0])
            validate_game(pid, game)
        changed = False
        if command == "开始":
            need(not args, "开始不接受参数。")
            need(game is None or game["phase"] != "playing", "你已有进行中的存档，请先 结束。")
            count = db.execute("SELECT count(*) FROM games WHERE updated>=?", (now - RETENTION_SECONDS,)).fetchone()[0]
            need(game is not None or count < MAX_RECORDS, "存档容量已满，请管理员处理本插件过期存档。")
            game = {"s_sudoku": new_sudoku, "s_nonogram": new_nonogram, "s_expedition": new_expedition}[pid]()
            result, changed = show(pid, game), True
        elif command in ("状态", "背包"):
            need(not args, "状态/背包不接受参数。")
            need(command != "背包" or pid == "s_expedition", "此游戏没有背包，请使用 状态。")
            result = show(pid, game)
        elif command == "结束":
            need(not args, "结束不接受参数。")
            active(game)
            game["phase"] = "quit"
            result, changed = show(pid, game), True
        else:
            active(game)
            need(game["moves"] < 10000, "行动次数已达上限，请结束本局。")
            result, changed = expedition_command(command, args, game) if pid == "s_expedition" else board_command(pid, command, args, game)
        if changed:
            validate_game(pid, game)
            body = json.dumps(game, ensure_ascii=False, separators=(",", ":"))
            need(len(body.encode("utf-8")) <= MAX_STATE_BYTES, "存档过大，本次操作未保存。")
            # Expiry housekeeping occurs only after a valid mutation and rolls back with it.
            db.execute("DELETE FROM games WHERE updated<?", (now - RETENTION_SECONDS,))
            db.execute("INSERT INTO games VALUES(?,?,?,?,?) ON CONFLICT(pid,sid,uid) DO UPDATE SET body=excluded.body,updated=excluded.updated", (pid, sid, uid, body, now))
        db.commit()
        return result
    except RuleError as exc:
        if db:
            db.rollback()
        return str(exc)
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, OverflowError, StopIteration):
        if db:
            db.rollback()
        return "状态暂不可用，请管理员检查插件数据；本次操作未保存。"
    finally:
        if db:
            db.close()
