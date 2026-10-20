import datetime as dt
import tempfile
import unittest
from pathlib import Path

from aiogram import types
from aiogram.methods import SendMessage

from message_registry import (
    is_message_recorded,
    list_messages,
    message_tracking_context,
    record_message,
    record_outgoing_result,
)


class MessageRegistryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "registry.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_incoming_max_event_is_enriched_as_schedule_source(self):
        record_message(
            platform="max",
            direction="incoming",
            chat_id=-100,
            message_id="source-1",
            message_kind="max_event",
            db_path=self.db_path,
        )
        record_message(
            platform="max",
            direction="incoming",
            chat_id=-100,
            message_id="source-1",
            message_kind="schedule_source",
            schedule_date="01.09.2026",
            schedule_type="groups",
            file_name="Расписание 01.09.2026 ГРУППЫ.pdf",
            db_path=self.db_path,
        )

        self.assertTrue(
            is_message_recorded(
                "max",
                "incoming",
                -100,
                "source-1",
                db_path=self.db_path,
            )
        )
        rows = list_messages(db_path=self.db_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["message_kind"], "schedule_source")
        self.assertEqual(rows[0]["schedule_date"], "01.09.2026")

    def test_outgoing_schedule_message_keeps_chat_message_and_entity(self):
        method = SendMessage(chat_id=42, text="Расписание на 01.09.2026")
        result = types.Message(
            message_id=77,
            date=dt.datetime(2026, 8, 31, tzinfo=dt.timezone.utc),
            chat=types.Chat(id=42, type="private"),
            text="Расписание на 01.09.2026",
        )

        with message_tracking_context(
            message_kind="schedule",
            schedule_date="01.09.2026",
            schedule_type="groups",
            entity="103-Д3-3ИНС",
        ):
            record_outgoing_result(method, result, db_path=self.db_path)

        rows = list_messages(
            schedule_date="01.09.2026",
            message_kind="schedule",
            db_path=self.db_path,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["chat_id"], "42")
        self.assertEqual(rows[0]["message_id"], "77")
        self.assertEqual(rows[0]["entity"], "103-Д3-3ИНС")
        self.assertEqual(rows[0]["telegram_method"], "SendMessage")

    def test_message_id_only_result_uses_method_chat(self):
        method = SendMessage(chat_id=1084693264, text="Проверка")
        result = types.MessageId(message_id=99)

        record_outgoing_result(method, result, db_path=self.db_path)

        rows = list_messages(chat_id=1084693264, db_path=self.db_path)
        self.assertEqual(rows[0]["message_id"], "99")


if __name__ == "__main__":
    unittest.main()
