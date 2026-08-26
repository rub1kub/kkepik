#!/usr/bin/env python3
"""Advance student and class-teacher groups to the next academic year."""

import argparse
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = ROOT_DIR / "data" / "database.db"
GROUP_RE = re.compile(r"^(?P<prefix>.+?-[^-]+-)(?P<course>[1-4])(?P<suffix>.*)$", re.IGNORECASE)


@dataclass
class MigrationPlan:
    students_to_delete: list[int]
    students_to_promote: list[tuple[str, int]]
    teachers_to_release: list[int]
    teachers_to_promote: list[tuple[str, int]]
    invalid_student_groups: list[tuple[int, str]]


def next_group(group: str) -> tuple[int | None, str | None]:
    match = GROUP_RE.match((group or "").strip())
    if not match:
        return None, None
    course = int(match.group("course"))
    if course == 4:
        return course, None
    return course, f'{match.group("prefix")}{course + 1}{match.group("suffix")}'


def build_plan(conn: sqlite3.Connection) -> MigrationPlan:
    students_to_delete: list[int] = []
    students_to_promote: list[tuple[str, int]] = []
    invalid_student_groups: list[tuple[int, str]] = []

    students = conn.execute(
        "SELECT user_id, name_or_group FROM users WHERE role = ?",
        ("Я студент",),
    ).fetchall()
    for user_id, group in students:
        course, promoted_group = next_group(group)
        if course is None:
            invalid_student_groups.append((user_id, group))
        elif course == 4:
            students_to_delete.append(user_id)
        else:
            students_to_promote.append((promoted_group, user_id))

    teachers_to_release: list[int] = []
    teachers_to_promote: list[tuple[str, int]] = []
    class_teachers = conn.execute(
        """
        SELECT user_id, class_group
        FROM users
        WHERE is_class_teacher = 1 AND class_group IS NOT NULL
        """
    ).fetchall()
    for user_id, group in class_teachers:
        course, promoted_group = next_group(group)
        if course == 4:
            teachers_to_release.append(user_id)
        elif promoted_group:
            teachers_to_promote.append((promoted_group, user_id))

    return MigrationPlan(
        students_to_delete=students_to_delete,
        students_to_promote=students_to_promote,
        teachers_to_release=teachers_to_release,
        teachers_to_promote=teachers_to_promote,
        invalid_student_groups=invalid_student_groups,
    )


def print_plan(plan: MigrationPlan) -> None:
    print(f"Студентов 4 курса к удалению: {len(plan.students_to_delete)}")
    print(f"Студентов к переводу: {len(plan.students_to_promote)}")
    print(f"Кураторов выпускных групп к снятию: {len(plan.teachers_to_release)}")
    print(f"Кураторских групп к переводу: {len(plan.teachers_to_promote)}")
    print(f"Некорректных студенческих групп: {len(plan.invalid_student_groups)}")
    for user_id, group in plan.invalid_student_groups:
        print(f"  без изменения: user_id={user_id}, group={group!r}")


def apply_plan(conn: sqlite3.Connection, plan: MigrationPlan) -> None:
    with conn:
        conn.executemany(
            "DELETE FROM users WHERE user_id = ?",
            [(user_id,) for user_id in plan.students_to_delete],
        )
        conn.executemany(
            "UPDATE users SET name_or_group = ? WHERE user_id = ?",
            plan.students_to_promote,
        )
        conn.executemany(
            "UPDATE users SET is_class_teacher = 0, class_group = NULL WHERE user_id = ?",
            [(user_id,) for user_id in plan.teachers_to_release],
        )
        conn.executemany(
            "UPDATE users SET class_group = ? WHERE user_id = ?",
            plan.teachers_to_promote,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="apply changes; default is dry-run")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    try:
        plan = build_plan(conn)
        print_plan(plan)
        if not args.apply:
            print("Dry-run: база не изменена.")
            return
        apply_plan(conn, plan)
        print("Переход учебного года выполнен.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
