import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from pixel_canvas import COOLDOWN_MS, create_app
from tests.test_telegram_auth import signed_init_data


class PixelCanvasTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.directory.name, 'canvas.sqlite3')
        self.app = create_app(self.path)
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        self.authentication = patch(
            'pixel_canvas.authenticated_telegram_user', return_value={'id': 42},
        )
        self.authentication.start()
        # These regression checks exercise the original busy-app tier.
        self.online = patch('pixel_canvas.online_count', return_value=9)
        self.online.start()

    def tearDown(self):
        self.authentication.stop()
        self.online.stop()
        self.directory.cleanup()

    def place(self, client=None, **fields):
        payload = {'x': 0, 'y': 0, 'color': 12, 'request_id': 'first-request'}
        payload.update(fields)
        return (client or self.client).post('/place', json=payload)

    def state(self, **fields):
        query = {'x0': -40, 'y0': -80, 'x1': 40, 'y1': 80}
        query.update(fields)
        return self.client.get('/state', query_string=query)

    def test_signed_telegram_authentication_is_required(self):
        self.authentication.stop()
        with patch('telegram_auth.get_bot_token', return_value='canvas-test-token'):
            self.assertEqual(self.state().status_code, 401)
            self.assertEqual(self.place().status_code, 401)
            import time
            valid = signed_init_data('canvas-test-token', 42, int(time.time()))
            response = self.client.get(
                '/state?x0=-20&y0=-20&x1=20&y1=20',
                headers={'X-Telegram-Init-Data': valid},
            )
            self.assertEqual(response.status_code, 200)
            tampered = valid.replace('%22id%22%3A42', '%22id%22%3A99')
            response = self.client.post('/place', json={
                'x': 0, 'y': 0, 'color': 1, 'request_id': 'tampered-request',
            }, headers={'X-Telegram-Init-Data': tampered})
            self.assertEqual(response.status_code, 401)

    def test_concurrent_tabs_cannot_bypass_one_second_limit(self):
        start = threading.Barrier(8)

        def attempt(index):
            start.wait()
            with self.app.test_client() as client:
                return self.place(client, x=index, request_id=f'parallel-{index}').status_code

        with ThreadPoolExecutor(max_workers=8) as executor:
            statuses = list(executor.map(attempt, range(8)))
        self.assertEqual(statuses.count(200), 1)
        self.assertEqual(statuses.count(429), 7)
        self.assertEqual(len(self.state().get_json()['pixels']), 1)

    def test_limit_survives_worker_restart_and_ignores_client_identity(self):
        self.assertEqual(self.place(user_id=99).status_code, 200)
        restarted = create_app(self.path).test_client()
        self.assertEqual(self.place(restarted, request_id='restart-request', user_id=123).status_code, 429)

    def test_exactly_one_second_and_no_accumulated_burst(self):
        with patch('pixel_canvas.time.time_ns', return_value=1_000_000_000_000):
            self.assertEqual(self.place().status_code, 200)
        with patch('pixel_canvas.time.time_ns', return_value=1_000_999_000_000):
            response = self.place(request_id='next-request')
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.get_json()['remaining_ms'], 1)
        with patch('pixel_canvas.time.time_ns', return_value=1_001_000_000_000):
            self.assertEqual(self.place(request_id='next-request').status_code, 200)
        with patch('pixel_canvas.time.time_ns', return_value=1_010_000_000_000):
            self.assertEqual(self.place(request_id='after-idle').status_code, 200)
            self.assertEqual(self.place(request_id='burst-request').status_code, 429)

    def test_retry_is_idempotent_and_cannot_change_original_pixel(self):
        first = self.place().get_json()
        retry = self.place().get_json()
        self.assertEqual(first['pixel'], retry['pixel'])
        self.assertEqual(self.state().get_json()['revision'], 1)
        self.assertEqual(self.place(color=1).status_code, 409)

    def test_overwriting_and_incremental_sync_use_server_order(self):
        first = self.place().get_json()
        with patch('pixel_canvas.authenticated_telegram_user', return_value={'id': 99}):
            self.assertEqual(self.place(x=2, request_id='other-pixel').status_code, 200)
            # Both accounts can modify any cell; no ownership or protected art.
            with patch('pixel_canvas.time.time_ns', return_value=2_000_000_000_000_000_000):
                self.assertEqual(self.place(color=5, request_id='overwrite-pixel').status_code, 200)
        data = self.state(since=first['pixel'][3]).get_json()
        self.assertFalse(data['full'])
        self.assertEqual(data['revision'], 3)
        self.assertEqual({tuple(pixel[:3]) for pixel in data['pixels']}, {(0, 0, 5), (2, 0, 12)})
        self.assertEqual(self.state(since=3).get_json()['pixels'], [])

    def test_finite_board_allows_last_cell_and_rejects_outside(self):
        self.assertEqual(self.place(x=63, y=-128).status_code, 200)
        data = self.state(x0=58,x1=64,y0=-128,y1=-122).get_json()
        self.assertEqual(data['pixels'][0][:3], [63,-128,12])
        self.assertEqual(data['bounds'], {'x0':-64,'y0':-128,'x1':64,'y1':128})
        self.assertEqual(self.place(x=64,request_id='outside-right').status_code,400)
        self.assertEqual(self.place(y=-129,request_id='outside-top').status_code,400)
        self.assertEqual(self.state(x0=0,x1=65).status_code,400)
        self.assertEqual(self.state(x0=-64,x1=64,y0=-128,y1=128).status_code,200)
        self.assertEqual(self.state(x0=10,x1=0).status_code,400)

    def test_invalid_pixels_do_not_consume_cooldown_or_write(self):
        for fields in ({'x': True}, {'y': 0.5}, {'color': 16}, {'color': -1},
                       {'x': 1_000_000}, {'request_id': '../oops!'}, {'color': '12'}):
            with self.subTest(fields=fields):
                self.assertEqual(self.place(**fields).status_code, 400)
        self.assertEqual(self.state().get_json()['revision'], 0)
        self.assertEqual(self.place().status_code, 200)

    def test_busy_database_fails_quickly_without_partial_placement(self):
        lock = sqlite3.connect(self.path, isolation_level=None)
        try:
            lock.execute('BEGIN IMMEDIATE')
            response = self.place()
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers['Retry-After'], '1')
        finally:
            lock.rollback()
            lock.close()
        self.assertEqual(self.state().get_json()['revision'], 0)
        self.assertEqual(self.place().status_code, 200)

    def test_private_responses_and_future_cursors_resynchronize(self):
        self.place()
        response = self.state(since=999999)
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        self.assertTrue(response.get_json()['full'])
        self.assertEqual(response.get_json()['cooldown_ms'], COOLDOWN_MS)


if __name__ == '__main__':
    unittest.main()
