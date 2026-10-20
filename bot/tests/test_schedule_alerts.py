import unittest
from unittest.mock import AsyncMock

from handlers.schedule_alerts import OWNER_ID, notify_schedule_error


class ScheduleAlertsTest(unittest.IsolatedAsyncioTestCase):
    async def test_error_is_sent_only_to_owner_with_source_message_id(self):
        bot = AsyncMock()

        sent = await notify_schedule_error(
            bot,
            error="не найдено <ни одной> группы",
            source="MAX",
            file_name="Расписание <01.09>.pdf",
            source_chat_id=-100,
            source_message_id="abc-123",
        )

        self.assertTrue(sent)
        bot.send_message.assert_awaited_once()
        args, kwargs = bot.send_message.await_args
        self.assertEqual(args[0], OWNER_ID)
        self.assertIn("abc-123", args[1])
        self.assertIn("&lt;ни одной&gt;", args[1])
        self.assertNotIn("<ни одной>", args[1])
        self.assertEqual(kwargs["parse_mode"], "HTML")


if __name__ == "__main__":
    unittest.main()
