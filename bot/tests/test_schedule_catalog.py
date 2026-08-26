import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import global_schedules
from schedules import parser_all
from schedules.schedule_catalog import (
    academic_year_for_date,
    extract_catalog_entries,
    load_catalog,
    update_schedule_catalog,
)


class ScheduleCatalogTest(unittest.TestCase):
    def _sample_df(self):
        rows = [[None] * 18 for _ in range(5)]
        rows[0][2] = "103-Д9-1ИНС"
        rows[1][0] = "1"
        rows[1][2] = "Основы алгоритмизации"
        rows[1][3] = "84"
        rows[1][4] = "Раздел2 не аудитория"
        rows[2][0] = "1"
        rows[2][2] = "Каркавин Д.О."
        rows[3][0] = "2"
        rows[3][2] = "Архитектура"
        rows[3][3] = "86-а"
        rows[4][0] = "2"
        rows[4][2] = "Каркавин ДО"
        return pd.DataFrame(rows)

    def test_academic_year_boundary(self):
        self.assertEqual(academic_year_for_date("31.07.2026"), "2025-2026")
        self.assertEqual(academic_year_for_date("01.08.2026"), "2026-2027")

    def test_extracts_and_deduplicates_entities(self):
        entries = extract_catalog_entries(self._sample_df())
        self.assertEqual(entries["groups"], ["103-Д9-1ИНС"])
        self.assertEqual(entries["teachers"], ["Каркавин Д.О."])
        self.assertEqual(entries["audiences"], ["84", "86-а"])

    def test_same_day_update_replaces_source_and_other_days_merge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "catalog.json"
            today = dt.date(2026, 8, 24)
            first = self._sample_df()
            update_schedule_catalog(
                first,
                "25.08.2026",
                "groups",
                source_name="first.pdf",
                today=today,
                path=path,
            )

            replacement = self._sample_df()
            replacement.iat[0, 2] = "104-Д9-1ИНС"
            replacement.iat[1, 3] = "121"
            update_schedule_catalog(
                replacement,
                "25.08.2026",
                "groups",
                source_name="replacement.pdf",
                today=today,
                path=path,
            )

            catalog = load_catalog(today=today, path=path)
            self.assertNotIn("103-Д9-1ИНС", catalog["groups"])
            self.assertEqual(catalog["groups"], ["104-Д9-1ИНС"])
            self.assertNotIn("84", catalog["audiences"])
            self.assertIn("121", catalog["audiences"])

    def test_old_schedule_cannot_seed_new_academic_year(self):
        df = self._sample_df()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "catalog.json"
            result = update_schedule_catalog(
                df,
                "24.06.2026",
                "groups",
                source_name="archived-2025-2026.pdf",
                today=dt.date(2026, 8, 24),
                path=path,
            )
            self.assertFalse(result["updated"])
            self.assertFalse(path.exists())

            first_new_year_result = update_schedule_catalog(
                df,
                "25.08.2026",
                "groups",
                source_name="first-2026-2027.pdf",
                today=dt.date(2026, 8, 24),
                path=path,
            )
            self.assertTrue(first_new_year_result["updated"])
            self.assertGreater(first_new_year_result["groups"], 0)
            self.assertGreater(first_new_year_result["teachers"], 0)
            self.assertGreater(first_new_year_result["audiences"], 0)

    def test_parser_does_not_load_latest_stale_file(self):
        old_groups = global_schedules.last_groups_df
        old_date = global_schedules.last_groups_date
        global_schedules.last_groups_df = None
        global_schedules.last_groups_date = None
        try:
            with (
                patch(
                    "schedules.schedule_catalog.get_catalog_values",
                    return_value=[],
                ),
                patch(
                    "schedules.parser_all._find_latest_schedule_file",
                    return_value="/tmp/Расписание на 24.06.2026_ГРУППЫ.xlsx",
                ),
                patch("schedules.parser_all._load_df_from_file") as load_df,
            ):
                self.assertEqual(parser_all.get_all_groups(), [])
            load_df.assert_not_called()
        finally:
            global_schedules.last_groups_df = old_groups
            global_schedules.last_groups_date = old_date

    def test_latest_file_ignores_newer_mtime_on_archived_schedule(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            current = Path(temp_dir) / "Расписание на 25.08.2026_ГРУППЫ.xlsx"
            archived = Path(temp_dir) / "Расписание на 24.06.2026_ГРУППЫ.xlsx"
            current.touch()
            archived.touch()
            os.utime(current, (1, 1))
            os.utime(archived, (2, 2))
            with (
                patch("config.DATA_DIR", temp_dir),
                patch(
                    "schedules.schedule_dates.current_college_date",
                    return_value=dt.date(2026, 8, 24),
                ),
            ):
                selected = parser_all._find_latest_schedule_file("groups")
            self.assertEqual(selected, str(current))


if __name__ == "__main__":
    unittest.main()
