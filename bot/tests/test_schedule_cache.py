import datetime as dt
import unittest
from unittest.mock import patch

import global_schedules


class ScheduleCacheTest(unittest.TestCase):
    def test_restart_does_not_load_stale_schedule(self):
        global_schedules.last_groups_df = object()
        global_schedules.last_groups_date = "24.06.2026"
        global_schedules.last_groups_crop_cache = {"OLD": b"image"}
        global_schedules.last_teachers_df = object()
        global_schedules.last_teachers_date = "24.06.2026"

        with (
            patch(
                "schedules.parser_all._find_latest_schedule_file",
                return_value="/tmp/Расписание на 24.06.2026_ГРУППЫ.xlsx",
            ),
            patch("schedules.parser_all._load_df_from_file") as load_df,
        ):
            global_schedules.reload_cache(today=dt.date(2026, 8, 23))

        load_df.assert_not_called()
        self.assertIsNone(global_schedules.last_groups_df)
        self.assertIsNone(global_schedules.last_groups_date)
        self.assertEqual(global_schedules.last_groups_crop_cache, {})
        self.assertIsNone(global_schedules.last_teachers_df)
        self.assertIsNone(global_schedules.last_teachers_date)


if __name__ == "__main__":
    unittest.main()
