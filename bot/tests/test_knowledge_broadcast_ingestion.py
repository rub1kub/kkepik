import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from handlers.schedule_broadcaster import process_and_broadcast
from schedules.knowledge_store import KnowledgeStore
from test_knowledge_store import source_frame


class KnowledgeBroadcastIngestionTests(unittest.TestCase):
    def test_previous_source_is_preserved_before_same_filename_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            incoming_dir = Path(directory) / "incoming"
            data_dir.mkdir()
            incoming_dir.mkdir()
            filename = "Расписание на 09.10.2026 ГРУППЫ.pdf"
            previous = data_dir / filename
            incoming = incoming_dir / filename
            previous.write_bytes(b"%PDF previous schedule")
            incoming.write_bytes(b"%PDF incoming schedule")
            old_frame = source_frame()
            new_frame = source_frame()
            new_frame.iat[1, 3] = "101"

            def load(path, date):
                return (old_frame if Path(path) == previous else new_frame), None

            with (patch("handlers.schedule_broadcaster.config.DATA_DIR", str(data_dir)),
                  patch("handlers.schedule_broadcaster.is_current_schedule", return_value=False),
                  patch("handlers.schedule_broadcaster.load_existing_schedule", return_value=old_frame),
                  patch("handlers.schedule_broadcaster.load_schedule_df", side_effect=load),
                  patch("schedules.schedule_catalog.update_schedule_catalog", return_value={"updated": False})):
                success, message = asyncio.run(process_and_broadcast(str(incoming), filename, broadcast=False))
            self.assertTrue(success, message)
            self.assertEqual(previous.read_bytes(), b"%PDF incoming schedule")
            store = KnowledgeStore(data_dir)
            revisions = store.changes("group", "103-Д9-3ИНС", "2026-10-09")["revisions"]
            self.assertEqual(len(revisions), 2)
            first_path, _ = store.source_path(revisions[0]["source"]["id"])
            second_path, _ = store.source_path(revisions[1]["source"]["id"])
            self.assertEqual(first_path.read_bytes(), b"%PDF previous schedule")
            self.assertEqual(second_path.read_bytes(), b"%PDF incoming schedule")
            self.assertEqual(revisions[0]["lessons"][0]["audience"], "108")
            self.assertEqual(revisions[1]["lessons"][0]["audience"], "101")
            self.assertFalse((data_dir / "database.db").exists())


if __name__ == "__main__":
    unittest.main()
