#!/usr/bin/env python3
"""
Скрипт удаления всех студентов 4 курса из базы данных.

Критерий 4 курса: номер группы имеет формат ЧТО-ТО-4***, то есть
третья часть (после второго дефиса '-') начинается с '4'.

Примеры подходящих групп:
- 103-Д9-4ИНС
- 089-КД9-4ИСП
"""

import os
import sys
import sqlite3
from typing import List, Tuple

# Добавляем корень проекта в PYTHONPATH, чтобы импортировать config
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

import config


def is_fourth_course_group(group_name: str) -> bool:
    """Возвращает True, если группа 4 курса.
    
    Формат группы: ЧТО-ТО-КУРС+СПЕЦИАЛЬНОСТЬ
    Примеры 4 курса: 57-КД9-4РАС, 54-КД9-4ИСП, 53-КД9-4ИНС
    """
    if not isinstance(group_name, str):
        return False
    normalized = group_name.strip().upper()
    parts = normalized.split('-')
    if len(parts) < 3:
        return False
    third = parts[2].strip()
    return len(third) > 0 and third[0] == '4'


def fetch_all_users(conn: sqlite3.Connection) -> List[Tuple[int, str]]:
    """Возвращает список (user_id, name_or_group) для всех записей в users."""
    cur = conn.cursor()
    cur.execute("SELECT user_id, name_or_group FROM users")
    rows = [(int(row[0]), row[1] or "") for row in cur.fetchall()]
    return rows


def delete_users(conn: sqlite3.Connection, user_ids: List[int]) -> int:
    """Удаляет пользователей по списку ID. Возвращает число удалённых записей."""
    if not user_ids:
        return 0
    cur = conn.cursor()
    # Удаляем по одному, чтобы не собирать динамический IN.
    cur.executemany("DELETE FROM users WHERE user_id = ?", [(uid,) for uid in user_ids])
    conn.commit()
    return cur.rowcount if cur.rowcount is not None else len(user_ids)


def main() -> None:
    # Переключаемся на тестовую базу, где есть записи 4 курса
    db_path = config.TEST_DB_PATH  # для тестовой базы
    # db_path = config.get_db_path()   # для продакшн базы
    
    print(f"Используется база данных: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        rows = fetch_all_users(conn)
        
        # Найдем группы 4 курса и выведем их для проверки
        fourth_course_groups = []
        to_delete = []
        for user_id, group in rows:
            if is_fourth_course_group(group):
                fourth_course_groups.append(group)
                to_delete.append(user_id)

        print(f"Найдено записей всего: {len(rows)}")
        print(f"Из них групп 4 курса к удалению: {len(to_delete)}")
        
        if fourth_course_groups:
            print("Найденные группы 4 курса:")
            for group in sorted(set(fourth_course_groups)):
                print(f"  - {group}")

        if not to_delete:
            print("Удалять нечего.")
            return

        deleted = delete_users(conn, to_delete)
        print(f"Удалено записей: {deleted}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()


