from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from ad_after_schedule import schedule_broadcast_completed
from ad_dispatch import pending_campaign
from admin_post_capture import OWNER, write_private_json


class CompletionReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / 'admin_campaigns'
        self.folder = self.base / 'ad-20261103'
        write_private_json(self.base / 'active.json', {'id':'ad-20261103'})
        write_private_json(self.folder / 'campaign.json', dict(id='ad-20261103', owner=OWNER,
            phase='awaiting_post', trigger={'type':'schedule_complete','schedule_date':'05.11.2026','broadcast_day':'2026-11-03'}))
        self.now = datetime(2026,11,3,12,tzinfo=timezone.utc)

    def call(self, date='05.11.2026', kind='groups', update=False, count=1736, now=None):
        return schedule_broadcast_completed(date, kind, update, count, data_dir=self.temp.name, now=now or self.now)

    def test_only_completed_primary_distribution_on_authorized_day_triggers_once(self):
        self.assertFalse(self.call(update=True))
        self.assertFalse(self.call(date='06.11.2026'))
        self.assertFalse(self.call(kind='teachers'))
        self.assertFalse(self.call(count=0))
        self.assertFalse(self.call(now=datetime(2026,11,4,12,tzinfo=timezone.utc)))
        self.assertFalse((self.folder/'schedule-complete.json').exists())
        self.assertTrue(self.call())
        self.assertFalse(self.call())
        receipt=json.loads((self.folder/'schedule-complete.json').read_text())
        self.assertEqual(receipt['schedule_date'],'05.11.2026')
        self.assertEqual(receipt['notified_count'],1736)

    def test_manual_draft_never_triggers_automatic_distribution(self):
        path = self.folder / 'campaign.json'
        campaign = json.loads(path.read_text())
        campaign.update(phase='ready', trigger={'type': 'manual'})
        write_private_json(path, campaign)
        self.assertFalse(self.call())
        self.assertFalse((self.folder / 'schedule-complete.json').exists())
        pending, reason = pending_campaign(data_dir=self.temp.name, now=self.now)
        self.assertIsNone(pending)
        self.assertEqual(reason, 'not_a_schedule_campaign')
