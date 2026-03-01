#!/usr/bin/env python3
"""
Скрипт для:
1. Повышения курса в поле class_group на 1
2. Удаления всех студентов 4 курса

Изменения в class_group:
- 1 курс → 2 курс
- 2 курс → 3 курс  
- 3 курс → 4 курс (затем удаляется)

Примеры изменений:
- 103-Д9-1ИНС → 103-Д9-2ИНС
- 84-Д9-2КСК → 84-Д9-3КСК
- 66-Д9-3ИНС → 66-Д9-4ИНС (затем удаляется)
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


def is_fourth_course_group(group_name: str) -> bool:
    """Возвращает True, если группа 4 курса."""
    if not isinstance(group_name, str):
        return False
    normalized = group_name.strip().upper()
    parts = normalized.split('-')
    if len(parts) < 3:
        return False
    third = parts[2].strip()
    return len(third) > 0 and third[0] == '4'


def fetch_class_teachers_to_promote(conn: sqlite3.Connection) -> List[Tuple[int, str, str]]:
    """Возвращает список (user_id, old_class_group, new_class_group) для классных руководителей."""
    cur = conn.cursor()
    cur.execute("SELECT user_id, class_group FROM users WHERE is_class_teacher = 1 AND class_group IS NOT NULL")
    rows = cur.fetchall()
    
    teachers_to_promote = []
    for user_id, class_group in rows:
        if class_group and is_group_format(class_group):
            new_class_group = promote_course(class_group)
            if new_class_group != class_group:  # Если группа изменилась
                teachers_to_promote.append((int(user_id), class_group, new_class_group))
    
    return teachers_to_promote


def fetch_students_to_delete(conn: sqlite3.Connection) -> List[int]:
    """Возвращает список user_id студентов 4 курса для удаления."""
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE role = ?", ("Я студент",))
    rows = cur.fetchall()
    
    students_to_delete = []
    for (user_id,) in rows:
        cur.execute("SELECT name_or_group FROM users WHERE user_id = ?", (user_id,))
        group = cur.fetchone()[0]
        if group and is_fourth_course_group(group):
            students_to_delete.append(int(user_id))
    
    return students_to_delete


def update_class_groups(conn: sqlite3.Connection, updates: List[Tuple[int, str]]) -> int:
    """Обновляет class_group для классных руководителей. Возвращает число обновленных записей."""
    if not updates:
        return 0
    
    cur = conn.cursor()
    cur.executemany(
        "UPDATE users SET class_group = ? WHERE user_id = ?",
        [(new_group, user_id) for user_id, new_group in updates]
    )
    conn.commit()
    return cur.rowcount if cur.rowcount is not None else len(updates)


def delete_students(conn: sqlite3.Connection, user_ids: List[int]) -> int:
    """Удаляет студентов по списку ID. Возвращает число удалённых записей."""
    if not user_ids:
        return 0
    cur = conn.cursor()
    cur.executemany("DELETE FROM users WHERE user_id = ?", [(uid,) for uid in user_ids])
    conn.commit()
    return cur.rowcount if cur.rowcount is not None else len(user_ids)


def main() -> None:
    # Работаем с основной базой данных
    db_path = config.get_db_path()
    # db_path = config.TEST_DB_PATH  # раскомментировать для тестовой базы
    
    print(f"Используется база данных: {db_path}")
    conn = sqlite3.connect(db_path)
    
    try:
        # 1. Повышаем курс в class_group для классных руководителей
        print("\n=== ШАГ 1: Повышение курса в class_group ===")
        teachers_to_promote = fetch_class_teachers_to_promote(conn)
        
        print(f"Найдено классных руководителей для повышения курса: {len(teachers_to_promote)}")
        
        if teachers_to_promote:
            # Показываем изменения
            print("\nПланируемые изменения в class_group:")
            for user_id, old_group, new_group in teachers_to_promote:
                print(f"  {old_group} → {new_group}")
            
            # Выполняем обновление
            updates = [(user_id, new_group) for user_id, old_group, new_group in teachers_to_promote]
            updated_count = update_class_groups(conn, updates)
            print(f"\n✅ Обновлено class_group для {updated_count} классных руководителей")
        else:
            print("Классных руководителей для повышения не найдено.")
        
        # 2. Пропускаем удаление студентов 4 курса (уже выполнено ранее)
        print("\n=== ШАГ 2: Удаление студентов 4 курса ===")
        print("Пропускаем - студенты 4 курса уже удалены ранее")
        
        # Показываем итоговую статистику
        print("\n=== ИТОГОВАЯ СТАТИСТИКА ===")
        cur = conn.cursor()
        
        # Статистика по курсам студентов
        print("\nСтуденты по курсам:")
        for course in ['1', '2', '3', '4']:
            cur.execute(
                "SELECT COUNT(*) FROM users WHERE role = ? AND name_or_group LIKE ?",
                ("Я студент", f"%-%-{course}%")
            )
            count = cur.fetchone()[0]
            print(f"  {course} курс: {count} студентов")
        
        # Статистика по class_group
        print("\nКлассные руководители по курсам class_group:")
        for course in ['1', '2', '3', '4']:
            cur.execute(
                "SELECT COUNT(*) FROM users WHERE is_class_teacher = 1 AND class_group LIKE ?",
                (f"%-%-{course}%",)
            )
            count = cur.fetchone()[0]
            print(f"  {course} курс: {count} классных руководителей")
        
    except Exception as e:
        print(f"❌ Ошибка: {str(e)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
