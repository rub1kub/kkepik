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
from aiogram.methods import ForwardMessage
from scripts import forward_ai_post_students_20261008 as forward


class ForwardAIStudentsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.database = self.root / 'users.db'
        self.registry = self.root / 'registry.db'
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE users (user_id,role TEXT)')
            db.executemany('INSERT INTO users VALUES (?,?)', [
                (10, forward.ROLE), (10, forward.ROLE), (11, forward.ROLE),
                (20, 'Я преподаватель'), (30, 'unknown'), (-1, forward.ROLE),
                (0, forward.ROLE), ('40', forward.ROLE)])
        with sqlite3.connect(self.registry) as db:
            db.execute('CREATE TABLE message_registry (platform,direction,chat_id,message_id,entity)')
        self.rows = []
        self.bot = AsyncMock()
        self.bot.forward_message.return_value = SimpleNamespace(message_id=123,
            chat=SimpleNamespace(id=10, type='private'), reply_markup=object(),
            forward_origin=SimpleNamespace(type='channel',
                chat=SimpleNamespace(id=forward.SOURCE_CHAT), message_id=forward.SOURCE_MESSAGE))
        self.bot.get_me.return_value = SimpleNamespace(username='kkepik_bot', is_bot=True)
        self.bot.get_chat.return_value = SimpleNamespace(id=forward.SOURCE_CHAT,
            type='channel', username=forward.SOURCE_USERNAME, has_protected_content=False)
        self.method = ForwardMessage(chat_id=10, from_chat_id=forward.SOURCE_CHAT,
                                     message_id=forward.SOURCE_MESSAGE)

    def tearDown(self):
        self.tmp.cleanup()

    async def deliver(self, uid=10, **kwargs):
        return await forward.deliver_student(self.bot, uid, self.rows.append,
                                            self.registry, self.database, **kwargs)

    def test_only_unique_positive_integer_students(self):
        self.assertEqual(forward.current_students(self.database), [10, 11])
        for value in (20, 30, -1, 0, True, '10', 99):
            self.assertFalse(forward.is_student(value, self.database))

    async def test_exact_forward_preserves_original_without_edits_or_copies(self):
        row = await self.deliver()
        self.assertEqual(row['status'], 'sent')
        self.assertTrue(row['original_buttons_present'])
        self.assertEqual(self.rows[0]['status'], 'attempting')
        self.bot.forward_message.assert_awaited_once_with(chat_id=10,
            from_chat_id=-1004435380193, message_id=12)
        self.bot.edit_message_reply_markup.assert_not_called()
        self.bot.copy_message.assert_not_called()
        self.bot.send_message.assert_not_called()

    async def test_teacher_or_changed_role_excluded_before_send(self):
        for uid in (20, 30, -1):
            self.assertEqual((await self.deliver(uid))['status'], 'excluded_role_changed')
        with sqlite3.connect(self.database) as db:
            db.execute('UPDATE users SET role=? WHERE user_id=10', ('Я преподаватель',))
        self.assertEqual((await self.deliver())['status'], 'excluded_role_changed')
        self.bot.forward_message.assert_not_awaited()
        self.assertEqual(self.rows, [])

    async def test_role_is_rechecked_after_explicit_rate_limit(self):
        self.bot.forward_message.side_effect = TelegramRetryAfter(method=self.method,
            message='Slow down', retry_after=2)
        with patch.object(forward, 'is_student', side_effect=[True, False]), \
             patch.object(forward.asyncio, 'sleep', new_callable=AsyncMock):
            row = await self.deliver()
        self.assertEqual(row['status'], 'excluded_role_changed')
        self.bot.forward_message.assert_awaited_once()
        self.assertEqual([row['status'] for row in self.rows], ['attempting', 'rate_limited'])

    async def test_retry_after_is_waited_in_at_most_30_second_chunks(self):
        result = self.bot.forward_message.return_value
        self.bot.forward_message.side_effect = [TelegramRetryAfter(method=self.method,
            message='Slow down', retry_after=65), result]
        progress = Mock()
        with patch.object(forward.asyncio, 'sleep', new_callable=AsyncMock) as sleep:
            row = await self.deliver(progress=progress)
        self.assertEqual(row['status'], 'sent')
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [30, 30, 6])
        self.assertEqual(self.bot.forward_message.await_count, 2)
        self.assertEqual(progress.call_count, 3)

    async def test_uncertain_network_failure_is_never_retried(self):
        self.bot.forward_message.side_effect = TelegramNetworkError(method=self.method,
            message='Timeout with https://secret-token.invalid')
        row = await self.deliver()
        self.assertEqual(row['status'], 'unknown_outcome')
        self.assertNotIn('error', row)
        self.bot.forward_message.assert_awaited_once()

    async def test_registry_deduplication_precedes_any_api_send(self):
        with sqlite3.connect(self.registry) as db:
            db.execute('INSERT INTO message_registry VALUES (?,?,?,?,?)',
                ('telegram', 'outgoing', '10', '77', forward.SOURCE_URL))
        row = await self.deliver()
        self.assertEqual(row['status'], 'already_delivered')
        self.assertEqual(row['message_id'], 77)
        self.bot.forward_message.assert_not_awaited()
        self.assertEqual(self.rows, [])

    async def test_registry_is_rechecked_after_rate_limit(self):
        self.bot.forward_message.side_effect = TelegramRetryAfter(method=self.method,
            message='Slow down', retry_after=2)
        with patch.object(forward, 'registered_delivery', side_effect=[None, 77]), \
             patch.object(forward.asyncio, 'sleep', new_callable=AsyncMock):
            row = await self.deliver()
        self.assertEqual(row['status'], 'already_delivered')
        self.bot.forward_message.assert_awaited_once()

    async def test_source_and_identity_checks_fail_closed(self):
        await forward.validate_bot_and_source(self.bot)
        for changes in ({'id': 12}, {'type': 'group'}, {'username': 'other'}, {'has_protected_content': True}):
            with self.subTest(changes=changes):
                self.bot.get_chat.return_value = SimpleNamespace(id=forward.SOURCE_CHAT,
                    type='channel', username=forward.SOURCE_USERNAME, has_protected_content=False)
                for key, value in changes.items():
                    setattr(self.bot.get_chat.return_value, key, value)
                with self.assertRaises(ValueError):
                    await forward.validate_bot_and_source(self.bot)
        self.bot.get_me.return_value = SimpleNamespace(username='other', is_bot=True)
        with self.assertRaises(ValueError):
            await forward.validate_bot_and_source(self.bot)
        self.bot.forward_message.assert_not_awaited()

    async def test_global_errors_stop_and_unreachable_is_local(self):
        for error, expected in (
            (TelegramBadRequest(method=self.method, message='message to forward not found'), 'source_error'),
            (TelegramUnauthorizedError(method=self.method, message='Unauthorized'), 'unauthorized'),
            (TelegramForbiddenError(method=self.method, message='bot is not a member of the channel chat'), 'source_error'),
            (TelegramBadRequest(method=self.method, message='chat not found'), 'unreachable'),
            (TelegramForbiddenError(method=self.method, message='bot was blocked by the user'), 'unreachable')):
            with self.subTest(error=type(error).__name__, expected=expected):
                self.bot.forward_message.side_effect = error
                self.assertEqual((await self.deliver())['status'], expected)

    def test_resume_skips_sent_attempting_unknown_but_latest_429_can_retry(self):
        rows = [{'user_id': 1, 'status': 'sent'}, {'user_id': 2, 'status': 'attempting'},
            {'user_id': 3, 'status': 'unknown_outcome'}, {'user_id': 4, 'status': 'attempting'},
            {'user_id': 4, 'status': 'rate_limited'}, {'user_id': 5, 'status': 'rate_limited'},
            {'user_id': 5, 'status': 'attempting'}]
        self.assertEqual(forward.already_attempted(rows), {1, 2, 3, 5})

    def test_summary_counts_each_uid_once(self):
        rows = [{'user_id': 10, 'status': 'attempting'}, {'user_id': 10, 'status': 'rate_limited'},
            {'user_id': 10, 'status': 'attempting'}, {'user_id': 10, 'status': 'sent'},
            {'user_id': 11, 'status': 'unreachable'}]
        result = forward.summary([10, 11], rows)
        self.assertEqual(result['counts'], {'sent': 1, 'unreachable': 1})
        self.assertEqual(result['processed'], 2)
        self.assertTrue(result['complete'])
        self.assertNotIn('user_ids', result)

    def test_bad_snapshot_or_damaged_journal_is_not_replayed(self):
        plan = {'source': forward.SOURCE_URL, 'source_chat': forward.SOURCE_CHAT,
            'source_message': 12, 'role': forward.ROLE, 'user_ids': [10, 11]}
        self.assertEqual(forward.validate_snapshot(plan), [10, 11])
        for changes in ({'source_message': 11}, {'role': 'Я преподаватель'}, {'user_ids': [True]}, {'user_ids': [10, 10]}):
            with self.assertRaises(ValueError):
                forward.validate_snapshot({**plan, **changes})
        fixture = self.root / 'damaged-fixture.jsonl'
        fixture.write_text('{"user_id":10,"status":')
        with self.assertRaises(ValueError):
            forward.read_journal(fixture)

    async def test_default_dry_run_is_read_only_and_does_not_create_campaign(self):
        self.assertFalse(forward.arguments([]).apply)
        self.assertFalse(forward.arguments(['--dry-run']).apply)
        self.assertTrue(forward.arguments(['--apply']).apply)
        audit = self.root / 'not-created'
        with patch.object(forward, 'AUDIT', audit), \
             patch.object(forward, 'dotenv_values', return_value={'MESSAGE_REGISTRY_PATH': str(self.registry)}), \
             patch.object(forward, 'current_students', return_value=[10, 11]), \
             patch.object(forward, 'Bot') as bot, patch('sys.stdout', new_callable=io.StringIO) as output:
            await forward.main()
        self.assertTrue(json.loads(output.getvalue())['no_messages_sent'])
        self.assertFalse(audit.exists())
        bot.assert_not_called()


if __name__ == '__main__':
    unittest.main()
