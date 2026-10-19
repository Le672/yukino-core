import json
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import puzzles


class PuzzleTests(unittest.TestCase):
    def setUp(self):
        # Windows AppContainer may expose a host TEMP alias that SQLite cannot
        # reopen. Keep disposable test state beside this test, under the checkout.
        self.temp = tempfile.TemporaryDirectory(prefix="puzzle-test-", dir=Path(__file__).resolve().parent)
        self.root = Path(self.temp.name).resolve()
        self.context = {"data_dir": str(self.root), "user_id": "111", "session_id": "g:222", "settings": {}}
        self.rng_patch = patch.object(puzzles, "RNG", random.Random(42))
        self.rng_patch.start()

    def tearDown(self):
        self.rng_patch.stop()
        self.temp.cleanup()

    def call(self, pid, action="", context=None):
        prefix = puzzles.PREFIXES[pid]
        return puzzles.handle(pid, prefix + (" " + action if action else ""), context or self.context)

    def state(self, pid, context=None):
        ctx = context or self.context
        with closing(sqlite3.connect(self.root / "puzzles.sqlite3")) as db, db:
            row = db.execute("SELECT body FROM games WHERE pid=? AND sid=? AND uid=?", (pid, ctx["session_id"], ctx["user_id"])).fetchone()
        return json.loads(row[0]) if row else None

    def snapshot(self):
        with closing(sqlite3.connect(self.root / "puzzles.sqlite3")) as db, db:
            return db.execute("SELECT * FROM games ORDER BY pid,sid,uid").fetchall()

    def test_help_has_no_database_or_model_dependency(self):
        for pid in puzzles.PREFIXES:
            self.assertIn("开始", self.call(pid))
        self.assertFalse((self.root / "puzzles.sqlite3").exists())

    def test_identity_and_private_scope_validation(self):
        invalid = [dict(self.context, user_id="0"), dict(self.context, user_id=111), dict(self.context, user_id="111x"), dict(self.context, session_id="g:abc"), dict(self.context, session_id="p:333")]
        for ctx in invalid:
            self.assertIn("身份", self.call("s_sudoku", "开始", ctx))
        self.assertFalse((self.root / "puzzles.sqlite3").exists())
        valid = dict(self.context, session_id="p:111")
        self.assertIn("4×4数独", self.call("s_sudoku", "开始", valid))

    def test_invalid_controls_and_prefix_are_rejected_before_state_io(self):
        for text in ("/数独evil 开始", "/数独\x00开始", "/数独 开始\n", "/数独 " + "x" * 181):
            result = puzzles.handle("s_sudoku", text, self.context)
            self.assertNotIn("进行中", result)
        self.assertFalse((self.root / "puzzles.sqlite3").exists())

    def test_relative_directory_is_rejected(self):
        self.assertIn("绝对", self.call("s_sudoku", "开始", dict(self.context, data_dir="relative-state")))

    def test_linked_state_file_is_rejected_without_touching_target(self):
        outside = self.root / "target.bin"
        outside.write_bytes(b"KEEP THIS FILE")
        target = self.root / "puzzles.sqlite3"
        try:
            os.link(outside, target)
        except OSError as exc:
            self.skipTest(f"Hard links unavailable: {exc}")
        self.assertIn("拒绝链接", self.call("s_sudoku", "开始"))
        self.assertEqual(outside.read_bytes(), b"KEEP THIS FILE")

    def test_sudoku_generator_is_legal_unique_and_has_immutable_clues(self):
        seen = set()
        for _ in range(20):
            game = puzzles.new_sudoku()
            self.assertTrue(puzzles.valid_sudoku(game["solution"]))
            self.assertNotIn(0, game["solution"])
            self.assertGreaterEqual(sum(bool(v) for v in game["puzzle"]), 6)
            self.assertEqual(puzzles.solve_sudoku(game["puzzle"]), [game["solution"]])
            seen.add(tuple(game["puzzle"]))
        self.assertGreater(len(seen), 1)

    def test_sudoku_solver_detects_multiple_invalid_and_budget(self):
        self.assertEqual(len(puzzles.solve_sudoku([0] * 16)), 2)
        self.assertEqual(puzzles.solve_sudoku([1, 1] + [0] * 14), [])
        self.assertEqual(puzzles.solve_sudoku([True] + [0] * 15), [])
        with self.assertRaises(puzzles.SearchLimit):
            puzzles.solve_sudoku([0] * 16, max_nodes=1)

    def test_sudoku_invalid_mutations_leave_rows_and_timestamps_unchanged(self):
        self.call("s_sudoku", "开始")
        original = self.snapshot()
        game = self.state("s_sudoku")
        fixed = next(i for i, n in enumerate(game["puzzle"]) if n)
        actions = ["填 0 2 1", "填 1 1 5", "填 1 1", "清除 -1 2", "填 1 1 1 extra", "开始", "结束 extra", "未知", "状态 222", f"清除 {fixed // 4 + 1} {fixed % 4 + 1}"]
        for action in actions:
            self.call("s_sudoku", action)
            self.assertEqual(self.snapshot(), original, action)
        self.call("s_sudoku", "检查")
        self.assertEqual(self.snapshot(), original)

    def test_sudoku_rejects_row_column_or_box_conflicts(self):
        self.call("s_sudoku", "开始")
        game = self.state("s_sudoku")
        for i, value in enumerate(game["board"]):
            if value:
                continue
            for number in range(1, 5):
                board = game["board"][:]
                board[i] = number
                if not puzzles.valid_sudoku(board):
                    before = self.snapshot()
                    result = self.call("s_sudoku", f"填 {i // 4 + 1} {i % 4 + 1} {number}")
                    self.assertIn("冲突", result)
                    self.assertEqual(self.snapshot(), before)
                    return
        self.fail("Generated puzzle did not expose a conflict test case")

    def test_sudoku_clear_undo_and_persistent_victory(self):
        self.call("s_sudoku", "开始")
        initial = self.state("s_sudoku")
        i = next(i for i, n in enumerate(initial["board"]) if not n)
        r, c, n = i // 4 + 1, i % 4 + 1, initial["solution"][i]
        self.call("s_sudoku", f"填 {r} {c} {n}")
        self.call("s_sudoku", f"清除 {r} {c}")
        self.call("s_sudoku", "撤销")
        self.assertEqual(self.state("s_sudoku")["board"][i], n)
        for _ in range(16):
            if self.state("s_sudoku")["phase"] == "won":
                break
            self.call("s_sudoku", "提示")
        self.assertEqual(self.state("s_sudoku")["phase"], "won")
        self.assertIn("胜利", self.call("s_sudoku", "状态"))
        before = self.snapshot()
        self.assertIn("已结束", self.call("s_sudoku", "提示"))
        self.assertEqual(self.snapshot(), before)
        self.assertIn("进行中", self.call("s_sudoku", "开始"))

    def test_nonogram_generated_puzzles_are_unique_and_not_downloaded(self):
        seen = set()
        for _ in range(16):
            game = puzzles.new_nonogram()
            self.assertEqual(puzzles.nonogram_clues(game["solution"]), (game["row_clues"], game["col_clues"]))
            self.assertEqual(puzzles.solve_nonogram(game["row_clues"], game["col_clues"]), [game["solution"]])
            seen.add(tuple(game["solution"]))
        self.assertGreater(len(seen), 1)

    def test_nonogram_solver_reports_ambiguity_impossibility_and_budget(self):
        one_each = [[1] for _ in range(5)]
        self.assertEqual(len(puzzles.solve_nonogram(one_each, one_each)), 2)
        self.assertEqual(puzzles.solve_nonogram([[5]] * 5, [[0]] * 5), [])
        with self.assertRaises(puzzles.SearchLimit):
            puzzles.solve_nonogram(one_each, one_each, max_nodes=1)

    def test_nonogram_bounded_generation_falls_back_to_unique_frame(self):
        with patch.object(puzzles.RNG, "randrange", return_value=0):
            game = puzzles.new_nonogram()
        self.assertEqual(sum(game["solution"]), 16)
        self.assertEqual(puzzles.solve_nonogram(game["row_clues"], game["col_clues"]), [game["solution"]])

    def test_nonogram_wrong_mark_check_clear_undo_and_full_victory(self):
        self.call("s_nonogram", "开始")
        game = self.state("s_nonogram")
        wrong = "标空" if game["solution"][0] else "涂黑"
        self.call("s_nonogram", f"{wrong} 1 1")
        before = self.snapshot()
        self.assertIn("1,1", self.call("s_nonogram", "检查"))
        self.assertEqual(self.snapshot(), before)
        self.call("s_nonogram", "清除 1 1")
        self.assertEqual(self.state("s_nonogram")["board"][0], -1)
        self.call("s_nonogram", "撤销")
        self.assertEqual(self.state("s_nonogram")["board"][0], 1 - game["solution"][0])
        for i, value in enumerate(game["solution"]):
            self.call("s_nonogram", f"{'涂黑' if value else '标空'} {i // 5 + 1} {i % 5 + 1}")
        self.assertEqual(self.state("s_nonogram")["phase"], "won")
        self.assertIn("胜利", self.call("s_nonogram", "状态"))

    def test_nonogram_invalid_input_does_not_change_state(self):
        self.call("s_nonogram", "开始")
        before = self.snapshot()
        for action in ("涂黑 6 1", "标空 0 0", "清除 1 1", "提示 extra", "涂黑 1 1 2", "背包", "撤销"):
            self.call("s_nonogram", action)
            self.assertEqual(self.snapshot(), before, action)

    def test_expedition_resource_management_and_victory(self):
        self.call("s_expedition", "开始")
        with patch.object(puzzles.RNG, "choice", return_value="clear"):
            for _ in range(8):
                if self.state("s_expedition")["energy"] < 2:
                    self.call("s_expedition", "休整")
                self.call("s_expedition", "探索")
        game = self.state("s_expedition")
        self.assertEqual((game["phase"], game["distance"], game["mail"]), ("won", 8, 3))
        self.assertLess(game["food"], 4)
        self.assertIn("远征胜利", self.call("s_expedition", "状态"))
        self.assertLessEqual(len(game["log"]), 8)
        before = self.snapshot()
        self.call("s_expedition", "探索")
        self.assertEqual(self.snapshot(), before)

    def test_expedition_defeat_medicine_and_restart(self):
        self.call("s_expedition", "开始")
        with patch.object(puzzles.RNG, "choice", return_value="stairs"):
            self.call("s_expedition", "探索")
            self.assertEqual(self.state("s_expedition")["health"], 9)
            self.call("s_expedition", "使用 药包")
            self.assertEqual((self.state("s_expedition")["health"], self.state("s_expedition")["medicine"]), (12, 0))
            self.call("s_expedition", "探索")
            self.call("s_expedition", "探索")
            self.call("s_expedition", "探索")
            self.call("s_expedition", "休整")
            self.call("s_expedition", "探索")
            self.call("s_expedition", "探索")
        self.assertEqual(self.state("s_expedition")["phase"], "lost")
        self.assertIn("失败", self.call("s_expedition", "状态"))
        self.call("s_expedition", "开始")
        self.assertEqual(self.state("s_expedition")["distance"], 0)

    def test_expedition_invalid_and_insufficient_resource_operations_are_atomic(self):
        self.call("s_expedition", "开始")
        before = self.snapshot()
        for action in ("休整", "使用 药包", "使用 口粮", "探索 extra", "使用", "背包 222", "开始"):
            self.call("s_expedition", action)
            self.assertEqual(self.snapshot(), before, action)
        with patch.object(puzzles.RNG, "choice", return_value="clear"):
            for _ in range(4):
                self.call("s_expedition", "探索")
        before = self.snapshot()
        self.assertIn("体力不足", self.call("s_expedition", "探索"))
        self.assertEqual(self.snapshot(), before)

    def test_different_users_sessions_and_plugins_never_share_progress(self):
        second_user = dict(self.context, user_id="333")
        second_group = dict(self.context, session_id="g:444")
        for ctx in (self.context, second_user, second_group):
            self.call("s_expedition", "开始", ctx)
        self.call("s_sudoku", "开始")
        original_other_user = self.state("s_expedition", second_user)
        original_other_group = self.state("s_expedition", second_group)
        with patch.object(puzzles.RNG, "choice", return_value="clear"):
            self.call("s_expedition", "探索")
        self.call("s_expedition", "结束")
        self.assertEqual(self.state("s_expedition", second_user), original_other_user)
        self.assertEqual(self.state("s_expedition", second_group), original_other_group)
        self.assertEqual(self.state("s_sudoku")["phase"], "playing")
        self.assertEqual(len(self.snapshot()), 4)

    def test_capacity_existing_players_allowed_new_players_rejected(self):
        self.call("s_expedition", "开始")
        self.call("s_sudoku", "开始")
        with patch.object(puzzles, "MAX_RECORDS", 2):
            before = self.snapshot()
            self.assertIn("容量已满", self.call("s_nonogram", "开始"))
            self.assertEqual(self.snapshot(), before)
            with patch.object(puzzles.RNG, "choice", return_value="clear"):
                self.assertIn("第1站", self.call("s_expedition", "探索"))

    def test_expired_own_row_does_not_bypass_capacity(self):
        self.call("s_sudoku", "开始")
        with closing(sqlite3.connect(self.root / "puzzles.sqlite3")) as db, db:
            db.execute("UPDATE games SET updated=?", (time.time() - puzzles.RETENTION_SECONDS - 100,))
        self.call("s_expedition", "开始")
        self.call("s_nonogram", "开始")
        with patch.object(puzzles, "MAX_RECORDS", 2):
            before = self.snapshot()
            self.assertIn("容量已满", self.call("s_sudoku", "开始"))
            self.assertEqual(self.snapshot(), before)

    def test_expiry_cleanup_only_after_successful_mutation(self):
        self.call("s_sudoku", "开始")
        with closing(sqlite3.connect(self.root / "puzzles.sqlite3")) as db, db:
            db.execute("UPDATE games SET updated=?", (time.time() - puzzles.RETENTION_SECONDS - 100,))
        before = self.snapshot()
        self.call("s_sudoku", "状态")
        self.call("s_sudoku", "填 1 1 2")
        self.assertEqual(self.snapshot(), before)
        self.call("s_expedition", "开始")
        self.assertEqual(len(self.snapshot()), 1)
        self.assertIsNone(self.state("s_sudoku"))

    def test_corrupt_saved_state_is_reported_and_not_overwritten(self):
        self.call("s_expedition", "开始")
        with closing(sqlite3.connect(self.root / "puzzles.sqlite3")) as db, db:
            db.execute("UPDATE games SET body=?", ('{"version":1,"phase":"playing","moves":0,"distance":99999}',))
        before = self.snapshot()
        self.assertIn("状态数据异常", self.call("s_expedition", "探索"))
        self.assertEqual(self.snapshot(), before)

    def test_saved_progress_survives_a_new_python_process(self):
        self.call("s_expedition", "开始")
        with patch.object(puzzles.RNG, "choice", return_value="clear"):
            self.call("s_expedition", "探索")
        script = "import json,sys;sys.path.insert(0,sys.argv[1]);import puzzles;print(puzzles.handle('s_expedition','/远征 状态',json.loads(sys.argv[2])))"
        result = subprocess.run([sys.executable, "-c", script, str(Path(puzzles.__file__).parent), json.dumps(self.context)], capture_output=True, text=True, encoding="utf-8", timeout=10, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("路程 1/8站", result.stdout)

    def test_concurrent_actions_are_serialized_without_lost_updates(self):
        self.call("s_expedition", "开始")
        with patch.object(puzzles.RNG, "choice", return_value="clear"), ThreadPoolExecutor(max_workers=4) as pool:
            outputs = list(pool.map(lambda _: self.call("s_expedition", "探索"), range(4)))
        self.assertTrue(all("进行中" in output for output in outputs), outputs)
        game = self.state("s_expedition")
        self.assertEqual((game["distance"], game["energy"], game["moves"]), (4, 0, 4))


if __name__ == "__main__":
    unittest.main()
