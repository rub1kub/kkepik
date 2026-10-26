from pathlib import Path
import time
import unittest
from unittest.mock import patch

from flask import Flask
import schedule_api as api
from config import STATIC_VERSION
from tests.test_public_schedule_api import upstream
from tests.test_telegram_auth import signed_init_data


class DutiesWebTest(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]
        app=Flask(__name__,template_folder=str(root/'templates'),static_folder=str(root/'static'))
        app.config.update(TESTING=True)
        app.register_blueprint(api.schedule_bp)
        self.client=app.test_client()
        api.limiter.reset()
        context=patch('telegram_auth.get_bot_token',return_value='test-token');context.start();self.addCleanup(context.stop)
        self.auth={'X-Telegram-Init-Data':signed_init_data('test-token',99,int(time.time()))}

    def test_page_is_public_shell_with_versioned_assets_and_no_private_data(self):
        r=self.client.get('/duties')
        self.assertEqual(r.status_code,200)
        text=r.get_data(as_text=True)
        for asset in ('js/duties.js','css/duties.css'):
            self.assertIn(asset+'?v='+STATIC_VERSION,text)
        self.assertIn('noindex, nofollow',text)
        self.assertNotIn('test-token',text)
        self.assertIn('dutyTabs',text)

    def test_every_personal_route_requires_verified_telegram_identity(self):
        with patch.object(api,'_upstream') as call:
            for method,path in (('get','/api/duties'),('get','/api/duties/1'),('post','/api/duties/people'),('post','/api/duties/1/actions'),('put','/api/duties/1/settings')):
                r=getattr(self.client,method)(path,json={'user_id':99})
                self.assertEqual(r.status_code,401)
                self.assertIn('no-store',r.headers['Cache-Control'])
            call.assert_not_called()

    def test_proxy_preserves_method_payload_and_signed_identity(self):
        for method,path,payload in (('get','/api/duties',None),('get','/api/duties/1',None),
                                    ('post','/api/duties/people',{'space':1}),
                                    ('post','/api/duties/1/actions',{'action':'swap','member':1,'other':4}),
                                    ('put','/api/duties/1/settings',{'audience':'assigned'})):
            fake=upstream({'ok':True})
            with patch.object(api.requests,method,return_value=fake) as call:
                r=getattr(self.client,method)(path,headers=self.auth,json=payload)
            self.assertEqual(r.status_code,200,r.get_data(as_text=True))
            self.assertEqual(call.call_args.kwargs['headers'],self.auth)
            self.assertTrue(call.call_args.args[0].endswith(path.removeprefix('/api')))
            if payload is not None:self.assertEqual(call.call_args.kwargs['json'],payload)
            fake.close.assert_called_once()

    def test_unknown_paths_and_invalid_bodies_never_reach_upstream(self):
        with patch.object(api,'_upstream') as call:
            self.assertEqual(self.client.get('/api/duties/1/../../admin',headers=self.auth).status_code,404)
            self.assertEqual(self.client.post('/api/duties/1/actions',headers=self.auth,json=[]).status_code,400)
            self.assertEqual(self.client.post('/api/duties/1/actions',headers=self.auth,data='x').status_code,415)
            call.assert_not_called()

    def test_people_are_not_exposed_by_get_or_an_arbitrary_group_path(self):
        with patch.object(api,'_upstream') as call:
            for path in ('/api/duties/people','/api/duties/people/116-КД9-3ССА','/api/duties/../user/99'):
                self.assertEqual(self.client.get(path,headers=self.auth).status_code,404)
            call.assert_not_called()

    def test_clear_duty_error_survives_proxy_and_errors_are_not_cached(self):
        fake=upstream({'detail':'Сначала отметьте незакрытые дежурства этих участников.'},status=400)
        with patch.object(api.requests,'post',return_value=fake):
            r=self.client.post('/api/duties/1/actions',headers=self.auth,json={'action':'swap','member':1,'other':4})
        self.assertEqual(r.status_code,400)
        self.assertIn('Сначала отметьте',r.get_json()['detail'])
        self.assertIn('no-store',r.headers['Cache-Control'])

    def test_home_button_follows_schedule_and_private_page_is_never_cached_by_worker(self):
        root=Path(__file__).resolve().parents[1]
        source=(root/'templates/index.html').read_text()
        self.assertLess(source.index('Найти расписание'),source.index('id="dutiesLink"'))
        worker=(root/'static/service-worker.js').read_text()
        self.assertIn("url.pathname === '/duties'",worker)
        self.assertIn('kkepik-shell-'+STATIC_VERSION,worker)


if __name__=='__main__':unittest.main()
