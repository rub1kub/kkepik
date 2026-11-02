from datetime import timedelta
import unittest
from unittest.mock import patch

from flask import Flask, render_template_string
import campaign_banner as campaign


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
        self.assertEqual(rendered, 'https://clck.ru/3WjwZy')


if __name__ == '__main__':
    unittest.main()
