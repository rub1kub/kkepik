import datetime as dt
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from schedules.knowledge_store import KnowledgeStore, extract_lessons, normalize_date


def source_frame():
    rows = [[None] * 18 for _ in range(5)]
    rows[0][2], rows[0][6] = "103-Д9-3ИНС", "104-Д9-1ИНС"
    rows[1][0] = "1"
    rows[1][2], rows[1][3] = "Иностранный язык", "108"
    rows[1][4], rows[1][5] = "Иностранный язык", "54"
    rows[2][2], rows[2][4] = "Борисова Е.В.", "Абдулла Б.М."
    rows[3][0] = "3"
    rows[3][2], rows[3][5] = "Физическая культура", "с/з_4"
    rows[4][2] = "Шапошник М.В."
    return pd.DataFrame(rows)


class KnowledgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = KnowledgeStore(self.temp.name)
        self.source = Path(self.temp.name) / "ГРУППЫ.pdf"
        self.source.write_bytes(b"%PDF-1.0 test schedule source")

    def ingest(self, frame=None, **kwargs):
        return self.store.ingest(self.source, "2026-10-09", source_frame() if frame is None else frame, **kwargs)

    def test_structured_subgroups_preserve_associations(self):
        groups, lessons = extract_lessons(source_frame(), "2026-10-09")
        self.assertEqual(groups, ["103-Д9-3ИНС", "104-Д9-1ИНС"])
        self.assertEqual(len(lessons), 3)
        self.assertEqual([(x["subgroup"], x["teacher"], x["audience"]) for x in lessons[:2]],
                         [(1, "Борисова Е.В.", "108"), (2, "Абдулла Б.М.", "54")])
        self.assertEqual(lessons[-1]["subgroup"], None)
        self.assertEqual(lessons[-1]["audience"], "С/З_4")
        self.assertEqual(lessons[-1]["start_time"], "12:05")

    def test_snapshot_and_ingestion_are_immutable_idempotent(self):
        first = self.ingest()
        second = self.ingest()
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(second["revision"], 1)
        path, mime = self.store.source_path(first["sha"])
        self.assertEqual(path.read_bytes(), self.source.read_bytes())
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), first["sha"])
        self.assertEqual(mime, "application/pdf")
        self.source.write_bytes(b"%PDF-1.0 changed schedule")
        updated = source_frame()
        updated.iat[1, 3] = "101"
        third = self.ingest(updated)
        self.assertEqual(third["revision"], 2)
        self.assertEqual(path.read_bytes(), b"%PDF-1.0 test schedule source")
        changes = self.store.changes("group", "103-Д9-3ИНС", "2026-10-09")
        self.assertEqual(len(changes["revisions"]), 2)
        self.assertEqual(changes["changes"][0]["removed"][0]["audience"], "108")
        self.assertEqual(changes["changes"][0]["added"][0]["audience"], "101")
        self.assertFalse(changes["history_complete"])

    def test_honest_day_statuses(self):
        self.ingest()
        result = self.store.schedule("group", "103-Д9-3ИНС", start="2026-10-08", end="2026-10-10")
        self.assertEqual([d["status"] for d in result["days"]], ["missing", "available", "missing"])
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(self.store.schedule("group", "104-Д9-1ИНС", date="2026-10-09")["days"][0]["status"], "no_lessons")
        self.assertEqual(self.store.schedule("group", "999-Д9-1ИНС", date="2026-10-09")["days"][0]["status"], "entity_not_found")
        self.assertEqual(result["days"][1]["source"]["published_at"], None)
        self.assertEqual(result["lesson_status"], "scheduled")

    def test_reverting_to_earlier_source_is_a_new_observed_revision(self):
        original_bytes = self.source.read_bytes()
        self.ingest(archived_import=True, received_at="2026-09-01T00:00:00+00:00")
        self.source.write_bytes(b"%PDF second revision")
        changed = source_frame()
        changed.iat[1, 3] = "101"
        self.ingest(changed)
        self.source.write_bytes(original_bytes)
        restored = self.ingest(received_at="2026-10-08T15:00:00+00:00")
        self.assertEqual(restored["revision"], 3)
        result = self.store.schedule("group", "103-Д9-3ИНС", date="2026-10-09")
        self.assertEqual(result["days"][0]["lessons"][0]["audience"], "108")
        self.assertEqual(result["days"][0]["source"]["revision"], 3)
        self.assertFalse(result["days"][0]["source"]["archived_import"])
        self.assertEqual(result["days"][0]["source"]["received_at"], "2026-10-08T15:00:00+00:00")
        self.assertFalse(self.ingest(archived_import=True)["created"])

    def test_corrupt_existing_snapshot_is_repaired_atomically(self):
        first = self.ingest()
        snapshot, _ = self.store.source_path(first["sha"])
        snapshot.write_bytes(b"broken snapshot")
        self.ingest()
        self.assertEqual(snapshot.read_bytes(), self.source.read_bytes())
        self.assertFalse(list(self.store.sources_dir.glob(".snapshot-*")))

    def test_database_connections_are_closed(self):
        self.ingest()
        with self.store._connect() as connection:
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
        with self.assertRaises(Exception):
            connection.execute("SELECT 1")

    def test_teacher_and_room_queries_do_not_cross_subgroups(self):
        self.ingest()
        result = self.store.schedule("teacher", "Борисова ЕВ", date="2026-10-09")
        self.assertEqual(len(result["days"][0]["lessons"]), 1)
        self.assertEqual(result["days"][0]["lessons"][0]["audience"], "108")
        rooms = self.store.schedule("audience", "54", date="2026-10-09")
        self.assertEqual(rooms["days"][0]["lessons"][0]["teacher"], "Абдулла Б.М.")
        empty = self.store.schedule("teacher", "Иванов И.И.", date="2026-10-09")
        self.assertEqual(empty["days"][0]["status"], "entity_not_found")

    def test_failed_revision_does_not_hide_last_valid_source(self):
        self.ingest()
        self.source.write_bytes(b"corrupt new PDF")
        self.store.ingest(self.source, "2026-10-09", None, error="private path should never be public")
        result = self.store.schedule("group", "103-Д9-3ИНС", date="2026-10-09")["days"][0]
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["source"]["revision"], 1)
        self.assertIn("warning", result)
        self.assertNotIn("private path", str(result))
        self.store.ingest(self.source, "2026-10-10", None, error="bad")
        self.assertEqual(self.store.schedule("group", "103-Д9-3ИНС", date="2026-10-10")["days"][0]["status"], "invalid_source")

    def test_same_hash_can_recover_from_failed_to_parsed_without_downgrade(self):
        for date, archived in (("2026-10-09", False), ("2026-10-10", True)):
            with self.subTest(archived_import=archived):
                options = {"archived_import": archived}
                failed = self.store.ingest(self.source, date, None, error="transient failure", **options)
                self.assertTrue(failed["created"])
                self.assertEqual(failed["status"], "failed")
                repeated_failure = self.store.ingest(self.source, date, None, error="still unavailable", **options)
                self.assertFalse(repeated_failure["created"])
                self.assertEqual(repeated_failure["revision"], 1)

                recovered = self.store.ingest(self.source, date, source_frame(), **options)
                self.assertTrue(recovered["created"])
                self.assertEqual(recovered["sha"], failed["sha"])
                self.assertEqual(recovered["revision"], 2)
                self.assertEqual(recovered["status"], "parsed")
                repeated_success = self.store.ingest(self.source, date, source_frame(), **options)
                self.assertFalse(repeated_success["created"])
                self.assertEqual(repeated_success["revision"], 2)

                downgrade = self.store.ingest(self.source, date, None, error="another transient failure", **options)
                self.assertFalse(downgrade["created"])
                self.assertEqual(downgrade["status"], "parsed")
                revisions = self.store.changes("group", "103-Д9-3ИНС", date)["revisions"]
                self.assertEqual([r["status"] for r in revisions], ["failed", "parsed"])
                day = self.store.schedule("group", "103-Д9-3ИНС", date=date)["days"][0]
                self.assertEqual(day["status"], "available")
                self.assertEqual(day["source"]["revision"], 2)

    def test_archive_coverage_and_resolution_are_not_guesses(self):
        self.ingest(archived_import=True, received_at="2026-10-01T00:00:00+00:00")
        ambiguous = self.store.resolve("group", "ИНС")
        self.assertTrue(ambiguous["ambiguous"])
        self.assertEqual(len(ambiguous["matches"]), 2)
        self.assertEqual(self.store.resolve("group", "103-д9-3инс")["matches"], ["103-Д9-3ИНС"])
        source = self.store.schedule("group", "103-Д9-3ИНС", date="2026-10-09")["days"][0]["source"]
        self.assertEqual(source["received_at_semantics"], "local_file_mtime")
        self.assertEqual(source["published_at"], None)
        coverage = self.store.coverage(entity_type="group", entity="103-Д9-3ИНС", start="2026-10-08", end="2026-10-10")
        self.assertEqual(coverage["available_dates"], ["2026-10-09"])
        self.assertEqual(coverage["missing_dates"], ["2026-10-08", "2026-10-10"])
        self.assertFalse(coverage["history_complete"])

    def test_no_users_database_and_safe_source_hashes(self):
        self.ingest()
        self.assertFalse((Path(self.temp.name) / "database.db").exists())
        self.assertIsNone(self.store.source_path("../database.db"))
        self.assertIsNone(self.store.source_path("0" * 64))
        self.assertFalse(self.store.catalog()["homework"]["supported"])
        self.assertEqual(self.store.catalog()["group_dates"]["103-Д9-3ИНС"], ["2026-10-09"])

    def test_ranges_and_relative_dates_are_bounded(self):
        with patch("schedules.knowledge_store.current_college_date", return_value=dt.date(2026, 10, 8)):
            self.assertEqual(normalize_date("tomorrow"), "2026-10-09")
            self.assertEqual(normalize_date("yesterday"), "2026-10-07")
        for arguments in ({"start": "2026-10-01", "end": "2026-11-01"},
                          {"date": "2026-10-08", "start": "2026-10-08", "end": "2026-10-09"},
                          {"date": "2026-02-30"}, {"start": "2026-10-08"}):
            with self.assertRaises(ValueError):
                self.store.schedule("group", "103-Д9-3ИНС", **arguments)

    def test_saturday_and_explicit_exception_times(self):
        _, lessons = extract_lessons(source_frame(), "2026-10-10")
        self.assertEqual(lessons[0]["end_time"], "10:00")
        rows = [[None] * 23 for _ in range(3)]
        rows[0][4] = "103-Д9-1ИНС"
        rows[1][0], rows[1][2], rows[1][4], rows[1][7] = "1", "10.15-11.20", "Математика", "48"
        rows[2][4] = "Емельянова Г.Ф."
        _, lessons = extract_lessons(pd.DataFrame(rows), "2026-09-01")
        self.assertEqual(lessons[0]["start_time"], "10:15")
        self.assertEqual(lessons[0]["end_time"], "11:20")
        self.assertEqual(lessons[0]["audience"], "48")


if __name__ == "__main__":
    unittest.main()
