import unittest
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import ForwardMessage
from scripts.forward_post_20260921 import deliver, private_ids


class ForwardPostTest(unittest.IsolatedAsyncioTestCase):
    def test_recipients_are_unique_private_integer_ids(self):
        self.assertEqual(private_ids([(42,), (42,), (-1001,), (0,), ('42',), (True,), (7,)]), [7, 42])

    async def test_exact_authorized_source_and_target(self):
        bot = AsyncMock()
        await deliver(bot, 42)
        bot.forward_message.assert_awaited_once_with(chat_id=42, from_chat_id='@perehodnikkepa', message_id=4)

    async def test_groups_cannot_be_sent_to(self):
        bot = AsyncMock()
        for uid in (-100, 0, '@some_group', True):
            with self.assertRaises(ValueError):
                await deliver(bot, uid)
        bot.forward_message.assert_not_called()

    async def test_network_error_is_not_retried(self):
        bot = AsyncMock()
        method = ForwardMessage(chat_id=42, from_chat_id='@perehodnikkepa', message_id=4)
        bot.forward_message.side_effect = TelegramNetworkError(method=method, message='Timeout')
        with self.assertRaises(TelegramNetworkError):
            await deliver(bot, 42)
        self.assertEqual(bot.forward_message.await_count, 1)

    async def test_explicit_rate_limit_is_respected(self):
        bot = AsyncMock()
        method = ForwardMessage(chat_id=42, from_chat_id='@perehodnikkepa', message_id=4)
        bot.forward_message.side_effect = [TelegramRetryAfter(method=method, message='Slow down', retry_after=2), 'sent']
        with patch('scripts.forward_post_20260921.asyncio.sleep', new_callable=AsyncMock) as sleep:
            self.assertEqual(await deliver(bot, 42), 'sent')
        sleep.assert_awaited_once_with(3)
        self.assertEqual(bot.forward_message.await_count, 2)
