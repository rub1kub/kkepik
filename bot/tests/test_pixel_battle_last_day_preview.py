from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock

from scripts.preview_pixel_battle_last_day import (
    OWNER, PROPOSED_SEND_AT, WEBAPP_URL, build_message, send_owner_preview,
)


class PixelBattleLastDayPreviewTest(unittest.IsolatedAsyncioTestCase):
    def test_countdown_is_for_planned_send_time_in_moscow(self):
        deadline = datetime(2026, 10, 7, 10, 26, 11, 329000, tzinfo=timezone.utc)
        text = build_message(deadline)
        self.assertIn('<b>1 ч 42 мин</b>', text)
        self.assertIn('<b>13:26 мск</b>', text)
        self.assertIn('Сегодня — последний день', text)
        self.assertNotIn('После финала рисовать уже нельзя', text)
        self.assertTrue(text.endswith('Успей!'))
        self.assertEqual(PROPOSED_SEND_AT.utcoffset(), timedelta(hours=3))

    def test_finished_event_and_different_day_are_rejected(self):
        for deadline in (PROPOSED_SEND_AT, PROPOSED_SEND_AT - timedelta(minutes=1),
                         PROPOSED_SEND_AT + timedelta(days=1)):
            with self.subTest(deadline=deadline), self.assertRaises(ValueError):
                build_message(deadline)

    def test_naive_times_are_rejected(self):
        with self.assertRaises(ValueError):
            build_message(datetime(2026, 10, 7, 13, 26))

    async def test_preview_only_goes_to_owner_with_one_open_button(self):
        bot = AsyncMock()
        await send_owner_preview(bot, 'draft')
        bot.send_message.assert_awaited_once()
        kwargs = bot.send_message.await_args.kwargs
        self.assertEqual(kwargs['chat_id'], OWNER)
        self.assertEqual(kwargs['text'], 'draft')
        self.assertEqual(kwargs['parse_mode'], 'HTML')
        rows = kwargs['reply_markup'].inline_keyboard
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 1)
        self.assertEqual(rows[0][0].web_app.url, WEBAPP_URL)
        self.assertEqual(rows[0][0].text, '🎨 Открыть пиксель-батл')
        self.assertIsNone(rows[0][0].callback_data)
