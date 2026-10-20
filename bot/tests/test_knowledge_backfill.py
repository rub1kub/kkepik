import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from schedules.knowledge_store import KnowledgeStore
from scripts.backfill_knowledge import import_archive


def legacy_frame(group, subject, teacher, room):
    rows = [[None] * 23 for _ in range(3)]
    rows[0][4] = group
    rows[1][0], rows[1][4], rows[1][7] = 1, subject, room
    rows[2][4] = teacher
    return pd.DataFrame(rows)


class KnowledgeBackfillTests(unittest.TestCase):
    def test_all_dated_sheets_and_academic_years_are_imported_without_users(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Расписание на 30.07.2025 ГРУППЫ.xlsx"
            with pd.ExcelWriter(path) as writer:
                legacy_frame("103-Д9-2ИНС", "Математика", "Иванов И.И.", "48").to_excel(writer, sheet_name="30.07.2025", header=False, index=False)
                legacy_frame("103-Д9-3ИНС", "Физика", "Петров П.П.", "27").to_excel(writer, sheet_name="01.09.2025", header=False, index=False)
                pd.DataFrame([["не расписание"]]).to_excel(writer, sheet_name="Лист3", header=False, index=False)
            store = KnowledgeStore(directory)
            with contextlib.redirect_stdout(io.StringIO()):
                first = import_archive(store)
                second = import_archive(store)
            self.assertEqual(first["created"], 2)
            self.assertEqual(first["failed"], 0)
            self.assertEqual(second["created"], 0)
            self.assertEqual(second["unchanged"], 2)
            self.assertEqual(store.catalog()["dates"], ["2025-07-30", "2025-09-01"])
            july = store.schedule("group", "103-Д9-2ИНС", date="2025-07-30")["days"][0]
            september = store.schedule("group", "103-Д9-3ИНС", date="2025-09-01")["days"][0]
            self.assertEqual(july["source"]["academic_year"], "2024-2025")
            self.assertEqual(september["source"]["academic_year"], "2025-2026")
            self.assertTrue(july["source"]["archived_import"])
            self.assertFalse((Path(directory) / "database.db").exists())

    def test_invalid_dated_file_is_visible_as_invalid_not_no_lessons(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Расписание на 09.10.2026 ГРУППЫ.xlsx"
            pd.DataFrame([["не расписание"]]).to_excel(path, header=False, index=False)
            store = KnowledgeStore(directory)
            with contextlib.redirect_stdout(io.StringIO()):
                result = import_archive(store)
            self.assertEqual(result["failed"], 1)
            day = store.schedule("group", "103-Д9-3ИНС", date="2026-10-09")["days"][0]
            self.assertEqual(day["status"], "invalid_source")


if __name__ == "__main__":
    unittest.main()
