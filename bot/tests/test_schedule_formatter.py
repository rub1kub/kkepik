import unittest

from schedules.schedule_formatter import (
    build_group_schedule_message,
    fit_photo_caption,
    format_group_schedule,
    should_show_pair_times,
    telegram_text_length,
)


LINES = [
    "▪️1 пара – Основы алгоритмизации и программирования – <b>Каркавин Д.О.</b> – 84",
    "▪️2 пара – Архитектура аппаратных средств – <b>Ломанов А.Е.</b> – 121",
    "▪️3 пара – Нет",
]


class ScheduleFormatterTest(unittest.TestCase):
    def test_senior_weekday_emphasizes_teacher_and_room(self):
        result = format_group_schedule(LINES, "103-Д9-2ИНС", "10.06.2026")

        self.assertNotIn("8:45", result)
        self.assertIn("Основы алгоритмизации и программирования", result)
        self.assertIn("<b>Каркавин Д.О. · аудитория 84</b>", result)

    def test_saturday_shows_saturday_times_for_every_course(self):
        result = format_group_schedule(LINES, "103-Д9-2ИНС", "06.06.2026")

        self.assertIn("<i>8:45–10:00</i>", result)
        self.assertIn("<i>10:10–11:25</i>", result)

    def test_first_course_september_shows_weekday_times(self):
        result = format_group_schedule(LINES, "150-Д9-1ИНС", "02.09.2026")

        self.assertTrue(should_show_pair_times("150-Д9-1ИНС", "02.09.2026"))
        self.assertIn("<i>8:45–10:05</i>", result)
        self.assertIn("<b>Основы алгоритмизации и программирования</b>", result)
        self.assertIn("аудитория <b>84</b>", result)
        self.assertNotIn("<b>Каркавин Д.О.", result)

    def test_first_course_after_september_hides_times(self):
        result = format_group_schedule(LINES, "150-Д9-1ИНС", "02.10.2026")

        self.assertFalse(should_show_pair_times("150-Д9-1ИНС", "02.10.2026"))
        self.assertNotIn("8:45", result)

    def test_subgroups_are_rendered_as_separate_blocks(self):
        lines = [
            "▪️1 пара – Английский язык – <b>Иванова И.И.</b> – 101\n"
            "▪️1 пара – Немецкий язык – <b>Петров П.П.</b> – 102"
        ]

        result = format_group_schedule(lines, "103-Д9-2ИНС", "10.06.2026")

        self.assertEqual(result.count("▪️<b>1 пара</b>"), 2)
        self.assertIn("<b>Иванова И.И. · аудитория 101</b>", result)
        self.assertIn("<b>Петров П.П. · аудитория 102</b>", result)

    def test_long_photo_caption_is_compacted(self):
        text = "<b>Заголовок</b>\n\n" + ("▪️Пара · аудитория 105\n\n" * 60)

        compact = fit_photo_caption(text)

        self.assertNotIn("аудитория", compact)
        self.assertNotIn("\n\n▪️", compact)
        self.assertLess(telegram_text_length(compact), telegram_text_length(text))

    def test_single_group_header_can_be_hidden(self):
        result = build_group_schedule_message(
            LINES,
            "103-Д9-2ИНС",
            "10.06.2026",
            show_group=False,
        )

        self.assertNotIn("Группа <b>", result)
        self.assertIn("Расписание на <b>10.06.2026</b>", result)


if __name__ == "__main__":
    unittest.main()
