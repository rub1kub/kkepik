from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage
from scripts.broadcast_pixel_battle_last_day import (
    ROLE, already_attempted, current_students, deliver_student, is_student,
    validate_approval, validate_start,
)
from scripts.preview_pixel_battle_last_day import PROPOSED_SEND_AT, WEBAPP_URL, build_message

DEADLINE = datetime(2026, 10, 7, 10, 26, 11, 329000, tzinfo=timezone.utc)


class LastDayBroadcastTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Path(self.tmp.name) / 'users.db'
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE users (user_id INTEGER PRIMARY KEY, role TEXT)')
            db.executemany('INSERT INTO users VALUES (?,?)', [
                (10, ROLE), (20, 'Я преподаватель'), (30, 'unknown'),
                (-1, ROLE), (0, ROLE),
            ])
        self.plan = {
            'approved': True, 'status': 'approved', 'broadcast_role': ROLE,
            'timezone_requires_confirmation': False,
            'proposed_send_at': PROPOSED_SEND_AT.isoformat(),
            'event_ends_at': DEADLINE.isoformat(),
            'text_html': build_message(DEADLINE),
            'button_web_app_url': WEBAPP_URL, 'button_text': '🎨 Открыть пиксель-батл',
        }
        self.bot = AsyncMock()
        self.bot.send_message.return_value = SimpleNamespace(
            message_id=123, chat=SimpleNamespace(id=10, type='private'),
        )
        self.rows = []

    def tearDown(self):
        self.tmp.cleanup()

    def test_audience_only_contains_positive_integer_students(self):
        self.assertEqual(current_students(self.database), [10])
        for uid in (20, 30, -1, 0, True, '10', 99):
            self.assertFalse(is_student(uid, self.database))
        self.assertTrue(is_student(10, self.database))

    def test_approval_time_and_deadline_are_strictly_verified(self):
        validate_approval(self.plan, DEADLINE)
        for key, value in (
            ('approved', False), ('status', 'pending_owner_approval'),
            ('broadcast_role', 'Я преподаватель'), ('timezone_requires_confirmation', True),
            ('text_html', 'unapproved replacement'), ('button_web_app_url', 'https://example.com/'),
            ('proposed_send_at', (PROPOSED_SEND_AT + timedelta(minutes=1)).isoformat()),
            ('event_ends_at', (DEADLINE + timedelta(hours=1)).isoformat()),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_approval({**self.plan, key: value}, DEADLINE)

    def test_start_cannot_be_early_or_after_the_event(self):
        validate_start(PROPOSED_SEND_AT, DEADLINE)
        for now in (PROPOSED_SEND_AT - timedelta(seconds=1), DEADLINE,
                    DEADLINE + timedelta(seconds=1)):
            with self.subTest(now=now), self.assertRaises(ValueError):
                validate_start(now, DEADLINE)

    def test_sent_and_uncertain_attempts_are_not_replayed(self):
        rows = [
            {'user_id': 1, 'status': 'sent'},
            {'user_id': 2, 'status': 'attempting'},
            {'user_id': 3, 'status': 'unknown_outcome'},
            {'user_id': 4, 'status': 'attempting'},
            {'user_id': 4, 'status': 'rate_limited'},
        ]
        self.assertEqual(already_attempted(rows), {1, 2, 3})

    async def test_teacher_or_changed_role_never_reaches_telegram(self):
        for uid in (20, 30, -1):
            row = await deliver_student(self.bot, uid, DEADLINE, self.rows.append, self.database)
            self.assertEqual(row['status'], 'excluded_role_changed')
        with sqlite3.connect(self.database) as db:
            db.execute('UPDATE users SET role=? WHERE user_id=10', ('Я преподаватель',))
        row = await deliver_student(self.bot, 10, DEADLINE, self.rows.append, self.database)
        self.assertEqual(row['status'], 'excluded_role_changed')
        self.bot.send_message.assert_not_awaited()
        self.assertEqual(self.rows, [])

    async def test_countdown_is_recalculated_and_open_button_is_kept(self):
        with patch('scripts.broadcast_pixel_battle_last_day.datetime') as clock:
            clock.now.return_value = PROPOSED_SEND_AT + timedelta(minutes=5)
            row = await deliver_student(self.bot, 10, DEADLINE, self.rows.append, self.database)
        self.assertEqual(row['status'], 'sent')
        self.assertEqual(self.rows[0]['status'], 'attempting')
        call = self.bot.send_message.await_args.kwargs
        self.assertEqual(call['chat_id'], 10)
        self.assertIn('<b>1 ч 37 мин</b>', call['text'])
        self.assertNotIn('После финала рисовать уже нельзя', call['text'])
        self.assertEqual(call['reply_markup'].inline_keyboard[0][0].web_app.url, WEBAPP_URL)

    async def test_uncertain_network_result_is_never_retried(self):
        self.bot.send_message.side_effect = TelegramNetworkError(
            method=SendMessage(chat_id=10, text='draft'), message='Timeout',
        )
        with patch('scripts.broadcast_pixel_battle_last_day.datetime') as clock:
            clock.now.return_value = PROPOSED_SEND_AT
            row = await deliver_student(self.bot, 10, DEADLINE, self.rows.append, self.database)
        self.assertEqual(row['status'], 'unknown_outcome')
        self.bot.send_message.assert_awaited_once()

    async def test_role_is_rechecked_after_rate_limit(self):
        self.bot.send_message.side_effect = TelegramRetryAfter(
            method=SendMessage(chat_id=10, text='draft'), message='Slow down', retry_after=2,
        )
        with (patch('scripts.broadcast_pixel_battle_last_day.is_student', side_effect=[True, False]),
              patch('scripts.broadcast_pixel_battle_last_day.datetime') as clock,
              patch('scripts.broadcast_pixel_battle_last_day.asyncio.sleep', new_callable=AsyncMock)):
            clock.now.return_value = PROPOSED_SEND_AT
            row = await deliver_student(self.bot, 10, DEADLINE, self.rows.append, self.database)
        self.assertEqual(row['status'], 'excluded_role_changed')
        self.bot.send_message.assert_awaited_once()
        self.assertEqual([row['status'] for row in self.rows], ['attempting', 'rate_limited'])

    async def test_finished_event_is_not_announced(self):
        with patch('scripts.broadcast_pixel_battle_last_day.datetime') as clock:
            clock.now.return_value = DEADLINE
            row = await deliver_student(self.bot, 10, DEADLINE, self.rows.append, self.database)
        self.assertEqual(row['status'], 'event_ended')
        self.bot.send_message.assert_not_awaited()

    async def test_blocked_student_does_not_stop_the_broadcast(self):
        self.bot.send_message.side_effect = TelegramForbiddenError(
            method=SendMessage(chat_id=10, text='draft'), message='Bot blocked',
        )
        with patch('scripts.broadcast_pixel_battle_last_day.datetime') as clock:
            clock.now.return_value = PROPOSED_SEND_AT
            row = await deliver_student(self.bot, 10, DEADLINE, self.rows.append, self.database)
        self.assertEqual(row['status'], 'unreachable')
