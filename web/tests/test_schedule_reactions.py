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

        self.db.execute(
            'CREATE TABLE schedule_reaction_adjustments ('
            'campaign TEXT PRIMARY KEY, date TEXT, reaction TEXT, quantity INTEGER)'
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

    def test_adjustment_survives_personal_toggle_and_is_date_scoped(self):
        self.db.execute('INSERT INTO schedule_reaction_adjustments VALUES(?,?,?,?)',
                        ('approved-joke', '2026-10-29', '🤮', 50))
        body = dict(tgWebAppData='signed-test-data', date='2026-10-29', reaction='🤮')
        with (
            patch.object(app_module, 'check_init_data', return_value=True),
            patch.object(app_module, 'parse_init_data_params', return_value={'user': '{"id": 42}'}),
            patch.object(app_module, 'get_db', return_value=self.db),
            patch.object(app_module, 'get_or_create_user', return_value=1),
        ):
            initial = self.client.get('/api/schedule/reactions', query_string={
                'tgWebAppData': 'signed-test-data', 'date': '2026-10-29'}).get_json()
            added = self.client.post('/api/schedule/reactions/toggle', json=body).get_json()
            removed = self.client.post('/api/schedule/reactions/toggle', json=body).get_json()
        self.assertEqual(initial['reactions'], [{'reaction': '🤮', 'count': 50}])
        self.assertEqual(initial['user_reactions'], [])
        self.assertEqual(added['reactions'], [{'reaction': '🤮', 'count': 51}])
        self.assertEqual(added['user_reactions'], ['🤮'])
        self.assertEqual(removed['reactions'], [{'reaction': '🤮', 'count': 50}])
        self.assertEqual(removed['user_reactions'], [])
        self.assertEqual(app_module.schedule_reaction_counts(self.db, '2026-10-28'), [])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM schedule_reactions').fetchone()[0], 0)

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
