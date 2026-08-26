import unittest

from max_bot.handlers import (
    _filename_from_headers,
    _has_valid_signature,
    _is_schedule_file,
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


if __name__ == "__main__":
    unittest.main()
