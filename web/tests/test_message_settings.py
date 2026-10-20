from pathlib import Path
import time
import unittest
from unittest.mock import patch

from flask import Flask
import schedule_api as api
from config import STATIC_VERSION
from tests.test_public_schedule_api import upstream
from tests.test_telegram_auth import signed_init_data


class MessageSettingsProxyTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'))
        self.app.config.update(TESTING=True, SECRET_KEY='test-only')
        self.app.register_blueprint(api.schedule_bp)
        self.client = self.app.test_client()
        api.limiter.reset()
        self.token_patch = patch('telegram_auth.get_bot_token', return_value='test-token')
        self.token_patch.start()
        self.addCleanup(self.token_patch.stop)
        self.auth = {'X-Telegram-Init-Data': signed_init_data('test-token', 42, int(time.time()))}

    def test_page_uses_local_versioned_assets_and_no_embedded_user_data(self):
        response = self.client.get('/settings')
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn('message_settings.js?v=' + STATIC_VERSION, text)
        self.assertNotIn('constructor-back', text)
        self.assertNotIn('К расписанию', text)
        self.assertIn('constructorPreview', text)
        self.assertNotIn('test-token', text)

    def test_personal_routes_reject_unsigned_identity_before_upstream(self):
        with patch.object(api, '_upstream') as upstream_call:
            for method, path, kwargs in [('get', '/api/message-settings?user_id=42', {}),
                ('put', '/api/message-settings', {'json': {'user_id': 42}}),
                ('post', '/api/message-settings/preview', {'json': {'preferences': {}}}),
                ('get', '/api/message-settings/image?entity=group:150', {})]:
                response = getattr(self.client, method)(path, **kwargs)
                self.assertEqual(response.status_code, 401)
                self.assertIn('no-store', response.headers['Cache-Control'])
            upstream_call.assert_not_called()

    def test_authenticated_get_and_put_preserve_signed_identity(self):
        for method in ('get', 'put'):
            preferences = {'preset': 'full', 'show_times': True}
            fake = upstream({'preferences': preferences})
            kwargs = {'headers': self.auth}
            if method == 'put': kwargs['json'] = preferences
            with patch.object(api.requests, method, return_value=fake) as call:
                response = getattr(self.client, method)('/api/message-settings', **kwargs)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(call.call_args.kwargs['headers'], self.auth)
            self.assertTrue(call.call_args.args[0].endswith('/message-settings'))
            fake.close.assert_called_once()
            self.assertIn('no-store', response.headers['Cache-Control'])

    def test_preview_is_forwarded_without_extra_identity(self):
        fake = upstream({'html': '<b>Расписание</b>', 'example': False})
        payload = {'preferences': {'show_times': True}, 'entity': 'group:150'}
        with patch.object(api.requests, 'post', return_value=fake) as call:
            response = self.client.post('/api/message-settings/preview', json=payload, headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(call.call_args.kwargs['json'], payload)
        self.assertEqual(call.call_args.kwargs['headers'], self.auth)
        fake.close.assert_called_once()

    def test_preview_rejects_extra_fields(self):
        with patch.object(api, '_upstream') as call:
            response = self.client.post('/api/message-settings/preview', json={'preferences': {}, 'user_id': 43}, headers=self.auth)
            self.assertEqual(response.status_code, 400)
            call.assert_not_called()

    def test_invalid_save_body_and_missing_media_type_are_rejected(self):
        with patch.object(api, '_upstream') as call:
            a = self.client.put('/api/message-settings', json=[], headers=self.auth)
            b = self.client.put('/api/message-settings', data='plain text', headers=self.auth)
        self.assertEqual(a.status_code, 400)
        self.assertEqual(b.status_code, 415)
        call.assert_not_called()

    def test_image_only_relays_png_and_closes_upstream(self):
        for content_type, expected in (('image/png', 200), ('text/html', 502)):
            fake = upstream(content_type=content_type)
            fake.content = b'png-fixture'
            with patch.object(api.requests, 'get', return_value=fake):
                response = self.client.get('/api/message-settings/image?entity=group:150', headers=self.auth)
            self.assertEqual(response.status_code, expected)
            fake.close.assert_called_once()
            self.assertIn('no-store', response.headers['Cache-Control'])

    def test_upstream_validation_and_authorization_errors_stay_errors(self):
        for status in (401, 403, 404, 422):
            fake = upstream({'detail': 'sensitive upstream path'}, status=status)
            with patch.object(api.requests, 'get', return_value=fake):
                response = self.client.get('/api/message-settings', headers=self.auth)
            self.assertEqual(response.status_code, status)
            self.assertNotIn('sensitive', response.get_data(as_text=True))
            fake.close.assert_called_once()

    def test_main_entry_is_a_small_footer_link_not_a_schedule_card(self):
        import app as app_module
        response = app_module.app.test_client().get('/')
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn('id="messageSettingsLink" class="feedback-link message-settings-link"', text)
        self.assertLess(text.index('id="reviewCard"'), text.index('id="messageSettingsLink"'))
        self.assertNotIn('Настроить сообщения бота', text)
        self.assertIn('kepik-entry.js?v=' + STATIC_VERSION, text)

    def test_worker_and_changed_assets_share_one_release_version(self):
        import app as app_module
        response = app_module.app.test_client().get('/service-worker.js')
        worker = response.get_data(as_text=True)
        self.assertIn('kkepik-shell-' + STATIC_VERSION, worker)
        for asset in ('css/main.css', 'css/message_settings.css', 'js/message_settings.js', 'js/kepik-entry.js'):
            self.assertIn('/static/' + asset + '?v=' + STATIC_VERSION, worker)
        self.assertIn('no-store', response.headers['Cache-Control'])
        response.close()


if __name__ == '__main__':
    unittest.main()
