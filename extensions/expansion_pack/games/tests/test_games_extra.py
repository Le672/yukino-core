"""Pure state/algorithm checks; no database, network, model or QQ side effects."""
from collections import Counter
import copy
import importlib.util
import json
from pathlib import Path
import random
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('games_extra_test', ROOT / 'helper_extra.py')
extra = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extra)
CTX = dict(user_id='11', owner_ids=['99'], session_id='p:11', settings={})
INVALID = {'x_hangman': '123', 'x_battleship': 'Z9', 'x_highlow': '猜 相同', 'x_knight': 'A2', 'x_arithmetic': 'abc', 'x_trivia': 'E', 'x_anagram': '123'}


def start(kind, settings=None, arg='开始'):
    return extra.run(kind, arg, None, {**CTX, 'settings': settings or {}})[0]


class Contracts(unittest.TestCase):
    pass


for item in extra.CATALOG:
    def starting(self, entry=item):
        state, reply = extra.run(entry['id'], '开始', None, CTX)
        self.assertIn(entry['expected_contains'], reply)
        self.assertFalse(state['done'])
        self.assertEqual(entry['id'], state['kind'])
        self.assertEqual(state, json.loads(json.dumps(state)))
        self.assertTrue(entry['config_schema'])
        self.assertTrue(entry['usage'].startswith(entry['commands'][0]))
    setattr(Contracts, 'test_start_' + item['id'], starting)

    def invalid(self, entry=item):
        state = start(entry['id'])
        before = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            extra.run(entry['id'], INVALID[entry['id']], state, CTX)
        self.assertEqual(before, state)
    setattr(Contracts, 'test_error_rollback_' + item['id'], invalid)

    def surrender(self, entry=item):
        state = start(entry['id'])
        before = copy.deepcopy(state)
        done, reply = extra.run(entry['id'], '投降', state, CTX)
        self.assertTrue(done['done'])
        self.assertTrue(done['surrendered'])
        self.assertEqual(before, state)
        self.assertIn('结束', reply)
        renewed, _ = extra.run(entry['id'], '开始', done, CTX)
        self.assertFalse(renewed['done'])
    setattr(Contracts, 'test_surrender_restart_' + item['id'], surrender)

    def active_restart(self, entry=item):
        state = start(entry['id'])
        before = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            extra.run(entry['id'], '开始', state, CTX)
        self.assertEqual(before, state)
        cloned, status = extra.run(entry['id'], '状态', state, CTX)
        self.assertEqual(state, cloned)
        self.assertIsNot(state, cloned)
        self.assertTrue(status)
    setattr(Contracts, 'test_active_restart_status_' + item['id'], active_restart)

    def help_without_state(self, entry=item):
        state, reply = extra.run(entry['id'], '帮助', None, CTX)
        self.assertIsNone(state)
        self.assertIn(entry['commands'][0], reply)
        self.assertIn('投降', reply)
        with self.assertRaises(ValueError):
            extra.run(entry['id'], '状态', None, CTX)
    setattr(Contracts, 'test_help_no_state_' + item['id'], help_without_state)


