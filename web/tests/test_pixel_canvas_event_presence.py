import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from pixel_canvas import create_app, EVENT_DURATION_MS, PRESENCE_TTL_MS
from tests import test_pixel_canvas_admin as helpers


class CanvasEventPresenceBanTest(unittest.TestCase):
    setUp = helpers.CanvasAdminTest.setUp
    tearDown = helpers.CanvasAdminTest.tearDown
    headers = helpers.CanvasAdminTest.headers
    state = helpers.CanvasAdminTest.state
    fill = helpers.CanvasAdminTest.fill
    history = helpers.CanvasAdminTest.history

    def pulse(self, uid=42, session='session-one', seq=1, active=True, **extra):
        return self.client.post('/presence', headers=self.headers(uid), json={
            'session_id': session, 'sequence': seq, 'active': active, **extra})

    def ban(self, blocked=True, uid=42, headers=None):
        return self.client.post('/admin/ban', headers=headers or self.owner,
                                json={'telegram_id': uid, 'banned': blocked})

    def place(self, headers=None, request='place-one'):
        return self.client.post('/place', headers=headers or self.student,
                                json={'x': 0, 'y': 0, 'color': 3, 'request_id': request})

    def start(self, headers=None):
        return self.client.post('/admin/event/start', json={}, headers=headers or self.owner)

    def test_presence_counts_whole_app_users_once_and_keeps_other_tab(self):
        self.assertEqual(self.pulse(page='schedule', user_id=99).get_json()['online_count'], 1)
        self.assertEqual(self.pulse(session='session-two', page='games').get_json()['online_count'], 1)
        self.assertEqual(self.pulse(uid=43).get_json()['online_count'], 2)
        self.assertEqual(self.pulse(seq=3, active=False).get_json()['online_count'], 2)
        # A delayed heartbeat cannot revive a tab that already sent leave.
        self.pulse(seq=2)
        self.assertEqual(self.pulse(session='session-two', seq=2, active=False).get_json()['online_count'], 1)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT DISTINCT telegram_id FROM miniapp_presence ORDER BY telegram_id').fetchall(), [(42,), (43,)])

    def test_presence_ttl_exact_boundary_duplicate_cannot_refresh(self):
        now = 1_000_000
        with patch('pixel_canvas.time.time_ns', return_value=now*1_000_000):
            self.pulse()
        with patch('pixel_canvas.time.time_ns', return_value=(now+PRESENCE_TTL_MS-1)*1_000_000):
            self.assertEqual(self.state().get_json()['online_count'], 1)
            self.pulse()  # repeated sequence must not extend TTL
        with patch('pixel_canvas.time.time_ns', return_value=(now+PRESENCE_TTL_MS)*1_000_000):
            self.assertEqual(self.state().get_json()['online_count'], 0)
            self.assertEqual(self.pulse(seq=2).get_json()['online_count'], 1)
        self.assertEqual(create_app(self.path).test_client().get('/state?x0=0&y0=0&x1=1&y1=1', headers=self.student).get_json()['online_count'], 0)

    def test_presence_rejects_missing_forged_auth_foreign_origin_invalid_payload(self):
        body = {'session_id': 'session-one', 'sequence': 1, 'active': True}
        self.assertEqual(self.client.post('/presence', json=body).status_code, 401)
        headers = {**self.student, 'Origin': 'https://evil.example'}
        self.assertEqual(self.client.post('/presence', json=body, headers=headers).status_code, 403)
        self.assertEqual(self.client.post('/presence', json={**body,'initData':self.student['X-Telegram-Init-Data']}).status_code,401)
        for extra in ({'sequence':True},{'sequence':0},{'active':1},{'session_id':'../evil!'}, {'session_id':'x'*65}):
            self.assertEqual(self.client.post('/presence', json={**body,**extra}, headers=self.student).status_code, 400)
        tampered = self.student['X-Telegram-Init-Data'].replace('%22id%22%3A42','%22id%22%3A99')
        self.assertEqual(self.client.post('/presence', json=body, headers={'X-Telegram-Init-Data':tampered}).status_code, 401)
        self.assertEqual(self.state().get_json()['online_count'], 0)

    def test_presence_storage_bounded_and_cleanup(self):
        for i in range(40):
            self.pulse(session=f'session-{i:03d}')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM miniapp_presence').fetchone()[0],16)
        self.assertEqual(self.state().get_json()['online_count'],1)
        with patch('pixel_canvas.time.time_ns', return_value=2_000_000_000_000_000_000):
            self.pulse(uid=43)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT telegram_id FROM miniapp_presence').fetchall(),[(43,)])

    def test_event_admin_only_persistent_seven_days_idempotent(self):
        self.assertEqual(self.start(self.student).status_code,403)
        with patch('pixel_canvas.time.time_ns',return_value=1_000_000_000_000):
            event=self.start().get_json()['event']
        self.assertEqual(event['ends_at_ms']-event['started_at_ms'],EVENT_DURATION_MS)
        self.assertTrue(event['active'])
        with patch('pixel_canvas.time.time_ns',return_value=1_200_000_000_000):
            later=self.start(self.web).get_json()['event']
        self.assertEqual(event['ends_at_ms'],later['ends_at_ms'])
        restarted=create_app(self.path).test_client().get('/state?x0=0&y0=0&x1=1&y1=1',headers=self.student).get_json()['event']
        self.assertEqual(restarted['ends_at_ms'],event['ends_at_ms'])
        self.assertTrue(restarted['finished'])

    def test_event_start_concurrent_workers_share_one_deadline(self):
        workers=[create_app(self.path) for _ in range(6)]
        gate=threading.Barrier(6)
        def start(app):
            gate.wait()
            return app.test_client().post('/admin/event/start',json={'ends_at_ms':9999999999999},headers=self.owner).get_json()['event']
        with ThreadPoolExecutor(max_workers=6) as pool:
            values=list(pool.map(start,workers))
        self.assertEqual(len({v['ends_at_ms'] for v in values}),1)
        self.assertEqual(values[0]['ends_at_ms']-values[0]['started_at_ms'],EVENT_DURATION_MS)

    def test_event_boundary_no_partial_write_admin_and_history_survive(self):
        with patch('pixel_canvas.time.time_ns',return_value=1_000_000_000_000):
            end=self.start().get_json()['event']['ends_at_ms']
        with patch('pixel_canvas.time.time_ns',return_value=(end-1)*1_000_000):
            self.assertEqual(self.place().status_code,200)
        before=self.state().get_json()['revision']
        with patch('pixel_canvas.time.time_ns',return_value=end*1_000_000):
            self.assertEqual(self.place(request='post-end').status_code,423)
            self.assertEqual(self.place().status_code,200)  # receipt for accepted request
            self.assertEqual(self.state().get_json()['revision'],before)
            self.assertEqual(len(self.history().get_json()['events']),1)
            self.assertEqual(self.fill().status_code,200)
            self.assertEqual(len(self.history().get_json()['events']),2)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT request_id FROM canvas_players WHERE telegram_id=42').fetchone()[0],'place-one')

    def test_ban_only_verified_admin_and_owner_protected(self):
        self.assertEqual(self.ban(headers=self.student).status_code,403)
        self.assertEqual(self.client.get('/admin/bans',headers=self.student).status_code,403)
        self.assertEqual(self.ban(uid=1084693264).status_code,400)
        for uid in [True,0,'42',-1,9_007_199_254_740_992]:
            self.assertEqual(self.ban(uid=uid).status_code,400)
        self.assertEqual(self.ban(headers={**self.owner,'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.state().get_json()['banned'],False)

    def test_ban_server_enforced_idempotent_audit_unban_and_authorship(self):
        self.assertEqual(self.place().status_code,200)
        self.assertEqual(self.ban().status_code,200)
        self.assertEqual(self.ban().status_code,200)
        state=self.state().get_json()
        self.assertTrue(state['banned']);self.assertEqual(state['access_revision'],1)
        denied=self.place(request='new-pixel')
        self.assertEqual(denied.status_code,403)
        self.assertEqual(denied.get_json()['access_revision'],1)
        self.assertEqual(self.place().status_code,403)  # no idempotent write bypass
        self.assertEqual(self.ban(False,headers=self.student).status_code,403)
        self.assertEqual(len(self.history().get_json()['events']),1)
        pixel=self.client.get('/admin/pixel?x=0&y=0',headers=self.owner).get_json()
        self.assertTrue(pixel['author_banned'])
        self.assertEqual(self.client.get('/admin/bans',headers=self.web).get_json()['bans'][0]['telegram_id'],42)
        self.assertEqual(self.pulse().get_json()['online_count'],1)  # still using mini-app
        self.ban(False,headers=self.web)
        self.assertFalse(self.state().get_json()['banned'])
        with patch('pixel_canvas.time.time_ns',return_value=2_000_000_000_000_000_000):
            self.assertEqual(self.place(request='unbanned-pixel').status_code,200)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT action,actor_id,actor_kind FROM canvas_moderation ORDER BY id').fetchall(), [('ban',1084693264,'telegram'),('unban',None,'web_admin')])

    def test_ban_survives_restart_and_write_waits_for_ban_transaction(self):
        db=sqlite3.connect(self.path,isolation_level=None)
        db.execute('BEGIN IMMEDIATE')
        db.execute("INSERT INTO canvas_bans VALUES (42,1,1084693264,'telegram')")
        # The writer must acquire the same lock before looking at the ban.
        with ThreadPoolExecutor(max_workers=1) as pool:
            ready=threading.Event()
            # Use an already-initialized worker while the ban holds its lock.
            def attempt():
                ready.set()
                return self.app.test_client().post('/place',headers=self.student,json={'x':0,'y':0,'color':3,'request_id':'race-one'}).status_code
            future=pool.submit(attempt);ready.wait(1);db.commit()
            self.assertEqual(future.result(),403)
        db.close()
        restarted=create_app(self.path).test_client()
        self.assertTrue(restarted.get('/state?x0=0&y0=0&x1=1&y1=1',headers=self.student).get_json()['banned'])
        self.assertEqual(self.state().get_json()['revision'],0)


if __name__=='__main__':
    unittest.main()
