import io
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiogram.exceptions import (TelegramBadRequest, TelegramForbiddenError,
    TelegramNetworkError, TelegramRetryAfter, TelegramUnauthorizedError)
from aiogram.methods import SendMessage
from scripts import announce_schedule_repair_20261013 as notice


class RepairNoticeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.users, self.registry = self.root / 'users.db', self.root / 'registry.db'
        with sqlite3.connect(self.users) as db:
            db.execute('CREATE TABLE users (user_id,role)')
            db.executemany('INSERT INTO users VALUES (?,?)',
                           [(10, 'Я студент'), (20, 'Я преподаватель'), (30, 'Я студент')])
        with sqlite3.connect(self.registry) as db:
            db.execute('CREATE TABLE message_registry (id INTEGER,platform,direction,chat_id,message_id,'
                       'schedule_date,schedule_type,entity,telegram_method,message_kind,content_hash)')
        self.rows = []
        self.bot = AsyncMock()
        self.bot.get_me.return_value = SimpleNamespace(username='kkepik_bot', is_bot=True)
        self.bot.send_message.return_value = SimpleNamespace(message_id=101,
            chat=SimpleNamespace(id=10, type='private'), text=notice.TEXT)
        self.student = {'user_id': 10, 'role': 'Я студент'}
        self.teacher = {'user_id': 20, 'role': 'Я преподаватель'}
        self.method = SendMessage(chat_id=10, text=notice.TEXT)

    def tearDown(self):
        self.temp.cleanup()

    async def deliver(self, recipient=None, **kwargs):
        return await notice.deliver(self.bot, recipient or self.student, self.rows.append,
                                    self.registry, self.users, **kwargs)

    def repair_fixture(self):
        folder = self.root / 'repair'
        folder.mkdir()
        targets = []
        for rid, uid, role in ((1, 10, 'Я студент'), (2, 10, 'Я студент'), (3, 20, 'Я преподаватель')):
            row = {'id': rid, 'platform': 'telegram', 'direction': 'outgoing', 'chat_id': str(uid),
                   'message_id': str(500 + rid), 'schedule_date': notice.DATE,
                   'schedule_type': 'groups', 'entity': 'affected-group', 'telegram_method': 'SendMessage'}
            target = {'row': row, 'new_hash': 'corrected-hash', 'new_kind': 'schedule',
                      'snapshot': {'user': {'role': role}}}
            targets.append(target)
            with sqlite3.connect(self.registry) as db:
                values = {**row, 'message_kind': 'schedule', 'content_hash': 'corrected-hash'}
                db.execute('INSERT INTO message_registry VALUES (?,?,?,?,?,?,?,?,?,?,?)', tuple(values.values()))
        payload = {'version': 1, 'date': notice.DATE, 'source_sha': notice.SOURCE_SHA, 'targets': targets}
        (folder / 'plan.json').write_text(json.dumps(payload))
        (folder / 'summary.json').write_text(json.dumps({'complete': True, 'finished': True,
            'date': notice.DATE, 'source_sha': notice.SOURCE_SHA, 'messages': 3}))
        (folder / 'edits.jsonl').write_text('\n'.join(json.dumps({'registry_id': rid, 'status': 'edited'})
                                                  for rid in (1, 2, 3)))
        return folder

    def test_fixed_cohort_deduplicates_and_includes_affected_teachers_only(self):
        repair = self.repair_fixture()
        with patch.object(notice, 'EXPECTED_MESSAGES', 3), patch.object(notice, 'EXPECTED_RECIPIENTS', 2):
            snapshot = notice.load_affected(repair, self.registry)
        self.assertEqual(snapshot['recipients'], [self.student, self.teacher])
        self.assertNotIn(30, [item['user_id'] for item in snapshot['recipients']])
        self.assertEqual(snapshot['text_sha256'], notice.TEXT_HASH)

    def test_incomplete_repair_or_unconfirmed_edit_fails_closed(self):
        repair = self.repair_fixture()
        with patch.object(notice, 'EXPECTED_MESSAGES', 3), patch.object(notice, 'EXPECTED_RECIPIENTS', 2):
            report = json.loads((repair / 'summary.json').read_text())
            (repair / 'summary.json').write_text(json.dumps({**report, 'complete': False}))
            with self.assertRaises(ValueError):
                notice.load_affected(repair, self.registry)
            (repair / 'summary.json').write_text(json.dumps(report))
            with (repair / 'edits.jsonl').open('a') as stream:
                stream.write('\n' + json.dumps({'registry_id': 3, 'status': 'attempting'}))
            with self.assertRaises(ValueError):
                notice.load_affected(repair, self.registry)

    def test_registry_fixed_hash_must_match_every_repaired_message(self):
        repair = self.repair_fixture()
        with sqlite3.connect(self.registry) as db:
            db.execute("UPDATE message_registry SET content_hash='wrong' WHERE id=2")
        with patch.object(notice, 'EXPECTED_MESSAGES', 3), patch.object(notice, 'EXPECTED_RECIPIENTS', 2):
            with self.assertRaises(ValueError):
                notice.load_affected(repair, self.registry)

    async def test_exact_plain_text_without_buttons_forward_or_other_messages(self):
        result = await self.deliver()
        self.assertEqual(result['status'], 'sent')
        self.assertEqual(self.rows[0]['status'], 'attempting')
        self.bot.send_message.assert_awaited_once_with(chat_id=10, text=notice.TEXT, parse_mode=None)
        self.assertEqual(notice.TEXT.count('\n\n'), 2)
        self.assertTrue(notice.TEXT.endswith('Если заметите проблемы, пишите мне — @rub1kub.'))
        self.bot.forward_message.assert_not_awaited()
        self.bot.edit_message_reply_markup.assert_not_awaited()

    async def test_affected_teacher_is_allowed_and_changed_registration_is_excluded(self):
        self.bot.send_message.return_value.chat.id = 20
        self.assertEqual((await self.deliver(self.teacher))['status'], 'sent')
        self.bot.send_message.reset_mock()
        with sqlite3.connect(self.users) as db:
            db.execute("UPDATE users SET role='Я преподаватель' WHERE user_id=10")
        self.assertEqual((await self.deliver())['status'], 'excluded_registration_changed')
        self.bot.send_message.assert_not_awaited()

    async def test_rate_limit_rechecks_registration_before_retry(self):
        self.bot.send_message.side_effect = TelegramRetryAfter(method=self.method, message='Slow', retry_after=2)
        with patch.object(notice, 'same_registration', side_effect=[True, False]), \
             patch.object(notice, 'wait_until', new_callable=AsyncMock):
            result = await self.deliver()
        self.assertEqual(result['status'], 'excluded_registration_changed')
        self.bot.send_message.assert_awaited_once()
        self.assertEqual([row['status'] for row in self.rows], ['attempting', 'rate_limited'])
        self.assertGreater(self.rows[1]['retry_not_before'], 0)

    async def test_rate_limit_rechecks_registry_and_resumes_remaining_wait(self):
        self.bot.send_message.side_effect = TelegramRetryAfter(method=self.method, message='Slow', retry_after=2)
        with patch.object(notice, 'registered_delivery', side_effect=[None, 99]), \
             patch.object(notice, 'wait_until', new_callable=AsyncMock) as wait:
            result = await self.deliver(retry_not_before=1234)
        self.assertEqual(result['status'], 'already_delivered')
        self.assertEqual(wait.await_args_list[0].args[0], 1234)
        self.bot.send_message.assert_awaited_once()

    async def test_retry_wait_uses_chunks_at_most_20_seconds(self):
        now = [100.0]
        async def advance(delay):
            now[0] += delay
        with patch.object(notice.time, 'time', side_effect=lambda: now[0]), \
             patch.object(notice.asyncio, 'sleep', side_effect=advance) as sleep:
            await notice.wait_until(145)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [20, 20, 5])

    async def test_network_timeout_is_not_retried_and_errors_are_sanitized(self):
        self.bot.send_message.side_effect = TelegramNetworkError(method=self.method, message='secret-bearing URL')
        result = await self.deliver()
        self.assertEqual(result['status'], 'unknown_outcome')
        self.assertNotIn('secret', json.dumps(result))
        self.bot.send_message.assert_awaited_once()

    async def test_registry_dedup_prevents_send_and_conflicting_hash_is_rejected(self):
        with sqlite3.connect(self.registry) as db:
            db.execute('INSERT INTO message_registry (platform,direction,chat_id,message_id,entity,message_kind,content_hash) '
                       'VALUES (?,?,?,?,?,?,?)', ('telegram', 'outgoing', '10', '99', notice.ENTITY, notice.KIND, notice.TEXT_HASH))
        self.assertEqual((await self.deliver())['status'], 'already_delivered')
        self.bot.send_message.assert_not_awaited()
        with sqlite3.connect(self.registry) as db:
            db.execute("UPDATE message_registry SET content_hash='wrong'")
        with self.assertRaises(ValueError):
            await self.deliver()

    async def test_error_classification_and_bot_identity_fail_closed(self):
        for error, status in (
            (TelegramForbiddenError(method=self.method, message='blocked'), 'unreachable'),
            (TelegramBadRequest(method=self.method, message='chat not found'), 'unreachable'),
            (TelegramBadRequest(method=self.method, message='bad text'), 'payload_error'),
            (TelegramUnauthorizedError(method=self.method, message='Unauthorized'), 'unauthorized')):
            self.bot.send_message.side_effect = error
            self.assertEqual((await self.deliver())['status'], status)
        await notice.validate_bot(self.bot)
        self.bot.get_me.return_value.username = 'other'
        with self.assertRaises(ValueError):
            await notice.validate_bot(self.bot)

    async def test_unexpected_response_is_unknown_and_stops_campaign(self):
        self.bot.send_message.return_value.text = 'wrong'
        result = await self.deliver()
        self.assertEqual(result['status'], 'unknown_outcome')
        self.assertTrue(result['stop'])

    def test_journal_and_summary_never_count_one_recipient_twice(self):
        rows = [{'user_id': 10, 'status': 'attempting'}, {'user_id': 10, 'status': 'rate_limited', 'retry_not_before': 999},
                {'user_id': 10, 'status': 'sent', 'message_id': 101}, {'user_id': 20, 'status': 'unreachable'}]
        path = self.root / 'journal.jsonl'
        path.write_text('\n'.join(json.dumps(row) for row in rows))
        self.assertEqual(notice.read_journal(path), rows)
        self.assertEqual(notice.summary([self.student, self.teacher], rows)['counts'], {'sent': 1, 'unreachable': 1})
        self.assertTrue(notice.summary([self.student, self.teacher], rows)['complete'])
        path.write_text('{"user_id":10,"status":')
        with self.assertRaises(ValueError):
            notice.read_journal(path)

    async def test_default_dry_run_is_read_only_and_never_creates_campaign(self):
        self.assertFalse(notice.arguments([]).apply)
        self.assertFalse(notice.arguments(['--dry-run']).apply)
        self.assertTrue(notice.arguments(['--apply']).apply)
        audit = self.root / 'not-created'
        with patch.object(notice, 'AUDIT', audit), \
             patch.object(notice, 'dotenv_values', return_value={'MESSAGE_REGISTRY_PATH': str(self.registry)}), \
             patch.object(notice, 'load_affected', return_value={'recipients': [self.student, self.teacher]}), \
             patch.object(notice, 'same_registration', return_value=True), \
             patch.object(notice, 'Bot') as bot, patch('sys.stdout', new_callable=io.StringIO) as output:
            await notice.main()
        result = json.loads(output.getvalue())
        self.assertEqual(result['planned_recipients'], 2)
        self.assertTrue(result['no_messages_sent'])
        self.assertFalse(audit.exists())
        bot.assert_not_called()

    async def test_apply_resumes_sent_and_uncertain_attempt_without_sending_again(self):
        audit = self.root / 'temporary-campaign'
        audit.mkdir()
        affected = {'entity': notice.ENTITY, 'text_sha256': notice.TEXT_HASH,
                    'recipients': [self.student, self.teacher]}
        (audit / 'plan.json').write_text(json.dumps(affected))
        (audit / 'results.jsonl').write_text('\n'.join(json.dumps(row) for row in (
            {'user_id': 10, 'status': 'sent', 'message_id': 101}, {'user_id': 20, 'status': 'attempting'})))
        manager = AsyncMock()
        manager.__aenter__.return_value = self.bot
        with patch.object(notice, 'AUDIT', audit), patch.object(notice, 'Bot', return_value=manager), \
             patch.object(notice, 'dotenv_values', return_value={'BOT_TOKEN': 'fake'}), \
             patch.object(notice, 'load_affected', return_value=affected), \
             patch.object(notice, 'save_delivery') as save, patch('sys.stdout', new_callable=io.StringIO):
            await notice.main(apply=True)
        save.assert_called_once()
        self.bot.send_message.assert_not_awaited()
        result = json.loads((audit / 'summary.json').read_text())
        self.assertEqual(result['counts'], {'sent': 1, 'unknown_outcome': 1})
        self.assertEqual((audit / 'results.jsonl').stat().st_mode & 0o777, 0o600)
        self.assertEqual(audit.stat().st_mode & 0o777, 0o700)


if __name__ == '__main__':
    unittest.main()
