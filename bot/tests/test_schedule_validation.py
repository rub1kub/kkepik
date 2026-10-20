import unittest

import pandas as pd

from handlers.schedule_broadcaster import validate_schedule_df


class ScheduleValidationTest(unittest.TestCase):
    def test_empty_schedule_is_rejected(self):
        error = validate_schedule_df(pd.DataFrame(), "groups")

        self.assertIn("пуста", error)

    def test_group_file_without_group_headers_is_rejected(self):
        error = validate_schedule_df(pd.DataFrame([["предмет", "аудитория"]]), "groups")

        self.assertIn("ни одного корректного номера группы", error)

    def test_d3_group_schedule_is_accepted(self):
        df = pd.DataFrame(
            [
                [None, None, "103-Д3-3ИНС", None],
                ["1", None, "Оптимизация веб-приложений", "37"],
                ["1", None, "Дмитриева А.С.", None],
            ]
        )

        self.assertIsNone(validate_schedule_df(df, "groups"))


if __name__ == "__main__":
    unittest.main()
