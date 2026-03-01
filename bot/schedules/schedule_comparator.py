# schedules/schedule_comparator.py

import pandas as pd
import os
from typing import Dict, List, Set, Optional
from schedules import group_schedule, teacher_schedule
import config


def normalize_schedule_line(line: str) -> str:
    """
    Нормализует строку расписания для сравнения.
    Убирает лишние пробелы и приводит к единому формату.
    """
    if not line:
        return ""
    
    # Убираем лишние пробелы и приводим к нижнему регистру для сравнения
    normalized = " ".join(line.split()).strip()
    return normalized


def get_schedule_hash(schedule_lines: List[str]) -> str:
    """
    Создает хеш расписания для быстрого сравнения.
    """
    if not schedule_lines:
        return ""
    
    # Нормализуем все строки и объединяем
    normalized_lines = [normalize_schedule_line(line) for line in schedule_lines]
    return "|".join(normalized_lines)


def compare_group_schedules(old_df: pd.DataFrame, new_df: pd.DataFrame, group_name: str) -> bool:
    """
    Сравнивает расписание группы между старым и новым файлом.
    
    Args:
        old_df: Старый DataFrame с расписанием
        new_df: Новый DataFrame с расписанием
        group_name: Название группы
        
    Returns:
        bool: True если расписание изменилось, False если не изменилось
    """
    try:
        # Получаем старое расписание
        old_schedule = group_schedule.get_schedule_for_group(old_df, group_name.upper())
        if old_schedule is None:
            old_schedule = []
        
        # Получаем новое расписание
        new_schedule = group_schedule.get_schedule_for_group(new_df, group_name.upper())
        if new_schedule is None:
            new_schedule = []
        
        # Сравниваем хеши расписаний
        old_hash = get_schedule_hash(old_schedule)
        new_hash = get_schedule_hash(new_schedule)
        
        return old_hash != new_hash
        
    except Exception as e:
        print(f"Ошибка при сравнении расписания группы {group_name}: {str(e)}")
        # В случае ошибки считаем, что расписание изменилось (безопасная сторона)
        return True


def compare_teacher_schedules(old_df: pd.DataFrame, new_df: pd.DataFrame, teacher_name: str) -> bool:
    """
    Сравнивает расписание преподавателя между старым и новым файлом.
    
    Args:
        old_df: Старый DataFrame с расписанием
        new_df: Новый DataFrame с расписанием
        teacher_name: ФИО преподавателя
        
    Returns:
        bool: True если расписание изменилось, False если не изменилось
    """
    try:
        # Получаем старое расписание
        old_schedule = teacher_schedule.get_schedule_for_teacher(old_df, teacher_name)
        if old_schedule is None:
            old_schedule = []
        
        # Получаем новое расписание
        new_schedule = teacher_schedule.get_schedule_for_teacher(new_df, teacher_name)
        if new_schedule is None:
            new_schedule = []
        
        # Сравниваем хеши расписаний
        old_hash = get_schedule_hash(old_schedule)
        new_hash = get_schedule_hash(new_schedule)
        
        return old_hash != new_hash
        
    except Exception as e:
        print(f"Ошибка при сравнении расписания преподавателя {teacher_name}: {str(e)}")
        # В случае ошибки считаем, что расписание изменилось (безопасная сторона)
        return True


def get_changed_users(old_df: pd.DataFrame, new_df: pd.DataFrame, 
                     schedule_type: str, users_data: List[tuple]) -> Set[int]:
    """
    Определяет список пользователей, у которых изменилось расписание.
    
    Args:
        old_df: Старый DataFrame с расписанием
        new_df: Новый DataFrame с расписанием
        schedule_type: Тип расписания ("groups" или "teachers")
        users_data: Список кортежей с данными пользователей из БД
        
    Returns:
        Set[int]: Множество user_id пользователей с изменившимся расписанием
    """
    changed_users = set()
    
    for user_data in users_data:
        user_id, role, name_or_group, is_class_teacher, class_group = user_data
        
        try:
            if schedule_type == "groups":
                # Проверяем изменения для студентов
                if role == config.ROLE_STUDENT:
                    if compare_group_schedules(old_df, new_df, name_or_group):
                        changed_users.add(user_id)
                
                # Проверяем изменения для классных руководителей
                elif role == config.ROLE_TEACHER and is_class_teacher and class_group:
                    if compare_group_schedules(old_df, new_df, class_group):
                        changed_users.add(user_id)
                        
            elif schedule_type == "teachers":
                # Проверяем изменения для преподавателей
                if role == config.ROLE_TEACHER:
                    if compare_teacher_schedules(old_df, new_df, name_or_group):
                        changed_users.add(user_id)
                        
        except Exception as e:
            print(f"Ошибка при проверке изменений для пользователя {user_id}: {str(e)}")
            # В случае ошибки добавляем пользователя в список изменившихся (безопасная сторона)
            changed_users.add(user_id)
    
    return changed_users


def load_existing_schedule(schedule_date: str, schedule_type: str) -> Optional[pd.DataFrame]:
    """
    Загружает существующий файл расписания, если он есть.
    
    Args:
        schedule_date: Дата расписания в формате dd.mm.yyyy
        schedule_type: Тип расписания ("groups" или "teachers")
        
    Returns:
        pd.DataFrame или None если файл не найден
    """
    try:
        import config
        
        data_dir = config.DATA_DIR
        file_date = schedule_date.replace(".", "_")
        
        # Ищем существующий файл (.xlsx или .pdf)
        for filename in os.listdir(data_dir):
            if filename.startswith("~$"):
                continue

            ext = os.path.splitext(filename)[1].lower()
            if ext not in (".xlsx", ".pdf"):
                continue

            # Проверяем, содержит ли файл нужную дату
            if schedule_date not in filename and file_date not in filename:
                continue

            type_match = (
                (schedule_type == "groups" and "ГРУППЫ" in filename.upper()) or
                (schedule_type == "teachers" and "ПРЕПОДАВАТЕЛИ" in filename.upper())
            )
            if not type_match:
                continue

            file_path = os.path.join(data_dir, filename)

            if ext == ".pdf":
                from schedules.pdf_to_df import pdf_to_dataframe
                return pdf_to_dataframe(file_path)
            else:
                return pd.read_excel(file_path, sheet_name=schedule_date, header=None)

        return None
        
    except Exception as e:
        print(f"Ошибка при загрузке существующего расписания: {str(e)}")
        return None
