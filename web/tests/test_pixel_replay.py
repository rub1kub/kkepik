import gzip
import io
from contextlib import redirect_stdout
from pathlib import Path
import time
import unittest

from ops.export_pixel_replay import export
from pixel_canvas import connect
from tests.test_pixel_canvas_admin import CanvasAdminTest


class ReplayTest(unittest.TestCase):
    setUp = CanvasAdminTest.setUp
    tearDown = CanvasAdminTest.tearDown
    headers = CanvasAdminTest.headers

    def prepare(self):
        self.client.post('/place', headers=self.student, json={'x': 0, 'y': 0, 'color': 3, 'request_id': 'replay-test-one'})
        self.client.post('/admin/fill', headers=self.owner, json={'x0': 1, 'y0': 1, 'x1': 3, 'y1': 3, 'color': 5})
        with connect(self.path) as db:
            now = time.time_ns()//1_000_000
            db.execute('UPDATE canvas_event SET started_at_ms=?,ends_at_ms=? WHERE id=1', (now-604800000, now-1))
        root = Path(self.tmp.name)/'archive'
        self.app.config['CANVAS_REPLAY_DIRECTORY'] = str(root)
        with redirect_stdout(io.StringIO()):
            self.manifest = export(self.path, root, Path(self.tmp.name)/'videos', fps=2)
        return root

    def test_finished_archive_is_anonymous_and_reproduces_whole_board(self):
        root = self.prepare()
        response = self.client.get('/replay', headers=self.student)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['stats']['participants'], 1)
        self.assertEqual(response.get_json()['default_speed'], 3000)
        self.assertNotIn('actor_id', response.get_data(as_text=True))
        data = self.client.get('/replay/data', headers=self.student)
        self.assertEqual(data.headers['Content-Encoding'], 'gzip')
        raw = gzip.decompress(data.data)
        self.assertEqual(raw[:8], b'KKEPBR01')
        self.assertEqual(len(raw), 8 + self.manifest['record_count']*36)
        self.assertNotIn(b'actor_name', raw)
        data.close()
        self.assertEqual(self.manifest['video']['width'], 768)
        self.assertEqual(self.manifest['video']['height'], 1536)
        self.assertTrue((root/self.manifest['export_id']/'history.bin.gz').is_file())

    def test_authentication_and_deadline_guard_apply_to_both_endpoints(self):
        for route in ['/replay', '/replay/data']:
            self.assertEqual(self.client.get(route).status_code, 401)
            self.assertEqual(self.client.get(route, headers=self.student).status_code, 423)
            forged = {**self.student, 'X-Telegram-Init-Data': 'user=1084693264'}
            self.assertEqual(self.client.get(route, headers=forged).status_code, 401)

    def test_archive_id_cannot_traverse_directories_and_reads_do_not_change_board(self):
        self.prepare()
        with connect(self.path) as db:
            before = tuple(db.execute('SELECT revision FROM canvas_meta').fetchone())
        for value in ['../current.json', '/etc/passwd', 'battle-20261007-not-a-hash']:
            self.assertEqual(self.client.get('/replay/data', query_string={'export_id': value}, headers=self.student).status_code, 400)
        response=self.client.get('/replay/data', query_string={'export_id': self.manifest['export_id']}, headers=self.student)
        self.assertEqual(response.status_code, 200)
        response.close()
        with connect(self.path) as db:
            self.assertEqual(tuple(db.execute('SELECT revision FROM canvas_meta').fetchone()), before)

    def test_incomplete_publication_is_retryable(self):
        with connect(self.path) as db:
            db.execute('UPDATE canvas_event SET started_at_ms=1,ends_at_ms=2 WHERE id=1')
        self.app.config['CANVAS_REPLAY_DIRECTORY'] = self.tmp.name
        self.assertEqual(self.client.get('/replay', headers=self.student).status_code, 404)


if __name__ == '__main__':
    unittest.main()
