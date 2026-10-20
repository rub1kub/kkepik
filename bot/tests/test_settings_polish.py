from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage

import config
from commands.settings import process_settings_callback
from handlers.schedule_broadcaster import _notify_users_about_missing_groups, _send_tracked_group_schedule
from schedule_preferences import SchedulePreferences
from schedules.personal_delivery import send_personal_schedule
from schedules.schedule_keyboard import create_schedule_keyboard
from schedules.schedule_notices import (
    SETTINGS_NOTICE, append_broadcast_schedule_notice, get_schedule_settings_notice,
)


def hide_keyboard():
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text='Скрыть', style='danger', callback_data='settings:hide-preview')
    ]])


def callback(data='settings:hide-preview', chat_id=42, markup=None):
    message = SimpleNamespace(chat=SimpleNamespace(id=chat_id, type='private'),
        reply_markup=markup if markup is not None else hide_keyboard(), delete=AsyncMock())
    return SimpleNamespace(data=data, message=message, from_user=SimpleNamespace(id=42),
                           bot=AsyncMock(), answer=AsyncMock())


class KeyboardTest(unittest.TestCase):
    def test_regular_schedule_has_only_open_and_download(self):
        keyboard = create_schedule_keyboard('07.10.2026')
        self.assertEqual(len(keyboard.inline_keyboard), 1)
        self.assertEqual([button.text for button in keyboard.inline_keyboard[0]], ['Открыть', 'Скачать PDF'])
        self.assertNotIn('/settings', keyboard.model_dump_json())


class PreviewHideTest(unittest.IsolatedAsyncioTestCase):
    async def test_own_marked_preview_is_deleted_without_loading_preferences(self):
        query = callback()
        with patch('commands.settings.get_user_role', return_value=config.ROLE_STUDENT), \
             patch('commands.settings.get_preferences') as preferences:
            await process_settings_callback(query)
        query.message.delete.assert_awaited_once()
        query.answer.assert_awaited_once_with('Предпросмотр скрыт')
        preferences.assert_not_called()

    async def test_unmarked_schedule_and_foreign_preview_cannot_be_deleted(self):
        for query in (callback(markup=create_schedule_keyboard('07.10.2026')), callback(chat_id=43)):
            with patch('commands.settings.get_user_role', return_value=config.ROLE_STUDENT):
                await process_settings_callback(query)
            query.message.delete.assert_not_awaited()
            self.assertTrue(query.answer.await_args.kwargs['show_alert'])

    async def test_telegram_delete_failure_is_reported(self):
        query = callback()
        query.message.delete.side_effect = TelegramBadRequest(
            method=DeleteMessage(chat_id=42, message_id=7), message='message cannot be deleted')
        with patch('commands.settings.get_user_role', return_value=config.ROLE_STUDENT):
            await process_settings_callback(query)
        self.assertTrue(query.answer.await_args.kwargs['show_alert'])

    async def test_generated_preview_has_a_red_hide_button(self):
        query = callback(data='settings:preview')
        payload = {'entity': None, 'has_image': False, 'example': True, 'html': '<b>Текст</b>',
                   'date': '06.10.2026', 'photo_notice': None}
        with patch('commands.settings.get_user_role', return_value=config.ROLE_STUDENT), \
             patch('commands.settings.get_preferences', return_value=SchedulePreferences()), \
             patch('message_settings_api.preview_payload', return_value=payload):
            await process_settings_callback(query)
        button = query.bot.send_message.await_args.kwargs['reply_markup'].inline_keyboard[0][0]
        self.assertEqual((button.text, button.style, button.callback_data),
                         ('Скрыть', 'danger', 'settings:hide-preview'))

    async def test_every_part_of_a_long_preview_can_be_hidden(self):
        bot = AsyncMock()
        keyboard = hide_keyboard()
        await send_personal_schedule(bot, 42, 'Длинный текст ' * 900, b'image', keyboard,
                                     SchedulePreferences(), preview=True)
        self.assertEqual(bot.send_photo.await_args.kwargs['reply_markup'], keyboard)
        self.assertGreater(len(bot.send_message.await_args_list), 1)
        for call in bot.send_message.await_args_list:
            self.assertEqual(call.kwargs['reply_markup'], keyboard)


class SettingsAnnouncementTest(unittest.IsolatedAsyncioTestCase):
    def test_notice_is_date_scoped_role_independent_and_not_duplicated(self):
        for role in (config.ROLE_STUDENT, config.ROLE_TEACHER, None):
            for date in ('06.10.2026', '07.10.2026', '08.10.2026', '07.10.2027'):
                result = append_broadcast_schedule_notice('Расписание', role, date)
                self.assertEqual(SETTINGS_NOTICE in result, date == '07.10.2026')
                if date == '07.10.2026':
                    self.assertEqual(append_broadcast_schedule_notice(result, role, date), result)
                    self.assertTrue(result.endswith(SETTINGS_NOTICE))
        self.assertIn('/settings', SETTINGS_NOTICE)

    async def test_all_broadcast_modes_and_roles_keep_notice_at_the_end(self):
        lines = ['▪️1 пара – Математика – Иванова И.И. – 48']
        for role in (config.ROLE_STUDENT, config.ROLE_TEACHER):
            for mode in ('photo_text', 'text', 'photo'):
                for update in (False, True):
                    bot = AsyncMock()
                    with patch('handlers.schedule_broadcaster.group_schedule.get_schedule_for_group', return_value=lines):
                        await _send_tracked_group_schedule(bot, 42, object(), '150-Д9-1ИНС',
                            '07.10.2026', {'150-Д9-1ИНС': b'image'}, is_update=update, tracked_count=1,
                            always_show_group=role == config.ROLE_TEACHER, recipient_role=role,
                            preferences=SchedulePreferences(delivery_mode=mode))
                    call = bot.send_message.await_args or bot.send_photo.await_args
                    text = call.kwargs.get('text', call.kwargs.get('caption'))
                    self.assertTrue(text.endswith(SETTINGS_NOTICE))
                    self.assertEqual(text.count('/settings'), 1)
                    self.assertEqual('ИЗМЕНИЛОСЬ' in text, update)

    async def test_missing_group_notification_also_has_the_notice(self):
        bot = AsyncMock()
        await _notify_users_about_missing_groups(bot, {42: ['150-Д9-1ИНС']}, '07.10.2026')
        self.assertTrue(bot.send_message.await_args.args[1].endswith(SETTINGS_NOTICE))

    async def test_other_dates_do_not_add_caption_to_photo_only_mode(self):
        bot = AsyncMock()
        await send_personal_schedule(bot, 42, 'Расписание', b'image', None,
            SchedulePreferences(delivery_mode='photo'), notice=get_schedule_settings_notice('08.10.2026'))
        self.assertIsNone(bot.send_photo.await_args.kwargs['caption'])


if __name__ == '__main__':
    unittest.main()
