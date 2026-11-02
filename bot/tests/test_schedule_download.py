import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendDocument
from activity_store import ActivityStore
from handlers import schedule_download as download
from schedules.schedule_keyboard import create_schedule_keyboard


class ScheduleDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.activity = ActivityStore(Path(self.temp.name) / 'events.db')
        self.file = Path(self.temp.name) / 'schedule.pdf';self.file.write_bytes(b'%PDF-1.7 test')
        self.query = SimpleNamespace(id='callback-1', data='schedule_pdf:groups:02.11.2026',
            from_user=SimpleNamespace(id=42), message=SimpleNamespace(chat=SimpleNamespace(id=999)),
            answer=AsyncMock(), bot=SimpleNamespace(send_document=AsyncMock(), send_message=AsyncMock()))
        self.store_patch = patch.object(download, 'ActivityStore', return_value=self.activity)
        self.store_patch.start();self.addCleanup(self.store_patch.stop)
        self.file_patch = patch.object(download, 'find_schedule_file', return_value=str(self.file))
        self.file_patch.start();self.addCleanup(self.file_patch.stop)

    def events(self):
        if not self.activity.path.exists():return []
        with sqlite3.connect(self.activity.path) as db:
            db.row_factory = sqlite3.Row
            return [{**dict(row), 'metadata': json.loads(row['metadata'])} for row in db.execute('SELECT * FROM activity_events ORDER BY id')]

    def test_button_is_a_direct_url_with_recipient_and_schedule_context(self):
        from urllib.parse import urlparse, parse_qs
        button = create_schedule_keyboard('02.11.2026', user_id=42,
            entity_type='group', entity_id='103-Д9-3ИНС').inline_keyboard[0][1]
        url = urlparse(button.url)
        self.assertEqual(parse_qs(url.query), {'user_id':['42'], 'source':['bot'],
            'entity_type':['group'], 'entity_id':['103-Д9-3ИНС']})
        self.assertTrue(url.path.endswith('/groups/02.11.2026'))
        self.assertIsNone(button.callback_data)

    async def test_file_goes_to_actual_actor_with_context_and_delivery_result(self):
        await download.download_schedule(self.query)
        args = self.query.bot.send_document.await_args.args
        self.assertEqual(args[0], 42)  # The forwarded message's original chat cannot select a recipient.
        self.assertEqual(Path(args[1].path), self.file)
        rows = self.events()
        self.assertEqual([r['action'] for r in rows], ['schedule.download.request', 'schedule.download.started', 'schedule.download.complete'])
        self.assertEqual({r['actor_id'] for r in rows}, {42})
        self.assertEqual({r['schedule_date'] for r in rows}, {'2026-11-02'})
        self.assertEqual(rows[-1]['metadata']['bytes_sent'], self.file.stat().st_size)
        self.assertEqual(rows[-1]['metadata']['delivery'], 'telegram')

    async def test_duplicate_callback_does_not_resend_file(self):
        await download.download_schedule(self.query)
        await download.download_schedule(self.query)
        self.query.bot.send_document.assert_awaited_once()

    async def test_malformed_date_and_type_do_not_read_or_send(self):
        for value in ['schedule_pdf:unknown:02.11.2026', 'schedule_pdf:groups:31.02.2026', 'schedule_pdf:groups:../../private']:
            self.query.data=value
            await download.download_schedule(self.query)
        self.query.bot.send_document.assert_not_awaited()
        self.assertEqual(self.events(), [])

    async def test_missing_file_and_delivery_error_are_not_successes(self):
        self.file.unlink()
        await download.download_schedule(self.query)
        self.assertEqual(self.events()[-1]['action'], 'schedule.download.error')
        self.query.bot.send_document.assert_not_awaited()
        self.file.write_bytes(b'%PDF test');self.query.id='second'
        self.query.bot.send_document.side_effect=TelegramForbiddenError(method=SendDocument(chat_id=42,document='dummy'),message='private response text')
        await download.download_schedule(self.query)
        rows = self.events()
        self.assertFalse(any(r['action']=='schedule.download.complete' for r in rows))
        self.assertNotIn('private response text',json.dumps(rows))

    async def test_journal_failure_does_not_block_sending(self):
        with patch.object(self.activity,'append',side_effect=sqlite3.OperationalError('failure')):
            await download.download_schedule(self.query)
        self.query.bot.send_document.assert_awaited_once()

    async def test_per_actor_rate_limit_prevents_download_spam(self):
        for i in range(11):
            self.query.id='callback-'+str(i)
            await download.download_schedule(self.query)
        self.assertEqual(self.query.bot.send_document.await_count, 10)
        self.assertEqual(self.events()[-1]['metadata']['reason'], 'rate_limited')
