#!/usr/bin/env python3
"""
Скрипт повышения курса всех студентов на 1.

Изменяет номер курса в группе:
- 1 курс → 2 курс
- 2 курс → 3 курс  
- 3 курс → 4 курс

Примеры изменений:
- 105-Д9-1ИСП → 105-Д9-2ИСП
- 84-Д9-2КСК → 84-Д9-3КСК
- 66-Д9-3ИНС → 66-Д9-4ИНС
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


def is_group_format(group_name: str) -> bool:
    """Проверяет, является ли строка группой в правильном формате."""
    if not isinstance(group_name, str):
        return False
    normalized = group_name.strip().upper()
    parts = normalized.split('-')
    if len(parts) < 3:
        return False
    
    # Проверяем, что третья часть начинается с цифры 1, 2 или 3
    third = parts[2].strip()
    return len(third) > 0 and third[0] in ['1', '2', '3']


def promote_course(group_name: str) -> str:
    """Повышает курс группы на 1."""
    parts = group_name.split('-')
    if len(parts) < 3:
        return group_name
    
    third = parts[2]
    if len(third) == 0:
        return group_name
    
    # Заменяем первый символ третьей части
    current_course = third[0]
    if current_course == '1':
        new_course = '2'
    elif current_course == '2':
        new_course = '3'
    elif current_course == '3':
        new_course = '4'
    else:
        return group_name  # Не изменяем, если курс не 1, 2 или 3
    
    # Собираем обратно
    new_third = new_course + third[1:]
    parts[2] = new_third
    return '-'.join(parts)


def fetch_students_to_promote(conn: sqlite3.Connection) -> List[Tuple[int, str, str]]:
    """Возвращает список (user_id, old_group, new_group) для студентов, которых нужно повысить."""
    cur = conn.cursor()
    cur.execute("SELECT user_id, name_or_group FROM users WHERE role = ?", ("Я студент",))
    rows = cur.fetchall()
    
    students_to_promote = []
    for user_id, group in rows:
        if group and is_group_format(group):
            new_group = promote_course(group)
            if new_group != group:  # Если группа изменилась
                students_to_promote.append((int(user_id), group, new_group))
    
    return students_to_promote


def update_students(conn: sqlite3.Connection, updates: List[Tuple[int, str]]) -> int:
    """Обновляет группы студентов. Возвращает число обновленных записей."""
    if not updates:
        return 0
    
    cur = conn.cursor()
    cur.executemany(
        "UPDATE users SET name_or_group = ? WHERE user_id = ?",
        [(new_group, user_id) for user_id, new_group in updates]
    )
    conn.commit()
    return cur.rowcount if cur.rowcount is not None else len(updates)


def main() -> None:
    # Работаем с основной базой данных
    db_path = config.get_db_path()
    # db_path = config.TEST_DB_PATH  # раскомментировать для тестовой базы
    
    print(f"Используется база данных: {db_path}")
    conn = sqlite3.connect(db_path)
    
    try:
        students_to_promote = fetch_students_to_promote(conn)
        
        print(f"Найдено студентов для повышения курса: {len(students_to_promote)}")
        
        if not students_to_promote:
            print("Студентов для повышения не найдено.")
            return
        
        # Показываем изменения
        print("\nПланируемые изменения:")
        for user_id, old_group, new_group in students_to_promote:
            print(f"  {old_group} → {new_group}")
        
        # Подтверждение
        confirmation = input(f"\nВы уверены, что хотите повысить курс {len(students_to_promote)} студентам? (да/нет): ")
        if confirmation.lower() not in ['да', 'yes', 'y']:
            print("Операция отменена.")
            return
        
        # Выполняем обновление
        updates = [(user_id, new_group) for user_id, old_group, new_group in students_to_promote]
        updated_count = update_students(conn, updates)
        
        print(f"\n✅ Успешно обновлено записей: {updated_count}")
        
        # Показываем статистику по курсам
        print("\nСтатистика по курсам после обновления:")
        cur = conn.cursor()
        for course in ['1', '2', '3', '4']:
            cur.execute(
                "SELECT COUNT(*) FROM users WHERE role = ? AND name_or_group LIKE ?",
                ("Я студент", f"%-%-{course}%")
            )
            count = cur.fetchone()[0]
            print(f"  {course} курс: {count} студентов")
        
    except Exception as e:
        print(f"❌ Ошибка: {str(e)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
