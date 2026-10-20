import unittest

from game_security import (
    InvalidGameState,
    add_2048_tile,
    advance_snake,
    move_2048,
    new_2048_board,
    parse_snake,
)


class GameSecurityTest(unittest.TestCase):
    def test_2048_score_is_derived_from_board(self):
        board = [
            [2, 2, 4, 4],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
        ]

        moved, score, changed = move_2048(board, 'left')

        self.assertTrue(changed)
        self.assertEqual(score, 12)
        self.assertEqual(moved[0], [4, 8, 0, 0])

    def test_2048_random_tile_is_server_selected(self):
        board = [[0] * 4 for _ in range(4)]
        values = iter((0, 9))

        result = add_2048_tile(board, lambda _limit: next(values))

        self.assertEqual(result[0][0], 2)
        self.assertEqual(sum(cell != 0 for row in result for cell in row), 1)

    def test_new_2048_board_has_two_tiles(self):
        board = new_2048_board(lambda _limit: 0)
        self.assertEqual(sum(cell != 0 for row in board for cell in row), 2)

    def test_snake_rejects_disconnected_or_duplicate_segments(self):
        with self.assertRaises(InvalidGameState):
            parse_snake([{'x': 2, 'y': 2}, {'x': 0, 'y': 0}])
        with self.assertRaises(InvalidGameState):
            parse_snake([{'x': 2, 'y': 2}, {'x': 2, 'y': 2}])

    def test_snake_replays_path_to_server_food(self):
        previous = [
            {'x': 2, 'y': 2},
            {'x': 1, 'y': 2},
            {'x': 0, 'y': 2},
        ]

        result = advance_snake(
            previous,
            {'x': 4, 'y': 2},
            ['right', 'right'],
            0,
            1.0,
        )

        self.assertEqual(result[0], {'x': 4, 'y': 2})
        self.assertEqual(len(result), 4)

    def test_snake_rejects_early_food(self):
        previous = [
            {'x': 2, 'y': 2},
            {'x': 1, 'y': 2},
            {'x': 0, 'y': 2},
        ]

        with self.assertRaises(InvalidGameState):
            advance_snake(
                previous,
                {'x': 3, 'y': 2},
                ['right', 'down'],
                0,
                10.0,
            )

    def test_snake_rejects_impossibly_fast_path(self):
        previous = [
            {'x': 2, 'y': 2},
            {'x': 1, 'y': 2},
            {'x': 0, 'y': 2},
        ]

        with self.assertRaises(InvalidGameState):
            advance_snake(previous, {'x': 4, 'y': 2}, ['right', 'right'], 0, 0.01)


if __name__ == '__main__':
    unittest.main()
