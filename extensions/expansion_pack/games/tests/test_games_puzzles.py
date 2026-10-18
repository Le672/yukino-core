"""Meaningful deterministic invariants for the seven solo puzzle helpers."""
from collections import Counter, deque
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('puzzles_under_test', ROOT/'helper_puzzles.py')
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
CONTEXT = {'session_id': 'g:1', 'user_id': '2', 'settings': {}}


def call(kind, arg, state=None, settings=None):
    return p.run(kind, arg, state, {**CONTEXT, 'settings': settings or {}})


def reachable_paths(state):
    size = state['size']; paths = set(state['paths']); seen = {state['player']}; todo = deque(seen); predecessor = {}
    while todo:
        index = todo.popleft(); row, col = divmod(index, size)
        for direction, (dr, dc) in p.DIRECTIONS.items():
            r, c = row+dr, col+dc; target = r*size+c
            if 0 <= r < size and 0 <= c < size and target in paths and target not in seen:
                seen.add(target); predecessor[target] = (index, direction); todo.append(target)
    return seen, predecessor


def lights_consistent(state):
    n = len(state['board']); size = state['size']; rows = []
    for target, value in enumerate(state['board']):
        mask = 0
        for switch in range(n):
            board = [0]*n; p.toggle_lights(board, size, switch)
            if board[target]: mask |= 1 << switch
        rows.append(mask | (value << n))
    pivot = 0
    for col in range(n):
        choice = next((r for r in range(pivot, n) if rows[r] & (1 << col)), None)
        if choice is None: continue
        rows[pivot], rows[choice] = rows[choice], rows[pivot]
        for r in range(n):
            if r != pivot and rows[r] & (1 << col): rows[r] ^= rows[pivot]
        pivot += 1
    return not any((row & ((1 << n)-1)) == 0 and row & (1 << n) for row in rows)


class ContractTests(unittest.TestCase):
    pass


for item in p.CATALOG:
    def opening(self, entry=item):
        state, reply = call(entry['id'], '开始')
        self.assertIn(entry['expected_contains'], reply)
        self.assertFalse(state['done'])
        self.assertEqual(json.loads(json.dumps(state, ensure_ascii=False)), state)
        self.assertTrue(entry['config_schema'])
        returned, help_reply = call(entry['id'], '帮助', state)
        self.assertEqual(entry['usage'], help_reply); self.assertEqual(state, returned)
        returned, _ = call(entry['id'], '状态', state)
        self.assertEqual(state, returned)
    setattr(ContractTests, 'test_open_'+item['id'], opening)

    def rejection(self, entry=item):
        with self.assertRaises(ValueError): call(entry['id'], '不存在')
        state, _ = call(entry['id'], '开始'); before = deepcopy(state)
        with self.assertRaises(ValueError): call(entry['id'], '不存在', state)
        self.assertEqual(before, state)
        ended, _ = call(entry['id'], '投降', state)
        self.assertTrue(ended['done']); self.assertEqual(before, state)
    setattr(ContractTests, 'test_reject_'+item['id'], rejection)


class MinesTests(unittest.TestCase):
    def test_first_open_safe_even_with_all_other_cells_mined(self):
        for index in range(25):
            state, _ = call('x_minesweeper', '开始 5 24')
            row, col = divmod(index, 5)
            state, result = call('x_minesweeper', f'开 {row+1} {col+1}', state)
            self.assertNotIn(index, state['mines']); self.assertIn(index, state['opened'])
            self.assertEqual(len(state['mines']), 24); self.assertTrue(state['won'])

    def test_no_mines_generated_before_real_first_open(self):
        state, result = call('x_minesweeper', '开始')
        self.assertIsNone(state['mines']); self.assertNotIn('✹', result)
        state, _ = call('x_minesweeper', '旗 1 1', state)
        with self.assertRaises(ValueError): call('x_minesweeper', '开 1 1', state)
        self.assertIsNone(state['mines']); self.assertEqual(state['opened'], [])

    def test_flood_does_not_open_flags_or_count_them_for_win(self):
        state = dict(size=5, bombs=1, mines=[0], opened=[], flags=[1], done=False, won=False)
        state, _ = call('x_minesweeper', '开 5 5', state)
        self.assertNotIn(1, state['opened']); self.assertNotIn(0, state['opened']); self.assertFalse(state['won'])
        state, _ = call('x_minesweeper', '旗 1 2', state)
        state, result = call('x_minesweeper', '开 1 2', state)
        self.assertTrue(state['won']); self.assertEqual(len(state['opened']), 24)

    def test_adjacency_numbers_and_terminal_loss(self):
        state = dict(size=5, bombs=2, mines=[0, 2], opened=[], flags=[], done=False, won=False)
        state, result = call('x_minesweeper', '开 1 2', state)
        self.assertEqual(sum(n in state['mines'] for n in p.neighbours(1, 5)), 2)
        self.assertEqual(state['opened'], [1]); self.assertNotIn('✹', result)
        state, result = call('x_minesweeper', '开 1 1', state)
        self.assertTrue(state['done']); self.assertFalse(state['won']); self.assertIn('踩雷', result)
        with self.assertRaises(ValueError): call('x_minesweeper', '开 5 5', state)


