import sqlite3
import unittest
from unittest.mock import patch

import app as app_module


class ScheduleReactionsApiTest(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            'CREATE TABLE schedule_reactions ('
            'id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'user_id INTEGER NOT NULL, '
            'date TEXT NOT NULL, '
            'reaction TEXT NOT NULL)'
        )

    def tearDown(self):
        self.db.close()

    def test_toggle_adds_then_removes_reaction(self):
        body = {
            'tgWebAppData': 'signed-test-data',
            'date': '2026-08-25',
            'reaction': '🔥',
        }
        patches = (
            patch.object(app_module, 'check_init_data', return_value=True),
            patch.object(app_module, 'parse_init_data_params', return_value={
                'user': '{"id": 1084693264, "first_name": "Owner"}'
            }),
            patch.object(app_module, 'get_db', return_value=self.db),
            patch.object(app_module, 'get_or_create_user', return_value=1),
        )

        with patches[0], patches[1], patches[2], patches[3]:
            added = self.client.post('/api/schedule/reactions/toggle', json=body)
            removed = self.client.post('/api/schedule/reactions/toggle', json=body)

        self.assertEqual(added.status_code, 200)
        self.assertEqual(added.get_json()['action'], 'added')
        self.assertEqual(added.get_json()['reactions'], [{'count': 1, 'reaction': '🔥'}])
        self.assertEqual(added.get_json()['user_reactions'], ['🔥'])
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.get_json()['action'], 'removed')
        self.assertEqual(removed.get_json()['reactions'], [])
        self.assertEqual(removed.get_json()['user_reactions'], [])

    def test_toggle_rejects_unsigned_request_without_writing(self):
        with patch.object(app_module, 'check_init_data', return_value=False):
            response = self.client.post('/api/schedule/reactions/toggle', json={
                'tgWebAppData': 'invalid',
                'date': '2026-08-25',
                'reaction': '🔥',
            })

        count = self.db.execute('SELECT COUNT(*) FROM schedule_reactions').fetchone()[0]
        self.assertFalse(response.get_json()['success'])
        self.assertEqual(count, 0)


if __name__ == '__main__':
    unittest.main()
