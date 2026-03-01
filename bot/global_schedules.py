# global_schedules.py
"""
Модуль, где храним последние загруженные расписания (DataFrame)
и даты расписаний для групп и преподавателей.
"""

last_groups_df = None
last_groups_date = None

last_teachers_df = None
last_teachers_date = None


def reload_cache():
    """Загружает последние файлы расписания из DATA_DIR в глобальный кэш."""
    global last_groups_df, last_groups_date, last_teachers_df, last_teachers_date

    import os
    import re

    try:
        from schedules.parser_all import _find_latest_schedule_file, _load_df_from_file
    except ImportError:
        return

    for stype in ("groups", "teachers"):
        fpath = _find_latest_schedule_file(stype)
        if not fpath:
            continue
        df = _load_df_from_file(fpath)
        if df is None:
            continue

        ext = os.path.splitext(fpath)[1].lower()
        if ext == ".pdf":
            from schedules.pdf_to_df import extract_date_from_pdf_content
            date = extract_date_from_pdf_content(fpath)
        else:
            m = re.search(r'(\d{1,2}[._]\d{1,2}[._]\d{4})', fpath)
            date = m.group(1).replace('_', '.') if m else None

        if stype == "groups":
            last_groups_df = df
            last_groups_date = date
        else:
            last_teachers_df = df
            last_teachers_date = date

        print(f"[cache] Загружено {stype} на {date} из {os.path.basename(fpath)}")
