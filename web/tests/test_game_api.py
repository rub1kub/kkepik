import json
import sqlite3
import unittest
from unittest.mock import patch

import app as app_module


class GameApiTest(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            '''
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                tg_id INTEGER,
                first_name TEXT,
                username TEXT
            );
            CREATE TABLE rating_2048 (user_id INTEGER PRIMARY KEY, score INTEGER);
            CREATE TABLE rating_snake (user_id INTEGER PRIMARY KEY, score INTEGER);
            CREATE TABLE game_sessions_2048 (
                game_id TEXT PRIMARY KEY,
                user_id INTEGER,
                start_time REAL,
                last_move_time REAL,
                current_score INTEGER,
                moves_count INTEGER,
                board_state TEXT,
                is_finished INTEGER DEFAULT 0
            );
            CREATE TABLE game_moves_2048 (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                game_id TEXT,
                move_number INTEGER,
                board_state TEXT,
                score INTEGER,
                timestamp REAL,
                move_hash TEXT,
                UNIQUE(game_id, move_number)
            );
            CREATE TABLE game_sessions_snake (
                game_id TEXT PRIMARY KEY,
                user_id INTEGER,
                start_time REAL,
                last_move_time REAL,
                current_score INTEGER,
                snake_length INTEGER,
                moves_count INTEGER,
                snake_state TEXT,
                food_position TEXT,
                is_finished INTEGER DEFAULT 0
            );
            CREATE TABLE game_moves_snake (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                game_id TEXT,
                move_number INTEGER,
                snake_state TEXT,
                food_position TEXT,
                score INTEGER,
                timestamp REAL,
                move_hash TEXT,
                UNIQUE(game_id, move_number)
            );
            CREATE TABLE sudoku_games (
                game_id TEXT PRIMARY KEY,
                user_id INTEGER,
                difficulty TEXT,
                puzzle TEXT,
                solution TEXT,
                created_at INTEGER,
                hints_used INTEGER DEFAULT 0
            );
            CREATE TABLE sudoku_scores (
                id INTEGER PRIMARY KEY,
                game_id TEXT UNIQUE,
                user_id INTEGER,
                first_name TEXT,
                difficulty TEXT,
                completion_time INTEGER,
                hints_used INTEGER,
                submitted_at INTEGER
            );
            CREATE TABLE casino_users (
                user_id INTEGER PRIMARY KEY,
                score INTEGER,
                last_bet_at REAL DEFAULT 0
            );
            INSERT INTO users (id, tg_id, first_name, username)
            VALUES (1, 1084693264, 'Test', 'test');
            '''
        )

    def tearDown(self):
        self.db.close()

    def test_2048_ignores_client_score_and_board(self):
        initial_board = [
            [2, 2, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
        ]
        with (
            patch.object(app_module, 'get_db', return_value=self.db),
            patch.object(app_module, '_authenticated_game_user', return_value=1),
            patch.object(app_module, 'new_2048_board', return_value=initial_board),
        ):
            session = self.client.post('/api/2048/new_game', json={'tgWebAppData': 'signed'})
            game_id = session.get_json()['game_id']
            self.db.execute(
                'UPDATE game_sessions_2048 SET last_move_time = 0 WHERE game_id = ?',
                (game_id,),
            )
            self.db.commit()
            with patch.object(app_module, 'add_2048_tile', side_effect=lambda board: board):
                response = self.client.post('/api/submit/2048', json={
                    'tgWebAppData': 'signed',
                    'game_id': game_id,
                    'move_number': 1,
                    'direction': 'left',
                    'score': 999999,
                    'board': [[131072] * 4 for _ in range(4)],
                })

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['score'], 4)
        self.assertEqual(payload['board'][0], [4, 0, 0, 0])
        self.assertEqual(
            self.db.execute('SELECT score FROM rating_2048 WHERE user_id = 1').fetchone()['score'],
            4,
        )

    def test_snake_replays_path_ignores_score_and_rejects_replay(self):
        first_food = {'x': 11, 'y': 10}
        second_food = {'x': 12, 'y': 10}
        with (
            patch.object(app_module, 'get_db', return_value=self.db),
            patch.object(app_module, '_authenticated_game_user', return_value=1),
            patch.object(app_module, 'choose_snake_food', side_effect=[first_food, second_food]),
        ):
            session = self.client.post('/api/snake/new_game', json={'tgWebAppData': 'signed'})
            game_id = session.get_json()['game_id']
            self.db.execute(
                'UPDATE game_sessions_snake SET last_move_time = 0 WHERE game_id = ?',
                (game_id,),
            )
            self.db.commit()
            accepted = self.client.post('/api/submit/snake', json={
                'tgWebAppData': 'signed',
                'game_id': game_id,
                'move_number': 1,
                'move_path': ['right'],
                'score': 50,
                'snake_state': [{'x': 19, 'y': 19}],
            })
            rejected = self.client.post('/api/submit/snake', json={
                'tgWebAppData': 'signed',
                'game_id': game_id,
                'move_number': 1,
                'move_path': ['right'],
            })

        payload = accepted.get_json()
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(payload['score'], 1)
        self.assertEqual(payload['snake_state'][0], first_food)
        self.assertEqual(len(payload['snake_state']), 4)
        self.assertEqual(rejected.status_code, 409)

    def test_sudoku_never_returns_solution_and_verifies_final_board(self):
        solution = [
            [((row * 3 + row // 3 + column) % 9) + 1 for column in range(9)]
            for row in range(9)
        ]
        puzzle = [row[:] for row in solution]
        puzzle[0][0] = 0
        telegram_params = {'user': json.dumps({'id': 1084693264, 'first_name': 'Test'})}
        with (
            patch.object(app_module, 'get_db', return_value=self.db),
            patch.object(app_module, 'check_init_data', return_value=True),
            patch.object(app_module, 'parse_init_data_params', return_value=telegram_params),
            patch.object(app_module, 'get_or_create_user', return_value=1),
            patch.object(app_module, 'generate_sudoku_puzzle', return_value=(puzzle, solution)),
        ):
            session = self.client.post('/api/sudoku/new_game', json={
                'tgWebAppData': 'signed',
                'difficulty': 'easy',
            })
        session_payload = session.get_json()
        self.assertNotIn('solution', session_payload)
        game_id = session_payload['game_id']
        self.db.execute('UPDATE sudoku_games SET created_at = 1 WHERE game_id = ?', (game_id,))
        self.db.commit()

        wrong_board = [row[:] for row in solution]
        wrong_board[0][0] = 2
        with (
            patch.object(app_module, 'get_db', return_value=self.db),
            patch.object(app_module, '_authenticated_game_user', return_value=1),
            patch.object(app_module.time, 'time', return_value=100),
        ):
            rejected = self.client.post('/api/sudoku/submit_score', json={
                'tgWebAppData': 'signed',
                'game_id': game_id,
                'board': wrong_board,
            })
            accepted = self.client.post('/api/sudoku/submit_score', json={
                'tgWebAppData': 'signed',
                'game_id': game_id,
                'board': solution,
            })

        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.get_json()['completion_time'], 99)

    def test_casino_ignores_client_balance_and_rate_limits_parallel_bets(self):
        self.db.execute('INSERT INTO casino_users (user_id, score, last_bet_at) VALUES (1, 10, 0)')
        self.db.commit()
        telegram_params = {'user': json.dumps({'id': 1084693264, 'first_name': 'Test'})}
        with (
            patch.object(app_module, 'get_db', return_value=self.db),
            patch.object(app_module, 'check_init_data', return_value=True),
            patch.object(app_module, 'parse_init_data_params', return_value=telegram_params),
            patch.object(app_module, 'get_or_create_user', return_value=1),
            patch.object(app_module.time, 'time', return_value=100),
            patch.object(app_module.secrets, 'randbelow', return_value=0),
        ):
            accepted = self.client.post('/api/casino/bet', json={
                'tgWebAppData': 'signed',
                'bet': 10,
                'score': 999999999,
            })
            rate_limited = self.client.post('/api/casino/bet', json={
                'tgWebAppData': 'signed',
                'bet': 10,
            })

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.get_json()['score'], 60)
        self.assertEqual(rate_limited.status_code, 429)
        self.assertEqual(
            self.db.execute('SELECT score FROM casino_users WHERE user_id = 1').fetchone()['score'],
            60,
        )


if __name__ == '__main__':
    unittest.main()
