import hashlib
import asyncio
import hmac
import json
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch

from fastapi import FastAPI

import duty_api as api
from duties.store import Store, MSK, parse_people


class AsgiClient:
    """Exercise the HTTP/ASGI boundary without an extra test-only HTTP client."""
    def __init__(self, app):
        self.app = app

    def request(self, method, path, *, json=None, headers=None):
        import json as codec
        body = b'' if json is None else codec.dumps(json).encode()
        raw_headers = [(b'host', b'testserver')]
        raw_headers.extend((key.lower().encode(),value.encode()) for key,value in (headers or {}).items())
        if json is not None:
            raw_headers.extend([(b'content-type',b'application/json'),(b'content-length',str(len(body)).encode())])
        scope = dict(type='http',asgi={'version':'3.0'},http_version='1.1',method=method,scheme='http',
                     path=path,raw_path=path.encode(),query_string=b'',root_path='',headers=raw_headers,
                     server=('testserver',80),client=('127.0.0.1',1234))
        messages=[]
        async def run():
            received=False
            async def receive():
                nonlocal received
                if not received:
                    received=True
                    return dict(type='http.request',body=body,more_body=False)
                await asyncio.Event().wait()
            async def send(message):
                messages.append(message)
            await self.app(scope,receive,send)
        asyncio.run(run())
        class Result:
            status_code=next(m['status'] for m in messages if m['type']=='http.response.start')
            text=b''.join(m.get('body',b'') for m in messages if m['type']=='http.response.body').decode()
            def json(self):
                return codec.loads(self.text)
        return Result()

    def get(self,path,**kwargs): return self.request('GET',path,**kwargs)
    def post(self,path,**kwargs): return self.request('POST',path,**kwargs)
    def put(self,path,**kwargs): return self.request('PUT',path,**kwargs)


class DutyApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profiles = self.root / 'database.db'
        with sqlite3.connect(self.profiles) as db:
            db.execute('CREATE TABLE users(user_id INTEGER PRIMARY KEY,role TEXT,name_or_group TEXT,class_group TEXT)')
            db.executemany('INSERT INTO users VALUES(?,?,?,NULL)', [(uid,'Я студент','103-Д9-3ИНС') for uid in (99,11,12,13,14,15,16,100)])
        self.store = Store(self.root / 'duties.db')
        self.now = datetime(2026,10,19,8,0,tzinfo=MSK)
        self.sid = self.store.create(99,'103-Д9-3ИНС',parse_people('Иван + Анна\nПётр + Мария'),parse_people('Ольга',reserves=True),now=self.now.replace(hour=7))
        with self.store.connect(True) as db:
            for uid,m in enumerate(db.execute('SELECT id FROM members ORDER BY id').fetchall(),11):
                db.execute('UPDATE members SET uid=? WHERE id=?',(uid,m['id']))
                db.execute("INSERT INTO access VALUES(?,?,'member',0)",(self.sid,uid))
        for uid,name in [(11,'Иван'),(12,'Анна'),(13,'Пётр'),(14,'Мария'),(15,'Ольга'),(16,'Максим'),(99,'Староста'),(100,'Другой староста')]:
            self.store.observe(uid,name,'student_'+str(uid),'103-Д9-3ИНС',now=self.now)
        app = FastAPI();app.include_router(api.router)
        app.dependency_overrides[api.get_store] = lambda: self.store
        self.client = AsgiClient(app)
        for name,value in [('config.get_bot_token',lambda:'test-token'),('config.get_db_path',lambda:str(self.profiles)),
                           ('config.DATA_DIR',str(self.root)),('duties.store.clock',lambda:self.now),('duties.views.clock',lambda:self.now)]:
            context=patch(name,value);context.start();self.addCleanup(context.stop)

    def auth(self, uid=99, age=0):
        data={'auth_date':str(int(time.time())-age),'user':json.dumps({'id':uid})}
        secret=hmac.new(b'WebAppData',b'test-token',hashlib.sha256).digest()
        data['hash']=hmac.new(secret,'\n'.join(f'{k}={v}' for k,v in sorted(data.items())).encode(),hashlib.sha256).hexdigest()
        return {'X-Telegram-Init-Data':urlencode(data)}

    def action(self, body, uid=99):
        return self.client.post(f'/duties/{self.sid}/actions',json=body,headers=self.auth(uid))

    def people(self, uid=99, space=None):
        return self.client.post('/duties/people',json={'space':space},headers=self.auth(uid))

    def choice_refs(self, uid=100, space=None):
        response=self.people(uid,space)
        self.assertEqual(response.status_code,200,response.text)
        return {p['name']:p['ref'] for p in response.json()['people']}

    def test_unsigned_and_expired_auth_cannot_read_any_personal_data(self):
        for path in ('/duties',f'/duties/{self.sid}'):
            self.assertEqual(self.client.get(path).status_code,401)
            self.assertEqual(self.client.get(path,headers=self.auth(age=90000)).status_code,401)

    def test_signed_outsider_and_regular_member_cannot_manage_list(self):
        self.assertEqual(self.client.get(f'/duties/{self.sid}',headers=self.auth(100)).status_code,403)
        self.assertEqual(self.client.put(f'/duties/{self.sid}/settings',json={},headers=self.auth(11)).status_code,403)
        self.assertEqual(self.action({'action':'swap','member':1,'other':5},11).status_code,400)

    def test_detail_includes_forecast_and_hides_account_ids_from_members(self):
        before=self.client.get(f'/duties/{self.sid}',headers=self.auth())
        self.assertEqual(before.status_code,200,before.text)
        self.assertEqual(len(before.json()['forecast']),28)
        student=self.client.get(f'/duties/{self.sid}',headers=self.auth(11)).json()
        self.assertTrue(student['members'][0]['mine'])
        self.assertNotIn('telegram_id',student['members'][0])
        self.assertNotIn('username',student['members'][0])
        self.assertEqual(student['events'],[])

    def test_reserve_swap_changes_same_store_and_keeps_identities(self):
        response=self.action({'action':'swap','member':5,'other':1})
        self.assertEqual(response.status_code,200,response.text)
        members={m['id']:m for m in self.store.view(self.sid,99)['members']}
        self.assertEqual(members[5]['pair'],1)
        self.assertIsNone(members[1]['pair'])
        self.assertEqual(members[1]['uid'],11)

    def test_foreign_reference_is_rejected_even_for_list_owner(self):
        other=self.store.create(100,'116-КД9-3ССА',parse_people('Чужой + Другой'),[],now=self.now)
        foreign=self.store.view(other,100)['members'][0]['id']
        for body in ({'action':'swap','member':1,'other':foreign},{'action':'rename','member':foreign,'value':'X'}):
            self.assertEqual(self.action(body).status_code,400)
        self.assertEqual(self.store.view(other,100)['members'][0]['name'],'Чужой')

    def test_settings_and_action_values_are_validated(self):
        for body in ({'audience':'all'},{'days':[]},{'days':[7]},{'time':'20:00'}, {'owner_daily':'false'}, {'owner':100}):
            self.assertIn(self.client.put(f'/duties/{self.sid}/settings',json=body,headers=self.auth()).status_code,(400,422))
        for body in ({'action':'active','member':1,'value':'false'},{'action':'rename','member':1,'value':True},{'action':'archive','uid':99}):
            self.assertIn(self.action(body).status_code,(400,422))

    def test_person_can_decline_only_their_own_assignment(self):
        did=self.store.ensure_day(self.sid,self.now,'available')
        self.assertEqual(self.action({'action':'decline','day':did},13).status_code,400)
        self.assertEqual(self.action({'action':'decline','day':did},11).status_code,200)
        self.assertEqual(self.store.day_view(did,99)['offers'][0]['kind'],'reserve')

    def test_preview_is_read_only_and_create_checks_same_group_before_linking(self):
        refs=self.choice_refs(100)
        body={'pairs':[[refs['Иван'],refs['Анна']]],'reserves':[refs['Пётр']]}
        response=self.client.post('/duties/preview',json=body,headers=self.auth(100))
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['pairs'],[[{'name':'Иван'},{'name':'Анна'}]])
        self.assertNotIn('resolved_ids',response.json())
        self.assertEqual(len(self.store.lists(100)),0)
        body['preview_token']=response.json()['preview_token']
        self.assertEqual(self.client.post('/duties',json=body,headers=self.auth(100)).status_code,200)
        created=self.store.lists(100)[0]['id']
        self.assertEqual({m['uid'] for m in self.store.view(created,100)['members']},{11,12,13})

    def test_people_return_only_display_names_and_opaque_requester_scoped_choices(self):
        with sqlite3.connect(self.profiles) as db:
            db.execute("INSERT INTO users VALUES(201,'Я студент','116-КД9-3ССА',NULL)")
        self.store.observe(201,'Чужая группа','foreign_username','116-КД9-3ССА',now=self.now)
        response=self.people().json()
        self.assertEqual(set(response),{'people','expires_at'})
        self.assertNotIn('Чужая группа',json.dumps(response,ensure_ascii=False))
        for value in response['people']:
            self.assertEqual(set(value),{'name','ref'})
            self.assertEqual(len(value['ref']),32)
        detail=self.client.get(f'/duties/{self.sid}',headers=self.auth()).json()
        self.assertNotIn('telegram_id',detail['members'][0])
        self.assertNotIn('username',detail['members'][0])
        self.assertTrue(all(set(e)=={'action','created'} for e in detail['events']))

    def test_catalog_does_not_accept_an_arbitrary_group_identity_or_paging(self):
        for body in ({'group':'116-КД9-3ССА'},{'uid':100},{'offset':10},{'all':True}):
            self.assertEqual(self.client.post('/duties/people',json=body,headers=self.auth()).status_code,422)
        self.assertEqual(self.client.post('/duties/people',json={}).status_code,401)
        self.assertEqual(self.people(11,self.sid).status_code,403)
        self.assertEqual(self.people(100,self.sid).status_code,403)

    def test_group_hopping_cannot_enumerate_another_class_and_lock_survives_restart(self):
        self.assertEqual(self.people().status_code,200)
        with sqlite3.connect(self.profiles) as db:
            db.execute("UPDATE users SET name_or_group='116-КД9-3ССА' WHERE user_id=99")
        api.people_directory.cache_clear()
        self.assertEqual(self.people().status_code,403)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT group_name FROM picker_access WHERE uid=99').fetchone()[0],'103-Д9-3ИНС')

    def test_choice_replay_by_another_person_or_in_another_context_is_rejected(self):
        refs=self.choice_refs(100)
        body={'pairs':[[refs['Иван'],refs['Анна']]]}
        self.assertEqual(self.client.post('/duties/preview',json=body,headers=self.auth(99)).status_code,400)
        self.assertEqual(self.client.post(f'/duties/{self.sid}/roster',json=body,headers=self.auth()).status_code,400)
        self.assertEqual(self.client.post('/duties/preview',json={'pairs':[['A'*32,'B'*32]]},headers=self.auth(100)).status_code,400)

    def test_removed_group_members_and_expired_selections_cannot_be_added(self):
        refs=self.choice_refs(100)
        body={'pairs':[[refs['Иван'],refs['Анна']]]}
        with sqlite3.connect(self.profiles) as db:
            db.execute("UPDATE users SET name_or_group='116-КД9-3ССА' WHERE user_id=11")
        self.assertEqual(self.client.post('/duties/preview',json=body,headers=self.auth(100)).status_code,400)
        with self.store.connect(True) as db:
            db.execute('UPDATE picker_choices SET expires=0')
        self.assertEqual(self.client.post('/duties/preview',json=body,headers=self.auth(100)).status_code,400)

    def test_confirmation_prevents_silent_roster_or_setting_changes(self):
        refs=self.choice_refs(100)
        body={'pairs':[[refs['Иван'],refs['Анна']]]}
        self.assertEqual(self.client.post('/duties',json=body,headers=self.auth(100)).status_code,400)
        preview=self.client.post('/duties/preview',json=body,headers=self.auth(100)).json()
        body.update(preview_token=preview['preview_token'],options={'time':'07:45'})
        self.assertEqual(self.client.post('/duties',json=body,headers=self.auth(100)).status_code,400)
        self.assertFalse(self.store.lists(100))

    def test_repeated_catalog_requests_are_limited_across_directory_instances(self):
        for _ in range(5):self.assertEqual(self.people().status_code,200)
        api.people_directory.cache_clear()
        self.assertEqual(self.people().status_code,429)

    def test_tracking_a_second_group_does_not_grant_access_to_its_directory(self):
        with sqlite3.connect(self.profiles) as db:
            db.execute('CREATE TABLE tracked_groups(user_id INTEGER,group_name TEXT,slot INTEGER)')
            db.execute("INSERT INTO tracked_groups VALUES(99,'116-КД9-3ССА',2)")
            db.execute("UPDATE users SET name_or_group='116-КД9-3ССА' WHERE user_id=16")
            db.execute("INSERT INTO tracked_groups VALUES(16,'103-Д9-3ИНС',2)")
        refs=self.choice_refs(100)
        self.assertIn('Максим',refs)
        self.assertEqual(api.people_directory(self.store).scope(99),'103-Д9-3ИНС')

    def test_old_raw_profile_lookup_and_numeric_ids_are_not_an_alternate_directory(self):
        for body in ({'pairs':'11 + 12'},{'pairs':[[11,12]]},{'pairs':[['11','12']]}, {'pairs':[], 'group':'116-КД9-3ССА'}):
            self.assertEqual(self.client.post('/duties/preview',json=body,headers=self.auth(100)).status_code,422)

    def test_append_uses_own_list_scope_excludes_existing_people_and_links_selected_account(self):
        refs=self.choice_refs(99,self.sid)
        self.assertNotIn('Иван',refs)
        self.assertIn('Максим',refs)
        r=self.client.post(f'/duties/{self.sid}/roster',json={'reserves':[refs['Максим']]},headers=self.auth())
        self.assertEqual(r.status_code,200,r.text)
        self.assertTrue(any(m['uid']==16 for m in self.store.view(self.sid,99)['members']))

    def test_repeated_person_and_non_ascii_confirmation_are_rejected_without_server_error(self):
        refs=self.choice_refs(100)
        self.assertEqual(self.client.post('/duties/preview',json={'pairs':[[refs['Иван'],refs['Иван']]]},headers=self.auth(100)).status_code,400)
        self.assertEqual(self.client.post('/duties',json={'pairs':[[refs['Иван'],refs['Анна']]],'preview_token':'1793000000.😇'},headers=self.auth(100)).status_code,422)

    def test_duplicate_names_without_identifiers_are_not_silently_assigned(self):
        self.store.observe(11,'- ',None,'103-Д9-3ИНС',now=self.now)
        self.store.observe(12,'-',None,'103-Д9-3ИНС',now=self.now)
        refs=self.choice_refs(100)
        self.assertNotIn('-',refs)

    def test_generated_id_suffixes_are_hidden_in_private_web_views(self):
        with self.store.connect(True) as db:
            db.execute("UPDATE members SET name='Иван (ID 11)' WHERE uid=11")
        did=self.store.ensure_day(self.sid,self.now,'available')
        r=self.client.get(f'/duties/{self.sid}',headers=self.auth())
        self.assertEqual(r.status_code,200,r.text)
        self.assertNotIn('(ID 11)',r.text)


if __name__=='__main__':
    unittest.main()
