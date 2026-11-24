import datetime as dt
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import config
from handlers.schedule_broadcaster import _send_tracked_group_schedule
from schedules.schedule_notices import (
    PIXEL_BATTLE_NOTICE,
    append_student_schedule_notice,
    take_next_schedule_notice,
)
from schedule_preferences import SchedulePreferences


class ScheduleNoticeTest(unittest.IsolatedAsyncioTestCase):
    def test_only_one_new_broadcast_can_claim_a_pending_notice(self):
        with TemporaryDirectory() as folder:
            (Path(folder) / 'next_schedule_notice.html').write_text('Новинка!', encoding='utf-8')
            self.assertIsNone(take_next_schedule_notice(folder, '25.11.2026', 'groups', is_update=True))
            with ThreadPoolExecutor(2) as pool:
                results = list(pool.map(lambda kind: take_next_schedule_notice(folder, '26.11.2026', kind, is_update=False),
                                        ('groups', 'teachers')))
            self.assertCountEqual(results, ['Новинка!', None])
            self.assertIsNone(take_next_schedule_notice(folder, '27.11.2026', 'groups', is_update=False))

    async def test_one_time_footer_in_every_group_delivery_mode(self):
        notice = '🔮 <b>Новинка!</b> Текст с модерации.'
        for mode in ('photo_text', 'photo', 'text'):
            for role in (config.ROLE_STUDENT, config.ROLE_TEACHER):
                bot = AsyncMock()
                with patch('handlers.schedule_broadcaster.group_schedule.get_schedule_for_group',
                           return_value=['▪️1 пара – История – Иванова – 35']):
                    await _send_tracked_group_schedule(bot, 42, object(), '103-Д9', '26.11.2026',
                        {'103-Д9': b'image'}, is_update=False, tracked_count=1, always_show_group=False,
                        preferences=SchedulePreferences(delivery_mode=mode), recipient_role=role, extra_notice=notice)
                call = bot.send_message.await_args or bot.send_photo.await_args
                text = call.kwargs.get('text', call.kwargs.get('caption'))
                self.assertEqual(text.count(notice), 1)
                if mode == 'photo':
                    self.assertEqual(text, notice)
                else:
                    self.assertTrue(text.endswith('\n\n' + notice))

    def test_notice_requires_exact_role_send_date_and_schedule_date(self):
        for today in (dt.date(2026, 9, 29), dt.date(2026, 9, 30), dt.date(2026, 10, 1), dt.date(2027, 9, 30)):
            for role in (config.ROLE_STUDENT, config.ROLE_TEACHER, None):
                for date in ('30.09.2026', '01.10.2026', '02.10.2026', '01.10.2027'):
                    with self.subTest(today=today, role=role, date=date):
                        with patch('schedules.schedule_notices.current_college_date', return_value=today):
                            result = append_student_schedule_notice('schedule', role, date)
                        expected = (today == dt.date(2026, 9, 30) and role == config.ROLE_STUDENT
                                    and date == '01.10.2026')
                        self.assertEqual(result, 'schedule\n\n' + PIXEL_BATTLE_NOTICE if expected else 'schedule')

    def test_exact_wording_bold_spoiler_and_custom_emoji(self):
        self.assertEqual(PIXEL_BATTLE_NOTICE,
            '<tg-spoiler><b><tg-emoji emoji-id="5431456208487716895">🎨</tg-emoji> '
            'Пиксель-батл в мини-аппе ККЭПика — общее полотно на весь колледж, '
            'рисовать может каждый, по пикселю в секунду.</b></tg-spoiler>')

    async def test_delivery_keeps_footer_and_keyboard_for_photo_text_updates_and_long_schedule(self):
        for photo in (False, True):
            for update in (False, True):
                for long in (False, True):
                    for role in (config.ROLE_STUDENT, config.ROLE_TEACHER):
                        with self.subTest(photo=photo, update=update, long=long, role=role):
                            bot = AsyncMock()
                            subject = 'Математика ' * (110 if long else 1)
                            lines = [f'▪️1 пара – {subject} – Иванов И.И. – 101']
                            with (
                                patch('schedules.schedule_notices.current_college_date', return_value=dt.date(2026, 9, 30)),
                                patch('handlers.schedule_broadcaster.group_schedule.get_schedule_for_group', return_value=lines),
                            ):
                                await _send_tracked_group_schedule(
                                    bot, 42, object(), '103-Д9-3ИНС', '01.10.2026',
                                    {'103-Д9-3ИНС': b'photo'} if photo else {}, is_update=update,
                                    tracked_count=1, always_show_group=role == config.ROLE_TEACHER,
                                    recipient_role=role,
                                )
                            call = bot.send_message.await_args or bot.send_photo.await_args
                            text = call.kwargs.get('text', call.kwargs.get('caption'))
                            if role == config.ROLE_STUDENT:
                                self.assertTrue(text.endswith('\n\n' + PIXEL_BATTLE_NOTICE))
                                self.assertEqual(text.count('5431456208487716895'), 1)
                            else:
                                self.assertNotIn('5431456208487716895', text)
                            self.assertIsNotNone(call.kwargs['reply_markup'])
                            self.assertEqual(call.kwargs['parse_mode'], 'HTML')
                            if photo and long:
                                bot.send_photo.assert_awaited_once()
                                bot.send_message.assert_awaited_once()

    async def test_missing_group_does_not_send_notice_on_its_own(self):
        bot = AsyncMock()
        with (
            patch('schedules.schedule_notices.current_college_date', return_value=dt.date(2026, 9, 30)),
            patch('handlers.schedule_broadcaster.group_schedule.get_schedule_for_group', return_value=None),
        ):
            result = await _send_tracked_group_schedule(
                bot, 42, object(), '103-Д9-3ИНС', '01.10.2026', {}, is_update=False,
                tracked_count=1, always_show_group=False, recipient_role=config.ROLE_STUDENT,
            )
        self.assertFalse(result)
        bot.send_message.assert_not_awaited()
        bot.send_photo.assert_not_awaited()
