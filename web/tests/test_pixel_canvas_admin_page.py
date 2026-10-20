import unittest
from unittest.mock import patch

import app as app_module


class CanvasAdminPageTest(unittest.TestCase):
    def setUp(self):
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

    def test_page_and_capability_require_website_admin_session(self):
        with patch('pixel_admin_auth.issue_admin_token') as issue:
            for route in ('/admin/canvas','/admin/canvas/token'):
                response = self.client.get(route)
                self.assertEqual(response.status_code,302)
                self.assertIn('/admin/login',response.headers['Location'])
            issue.assert_not_called()
        with self.client.session_transaction() as session:
            # VPN's unrelated administrator flag gives no canvas permissions.
            session['admin_authenticated'] = True
        self.assertEqual(self.client.get('/admin/canvas/token').status_code,302)

    def test_authorized_page_and_token_are_private_and_not_cross_origin(self):
        with self.client.session_transaction() as session:
            session['superadmin_authenticated'] = True
        with patch('pixel_admin_auth.issue_admin_token',return_value='test-capability'):
            for route in ('/admin/canvas','/admin/canvas/token'):
                response = self.client.get(route,headers={'User-Agent':'Telegram WebView','Origin':'https://untrusted.example'})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.headers['Cache-Control'],'private, no-store')
                self.assertNotIn('Access-Control-Allow-Origin',response.headers)
                self.assertEqual(response.headers['X-Frame-Options'],'DENY')
                self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
            self.assertEqual(response.get_json()['token'],'test-capability')
        self.client.get('/admin/logout')
        self.assertEqual(self.client.get('/admin/canvas/token').status_code,302)
