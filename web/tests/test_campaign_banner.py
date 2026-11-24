from datetime import datetime, timedelta
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, render_template_string
import campaign_banner as campaign
from tests.test_telegram_auth import signed_init_data


class CampaignBannerTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(campaign.campaign_banner_bp)
        self.app.context_processor(campaign.banner_context)
        self.client = self.app.test_client()

    def test_display_window_is_exactly_72_hours(self):
        self.assertEqual(campaign.ENDS_AT - campaign.STARTS_AT, timedelta(hours=72))

    def test_window_boundaries(self):
        self.assertFalse(campaign.campaign_state(campaign.STARTS_AT - timedelta(microseconds=1))['active'])
        self.assertTrue(campaign.campaign_state(campaign.STARTS_AT)['active'])
        self.assertTrue(campaign.campaign_state(campaign.ENDS_AT - timedelta(microseconds=1))['active'])
        self.assertFalse(campaign.campaign_state(campaign.ENDS_AT)['active'])

    def test_naive_clock_is_rejected(self):
        with self.assertRaises(ValueError):
            campaign.campaign_state(campaign.STARTS_AT.replace(tzinfo=None))

    def test_status_is_public_uncached_and_contains_no_user_data(self):
        response = self.client.get('/api/campaign/banner')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(set(response.get_json()),
                         {'id', 'active', 'starts_at_ms', 'ends_at_ms', 'server_now_ms'})

    def test_expired_campaign_disappears_from_server_template(self):
        expired = campaign.campaign_state(campaign.ENDS_AT)
        with patch.object(campaign, 'campaign_state', return_value=expired), self.app.test_request_context('/'):
            rendered = render_template_string('{% if championship_banner %}advertisement{% endif %}')
        self.assertEqual(rendered, '')

    def test_active_template_has_the_exact_advertiser_link(self):
        active = campaign.campaign_state(campaign.STARTS_AT)
        with patch.object(campaign, 'campaign_state', return_value=active), self.app.test_request_context('/'):
            rendered = render_template_string('{{ championship_banner.url }}')
        self.assertEqual(rendered, 'https://u.to/_5nhIg')


class OwnerPreviewTest(unittest.TestCase):
    path = '/api/campaign/owner-preview'

    def setUp(self):
        app = Flask(__name__, template_folder=str(Path(campaign.__file__).parent / 'templates'))
        app.register_blueprint(campaign.campaign_banner_bp)
        self.client = app.test_client()
        self.token = 'private-preview-test-token'
        self.now = int(time.time())
        self.start_patch(patch('telegram_auth.get_bot_token', return_value=self.token))
        self.clock = self.start_patch(patch.object(campaign, 'datetime', wraps=datetime))
        self.clock.now.return_value = campaign.CHAMPION_STARTS_AT - timedelta(seconds=1)

    def start_patch(self, mock):
        self.addCleanup(mock.stop)
        return mock.start()

    def signature(self, user_id, *, age=0):
        return signed_init_data(self.token, user_id, self.now - age)

    def test_requires_current_owner_signature_in_header(self):
        owner = self.signature(campaign.PREVIEW_OWNER)
        other = self.signature(42)
        forged = other.replace('%22id%22%3A42', f'%22id%22%3A{campaign.PREVIEW_OWNER}')
        expired = self.signature(campaign.PREVIEW_OWNER, age=86401)
        for signature in ('', other, forged, expired):
            with self.subTest(signature_state=signature[:25]):
                response = self.client.get(self.path, headers={'X-Telegram-Init-Data': signature})
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.data, b'')
                self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        response = self.client.get(self.path, query_string={'tgWebAppData': owner})
        self.assertEqual(response.status_code, 404)

    def test_owner_gets_both_slides_and_their_photos_without_caching(self):
        owner = self.signature(campaign.PREVIEW_OWNER)
        with patch.object(Path, 'read_bytes', side_effect=[b'gym photo', b'pool photo']):
            response = self.client.get(self.path, headers={'X-Telegram-Init-Data': owner})
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Студент 12', html)
        self.assertIn('data-slide="gym" src="data:image/webp;base64,Z3ltIHBob3Rv"', html)
        self.assertIn('data-slide="pool" src="data:image/webp;base64,cG9vbCBwaG90bw=="', html)
        self.assertIn(campaign.PREVIEW_DESTINATION, html)
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        self.assertIn('X-Telegram-Init-Data', response.headers['Vary'])

    def test_any_signed_mini_app_user_can_see_the_banner_during_the_72_hour_window(self):
        self.assertEqual(campaign.CHAMPION_ENDS_AT - campaign.CHAMPION_STARTS_AT, timedelta(days=3))
        self.clock.now.return_value = campaign.CHAMPION_STARTS_AT
        ordinary_user = {'X-Telegram-Init-Data': self.signature(42)}
        with patch.object(Path, 'read_bytes', return_value=b'photo'):
            response = self.client.get(self.path, headers=ordinary_user)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(int(response.headers['X-Campaign-Remaining-Ms']), 72 * 3600000)
            self.assertEqual(self.client.get(self.path).status_code, 404)
            self.assertEqual(self.client.get(self.path, headers={'X-Telegram-Init-Data': 'fake'}).status_code, 404)
        self.clock.now.return_value = campaign.CHAMPION_ENDS_AT
        self.assertEqual(self.client.get(self.path, headers=ordinary_user).status_code, 404)
        self.assertEqual(self.client.get(self.path, headers={'X-Telegram-Init-Data': self.signature(campaign.PREVIEW_OWNER)}).status_code, 404)


if __name__ == '__main__':
    unittest.main()
