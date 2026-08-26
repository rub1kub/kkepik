# global_schedules.py
"""
Модуль, где храним последние загруженные расписания (DataFrame)
и даты расписаний для групп и преподавателей.
"""

last_groups_df = None
last_groups_date = None
last_groups_crop_cache: dict[str, bytes] = {}

last_teachers_df = None
last_teachers_date = None


def reload_cache(*, today=None):
    """Загружает последние файлы расписания из DATA_DIR в глобальный кэш."""
    global last_groups_df, last_groups_date, last_groups_crop_cache
    global last_teachers_df, last_teachers_date

    import os
    import re

    try:
        from schedules.parser_all import _find_latest_schedule_file, _load_df_from_file
        from schedules.schedule_dates import is_current_schedule
    except ImportError:
        return

    # A process restart must not revive the previous academic year's schedule.
    last_groups_df = None
    last_groups_date = None
    last_groups_crop_cache = {}
    last_teachers_df = None
    last_teachers_date = None

    for stype in ("groups", "teachers"):
        fpath = _find_latest_schedule_file(stype)
        if not fpath:
            continue

        ext = os.path.splitext(fpath)[1].lower()
        if ext == ".pdf":
            from schedules.pdf_to_df import extract_date_from_pdf_content
            date = extract_date_from_pdf_content(fpath)
        else:
            m = re.search(r'(\d{1,2}[._]\d{1,2}[._]\d{4})', fpath)
            date = m.group(1).replace('_', '.') if m else None

        if not is_current_schedule(date, today=today):
            print(f"[cache] Пропущено устаревшее расписание {stype} на {date}")
            continue

        df = _load_df_from_file(fpath)
        if df is None:
            continue

        if stype == "groups":
            last_groups_df = df
            last_groups_date = date
            # Генерируем кропы из PDF
            if ext == ".pdf":
                try:
                    from schedules.pdf_crop import crop_group_screenshots
                    last_groups_crop_cache = crop_group_screenshots(fpath)
                    print(f"[cache] PDF кропы: {len(last_groups_crop_cache)} групп")
                except Exception as e:
                    print(f"[cache] PDF crop failed: {e}")
                    last_groups_crop_cache = {}
            else:
                last_groups_crop_cache = {}
        else:
            last_teachers_df = df
            last_teachers_date = date

        try:
            from schedules.schedule_catalog import update_schedule_catalog

            update_schedule_catalog(
                df,
                date,
                stype,
                source_name=os.path.basename(fpath),
                today=today,
            )
        except Exception as e:
            print(f"[catalog] Не удалось обновить каталог: {e}")

        print(f"[cache] Загружено {stype} на {date} из {os.path.basename(fpath)}")