class SlidingTests(unittest.TestCase):
    def test_random_boards_are_nontrivial_even_parity_permutations(self):
        for _ in range(40):
            state, _ = call('x_sliding', '开始')
            self.assertEqual(sorted(state['board']), list(range(9)))
            values = [x for x in state['board'] if x]
            inversions = sum(a > b for i, a in enumerate(values) for b in values[i+1:])
            self.assertEqual(inversions % 2, 0)
            self.assertNotEqual(state['board'], list(range(1, 9))+[0])

    def test_real_move_finishes_puzzle_and_invalid_move_preserves_state(self):
        state = dict(board=[1,2,3,4,5,6,7,0,8], steps=0, done=False, won=False); before = deepcopy(state)
        with self.assertRaises(ValueError): call('x_sliding', '移动 1', state)
        self.assertEqual(before, state)
        state, reply = call('x_sliding', '移动 8', state)
        self.assertTrue(state['won']); self.assertEqual(state['steps'], 1); self.assertIn('复原', reply)

    def test_direction_refers_to_empty_square(self):
        state = dict(board=[1,2,3,4,5,6,7,0,8], steps=0, done=False, won=False)
        state, _ = call('x_sliding', '上', state)
        self.assertEqual(state['board'].index(0), 4)


class MergeTests(unittest.TestCase):
    def test_each_tile_merges_once_per_move(self):
        self.assertEqual(p.merge_line([2,2,4,0]), ([4,4,0,0], 4))
        self.assertEqual(p.merge_line([4,4,4,4]), ([8,8,0,0], 16))
        self.assertEqual(p.merge_line([2,2,2,0]), ([4,2,0,0], 4))

    def test_directional_merge_conserves_sum_before_spawn(self):
        board = [2,2,4,0,4,0,4,0,8,8,8,8,0,2,0,2]
        for direction in p.DIRECTIONS:
            shifted, _ = p.shift_2048(board, direction)
            self.assertEqual(sum(board), sum(shifted))
        self.assertEqual(p.shift_2048([2,2,4,0]+[0]*12, '右')[0][:4], [0,0,4,4])

    def test_real_move_spawns_once_and_noop_spawns_nothing(self):
        state = dict(board=[2,2,4,0]+[0]*12, score=0, steps=0, target=2048, reached=False, done=False)
        with patch.object(p.RNG, 'choice', side_effect=lambda xs: xs[0]), patch.object(p.RNG, 'random', return_value=.5):
            updated, _ = call('x_2048', '左', state)
        self.assertEqual(sum(updated['board']), 10); self.assertEqual(updated['score'], 4); self.assertEqual(updated['steps'], 1)
        no_op = dict(board=[2,0,0,0]+[0]*12, score=0, steps=0, target=2048, reached=False, done=False); before = deepcopy(no_op)
        with self.assertRaises(ValueError): call('x_2048', '左', no_op)
        self.assertEqual(before, no_op)

    def test_full_checkerboard_has_no_moves_but_equal_neighbours_do(self):
        board = [2,4,2,4,4,2,4,2,2,4,2,4,4,2,4,2]
        self.assertFalse(p.can_2048_move(board)); board[1] = 2; self.assertTrue(p.can_2048_move(board))

    def test_target_configuration_changes_success_condition(self):
        state, _ = call('x_2048', '开始', settings={'target': 128})
        self.assertEqual(state['target'], 128)
        state['board'] = [64,64,0,0]+[0]*12
        state, reply = call('x_2048', '左', state)
        self.assertTrue(state['reached']); self.assertFalse(state['done']); self.assertIn('目标已达成', reply)


class LightsTests(unittest.TestCase):
    def test_random_generated_boards_have_a_gf2_solution(self):
        for size in (3,4,5):
            for _ in range(5):
                state, _ = call('x_lights', '开始', settings={'board_size': size})
                self.assertTrue(any(state['board'])); self.assertTrue(lights_consistent(state))

    def test_switch_twice_is_involution_and_real_single_switch_wins(self):
        board = [0]*9; p.toggle_lights(board, 3, 0)
        self.assertEqual(sum(board), 3); p.toggle_lights(board, 3, 0); self.assertEqual(sum(board), 0)
        with patch.object(p.RNG, 'randrange', return_value=0): state, _ = call('x_lights', '开始', settings={'board_size':3, 'shuffle_steps':1})
        state, reply = call('x_lights', '按 1 1', state)
        self.assertTrue(state['won']); self.assertIn('获胜', reply)

    def test_invalid_position_does_not_toggle(self):
        state, _ = call('x_lights', '开始'); before = deepcopy(state)
        with self.assertRaises(ValueError): call('x_lights', '按 0 1', state)
        self.assertEqual(before, state)


