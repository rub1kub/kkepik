import unittest
from pathlib import Path

from schedules.group_schedule import get_schedule_for_group, is_group_name
from schedules.pdf_to_df import (
    _find_group_positions,
    _find_unrecognized_group_headers,
    pdf_to_dataframe,
)
from schedules.schedule_catalog import extract_catalog_entries
from tracked_groups import is_valid_group_name


class StubPage:
    def extract_words(self, **kwargs):
        return [
            {"text": "102-Д9-3ССА", "x0": 100.0, "top": 10.0},
            {"text": "103-Д3-3ИНС", "x0": 200.0, "top": 10.0},
            {"text": "151-КД9-1КСК", "x0": 300.0, "top": 10.0},
            {"text": "155-КД9-1РУПО", "x0": 400.0, "top": 10.0},
            {"text": "не-группа", "x0": 500.0, "top": 10.0},
        ]


class UnsupportedGroupPage:
    def extract_words(self, **kwargs):
        return [
            {"text": "102-Д9-3ССА", "x0": 100.0, "top": 10.0},
            {"text": "103-ОЧНО-3ИНС", "x0": 200.0, "top": 10.0},
        ]


class GroupNameFormatsTest(unittest.TestCase):
    def test_supported_study_codes_and_specialty_lengths(self):
        for group in (
            "103-Д3-3ИНС",
            "103-Д9-3ИНС",
            "151-КД9-1КСК",
            "155-КД9-1РУПО",
            "119-З11-3ССА",
        ):
            with self.subTest(group=group):
                self.assertTrue(is_group_name(group))
                self.assertTrue(is_valid_group_name(group))

    def test_pdf_header_discovery_does_not_drop_d3_group(self):
        groups = _find_group_positions(StubPage())

        self.assertEqual(
            [group["name"] for group in groups],
            [
                "102-Д9-3ССА",
                "103-Д3-3ИНС",
                "151-КД9-1КСК",
                "155-КД9-1РУПО",
            ],
        )

    def test_unknown_group_header_is_reported_instead_of_merged(self):
        self.assertEqual(
            _find_unrecognized_group_headers(UnsupportedGroupPage()),
            ["103-ОЧНО-3ИНС"],
        )

    def test_current_pdf_keeps_102_and_103_schedules_separate(self):
        pdf_path = (
            Path(__file__).parents[1]
            / "data"
            / "Расписание занятий 01.09.2026 ГРУППЫ.pdf"
        )
        if not pdf_path.exists():
            self.skipTest("Production regression PDF is not available")

        df = pdf_to_dataframe(str(pdf_path))
        groups = extract_catalog_entries(df)["groups"]
        schedule_102 = "\n".join(get_schedule_for_group(df, "102-Д9-3ССА") or [])
        schedule_103 = "\n".join(get_schedule_for_group(df, "103-Д3-3ИНС") or [])
        schedule_97 = "\n".join(get_schedule_for_group(df, "97-КД9-4БТС") or [])
        schedule_112 = "\n".join(get_schedule_for_group(df, "112-Д9-3РЭС") or [])

        self.assertEqual(len(groups), 65)
        self.assertIn("103-Д3-3ИНС", groups)
        self.assertIn("Организация, принципы построения", schedule_102)
        self.assertNotIn("Оптимизация веб-приложений", schedule_102)
        self.assertIn("Оптимизация веб-приложений", schedule_103)
        self.assertNotIn("Организация, принципы построения", schedule_103)
        self.assertNotIn("Защита информации в информационно", schedule_97)
        self.assertIn("Физическая культура", schedule_112)
        self.assertIn("с/з_1", schedule_112)
        self.assertNotIn("культурас/з", schedule_112)

        all_schedules = "\n".join(
            line
            for group in groups
            for line in (get_schedule_for_group(df, group) or [])
        )
        self.assertNotRegex(all_schedules, r"(?: – |\s)(?:0|00|0_0)(?:\n|$)")


if __name__ == "__main__":
    unittest.main()
