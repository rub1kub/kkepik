import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pixel_canvas import create_app, initialize
from pixel_admin_auth import issue_admin_token, serializer
from tests.test_telegram_auth import signed_init_data


class CanvasAdminTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'canvas.db')
        key = Path(self.tmp.name) / 'admin.key'
        key.write_text('4e' * 32)
        self.environment = patch.dict(os.environ, {'KEPIK_CANVAS_ADMIN_KEY': str(key)})
        self.environment.start()
        self.bot = patch('telegram_auth.get_bot_token', return_value='admin-canvas-test')
        self.bot.start()
        self.app = create_app(self.path)
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        self.owner = self.headers(1084693264)
        self.student = self.headers(42)
        self.web = {'X-Canvas-Admin-Token': issue_admin_token()}

    def tearDown(self):
        self.bot.stop()
        self.environment.stop()
        self.tmp.cleanup()

    def headers(self, user):
        return {'X-Telegram-Init-Data': signed_init_data('admin-canvas-test', user, int(time.time()))}

    def fill(self, headers=None, **values):
        payload = {'x0': -2, 'y0': -2, 'x1': 3, 'y1': 3, 'color': 5}
        payload.update(values)
        return self.client.post('/admin/fill', json=payload, headers=headers or self.owner)

    def state(self, headers=None, since=0):
        return self.client.get('/state?x0=-10&y0=-10&x1=10&y1=10&since=' + str(since), headers=headers or self.student)

    def history(self, headers=None, **values):
        query = {'x0': -10, 'y0': -10, 'x1': 10, 'y1': 10}
        query.update(values)
        return self.client.get('/admin/history', query_string=query, headers=headers or self.owner)

    def test_server_verifies_identity_not_claimed_id(self):
        self.assertTrue(self.state(self.owner).get_json()['admin'])
        self.assertFalse(self.state().get_json()['admin'])
        self.assertEqual(self.fill(self.student, telegram_id=1084693264, admin=True).status_code, 403)
        self.assertEqual(self.history(self.student).status_code, 403)
        self.assertEqual(self.client.post('/admin/fill', json={'admin': True}).status_code, 403)
        import urllib.parse
        data = dict(urllib.parse.parse_qsl(self.student['X-Telegram-Init-Data']))
        data['user'] = '{"id":1084693264}'
        forged = {'X-Telegram-Init-Data': urllib.parse.urlencode(data)}
        self.assertEqual(self.fill(forged).status_code, 403)
        self.assertEqual(self.state().get_json()['revision'], 0)

    def test_website_capability_expiry_tampering_and_purpose(self):
        self.assertEqual(self.fill(self.web).status_code, 200)
        self.assertEqual(self.history(self.web).status_code, 200)
        self.assertEqual(self.client.get('/admin/state?x0=0&x1=10&y0=0&y1=10', headers=self.web).status_code, 200)
        self.assertEqual(self.client.get('/state?x0=0&x1=10&y0=0&y1=10', headers=self.web).status_code, 401)
        for value in ('broken', self.web['X-Canvas-Admin-Token'] + 'x', serializer().dumps({'scope': 'canvas:read'})):
            self.assertEqual(self.fill({'X-Canvas-Admin-Token': value}).status_code, 403)
        with patch('itsdangerous.timed.time.time', return_value=1000):
            token = issue_admin_token()
        with patch('itsdangerous.timed.time.time', return_value=1901):
            self.assertEqual(self.fill({'X-Canvas-Admin-Token': token}).status_code, 403)

    def test_privileged_actions_reject_cross_origin_and_credentials_in_url(self):
        self.assertEqual(self.fill({**self.owner,'Origin':'https://untrusted.example'}).status_code,403)
        self.assertEqual(self.fill({**self.web,'Origin':'https://untrusted.example'}).status_code,403)
        self.assertEqual(self.client.get('/admin/history', query_string={
            'x0':0,'y0':0,'x1':10,'y1':10,
            'tgWebAppData':self.owner['X-Telegram-Init-Data'],
        }).status_code,403)

    def test_fill_is_atomic_and_erase_reaches_incremental_clients(self):
        result = self.fill().get_json()
        self.assertEqual(result['revision'], 1)
        pixels = self.state().get_json()['pixels']
        self.assertEqual(len(pixels), 25)
        self.assertTrue(all(pixel[2:] == [5, 1] for pixel in pixels))
        self.assertEqual(len(self.history().get_json()['events']), 1)
        self.fill(color=-1, x0=0, y0=0, x1=2, y1=2)
        delta = self.state(since=1).get_json()['pixels']
        self.assertEqual(len(delta), 4)
        self.assertTrue(all(pixel[2:] == [-1, 2] for pixel in delta))
        self.assertEqual(self.history().get_json()['events'][-1]['color'], -1)
        # Erased cells can still be drawn over by ordinary participants.
        self.assertEqual(self.client.post('/place', json={'x':0,'y':0,'color':3,'request_id':'redraw-request'}, headers=self.student).status_code, 200)

    def test_fast_admin_does_not_reset_student_cooldown(self):
        body = {'x':0,'y':0,'color':3,'request_id':'normal-request'}
        self.assertEqual(self.client.post('/place',json=body,headers=self.student).status_code,200)
        for _ in range(3):
            self.assertEqual(self.fill().status_code,200)
        body['request_id'] = 'second-request'
        self.assertEqual(self.client.post('/place',json=body,headers=self.student).status_code,429)
        self.assertEqual(len(self.history().get_json()['events']),4)

    def test_limits_and_invalid_data_have_no_partial_effect(self):
        for fields in ({'x0': True}, {'color': 16}, {'color': -2}, {'x1': 1.5},
                       {'x0':0,'x1':257,'y0':0,'y1':256}, {'x1':-3}, {'x1':1_000_001}):
            self.assertEqual(self.fill(**fields).status_code,400)
        self.assertEqual(self.state().get_json()['revision'],0)
        self.assertEqual(self.history().get_json()['events'],[])

    def test_history_order_pagination_and_region_intersection(self):
        self.fill(x0=0,y0=0,x1=3,y1=3,color=4)
        self.fill(x0=40,y0=80,x1=41,y1=81,color=5)
        self.fill(x0=0,y0=0,x1=1,y1=1,color=-1)
        first = self.history(limit=1).get_json()
        self.assertTrue(first['more'])
        self.assertEqual(first['events'][0]['color'],4)
        second = self.history(after=first['cursor'],limit=1).get_json()
        self.assertFalse(second['more'])
        self.assertEqual(second['events'][0]['color'],-1)
        self.assertEqual(self.history(after=second['cursor']).get_json()['events'],[])
        self.assertEqual(len(self.history(x0=2,x1=4,y0=2,y1=4).get_json()['events']),1)
        self.assertEqual(self.history(limit=2001).status_code,400)

    def test_migration_baseline_survives_restarts_without_duplicates(self):
        with sqlite3.connect(self.path) as db:
            db.execute('DELETE FROM canvas_history_meta')
            db.execute('INSERT INTO pixels (x,y,color,revision) VALUES (1, 2, 3, 9)')
            db.execute('UPDATE canvas_meta SET revision=9')
        initialize(self.path)
        initialize(self.path)
        events = self.history().get_json()['events']
        self.assertEqual(len(events),1)
        self.assertEqual((events[0]['x0'],events[0]['y0'],events[0]['color']), (1,2,3))
        self.assertEqual(events[0]['source'],'baseline')
        self.assertEqual(self.state().get_json()['revision'],9)
        # Retrying an ordinary placement adds no second history record.
        body = {'x':0,'y':0,'color':3,'request_id':'normal-request'}
        self.client.post('/place',json=body,headers=self.student)
        self.client.post('/place',json=body,headers=self.student)
        self.assertEqual(len(self.history().get_json()['events']),2)

    def test_busy_admin_fill_does_not_write_history_or_partial_pixels(self):
        db = sqlite3.connect(self.path,isolation_level=None)
        try:
            db.execute('BEGIN IMMEDIATE')
            self.assertEqual(self.fill().status_code,503)
        finally:
            db.rollback(); db.close()
        self.assertEqual(self.history().get_json()['events'],[])
        self.assertEqual(self.state().get_json()['revision'],0)


if __name__ == '__main__':
    unittest.main()