class SokobanTests(unittest.TestCase):
    def test_original_map_has_a_real_solution_and_undo(self):
        state, _ = call('x_sokoban', '开始')
        for direction in ('上','下','左','下','右','右'): state, reply = call('x_sokoban', direction, state)
        self.assertTrue(state['won']); self.assertEqual(set(state['boxes']), set(state['goals'])); self.assertIn('获胜', reply)
        state, _ = call('x_sokoban', '撤销', state)
        self.assertFalse(state['done']); self.assertEqual(state['steps'], 5)

    def test_cannot_push_box_into_wall_and_no_mutation_on_failure(self):
        state, _ = call('x_sokoban', '开始'); state, _ = call('x_sokoban', '上', state); before = deepcopy(state)
        with self.assertRaises(ValueError): call('x_sokoban', '上', state)
        self.assertEqual(before, state)

    def test_undo_bound_is_effective(self):
        state, _ = call('x_sokoban', '开始', settings={'undo_limit':1})
        state, _ = call('x_sokoban', '左', state); state, _ = call('x_sokoban', '右', state)
        self.assertEqual(len(state['history']), 1)
        state, _ = call('x_sokoban', '撤销', state)
        with self.assertRaises(ValueError): call('x_sokoban', '撤销', state)


class MazeTests(unittest.TestCase):
    def test_generated_exits_are_reachable_and_outer_walls_closed(self):
        for size in (7,9,15):
            for _ in range(8):
                state, _ = call('x_maze', '开始', settings={'board_size':size}); seen, _ = reachable_paths(state)
                self.assertIn(state['exit'], seen); self.assertEqual(seen, set(state['paths']))
                self.assertFalse(any(i//size in (0,size-1) or i%size in (0,size-1) for i in state['paths']))

    def test_follow_bfs_route_reaches_exit_and_ends(self):
        state, _ = call('x_maze', '开始'); _, parent = reachable_paths(state); route = []; cursor = state['exit']
        while cursor != state['player']: cursor, direction = parent[cursor]; route.append(direction)
        for direction in reversed(route): state, result = call('x_maze', direction, state)
        self.assertTrue(state['won']); self.assertEqual(state['steps'], len(route)); self.assertIn('出口', result)

    def test_cannot_walk_through_wall(self):
        state, _ = call('x_maze', '开始'); before = deepcopy(state)
        with self.assertRaises(ValueError): call('x_maze', '上', state)
        self.assertEqual(state, before)


class MemoryTests(unittest.TestCase):
    def test_start_and_status_do_not_disclose_hidden_cards(self):
        state, reply = call('x_memory', '开始')
        self.assertEqual(set(Counter(state['cards']).values()), {2})
        self.assertFalse(any(symbol in reply for symbol in p.MEMORY_SYMBOLS))
        state, reply = call('x_memory', '状态', state)
        self.assertFalse(any(symbol in reply for symbol in p.MEMORY_SYMBOLS))
        state, reply = call('x_memory', '翻 1', state)
        self.assertIn(state['cards'][0], reply)
        self.assertFalse(any(symbol in reply for symbol in p.MEMORY_SYMBOLS if symbol != state['cards'][0]))

    def test_mismatch_shows_only_two_then_status_hides_them(self):
        state, _ = call('x_memory', '开始'); other = next(i for i,v in enumerate(state['cards']) if v != state['cards'][0])
        state, reply = call('x_memory', f'翻 1 {other+1}', state)
        self.assertIn(state['cards'][0], reply); self.assertIn(state['cards'][other], reply); self.assertEqual(state['faceup'], []); self.assertEqual(state['matched'], [])
        _, status = call('x_memory', '状态', state)
        self.assertFalse(any(symbol in status for symbol in p.MEMORY_SYMBOLS))

    def test_all_pairs_can_be_matched_to_win(self):
        state, _ = call('x_memory', '开始', settings={'pairs':4})
        for symbol in set(state['cards']):
            indices = [i+1 for i,v in enumerate(state['cards']) if v == symbol]
            state, reply = call('x_memory', f'翻 {indices[0]} {indices[1]}', state)
        self.assertTrue(state['won']); self.assertEqual(state['turns'], 4); self.assertIn('获胜', reply)

    def test_duplicate_and_third_flip_rejected_without_state_changes(self):
        state, _ = call('x_memory', '开始'); before = deepcopy(state)
        with self.assertRaises(ValueError): call('x_memory', '翻 1 1', state)
        self.assertEqual(before, state); state, _ = call('x_memory', '翻 1', state); before = deepcopy(state)
        with self.assertRaises(ValueError): call('x_memory', '翻 2 3', state)
        self.assertEqual(before, state)

    def test_surrender_does_not_reveal_remaining_answers(self):
        state, _ = call('x_memory', '开始'); state, reply = call('x_memory', '投降', state)
        self.assertTrue(state['done']); self.assertFalse(any(symbol in reply for symbol in p.MEMORY_SYMBOLS))


if __name__ == '__main__': unittest.main()