class Algorithms(unittest.TestCase):
    def test_catalog_unique_commands_and_ids(self):
        self.assertEqual(7, len(extra.CATALOG))
        self.assertEqual(7, len({item['id'] for item in extra.CATALOG}))
        self.assertEqual(7, len({item['commands'][0] for item in extra.CATALOG}))

    def test_hangman_correct_letters_and_word_win(self):
        state = start('x_hangman')
        state, _ = extra.run('x_hangman', '猜 ' + state['answer'][0], state, CTX)
        self.assertEqual(0, state['mistakes'])
        state, reply = extra.run('x_hangman', '猜 ' + state['answer'], state, CTX)
        self.assertTrue(state['done'])
        self.assertIn('猜中了', reply)

    def test_hangman_duplicate_and_exhaustion(self):
        state = start('x_hangman', {'max_mistakes': 4})
        misses = [letter for letter in 'abcdefghijklmnopqrstuvwxyz' if letter not in state['answer']]
        state, _ = extra.run('x_hangman', misses[0], state, CTX)
        before = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            extra.run('x_hangman', misses[0], state, CTX)
        self.assertEqual(before, state)
        for letter in misses[1:4]:
            state, reply = extra.run('x_hangman', letter, state, CTX)
        self.assertTrue(state['done'])
        self.assertEqual(4, state['mistakes'])
        self.assertIn(state['answer'], reply)

    def test_battleship_board_integrity_and_win(self):
        for seed in range(20):
            with patch.object(extra, 'RNG', random.Random(seed)):
                state = start('x_battleship')
            self.assertEqual([3, 2, 2], list(map(len, state['ships'])))
            occupied = [pos for ship in state['ships'] for pos in ship]
            self.assertEqual(7, len(set(occupied)))
            for ship in state['ships']:
                delta = ship[1] - ship[0]
                self.assertIn(delta, (1, 5))
                self.assertTrue(all(ship[i + 1] - ship[i] == delta for i in range(len(ship) - 1)))
            for pos in occupied:
                state, reply = extra.run('x_battleship', '攻击 ' + extra.label(pos, 5), state, CTX)
            self.assertTrue(state['done'])
            self.assertIn('你赢了', reply)

    def test_battleship_duplicate_and_shot_limit(self):
        state = start('x_battleship', {'max_shots': 10})
        occupied = {pos for ship in state['ships'] for pos in ship}
        misses = [pos for pos in range(25) if pos not in occupied][:10]
        state, _ = extra.run('x_battleship', extra.label(misses[0], 5), state, CTX)
        before = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            extra.run('x_battleship', extra.label(misses[0], 5), state, CTX)
        self.assertEqual(before, state)
        for pos in misses[1:]:
            state, reply = extra.run('x_battleship', extra.label(pos, 5), state, CTX)
        self.assertTrue(state['done'])
        self.assertEqual(10, len(state['shots']))
        self.assertIn('次数用完', reply)

    def test_highlow_standard_deck_and_scoring(self):
        state = start('x_highlow')
        self.assertEqual(52, len({(card['suit'], card['rank']) for card in state['deck']}))
        self.assertEqual(set(range(2, 15)), {card['rank'] for card in state['deck']})
        state['deck'] = [dict(rank=rank, suit='♠') for rank in (2, 2, 3, 4, 5)]
        state['target'] = 3
        state, reply = extra.run('x_highlow', '高', state, CTX)
        self.assertIn('平局', reply)
        self.assertEqual(0, state['score'])
        for _ in range(3):
            state, reply = extra.run('x_highlow', '高', state, CTX)
        self.assertTrue(state['done'])
        self.assertIn('你赢了', reply)

    def test_highlow_wrong_guess_ends_without_modifying_old(self):
        state = start('x_highlow')
        state['deck'] = [dict(rank=2, suit='♠'), dict(rank=14, suit='♥')]
        before = copy.deepcopy(state)
        done, reply = extra.run('x_highlow', '低', state, CTX)
        self.assertTrue(done['done'])
        self.assertIn('猜错', reply)
        self.assertEqual(before, state)

    def test_knight_day_move_and_no_revisit(self):
        state = start('x_knight')
        self.assertEqual({'B3', 'C2'}, {extra.label(pos, 5) for pos in extra.knight_moves(state)})
        state, reply = extra.run('x_knight', '移动 B3', state, CTX)
        self.assertIn('当前 B3', reply)
        self.assertEqual([0, 11], state['visited'])
        before = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            extra.run('x_knight', 'A1', state, CTX)
        self.assertEqual(before, state)

    def test_knight_odd_board_parity_and_six_board(self):
        with self.assertRaises(ValueError):
            start('x_knight', arg='开始 B1')
        state = start('x_knight', {'board_size': 6}, '开始 F6')
        self.assertEqual(6, state['size'])
        self.assertEqual(35, state['visited'][0])

    def test_knight_full_legal_tour_wins(self):
        # A known 5x5 open tour, independently checked at every transition.
        route = [0, 7, 4, 13, 24, 17, 20, 11, 2, 9, 18, 21, 10, 1, 8, 19, 22, 15, 6, 3, 14, 23, 16, 5, 12]
        state = start('x_knight')
        for position in route[1:]:
            self.assertIn(position, extra.knight_moves(state))
            state, reply = extra.run('x_knight', extra.label(position, 5), state, CTX)
        self.assertTrue(state['done'])
        self.assertIn('你赢了', reply)
        self.assertEqual(25, len(set(state['visited'])))

    def test_arithmetic_generated_operations_are_correct(self):
        seen = set()
        with patch.object(extra, 'RNG', random.Random(17)):
            for _ in range(100):
                question = extra.arithmetic_question(20)
                a, b, op, answer = (question[key] for key in ('a', 'b', 'operation', 'answer'))
                seen.add(op)
                expected = a + b if op == '+' else a - b if op == '-' else a * b if op == '×' else a // b
                self.assertEqual(expected, answer)
                if op == '÷':
                    self.assertEqual(0, a % b)
        self.assertEqual({'+', '-', '×', '÷'}, seen)

    def test_arithmetic_correct_completion_and_wrong_progress(self):
        state = start('x_arithmetic', {'question_count': 3})
        for _ in range(3):
            state, reply = extra.run('x_arithmetic', '答 ' + str(state['question']['answer']), state, CTX)
        self.assertTrue(state['done'])
        self.assertIn('答对 3/3', reply)
        state = start('x_arithmetic')
        answer = state['question']['answer']
        state, reply = extra.run('x_arithmetic', str(answer + 1), state, CTX)
        self.assertEqual(1, state['answered'])
        self.assertEqual(0, state['correct'])
        self.assertIn('正确答案是 ' + str(answer), reply)

    def test_trivia_unique_and_correct_completion(self):
        state = start('x_trivia', {'question_count': 3})
        self.assertEqual(3, len({item['prompt'] for item in state['questions']}))
        for _ in range(3):
            question = state['questions'][state['answered']]
            state, reply = extra.run('x_trivia', chr(65 + question['correct']), state, CTX)
        self.assertTrue(state['done'])
        self.assertIn('答对 3/3', reply)

    def test_trivia_wrong_option_consumes_one_question(self):
        state = start('x_trivia')
        answer = (state['questions'][0]['correct'] + 1) % 4
        state, reply = extra.run('x_trivia', chr(65 + answer), state, CTX)
        self.assertEqual(1, state['answered'])
        self.assertEqual(0, state['correct'])
        self.assertIn('正确选项', reply)

    def test_anagram_uses_exact_letters_and_never_same_order(self):
        for seed in range(30):
            with patch.object(extra, 'RNG', random.Random(seed)):
                state = start('x_anagram')
            self.assertEqual(Counter(state['answer']), Counter(state['scrambled']))
            self.assertNotEqual(state['answer'], state['scrambled'])
        state, reply = extra.run('x_anagram', '猜 ' + state['answer'], state, CTX)
        self.assertTrue(state['done'])
        self.assertIn('猜中了', reply)

    def test_anagram_wrong_duplicate_and_hidden_hint(self):
        state = start('x_anagram', {'max_mistakes': 2, 'show_hint': False})
        self.assertNotIn('线索：', extra.anagram_status(state))
        state, _ = extra.run('x_anagram', 'wrong', state, CTX)
        before = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            extra.run('x_anagram', 'wrong', state, CTX)
        self.assertEqual(before, state)
        state, reply = extra.run('x_anagram', 'wrongagain', state, CTX)
        self.assertTrue(state['done'])
        self.assertIn(state['answer'], reply)

    def test_input_limit_and_cross_kind_are_rejected(self):
        with self.assertRaises(ValueError):
            extra.run('x_anagram', 'a' * 1001, None, CTX)
        state = start('x_anagram')
        with self.assertRaises(ValueError):
            extra.run('x_hangman', '状态', state, CTX)


if __name__ == '__main__':
    unittest.main()
