from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from flask import Flask, jsonify
from activity_audit import init_activity
from activity_store import ActivityStore, RateLimited
from telegram_auth import get_bot_token
from tests.test_telegram_auth import signed_init_data


class ActivityAuditTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'events.db'
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, ACTIVITY_DB=str(self.path))
        init_activity(self.app)
        self.app.add_url_rule('/api/favorites/add', endpoint='favorite_add', view_func=lambda: jsonify(success=True), methods=['POST'])
        self.app.add_url_rule('/api/favorites/remove', endpoint='favorite_remove', view_func=lambda: jsonify(success=False), methods=['POST'])
        self.client = self.app.test_client()
        self.token = patch('telegram_auth.get_bot_token', return_value='test-token')
        self.token.start()
        self.headers = {'X-Telegram-Init-Data': signed_init_data('test-token', 42, int(time.time())),
                        'Origin': 'http://localhost'}

    def tearDown(self):
        self.token.stop()
        self.directory.cleanup()

    def event(self, **changes):
        result = dict(id='one', action='schedule.view', entity_type='group',
            entity_id='103-Д9-3ИНС', date='02.11.2026',
            details=dict(page='/', is_favorite=True, transport='cache'))
        result.update(changes)
        return result

    def events(self):
        if not self.path.exists(): return []
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute('SELECT * FROM activity_events ORDER BY id')]

    def send(self, event=None, headers=None):
        return self.client.post('/api/activity/events', json={'events': [event or self.event()]},
                                headers=headers if headers is not None else self.headers)

    def test_verified_actor_and_favorite_cached_view(self):
        response = self.send()
        self.assertEqual(response.status_code, 200)
        row = self.events()[0]
        self.assertEqual(row['actor_id'], 42)
        self.assertEqual(row['schedule_date'], '2026-11-02')
        self.assertEqual(row['entity_id'], '103-Д9-3ИНС')
        self.assertEqual(row['origin'], 'client')
        self.assertEqual(row['outcome'], 'observed')
        self.assertTrue(json.loads(row['metadata'])['is_favorite'])
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_no_other_user_can_be_injected(self):
        event = self.event(actor_id=123)
        self.assertEqual(self.send(event).status_code, 400)
        self.assertEqual(self.events(), [])

    def test_invalid_expired_and_missing_authentication_do_not_write(self):
        for headers in ({}, {'X-Telegram-Init-Data': 'bad'},
                        {'X-Telegram-Init-Data': signed_init_data('test-token', 42, int(time.time()) - 86401)}):
            self.assertEqual(self.send(headers=headers).status_code, 401)
        self.assertEqual(self.events(), [])

    def test_cross_origin_is_rejected(self):
        self.assertEqual(self.send(headers={**self.headers, 'Origin': 'https://evil.invalid'}).status_code, 403)
        self.assertEqual(self.events(), [])

    def test_duplicate_retry_is_idempotent(self):
        self.assertEqual(self.send().get_json()['accepted'], 1)
        self.assertEqual(self.send().get_json()['accepted'], 0)
        self.assertEqual(len(self.events()), 1)

    def test_no_arbitrary_payload_or_private_message_fields(self):
        for changes in (dict(text='private'), dict(details={'initData': 'secret'}),
                        dict(action='anything'), dict(entity_id='x<script>'),
                        dict(details={'is_favorite': 'true'}), dict(details={'transport': 'fake'})):
            self.assertEqual(self.send(self.event(**changes)).status_code, 400)
        self.assertEqual(self.events(), [])

    def test_client_cannot_claim_a_server_action(self):
        self.assertEqual(self.send(self.event(action='favorite.add')).status_code, 400)

    def test_batch_has_bounded_size(self):
        response = self.client.post('/api/activity/events', json={'events': [self.event()] * 21}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.events(), [])

    def test_server_logs_success_and_rejection_without_post_contents(self):
        body = dict(entity_type='group', entity_id='158-КД9-1ИНБ', tgWebAppData='DO-NOT-LOG',
                    text='PRIVATE-MESSAGE', another_user_id=999)
        for route in ('add', 'remove'):
            response = self.client.post('/api/favorites/' + route, json=body, headers=self.headers)
            self.assertEqual(response.status_code, 200)
        rows = self.events()
        self.assertEqual([r['action'] for r in rows], ['favorite.add', 'favorite.remove'])
        self.assertEqual([r['outcome'] for r in rows], ['ok', 'rejected'])
        serialized = json.dumps(rows)
        for secret in ('DO-NOT-LOG', 'PRIVATE-MESSAGE', 'X-Telegram-Init-Data', 'another_user_id'):
            self.assertNotIn(secret, serialized)

    def test_failure_of_logging_does_not_break_a_real_action(self):
        with patch('activity_store.ActivityStore.append', side_effect=sqlite3.OperationalError('unavailable')):
            response = self.client.post('/api/favorites/add', json={}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])

    def test_client_storage_failure_is_retryable(self):
        with patch('activity_store.ActivityStore.append_batch', side_effect=sqlite3.OperationalError('unavailable')):
            self.assertEqual(self.send().status_code, 503)

    def test_local_report_has_no_public_read_endpoint(self):
        self.assertEqual(self.client.get('/api/activity/events', headers=self.headers).status_code, 405)

    def test_old_rows_expire_from_receive_time_not_client_time(self):
        self.send()
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE activity_events SET received_at_ms=?', (int(time.time() * 1000) - 31 * 86400000,))
            db.execute("DELETE FROM activity_meta WHERE key='pruned_at'")
        self.send(self.event(id='two', client_at_ms=1))
        rows = self.events()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['event_id'], 'two')
        self.assertIsNone(rows[0]['client_at_ms'])

    def test_rate_limit_survives_a_new_store_instance(self):
        events = [dict(action='page.open', event_id='batch:' + str(i)) for i in range(20)]
        for _ in range(9):
            ActivityStore(self.path).append_batch(42, 'miniapp', 'client', events, rate_limit=True)
        with self.assertRaises(RateLimited):
            ActivityStore(self.path).append_batch(42, 'miniapp', 'client', events, rate_limit=True)
        self.assertEqual(len(self.events()), 20)


if __name__ == '__main__': unittest.main()
