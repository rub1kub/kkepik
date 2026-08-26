import unittest

from schedules.pair_times import get_pair_time


class PairTimesTest(unittest.TestCase):
    def test_weekday_practice_times(self):
        self.assertEqual(get_pair_time(5, "24.08.2026"), "15:05–16:25")
        self.assertEqual(get_pair_time(6, "24.08.2026"), "16:35–17:55")

    def test_saturday_practice_times(self):
        self.assertEqual(get_pair_time(5, "29.08.2026"), "14:25–15:40")
        self.assertEqual(get_pair_time(6, "29.08.2026"), "15:50–17:05")


if __name__ == "__main__":
    unittest.main()
