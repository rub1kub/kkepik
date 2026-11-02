import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from flask import Flask, Response
from activity_audit import init_activity
from activity_store import ActivityStore
from download_audit import init_download_audit, Transfer
import schedule_api
from tests.test_telegram_auth import signed_init_data

PDF = b'%PDF-1.7\n' + b'test contents' * 800
URL = '/api/schedule/download/groups/02.11.2026'


class DownloadAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'activity.db'
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, ACTIVITY_DB=str(self.path))
        self.app.register_blueprint(schedule_api.schedule_bp)
        init_activity(self.app); init_download_audit(self.app)
        self.app.add_url_rule('/api/v2/sources/' + 'a'*64, 'source', lambda: Response(PDF, mimetype='application/pdf'))
        schedule_api.limiter.reset()
        self.client = self.app.test_client()
        self.token = patch('telegram_auth.get_bot_token', return_value='test-token'); self.token.start();self.addCleanup(self.token.stop)
        self.headers = {'X-Telegram-Init-Data': signed_init_data('test-token', 42, int(time.time()))}

    def events(self):
        if not self.path.exists(): return []
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            return [{**dict(r), 'metadata': json.loads(r['metadata'])} for r in db.execute('SELECT * FROM activity_events ORDER BY id')]

    def upstream(self, status=200, mime='application/pdf'):
        fake = Mock(status_code=status, headers={'Content-Type': mime})
        fake.raw = io.BytesIO(PDF);fake.json.return_value = {'detail': 'missing'}
        return fake

    def test_url_id_and_complete_transfer_keep_recipient_and_context(self):
        fake = self.upstream()
        with patch.object(schedule_api.requests, 'get', return_value=fake):
            response = self.client.get(URL, query_string={'user_id':42, 'source':'miniapp',
                'entity_type':'group', 'entity_id':'103-Д9-3ИНС'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, PDF);response.close()
        rows = self.events()
        self.assertEqual([r['action'] for r in rows], ['schedule.download.request', 'schedule.download.started', 'schedule.download.complete'])
        self.assertEqual({r['actor_id'] for r in rows}, {42})
        self.assertEqual({r['schedule_date'] for r in rows}, {'2026-11-02'})
        self.assertEqual({r['entity_id'] for r in rows}, {'103-Д9-3ИНС'})
        self.assertEqual(len({r['metadata']['download_id'] for r in rows}), 1)
        self.assertEqual(rows[-1]['metadata']['bytes_sent'], len(PDF))
        self.assertEqual(rows[-1]['metadata']['attribution'], 'url_id')
        self.assertEqual(rows[-1]['metadata']['file_type'], 'pdf')
        fake.close.assert_called_once()
        for secret in ('auth_date', 'test-token', 'test contents'):
            self.assertNotIn(secret, json.dumps(rows))

    def test_bot_url_uses_telegram_channel_and_does_not_need_ticket(self):
        with patch.object(schedule_api.requests, 'get', return_value=self.upstream()):
            r = self.client.get(URL, query_string={'user_id':42, 'source':'bot',
                'entity_type':'teacher','entity_id':'Шульга Н.Н.'})
            self.assertEqual(r.get_data(),PDF);r.close()
        rows=self.events()
        self.assertEqual({r['channel'] for r in rows},{'telegram'})
        self.assertEqual(rows[-1]['entity_type'],'teacher')
        self.assertEqual(rows[-1]['entity_id'],'Шульга Н.Н.')
        self.assertEqual(rows[-1]['metadata']['attribution'],'url_id')

    def test_signed_header_takes_precedence_over_url_label(self):
        with patch.object(schedule_api.requests, 'get', return_value=self.upstream()):
            r=self.client.get(URL+'?user_id=999',headers=self.headers);r.get_data();r.close()
        self.assertEqual({r['actor_id'] for r in self.events()},{42})
        self.assertEqual(self.events()[-1]['metadata']['attribution'],'signed_header')

    def test_invalid_ids_stay_anonymous_without_blocking_public_file(self):
        for value in ('-1','0','invalid','1e3','4503599627370496','9'*100):
            with patch.object(schedule_api.requests, 'get', return_value=self.upstream()):
                r=self.client.get(URL,query_string={'user_id':value});self.assertEqual(r.get_data(),PDF);r.close()
        self.assertEqual({r['actor_id'] for r in self.events()},{0})

    def test_invalid_entity_annotation_is_not_stored(self):
        with patch.object(schedule_api.requests,'get',return_value=self.upstream()):
            r=self.client.get(URL,query_string={'user_id':42,'entity_type':'group','entity_id':'<script>'});r.get_data();r.close()
        self.assertIsNone(self.events()[-1]['entity_id'])
        self.assertNotIn('<script>',json.dumps(self.events()))

    def test_head_is_a_probe_without_a_completed_download(self):
        with patch.object(schedule_api.requests,'get',return_value=self.upstream()):
            r=self.client.head(URL+'?user_id=42');self.assertEqual(r.status_code,200);r.close()
        rows=self.events()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['action'],'schedule.download.probe')
        self.assertEqual(rows[0]['actor_id'],42)

    def test_ordinary_public_files_are_anonymous_and_legacy_urls_still_open(self):
        for suffix in ('','?download=obsolete'):
            with patch.object(schedule_api.requests,'get',return_value=self.upstream()):
                r=self.client.get(URL+suffix);self.assertEqual(r.get_data(),PDF);r.close()
        self.assertEqual({r['actor_id'] for r in self.events()},{0})
        self.assertEqual(self.events()[-1]['action'],'schedule.download.complete')

    def test_temporary_ticket_endpoint_is_removed(self):
        self.assertEqual(self.client.post('/api/schedule/download-ticket',headers=self.headers,json={}).status_code,404)
        with ActivityStore(self.path).connect() as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='activity_download_tickets'").fetchone())

    def test_signed_direct_download_and_archived_source_are_audited(self):
        with patch.object(schedule_api.requests, 'get', return_value=self.upstream()):
            response = self.client.get(URL, headers=self.headers);response.get_data();response.close()
        response = self.client.get('/api/v2/sources/' + 'a'*64);response.get_data();response.close()
        rows = self.events()
        self.assertEqual(rows[2]['actor_id'], 42)
        self.assertEqual(rows[-1]['actor_id'], 0)
        self.assertEqual(rows[-1]['metadata']['source_id'], 'a'*64)

    def test_not_found_and_bad_mime_are_failures_not_completions(self):
        for status, mime in [(404, 'application/json'), (200, 'text/html')]:
            with patch.object(schedule_api.requests, 'get', return_value=self.upstream(status, mime)):
                r = self.client.get(URL, headers=self.headers);r.close()
            self.assertEqual(self.events()[-1]['action'], 'schedule.download.error')
        self.assertFalse(any(r['action'] == 'schedule.download.complete' for r in self.events()))

    def test_cancelled_response_does_not_look_like_a_complete_file(self):
        with patch.object(schedule_api.requests, 'get', return_value=self.upstream()):
            r = self.client.get(URL, headers=self.headers, buffered=False)
            r.close()
        rows = self.events()
        self.assertEqual(rows[-1]['action'], 'schedule.download.interrupted')
        self.assertFalse(any(r['action'] == 'schedule.download.complete' for r in rows))

    def test_logging_failure_does_not_break_public_file(self):
        with patch('activity_store.ActivityStore.append', side_effect=sqlite3.OperationalError('private detail')), patch.object(schedule_api.requests, 'get', return_value=self.upstream()):
            r = self.client.get(URL);self.assertEqual(r.get_data(), PDF);r.close()

    def test_client_download_action_is_observation_and_cannot_claim_completion(self):
        for phase, status in [('click', 200), ('launch', 200), ('complete', 400)]:
            response = self.client.post('/api/activity/events', headers=self.headers, json={'events': [{
                'id': phase, 'action': 'schedule.download.' + phase, 'date': '02.11.2026', 'details': {'control': 'download_schedule'}}]})
            self.assertEqual(response.status_code, status)
        self.assertEqual({r['outcome'] for r in self.events()}, {'observed'})


class TransferTests(unittest.TestCase):
    def test_stream_failure_is_not_completion_and_close_is_idempotent(self):
        def body():
            yield b'a'
            raise OSError('not for the journal')
        done = Mock();transfer = Transfer(body(), done)
        self.assertEqual(next(transfer), b'a')
        with self.assertRaises(OSError):next(transfer)
        transfer.close();transfer.close()
        done.assert_called_once_with('error', bytes_sent=1, error_type='OSError')
