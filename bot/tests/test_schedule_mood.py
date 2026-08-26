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
            (["▪️1 пара – Программирование – Иванов И.И. – 101"], 3),
            (["▪️1 пара – Физика – Иванов И.И. – 101"], 4),
        ]
        for lines, score in cases:
            with self.subTest(score=score):
                self.assertEqual(
                    emoji_id(get_mood_emoji(lines)),
                    DIFFICULTY_EMOJIS[score][0],
                )


if __name__ == "__main__":
    unittest.main()
