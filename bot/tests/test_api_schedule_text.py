import unittest

from api import plain_schedule_lines


class ApiScheduleTextTest(unittest.TestCase):
    def test_telegram_markup_is_removed_from_web_payload(self):
        lines = [
            "▪️1 пара – Математика – <b>Шульга Н.Н.</b> – 27",
            "▪️2 пара – A &amp; B – <i>Иванов И.И.</i> – 84",
        ]

        self.assertEqual(
            plain_schedule_lines(lines),
            [
                "▪️1 пара – Математика – Шульга Н.Н. – 27",
                "▪️2 пара – A & B – Иванов И.И. – 84",
            ],
        )


if __name__ == "__main__":
    unittest.main()
