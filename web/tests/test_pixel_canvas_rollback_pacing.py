import sqlite3
import threading
import random
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from pixel_canvas import create_app, initialize, CANVAS_BOUNDS
from tests import test_pixel_canvas_admin as helpers


class CanvasRollbackPacingTest(unittest.TestCase):
    headers=helpers.CanvasAdminTest.headers
    fill=helpers.CanvasAdminTest.fill
    history=helpers.CanvasAdminTest.history
    def setUp(self):
        helpers.CanvasAdminTest.setUp(self)
        self.clock=2_000_000_000_000
        self.clock_patch=patch('pixel_canvas.time.time_ns',side_effect=lambda:self.clock*1_000_000)
        self.clock_patch.start()
        self.serial=0
    def tearDown(self):
        self.clock_patch.stop()
        helpers.CanvasAdminTest.tearDown(self)
    def put(self,uid=42,x=0,y=0,color=5,advance=1000,**extra):
        self.clock+=advance;self.serial+=1
        return self.client.post('/place',headers=self.headers(uid),json={
            'x':x,'y':y,'color':color,'request_id':f'placement-{self.serial}',**extra})
    def state(self,uid=42,since=0):
        return self.client.get('/state',headers=self.headers(uid),query_string={**CANVAS_BOUNDS,'since':since}).get_json()
    def count(self,n):
        with sqlite3.connect(self.path) as db:db.execute('DELETE FROM miniapp_presence')
        for uid in ([42]+list(range(100,100+n-1)) if n else []):
            response=self.client.post('/presence',headers=self.headers(uid),json={'session_id':'online-session','sequence':1,'active':True})
            self.assertEqual(response.status_code,200)
    def preview(self,uid=42,headers=None):
        return self.client.get('/admin/rollback',headers=headers or self.owner,query_string={'telegram_id':uid})
    def undo(self,uid=42,headers=None,preview=None,request_id=None):
        data=preview if preview is not None else self.preview(uid).get_json()
        return self.client.post('/admin/rollback',headers=headers or self.owner,json={
            'telegram_id':uid,'through_id':data['through_id'],'request_id':request_id or f'rollback-{uid}-{data["through_id"]}'})
    def colors(self):
        return {(p[0],p[1]):p[2] for p in self.state()['pixels']}

    def test_real_presence_tiers_and_exact_cooldown_boundaries(self):
        for n,period in [(0,250),(4,250),(5,500),(8,500),(9,1000),(12,1000)]:
            with self.subTest(online=n):
                with sqlite3.connect(self.path) as db:db.execute('DELETE FROM canvas_players')
                self.count(n)
                first=self.put()
                self.assertEqual(first.status_code,200)
                self.assertEqual(first.get_json()['cooldown_ms'],period)
                self.assertEqual(self.state()['cooldown_ms'],period)
                denied=self.put(advance=period-1,cooldown_ms=1,online_count=0)
                self.assertEqual(denied.status_code,429)
                self.assertEqual(denied.get_json()['remaining_ms'],1)
                self.assertEqual(self.put(advance=1).status_code,200)

    def test_current_slot_survives_presence_change_worker_restart_and_retry(self):
        self.count(9);first=self.put()
        self.count(0)
        restarted=create_app(self.path).test_client()
        self.clock+=999
        payload={'x':0,'y':0,'color':5,'request_id':'after-restart'}
        self.assertEqual(restarted.post('/place',headers=self.student,json=payload).status_code,429)
        self.clock+=1
        self.assertEqual(restarted.post('/place',headers=self.student,json=payload).get_json()['cooldown_ms'],250)
        self.count(9)
        self.clock+=249
        retry=restarted.post('/place',headers=self.student,json=payload).get_json()
        self.assertEqual(retry['remaining_ms'],1)
        self.assertEqual(self.put(advance=0).status_code,429)
        self.assertEqual(self.put(advance=1).get_json()['cooldown_ms'],1000)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT cooldown_ms FROM canvas_players WHERE telegram_id=42').fetchone()[0],1000)

    def test_adaptive_rate_is_shared_by_concurrent_tabs(self):
        self.count(4);gate=threading.Barrier(8)
        def attempt(i):
            gate.wait()
            return self.app.test_client().post('/place',headers=self.student,json={'x':i,'y':0,'color':1,'request_id':f'parallel-{i}'}).status_code
        with ThreadPoolExecutor(max_workers=8) as pool:statuses=list(pool.map(attempt,range(8)))
        self.assertEqual(statuses.count(200),1);self.assertEqual(statuses.count(429),7)

    def test_recent_drawers_cannot_hide_presence_to_accelerate(self):
        for uid in range(42,51):
            self.assertEqual(self.put(uid=uid).status_code,200)
        self.assertEqual(self.state()['online_count'],0)
        self.assertEqual(self.state()['cooldown_ms'],1000)
        self.assertEqual(self.put().get_json()['cooldown_ms'],1000)
        self.clock+=35000
        self.assertEqual(self.state()['cooldown_ms'],250)

    def test_undo_restores_previous_colors_and_preserves_later_other_users(self):
        self.put(uid=43,color=4)
        self.put(color=5);self.put(color=6);self.put(x=1,color=7);self.put(x=2,color=8)
        self.put(uid=44,x=1,color=9)
        before=self.state()['revision'];preview=self.preview().get_json()
        self.assertEqual((preview['undone_events'],preview['changed_cells']),(4,2))
        # Preview has no effect, and the operation has one global revision.
        self.assertEqual(self.state()['revision'],before)
        result=self.undo(preview=preview,headers=self.web).get_json()
        self.assertEqual((result['undone_events'],result['changed_cells']),(4,2))
        self.assertEqual(self.colors(),{(0,0):4,(1,0):9,(2,0):-1})
        delta=self.state(since=before)
        self.assertFalse(delta['full']);self.assertEqual(len(delta['pixels']),2)
        self.assertEqual({p[3] for p in delta['pixels']},{before+1})
        events=self.history().get_json()['events']
        self.assertEqual(len(events),8)
        self.assertEqual([e['source'] for e in events[-2:]],['rollback','rollback'])
        self.assertTrue(all(e['actor_kind']=='web_admin' and e['actor_id'] is None for e in events[-2:]))

    def test_two_users_undo_does_not_resurrect_already_removed_pixels(self):
        self.fill(self.web,x0=0,y0=0,x1=1,y1=1,color=4)
        self.put(color=5);self.put(uid=43,color=6)
        self.undo(43);self.assertEqual(self.colors()[(0,0)],5)
        self.undo(42);self.assertEqual(self.colors()[(0,0)],4)
        before=self.state()['revision'];self.assertEqual(self.undo(43).get_json()['undone_events'],0)
        self.assertEqual(self.state()['revision'],before)

    def test_preview_cutoff_keeps_new_edits_by_target_and_others(self):
        self.put(color=3);self.put(x=1,color=4)
        preview=self.preview().get_json()
        self.put(color=5);self.put(uid=43,x=1,color=6)
        result=self.undo(preview=preview).get_json()
        self.assertEqual(result['undone_events'],2);self.assertEqual(result['changed_cells'],0)
        self.assertEqual(self.colors(),{(0,0):5,(1,0):6})
        self.assertEqual(self.preview().get_json()['undone_events'],1)
        self.undo();self.assertEqual(self.colors(),{(0,0):-1,(1,0):6})

    def test_existing_baseline_and_unknown_authors_are_preserved(self):
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO pixels (x,y,color,revision) VALUES (0,0,4,1)')
            db.execute('UPDATE canvas_meta SET revision=1')
            db.execute('DELETE FROM canvas_history_meta')
        initialize(self.path)
        self.put(color=5);self.undo()
        self.assertEqual(self.colors()[(0,0)],4)
        self.assertEqual(self.history().get_json()['events'][0]['source'],'baseline')

    def test_owner_rectangles_and_erase_undo_with_other_overlap(self):
        self.fill(self.web,x0=0,y0=0,x1=3,y1=3,color=4)
        self.fill(x0=0,y0=0,x1=3,y1=3,color=7)
        self.fill(x0=0,y0=0,x1=1,y1=1,color=-1)
        self.put(uid=43,x=1,y=1,color=9)
        result=self.undo(1084693264).get_json()
        self.assertEqual(result['undone_events'],2);self.assertEqual(result['changed_cells'],8)
        for cell,color in self.colors().items():self.assertEqual(color,9 if cell==(1,1) else 4)

    def test_full_board_rollback_stays_finite_and_keeps_erasure_tombstones(self):
        self.fill(**CANVAS_BOUNDS,color=3)
        result=self.undo(1084693264).get_json()
        self.assertEqual(result['changed_cells'],32768)
        state=self.state();self.assertEqual(len(state['pixels']),32768)
        self.assertTrue(all(p[2]==-1 for p in state['pixels']))

    def test_legacy_outside_coordinates_are_not_rewritten_or_expanded(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO pixels (x,y,color,revision,actor_id,actor_kind) VALUES (64,0,5,1,42,'telegram')")
            db.execute("INSERT INTO canvas_history (revision,at_ms,x0,y0,x1,y1,color,source,actor_id,actor_kind) VALUES (1,1,64,0,65,1,5,'player',42,'telegram')")
            db.execute('UPDATE canvas_meta SET revision=1')
        result=self.undo().get_json()
        self.assertEqual((result['undone_events'],result['changed_cells']),(1,0))
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT x,color,revision FROM pixels').fetchall(),[(64,5,1)])

    def test_retry_is_idempotent_does_not_remove_new_writes_and_is_actor_bound(self):
        self.put();preview=self.preview().get_json()
        self.assertEqual(self.undo(preview=preview,request_id='same-rollback').status_code,200)
        self.put(uid=43,color=9)
        before=self.state()['revision']
        retry=self.undo(preview=preview,request_id='same-rollback')
        self.assertEqual(retry.status_code,200);self.assertEqual(retry.get_json()['changed_cells'],1)
        self.assertEqual(self.colors()[(0,0)],9);self.assertEqual(self.state()['revision'],before)
        self.assertEqual(self.undo(preview=preview,request_id='same-rollback',headers=self.web).status_code,409)
        self.assertEqual(self.undo(43,preview=preview,request_id='same-rollback').status_code,409)

    def test_authorization_invalid_payload_and_foreign_origin_have_no_effect(self):
        self.put();preview=self.preview().get_json()
        self.assertEqual(self.preview(headers=self.student).status_code,403)
        self.assertEqual(self.undo(headers=self.student,preview=preview).status_code,403)
        self.assertEqual(self.undo(headers={**self.owner,'Origin':'https://evil.example'},preview=preview).status_code,403)
        for extra in [{'telegram_id':True},{'through_id':True},{'request_id':'oops!'}, {'telegram_id':-1}]:
            response=self.client.post('/admin/rollback',headers=self.owner,json={'telegram_id':42,'through_id':preview['through_id'],'request_id':'invalid-test',**extra})
            self.assertEqual(response.status_code,400)
        self.assertEqual(self.undo(preview={'through_id':preview['through_id']+100}).status_code,409)
        self.assertEqual(self.colors()[(0,0)],5)
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM canvas_rollbacks').fetchone()[0],0)

    def test_atomic_busy_failure_and_concurrent_retries(self):
        self.put();preview=self.preview().get_json()
        with sqlite3.connect(self.path,isolation_level=None) as db:
            db.execute('BEGIN IMMEDIATE')
            self.assertEqual(self.undo(preview=preview).status_code,503)
            db.rollback()
        gate=threading.Barrier(2)
        def attempt(_):
            gate.wait()
            return self.app.test_client().post('/admin/rollback',headers=self.owner,json={'telegram_id':42,'through_id':preview['through_id'],'request_id':'parallel-rollback'}).get_json()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,range(2)))
        self.assertEqual(results[0],results[1]);self.assertEqual(self.colors()[(0,0)],-1)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM canvas_rollbacks').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM canvas_rollback_edits').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM canvas_history WHERE source='rollback'").fetchone()[0],1)

    def test_rollback_does_not_reset_cooldown_ban_or_event_and_survives_restart(self):
        receipt=self.put().get_json();preview=self.preview().get_json()
        self.client.post('/admin/ban',headers=self.owner,json={'telegram_id':42,'banned':True})
        self.client.post('/admin/event/start',headers=self.owner,json={})
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE canvas_event SET ends_at_ms=?',(self.clock-1,))
            player_before=db.execute('SELECT * FROM canvas_players WHERE telegram_id=42').fetchone()
        self.assertEqual(self.undo(preview=preview).status_code,200)
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT * FROM canvas_players WHERE telegram_id=42').fetchone(),player_before)
        restarted=create_app(self.path).test_client().get('/state',headers=self.student,query_string=CANVAS_BOUNDS).get_json()
        self.assertTrue(restarted['banned']);self.assertTrue(restarted['event']['finished'])
        self.assertEqual(restarted['pixels'][0][2],-1)
        self.assertGreater(restarted['pixels'][0][3],receipt['pixel'][3])

    def test_interleaved_rollbacks_match_independent_per_cell_edit_stacks(self):
        rng=random.Random(79031);stacks={}
        def write():
            uid=rng.choice([42,43,44]);cell=(rng.randrange(4),rng.randrange(4));color=rng.randrange(16)
            self.assertEqual(self.put(uid=uid,x=cell[0],y=cell[1],color=color).status_code,200)
            with sqlite3.connect(self.path) as db:edit=db.execute('SELECT MAX(id) FROM canvas_history').fetchone()[0]
            stacks.setdefault(cell,[]).append((edit,uid,color))
        for step in range(90):
            if rng.random()<.65:
                write()
            else:
                uid=rng.choice([42,43,44]);preview=self.preview(uid).get_json()
                if rng.random()<.5:write()  # real activity between preview and confirmation
                self.assertEqual(self.undo(uid,preview=preview,request_id=f'model-undo-{step}').status_code,200)
                for cell,edits in stacks.items():
                    stacks[cell]=[entry for entry in edits if not (entry[1]==uid and entry[0]<=preview['through_id'])]
            actual={cell:color for cell,color in self.colors().items() if color>=0}
            expected={cell:edits[-1][2] for cell,edits in stacks.items() if edits}
            self.assertEqual(actual,expected,step)


if __name__=='__main__':unittest.main()
