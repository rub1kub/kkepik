import datetime as dt
import unittest
from unittest.mock import patch

from schedules.schedule_dates import COLLEGE_TIMEZONE, current_college_date, is_current_schedule


class ScheduleDatesTest(unittest.TestCase):
    def test_college_timezone_is_moscow(self):
        self.assertEqual(COLLEGE_TIMEZONE.key, "Europe/Moscow")

    def test_relative_date_uses_college_midnight_not_server_midnight(self):
        instant = dt.datetime(2026, 10, 8, 20, 59, tzinfo=dt.timezone.utc)
        with patch("schedules.schedule_dates.dt.datetime") as clock:
            clock.now.return_value = instant.astimezone(COLLEGE_TIMEZONE)
            self.assertEqual(current_college_date(), dt.date(2026, 10, 8))
            clock.now.assert_called_once_with(COLLEGE_TIMEZONE)

    def setUp(self):
        self.today = dt.date(2026, 8, 23)

    def test_working_days_in_next_two_days_are_current(self):
        self.assertFalse(is_current_schedule("23.08.2026", today=self.today))
        self.assertTrue(is_current_schedule("24.08.2026", today=self.today))
        self.assertTrue(is_current_schedule("25.08.2026", today=self.today))

    def test_old_and_distant_schedules_are_not_current(self):
        self.assertFalse(is_current_schedule("24.06.2026", today=self.today))
        self.assertFalse(is_current_schedule("22.08.2026", today=self.today))
        self.assertFalse(is_current_schedule("26.08.2026", today=self.today))

    def test_sunday_is_never_a_schedule_day(self):
        saturday = dt.date(2026, 8, 22)

        self.assertFalse(is_current_schedule("23.08.2026", today=saturday))
        self.assertTrue(is_current_schedule("24.08.2026", today=saturday))


if __name__ == "__main__":
    unittest.main()
