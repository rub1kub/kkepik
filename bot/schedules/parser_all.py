import os
import sys

# Добавляем корневую директорию проекта в PYTHONPATH
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(root_dir)

import pandas as pd
import re
from typing import Optional
from schedules.group_schedule import is_group_name
from schedules.teacher_schedule import is_likely_teacher_name, normalize_teacher_name


# ──────────────────────────────────────────────────────────────────────────────
# Вспомогательные функции
# ──────────────────────────────────────────────────────────────────────────────

def _load_df_from_file(file_path: str) -> Optional[pd.DataFrame]:
    """
    Читает DataFrame из .xlsx или .pdf файла.
    Для XLSX читает первый лист (header=None).
    Для PDF использует pdf_to_dataframe().
    """
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".pdf":
            from schedules.pdf_to_df import pdf_to_dataframe
            return pdf_to_dataframe(file_path)
        else:
            return pd.read_excel(file_path, header=None)
    except Exception as e:
        print(f"[parser_all] Ошибка при чтении {file_path}: {e}")
        return None


def _find_latest_schedule_file(schedule_type: str) -> Optional[str]:
    """
    Находит самый свежий файл расписания (.xlsx или .pdf) в DATA_DIR.
    schedule_type: "groups" → ищет файлы с «ГРУППЫ»
                   "teachers" → ищет файлы с «ПРЕПОДАВАТЕЛИ»
    Возвращает полный путь к файлу или None.
    """
    try:
        import config
        data_dir = config.DATA_DIR
    except Exception:
        return None

    type_kw = "ГРУППЫ" if schedule_type == "groups" else "ПРЕПОДАВАТЕЛИ"
    best_path  = None
    best_mtime = 0.0

    for filename in os.listdir(data_dir):
        if filename.startswith("~$"):
            continue
        ext = os.path.splitext(filename)[1].lower()
        if ext not in (".xlsx", ".pdf"):
            continue
        if type_kw not in filename.upper():
            continue

        full_path = os.path.join(data_dir, filename)
        mtime = os.path.getmtime(full_path)
        if mtime > best_mtime:
            best_mtime = mtime
            best_path  = full_path

    return best_path


def _resolve_df(
    df: Optional[pd.DataFrame],
    schedule_type: str,
) -> Optional[pd.DataFrame]:
    """
    Возвращает DataFrame для работы в следующем порядке приоритетов:
    1. Переданный аргумент df (если не None)
    2. Кэш из global_schedules (если подходящий)
    3. Самый свежий файл в DATA_DIR
    """
    if df is not None:
        return df

    # 2. Кэш из global_schedules
    try:
        import global_schedules
        cached = (
            global_schedules.last_groups_df
            if schedule_type == "groups"
            else global_schedules.last_teachers_df
        )
        if cached is not None:
            return cached
    except Exception:
        pass

    # 3. Файл в DATA_DIR
    file_path = _find_latest_schedule_file(schedule_type)
    if file_path:
        return _load_df_from_file(file_path)

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Публичные функции
# ──────────────────────────────────────────────────────────────────────────────

def get_all_groups(df: Optional[pd.DataFrame] = None) -> list:
    """
    Возвращает отсортированный список всех групп из расписания.

    Аргументы:
        df  — готовый DataFrame (например, уже загруженный).
              Если None — функция сама найдёт последний файл ГРУППЫ в DATA_DIR.
    """
    df = _resolve_df(df, "groups")
    if df is None:
        print("[parser_all] Файл расписания групп не найден")
        return []

    groups = set()
    for row in range(df.shape[0]):
        for col in range(df.shape[1]):
            val = df.iat[row, col]
            if val is None:
                continue
            try:
                if pd.isna(val):
                    continue
            except Exception:
                pass
            val_str = str(val).strip()
            if is_group_name(val_str):
                groups.add(val_str.upper())

    return sorted(groups)


def _get_teachers_from_groups_df(df: pd.DataFrame) -> list:
    """
    Извлекает список преподавателей из PDF расписания групп.
    Teacher rows: колонки 2,6,10,14 (sg1) и 4,8,12,16 (sg2).
    """
    _PDF_TEACHER_COLS = [2, 4, 6, 8, 10, 12, 14, 16]
    teachers = set()
    for row in range(df.shape[0]):
        for col in _PDF_TEACHER_COLS:
            if col >= df.shape[1]:
                continue
            val = df.iat[row, col]
            if isinstance(val, str) and is_likely_teacher_name(val):
                teachers.add(val.strip())
    result = sorted(teachers)
    print(f"[parser_all] Найдено преподавателей в расписании групп: {len(result)}")
    return result


def get_all_teachers(df: Optional[pd.DataFrame] = None) -> list:
    """
    Возвращает отсортированный список всех преподавателей из расписания.

    Аргументы:
        df  — готовый DataFrame.
              Если None — функция сама найдёт последний файл ПРЕПОДАВАТЕЛИ в DATA_DIR.
              Если файл ПРЕПОДАВАТЕЛИ не найден — ищет в файле ГРУППЫ.
    """
    df = _resolve_df(df, "teachers")
    if df is None:
        # Fallback: извлекаем преподавателей из расписания групп
        groups_df = _resolve_df(None, "groups")
        if groups_df is not None:
            return _get_teachers_from_groups_df(groups_df)
        print("[parser_all] Файл расписания преподавателей не найден")
        return []

    teachers = set()
    for col in df.columns:
        for cell in df[col]:
            if not isinstance(cell, str):
                continue
            if is_likely_teacher_name(cell):
                teachers.add(cell.strip())

    teachers_list = sorted(teachers)
    print(f"[parser_all] Найдено преподавателей: {len(teachers_list)}")
    return teachers_list


def get_groups_by_course(groups: list) -> dict:
    """
    Группирует список групп по курсам (1–4).

    Returns:
        dict[int, list[str]]
    """
    groups_by_course: dict = {1: [], 2: [], 3: [], 4: []}
    for group in groups:
        try:
            course = int(group.split("-")[2][0])
            if course in groups_by_course:
                groups_by_course[course].append(group)
        except (IndexError, ValueError):
            pass
    return groups_by_course


def get_teachers_by_department(teachers: list) -> dict:
    """
    Группирует преподавателей по первой букве фамилии.

    Returns:
        dict[str, list[str]]
    """
    result: dict = {}
    for teacher in teachers:
        if not teacher:
            continue
        letter = teacher[0].upper()
        result.setdefault(letter, []).append(teacher)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    groups   = get_all_groups()
    teachers = get_all_teachers()

    print("=== Список всех групп ===")
    for g in groups:
        print(g)

    print("\n=== Список всех преподавателей ===")
    for t in teachers:
        print(t)

    print("\n=== Группы по курсам ===")
    for course, cg in get_groups_by_course(groups).items():
        print(f"\nКурс {course}:")
        for g in cg:
            print(f"  {g}")

    print("\n=== Преподаватели по первой букве фамилии ===")
    for letter, lt in sorted(get_teachers_by_department(teachers).items()):
        print(f"\n{letter}:")
        for t in lt:
            print(f"  {t}")
