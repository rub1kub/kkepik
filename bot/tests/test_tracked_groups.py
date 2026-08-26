import sqlite3
import tempfile
import unittest
from pathlib import Path

from tracked_groups import (
    add_tracked_group,
    get_tracked_groups,
    migrate_legacy_groups,
    remove_tracked_group,
)


class TrackedGroupsTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "test.db")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    role TEXT NOT NULL,
                    name_or_group TEXT NOT NULL,
                    is_class_teacher INTEGER DEFAULT 0,
                    class_group TEXT
                )
                """
            )

    def tearDown(self):
        self.tempdir.cleanup()

    def add_user(self, user_id, role, name, is_class_teacher=0, class_group=None):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?, ?)",
                (user_id, role, name, is_class_teacher, class_group),
            )

    def migrate(self):
        with sqlite3.connect(self.db_path) as conn:
            migrate_legacy_groups(conn)

    def test_migration_normalizes_valid_legacy_groups(self):
        self.add_user(1, "Я студент", "103-д9-2инс")
        self.add_user(2, "Я студент", "124-Д4-2ССА")
        self.add_user(3, "Я студент", "впадлу")
        self.add_user(4, "Я преподаватель", "Иванов И.И.", 1, "105-Д9-3ИСП")

        self.migrate()

        self.assertEqual(get_tracked_groups(self.db_path, 1), ["103-Д9-2ИНС"])
        self.assertEqual(get_tracked_groups(self.db_path, 2), ["124-Д4-2ССА"])
        self.assertEqual(get_tracked_groups(self.db_path, 3), [])
        self.assertEqual(get_tracked_groups(self.db_path, 4), ["105-Д9-3ИСП"])

    def test_student_can_track_two_groups_but_not_three(self):
        self.add_user(1, "Я студент", "103-Д9-2ИНС")
        self.migrate()

        self.assertEqual(add_tracked_group(self.db_path, 1, "104-д9-2исп"), "added")
        self.assertEqual(add_tracked_group(self.db_path, 1, "105-Д9-2ИСП"), "full")
        self.assertEqual(
            get_tracked_groups(self.db_path, 1),
            ["103-Д9-2ИНС", "104-Д9-2ИСП"],
        )

    def test_removing_primary_promotes_second_group(self):
        self.add_user(1, "Я студент", "103-Д9-2ИНС")
        self.migrate()
        add_tracked_group(self.db_path, 1, "104-Д9-2ИСП")

        self.assertEqual(remove_tracked_group(self.db_path, 1, 1), "removed")
        self.assertEqual(get_tracked_groups(self.db_path, 1), ["104-Д9-2ИСП"])
        with sqlite3.connect(self.db_path) as conn:
            primary = conn.execute(
                "SELECT name_or_group FROM users WHERE user_id = 1"
            ).fetchone()[0]
        self.assertEqual(primary, "104-Д9-2ИСП")

    def test_student_cannot_remove_last_group(self):
        self.add_user(1, "Я студент", "103-Д9-2ИНС")
        self.migrate()

        self.assertEqual(
            remove_tracked_group(self.db_path, 1, 1),
            "last_student_group",
        )

    def test_teacher_can_remove_last_tracked_group(self):
        self.add_user(1, "Я преподаватель", "Иванов И.И.", 1, "103-Д9-2ИНС")
        self.migrate()

        self.assertEqual(remove_tracked_group(self.db_path, 1, 1), "removed")
        with sqlite3.connect(self.db_path) as conn:
            teacher = conn.execute(
                "SELECT is_class_teacher, class_group FROM users WHERE user_id = 1"
            ).fetchone()
        self.assertEqual(teacher, (0, None))


if __name__ == "__main__":
    unittest.main()
