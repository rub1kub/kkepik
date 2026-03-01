#!/usr/bin/env python3
"""
Скрипт для вывода статистики по базе данных.

Показывает:
- Количество групп по курсам (1, 2, 3, 4)
- Количество преподавателей
- Общее количество пользователей
- Статистику по ролям
- Примеры групп каждого курса
"""

import os
import sys
import sqlite3
from typing import Dict, List, Tuple

# Добавляем корень проекта в PYTHONPATH, чтобы импортировать config
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

import config


def get_course_from_group(group_name: str) -> int:
    """Извлекает номер курса из названия группы."""
    if not isinstance(group_name, str):
        return 0
    
    normalized = group_name.strip().upper()
    parts = normalized.split('-')
    if len(parts) < 3:
        return 0
    
    third = parts[2].strip()
    if len(third) > 0 and third[0].isdigit():
        return int(third[0])
    
    return 0


def get_statistics(conn: sqlite3.Connection) -> Dict:
    """Собирает статистику по базе данных."""
    cur = conn.cursor()
    
    # Общее количество пользователей
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]
    
    # Статистика по ролям
    cur.execute("SELECT role, COUNT(*) FROM users GROUP BY role")
    roles_stats = dict(cur.fetchall())
    
    # Статистика по курсам для студентов
    cur.execute("SELECT name_or_group FROM users WHERE role = 'Я студент'")
    student_groups = [row[0] for row in cur.fetchall()]
    
    course_stats = {1: 0, 2: 0, 3: 0, 4: 0, 0: 0}  # 0 для некорректных групп
    course_examples = {1: [], 2: [], 3: [], 4: [], 0: []}
    
    for group in student_groups:
        course = get_course_from_group(group)
        course_stats[course] += 1
        if len(course_examples[course]) < 5:  # Показываем до 5 примеров
            course_examples[course].append(group)
    
    # Статистика по классным руководителям
    cur.execute("SELECT class_group FROM users WHERE role = 'Я классный руководитель' AND class_group IS NOT NULL")
    class_groups = [row[0] for row in cur.fetchall()]
    
    class_course_stats = {1: 0, 2: 0, 3: 0, 4: 0, 0: 0}
    class_course_examples = {1: [], 2: [], 3: [], 4: [], 0: []}
    
    for group in class_groups:
        course = get_course_from_group(group)
        class_course_stats[course] += 1
        if len(class_course_examples[course]) < 5:
            class_course_examples[course].append(group)
    
    return {
        'total_users': total_users,
        'roles_stats': roles_stats,
        'course_stats': course_stats,
        'course_examples': course_examples,
        'class_course_stats': class_course_stats,
        'class_course_examples': class_course_examples,
        'total_students': course_stats[1] + course_stats[2] + course_stats[3] + course_stats[4],
        'total_teachers': roles_stats.get('Я преподаватель', 0),
        'total_class_managers': roles_stats.get('Я классный руководитель', 0)
    }


def print_statistics(stats: Dict) -> None:
    """Выводит статистику в удобном формате."""
    print("=" * 60)
    print("📊 СТАТИСТИКА БАЗЫ ДАННЫХ")
    print("=" * 60)
    
    # Общая информация
    print(f"\n👥 ОБЩАЯ ИНФОРМАЦИЯ:")
    print(f"   Всего пользователей: {stats['total_users']}")
    print(f"   Студентов: {stats['total_students']}")
    print(f"   Преподавателей: {stats['total_teachers']}")
    print(f"   Классных руководителей: {stats['total_class_managers']}")
    
    # Статистика по ролям
    print(f"\n📋 СТАТИСТИКА ПО РОЛЯМ:")
    for role, count in stats['roles_stats'].items():
        print(f"   {role}: {count}")
    
    # Статистика по курсам студентов
    print(f"\n🎓 СТУДЕНТЫ ПО КУРСАМ:")
    for course in [1, 2, 3, 4]:
        count = stats['course_stats'][course]
        print(f"   {course} курс: {count} студентов")
        if stats['course_examples'][course]:
            examples = ", ".join(stats['course_examples'][course][:3])
            print(f"      Примеры: {examples}")
    
    # Некорректные группы
    if stats['course_stats'][0] > 0:
        print(f"   Некорректные группы: {stats['course_stats'][0]}")
        if stats['course_examples'][0]:
            examples = ", ".join(stats['course_examples'][0][:3])
            print(f"      Примеры: {examples}")
    
    # Статистика по курсам классных руководителей
    print(f"\n👨‍🏫 КЛАССНЫЕ РУКОВОДИТЕЛИ ПО КУРСАМ:")
    for course in [1, 2, 3, 4]:
        count = stats['class_course_stats'][course]
        if count > 0:
            print(f"   {course} курс: {count} классных руководителей")
            if stats['class_course_examples'][course]:
                examples = ", ".join(stats['class_course_examples'][course][:3])
                print(f"      Примеры: {examples}")
    
    # Некорректные группы классных руководителей
    if stats['class_course_stats'][0] > 0:
        print(f"   Некорректные группы: {stats['class_course_stats'][0]}")
        if stats['class_course_examples'][0]:
            examples = ", ".join(stats['class_course_examples'][0][:3])
            print(f"      Примеры: {examples}")
    
    print("\n" + "=" * 60)


def main() -> None:
    """Основная функция."""
    db_path = config.get_db_path()
    print(f"Используется база данных: {db_path}")
    
    try:
        conn = sqlite3.connect(db_path)
        stats = get_statistics(conn)
        print_statistics(stats)
        conn.close()
        
    except Exception as e:
        print(f"Ошибка при работе с базой данных: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
