"""Persistence helpers for user group subscriptions."""

from __future__ import annotations

import sqlite3
import re


MAX_TRACKED_GROUPS = 2
_GROUP_RE = re.compile(
    r"^\d{2,3}-[А-ЯЁA-Z]{1,2}\d{1,2}-\d{1,2}[А-ЯЁA-Z]{3}(?:-\d)?$"
)


def normalize_group_name(group_name: str) -> str:
    return group_name.strip().upper()


def is_valid_group_name(group_name: str) -> bool:
    return bool(_GROUP_RE.fullmatch(normalize_group_name(group_name)))


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tracked_groups (
            user_id INTEGER NOT NULL,
            group_name TEXT NOT NULL COLLATE NOCASE,
            slot INTEGER NOT NULL CHECK (slot BETWEEN 1 AND 2),
            PRIMARY KEY (user_id, group_name),
            UNIQUE (user_id, slot)
        )
        """
    )


def migrate_legacy_groups(conn: sqlite3.Connection) -> None:
    ensure_schema(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO tracked_groups (user_id, group_name, slot)
        SELECT user_id, UPPER(name_or_group), 1
        FROM users
        WHERE role = 'Я студент'
          AND (UPPER(name_or_group) LIKE '%-Д9-%' OR UPPER(name_or_group) LIKE '%-КД9-%')
        """
    )
    student_rows = conn.execute(
        "SELECT user_id, name_or_group FROM users WHERE role = 'Я студент'"
    ).fetchall()
    conn.executemany(
        "INSERT OR IGNORE INTO tracked_groups (user_id, group_name, slot) VALUES (?, ?, 1)",
        [
            (user_id, normalize_group_name(group_name))
            for user_id, group_name in student_rows
            if is_valid_group_name(group_name)
        ],
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO tracked_groups (user_id, group_name, slot)
        SELECT user_id, UPPER(class_group), 1
        FROM users
        WHERE role = 'Я преподаватель'
          AND is_class_teacher = 1
          AND class_group IS NOT NULL
          AND TRIM(class_group) != ''
        """
    )


def initialize(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        migrate_legacy_groups(conn)


def get_user_role(db_path: str, user_id: int) -> str | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT role FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return row[0] if row else None


def get_tracked_groups(db_path: str, user_id: int) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT group_name
            FROM tracked_groups
            WHERE user_id = ?
            ORDER BY slot
            """,
            (user_id,),
        ).fetchall()
    return [row[0] for row in rows]


def get_all_tracked_groups(db_path: str) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT user_id, group_name
            FROM tracked_groups
            ORDER BY user_id, slot
            """
        ).fetchall()
    for user_id, group_name in rows:
        result.setdefault(user_id, []).append(group_name)
    return result


def replace_tracked_groups(db_path: str, user_id: int, groups: list[str]) -> None:
    normalized = list(dict.fromkeys(normalize_group_name(group) for group in groups if group.strip()))
    if len(normalized) > MAX_TRACKED_GROUPS:
        raise ValueError("Too many tracked groups")

    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute("DELETE FROM tracked_groups WHERE user_id = ?", (user_id,))
        conn.executemany(
            "INSERT INTO tracked_groups (user_id, group_name, slot) VALUES (?, ?, ?)",
            [(user_id, group, slot) for slot, group in enumerate(normalized, 1)],
        )
        _sync_legacy_primary(conn, user_id, normalized)


def add_tracked_group(db_path: str, user_id: int, group_name: str) -> str:
    group = normalize_group_name(group_name)
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        role_row = conn.execute(
            "SELECT role FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not role_row:
            return "unregistered"

        current = [
            row[0]
            for row in conn.execute(
                "SELECT group_name FROM tracked_groups WHERE user_id = ? ORDER BY slot",
                (user_id,),
            ).fetchall()
        ]
        if group.casefold() in {item.casefold() for item in current}:
            return "exists"
        if len(current) >= MAX_TRACKED_GROUPS:
            return "full"

        current.append(group)
        conn.execute("DELETE FROM tracked_groups WHERE user_id = ?", (user_id,))
        conn.executemany(
            "INSERT INTO tracked_groups (user_id, group_name, slot) VALUES (?, ?, ?)",
            [(user_id, item, slot) for slot, item in enumerate(current, 1)],
        )
        _sync_legacy_primary(conn, user_id, current)
    return "added"


def remove_tracked_group(db_path: str, user_id: int, slot: int) -> str:
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        role_row = conn.execute(
            "SELECT role FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not role_row:
            return "unregistered"

        role = role_row[0]
        current = [
            row[0]
            for row in conn.execute(
                "SELECT group_name FROM tracked_groups WHERE user_id = ? ORDER BY slot",
                (user_id,),
            ).fetchall()
        ]
        if slot < 1 or slot > len(current):
            return "missing"
        if role == "Я студент" and len(current) == 1:
            return "last_student_group"

        current.pop(slot - 1)
        conn.execute("DELETE FROM tracked_groups WHERE user_id = ?", (user_id,))
        conn.executemany(
            "INSERT INTO tracked_groups (user_id, group_name, slot) VALUES (?, ?, ?)",
            [(user_id, item, new_slot) for new_slot, item in enumerate(current, 1)],
        )
        _sync_legacy_primary(conn, user_id, current)
    return "removed"


def delete_user_groups(db_path: str, user_id: int) -> None:
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute("DELETE FROM tracked_groups WHERE user_id = ?", (user_id,))


def _sync_legacy_primary(
    conn: sqlite3.Connection,
    user_id: int,
    groups: list[str],
) -> None:
    role_row = conn.execute(
        "SELECT role FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not role_row:
        return

    primary = groups[0] if groups else None
    if role_row[0] == "Я студент" and primary:
        conn.execute(
            "UPDATE users SET name_or_group = ? WHERE user_id = ?",
            (primary, user_id),
        )
    elif role_row[0] == "Я преподаватель":
        conn.execute(
            """
            UPDATE users
            SET is_class_teacher = ?, class_group = ?
            WHERE user_id = ?
            """,
            (1 if primary else 0, primary, user_id),
        )
