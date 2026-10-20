import unittest
from unittest.mock import AsyncMock, patch

from pyromax.mapping.envelope.v11.payloads.models import FileMappingModel
from pyromax.models import Message

from max_bot.handlers import (
    _filename_from_headers,
    _has_valid_signature,
    _hydrate_file_attachments,
    _handle_message,
    _is_schedule_file,
    _seen_message_ids,
    _safe_file_name,
)


class MaxHandlersTest(unittest.TestCase):
    def test_only_schedule_documents_are_accepted(self):
        self.assertTrue(_is_schedule_file("Расписание на 24.08.2026_ГРУППЫ.pdf"))
        self.assertTrue(_is_schedule_file("расписание преподаватели.xlsx"))
        self.assertFalse(_is_schedule_file("photo.pdf"))
        self.assertFalse(_is_schedule_file("расписание.exe"))

    def test_file_name_cannot_escape_temp_directory(self):
        self.assertEqual(_safe_file_name("../../tmp/Расписание.pdf"), "Расписание.pdf")
        self.assertEqual(_safe_file_name(r"C:\tmp\Расписание.xlsx"), "Расписание.xlsx")

    def test_content_disposition_filename_is_parsed(self):
        headers = {
            "Content-Disposition": "attachment; filename*=UTF-8''%D0%A0%D0%B0%D1%81%D0%BF%D0%B8%D1%81%D0%B0%D0%BD%D0%B8%D0%B5.pdf"
        }
        self.assertEqual(_filename_from_headers(headers), "Расписание.pdf")

    def test_document_signature_must_match_extension(self):
        self.assertTrue(_has_valid_signature(b"%PDF-1.7 data", "Расписание.pdf"))
        self.assertTrue(_has_valid_signature(b"PK\x03\x04data", "Расписание.xlsx"))
        self.assertFalse(_has_valid_signature(b"<html>", "Расписание.pdf"))

    def test_history_file_attachment_is_hydrated_for_download(self):
        attachment = FileMappingModel(
            type="FILE",
            token="file-token",
            file_id=123,
            size=10,
            name="Расписание на 03.09.2026_ГРУППЫ.pdf",
        )
        message = Message(
            message_id=456,
            chat_id=-789,
            time=1,
            type="MESSAGE",
            text=None,
            cid=None,
            attaches=[attachment],
        )

        _hydrate_file_attachments(message)

        self.assertTrue(attachment.uploaded)
        self.assertEqual(attachment.chat_id, -789)
        self.assertEqual(attachment.message_id, 456)


class MaxHandlerRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_retryable_failure_is_not_persistently_deduplicated(self):
        attachment = FileMappingModel(
            type="FILE",
            token="file-token",
            file_id=123,
            size=10,
            name="Расписание на 03.09.2026_ГРУППЫ.pdf",
        )
        message = Message(
            message_id=987654,
            chat_id=-69688611509513,
            time=2_000,
            type="MESSAGE",
            text=None,
            cid=None,
            attaches=[attachment],
        )
        message_key = f"{message.chat_id}:{message.message_id}"
        _seen_message_ids.pop(message_key, None)

        with (
            patch("max_bot.config.MAX_WATCH_CHAT_IDS", [message.chat_id]),
            patch("max_bot.handlers.is_message_recorded", return_value=False),
            patch("max_bot.handlers.record_message") as record_message,
            patch(
                "max_bot.handlers._process_file_attachment",
                new=AsyncMock(return_value=False),
            ),
        ):
            await _handle_message(message, AsyncMock(), accept_after_ms=1_000)

        record_message.assert_not_called()
        self.assertNotIn(message_key, _seen_message_ids)


if __name__ == "__main__":
    unittest.main()
