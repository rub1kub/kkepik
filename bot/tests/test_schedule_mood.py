import re
import unittest

from schedules.schedule_mood import (
    CRITICAL_EMOJIS,
    DIFFICULTY_EMOJIS,
    EASY_EMOJIS,
    HARD_EMOJIS,
    MEDIUM_EMOJIS,
    NORMAL_EMOJIS,
    REST_EMOJIS,
    DISCIPLINE_WEIGHTS,
    _get_discipline_weight,
    _parse_line,
    _emoji_for_score,
    get_mood_emoji,
)


def emoji_id(value: str) -> int:
    match = re.search(r'emoji-id="(\d+)"', value)
    if match is None:
        raise AssertionError("custom emoji ID is missing")
    return int(match.group(1))


class ScheduleMoodTest(unittest.TestCase):
    def test_all_supplied_custom_emojis_are_registered(self):
        pools = [
            REST_EMOJIS,
            EASY_EMOJIS,
            NORMAL_EMOJIS,
            MEDIUM_EMOJIS,
            HARD_EMOJIS,
            CRITICAL_EMOJIS,
        ]
        options = [option for pool in pools for option in pool]

        self.assertEqual(len(options), 24)
        self.assertEqual(len({option[0] for option in options}), 24)
        self.assertEqual(options, DIFFICULTY_EMOJIS)

    def test_every_emoji_is_a_separate_score_level(self):
        for score, option in enumerate(DIFFICULTY_EMOJIS):
            with self.subTest(score=score):
                self.assertEqual(_emoji_for_score(score), option)

    def test_scores_above_scale_use_sos_level(self):
        self.assertEqual(_emoji_for_score(999), DIFFICULTY_EMOJIS[-1])

    def test_schedule_score_moves_through_individual_levels(self):
        cases = [
            ([], 0),
            (["▪️1 пара – Физическая культура – Иванов И.И. – 101"], 1),
            (["▪️1 пара – Литература – Иванов И.И. – 101"], 2),
            (["▪️1 пара – Неизвестная дисциплина – Иванов И.И. – 101"], 3),
            (["▪️1 пара – Русский язык – Иванов И.И. – 101"], 4),
            (["▪️1 пара – Физика – Иванов И.И. – 101"], 5),
            (["▪️1 пара – Системное программирование – Иванов И.И. – 101"], 6),
        ]
        for lines, score in cases:
            with self.subTest(score=score):
                self.assertEqual(
                    emoji_id(get_mood_emoji(lines)),
                    DIFFICULTY_EMOJIS[score][0],
                )

    def test_current_disciplines_have_individual_exact_weights(self):
        self.assertEqual(len(DISCIPLINE_WEIGHTS), 68)
        self.assertTrue(all(1 <= value <= 6 for value in DISCIPLINE_WEIGHTS.values()))
        self.assertEqual(_get_discipline_weight("Основы философии"), 1)
        self.assertEqual(_get_discipline_weight("Оптимизация веб-приложений"), 4)
        self.assertEqual(_get_discipline_weight("Криптографические средства защиты информации"), 6)

    def test_bold_teacher_markup_does_not_break_teacher_scoring(self):
        line = "▪️1 пара – Основы философии – <b>Шульга Н.Н.</b> – 27"
        discipline, teachers = _parse_line(line)

        self.assertEqual(discipline, "Основы философии")
        self.assertEqual(teachers, ["Шульга Н.Н."])
        self.assertEqual(
            emoji_id(get_mood_emoji([line])),
            DIFFICULTY_EMOJIS[4][0],
        )


if __name__ == "__main__":
    unittest.main()
