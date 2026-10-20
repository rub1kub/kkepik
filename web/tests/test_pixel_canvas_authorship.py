import sqlite3
import unittest
from unittest.mock import patch

from tests import test_pixel_canvas_admin as admin_tests
from pixel_canvas import create_app, initialize


class CanvasAuthorshipTest(unittest.TestCase):
    setUp = admin_tests.CanvasAdminTest.setUp
    tearDown = admin_tests.CanvasAdminTest.tearDown
    headers = admin_tests.CanvasAdminTest.headers
    fill = admin_tests.CanvasAdminTest.fill
    state = admin_tests.CanvasAdminTest.state
    history = admin_tests.CanvasAdminTest.history
    # Reuse only the isolated setup/helpers, not the inherited tests.
    def test_author_comes_only_from_verified_launch_data_and_overwrite_keeps_history(self):
        data={'x':0,'y':0,'color':3,'request_id':'authored-request',
              'actor_id':1084693264,'actor_name':'Forged author','username':'fake'}
        self.assertEqual(self.client.post('/place',json=data,headers=self.student).status_code,200)
        with patch('pixel_canvas.authenticated_telegram_user',return_value={
            'id':99,'first_name':'Настя','last_name':'Иванова','username':'nastya',
        }):
            self.assertEqual(self.client.post('/place',json={**data,'color':5,'request_id':'next-request'}).status_code,200)
        response=self.client.get('/admin/pixel?x=0&y=0',headers=self.owner)
        result=response.get_json()
        self.assertEqual(result['pixel']['actor_id'],99)
        self.assertEqual(result['pixel']['actor_name'],'Настя Иванова')
        self.assertEqual(result['pixel']['actor_username'],'nastya')
        self.assertEqual(result['pixel']['actor_kind'],'telegram')
        self.assertGreater(result['pixel']['placed_at_ms'],0)
        self.assertEqual([e['actor_id'] for e in result['edits']],[99,42])
        public=self.state().get_json()
        self.assertEqual(len(public['pixels'][0]),4)
        self.assertNotIn('actor_id',public)
        self.assertEqual(self.client.get('/admin/pixel?x=0&y=0',headers=self.student).status_code,403)

    def test_owner_brush_web_fill_and_erasure_have_distinct_authors(self):
        self.fill(x0=0,y0=0,x1=2,y1=2,color=3)
        self.fill(self.web,x0=0,y0=0,x1=1,y1=1,color=5,actor_id=1084693264)
        first=self.client.get('/admin/pixel?x=0&y=0',headers=self.owner).get_json()
        self.assertEqual(first['pixel']['actor_kind'],'web_admin')
        self.assertIsNone(first['pixel']['actor_id'])
        self.assertEqual(first['edits'][1]['actor_id'],1084693264)
        self.fill(x0=0,y0=0,x1=1,y1=1,color=-1)
        erased=self.client.get('/admin/pixel?x=0&y=0',headers=self.owner).get_json()
        self.assertEqual(erased['pixel']['actor_id'],1084693264)
        self.assertEqual(erased['pixel']['color'],-1)
        self.assertEqual(erased['edits'][0]['actor_kind'],'telegram')
        initialize(self.path)
        persisted=create_app(self.path).test_client().get('/admin/pixel?x=0&y=0',headers=self.owner).get_json()
        self.assertEqual(persisted,erased)

    def test_lookup_is_bounded_and_empty_cells_are_explicit(self):
        for query in ('x=64&y=0','x=0&y=128','x=-65&y=0','x=1.5&y=0','x=0'):
            self.assertEqual(self.client.get('/admin/pixel?'+query,headers=self.owner).status_code,400)
        result=self.client.get('/admin/pixel?x=0&y=0',headers=self.web).get_json()
        self.assertIsNone(result['pixel'])
        self.assertEqual(result['edits'],[])

    def test_blocked_or_retried_requests_do_not_create_author_records(self):
        body={'x':0,'y':0,'color':3,'request_id':'same-request'}
        self.client.post('/place',json=body,headers=self.student)
        self.client.post('/place',json=body,headers=self.student)
        self.client.post('/place',json={**body,'request_id':'early-request'},headers=self.student)
        self.assertEqual(len(self.history().get_json()['events']),1)
        self.assertEqual(self.history().get_json()['events'][0]['actor_id'],42)

    def test_migration_recovers_only_evidenced_authors_without_losing_old_art(self):
        legacy=self.tmp.name+'/legacy.db'
        with sqlite3.connect(legacy) as db:
            db.executescript('''
                CREATE TABLE canvas_meta(id INTEGER PRIMARY KEY,revision INTEGER NOT NULL);
                INSERT INTO canvas_meta VALUES(1,9);
                CREATE TABLE pixels(x INTEGER,y INTEGER,color INTEGER,revision INTEGER,PRIMARY KEY(x,y)) WITHOUT ROWID;
                INSERT INTO pixels VALUES(1,2,3,9),(3,4,5,8);
                CREATE TABLE canvas_players(telegram_id INTEGER PRIMARY KEY,placed_at_ms INTEGER,request_id TEXT,x INTEGER,y INTEGER,color INTEGER,revision INTEGER);
                INSERT INTO canvas_players VALUES(42,123456,'old-request',1,2,3,9);
                CREATE TABLE canvas_history(id INTEGER PRIMARY KEY,revision INTEGER,at_ms INTEGER,x0 INTEGER,y0 INTEGER,x1 INTEGER,y1 INTEGER,color INTEGER,source TEXT);
                INSERT INTO canvas_history VALUES(1,8,123000,3,4,4,5,5,'admin'),(2,9,123456,1,2,2,3,3,'player');
                CREATE TABLE canvas_history_meta(id INTEGER PRIMARY KEY,started_at_ms INTEGER);
                INSERT INTO canvas_history_meta VALUES(1,123000);
            ''')
        app=create_app(legacy)
        initialize(legacy)
        client=app.test_client()
        known=client.get('/admin/pixel?x=1&y=2',headers=self.owner).get_json()
        unknown=client.get('/admin/pixel?x=3&y=4',headers=self.owner).get_json()
        self.assertEqual(known['pixel']['actor_id'],42)
        self.assertEqual(known['pixel']['placed_at_ms'],123456)
        self.assertEqual(known['edits'][0]['actor_id'],42)
        self.assertIsNone(unknown['pixel']['actor_id'])
        self.assertEqual(unknown['pixel']['actor_kind'],'unknown')
        self.assertEqual(unknown['pixel']['color'],5)
        self.assertEqual(len(unknown['edits']),1)
        self.assertIsNone(unknown['edits'][0]['actor_id'])


if __name__ == '__main__':
    unittest.main()
