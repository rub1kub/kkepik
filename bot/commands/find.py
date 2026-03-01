# commands/find.py
from aiogram import types
from schedules import group_schedule, teacher_schedule
from schedules.schedule_mood import get_mood_emoji
from schedules.pair_times import add_pair_times, is_saturday
import global_schedules
import config
import os
import pandas as pd
import re


def find_schedule_file(date: str, schedule_type: str) -> str | None:
    """Ищет файл расписания по дате и типу (.xlsx или .pdf)."""
    data_dir = config.DATA_DIR
    if not os.path.exists(data_dir):
        return None

    date_pattern = date.replace(".", "_")
    type_kw = "ГРУППЫ" if schedule_type == "groups" else "ПРЕПОДАВАТЕЛИ"

    for ext_pref in (".xlsx", ".pdf"):
        for filename in os.listdir(data_dir):
            if filename.startswith("~$") or filename == ".DS_Store":
                continue
            if os.path.splitext(filename)[1].lower() != ext_pref:
                continue
            if type_kw not in filename.upper():
                continue
            if date in filename or date_pattern in filename:
                return os.path.join(data_dir, filename)

    return None


def _load_df(file_path: str, date: str) -> pd.DataFrame | None:
    """Читает DataFrame из .xlsx или .pdf файла расписания."""
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".pdf":
            from schedules.pdf_to_df import pdf_to_dataframe
            return pdf_to_dataframe(file_path)
        else:
            df = pd.read_excel(file_path, sheet_name=0, header=None)
            if df is not None and 0 in df.columns:
                df[0] = df[0].ffill()
            return df
    except Exception:
        # Для XLSX пробуем лист с датой как именем
        if ext != ".pdf":
            try:
                df = pd.read_excel(file_path, sheet_name=date, header=None)
                if df is not None and 0 in df.columns:
                    df[0] = df[0].ffill()
                return df
            except Exception:
                pass
    return None


def is_teacher_name(name: str) -> bool:
    pattern = r'^[А-ЯЁ][а-яё]+\s+([А-ЯЁ]\.[А-ЯЁ]\.|[А-ЯЁ][А-ЯЁ]|[А-ЯЁ]\s*[А-ЯЁ])$'
    print(f"Проверяем ФИО: '{name}'")
    return bool(re.match(pattern, name))


def is_group_name(name: str) -> bool:
    pattern = r'^\d{2,3}-(?:К?Д9)-\d[А-Я]{2,}$'
    name = name.upper()
    print(f"Проверяем группу: '{name}'")
    return bool(re.match(pattern, name))


async def cmd_find(message: types.Message):
    """
    Обработчик команды /find для поиска расписания по номеру группы или ФИО преподавателя.
    """
    parts = message.text.split()

    print(f"Получена команда: {message.text}")
    print(f"Разобранные части: {parts}")

    if len(parts) < 2:
        await message.answer(
            "⚠️ Использование команды:\n"
            "- Для групп: /find 103-Д9-1ИНС 17.03.2025\n"
            "- Для преподавателей: /find Ермолов И.А. 17.03.2025\n\n"
            "💡 Воспользуйтесь приложением для удобного поиска расписания: /app."
        )
        return

    first_arg = parts[1].strip()
    if is_group_name(first_arg):
        search_name = first_arg
        search_date = parts[2] if len(parts) > 2 else None
    else:
        if len(parts) < 3:
            await message.answer(
                "⚠️ Для поиска по преподавателю используйте формат:\n"
                "/find Фамилия И.О. [Дата]\n\n"
                "💡 Воспользуйтесь приложением для удобного поиска расписания: /app."
            )
            return

        if len(parts) >= 4 and not re.match(r'\d{2}\.\d{2}\.\d{4}', parts[3]):
            search_name = f"{parts[1]} {parts[2]} {parts[3]}"
            search_date = parts[4] if len(parts) > 4 else None
        else:
            search_name = f"{parts[1]} {parts[2]}"
            search_date = parts[3] if len(parts) > 3 else None

        if not is_teacher_name(search_name):
            await message.answer(
                "⚠️ Неверный формат ФИО преподавателя. Используйте формат:\n"
                "/find Фамилия И.О. [Дата]\n\n"
                "💡 Воспользуйтесь приложением для удобного поиска расписания: /app."
            )
            return

    print(f"Поисковое имя: '{search_name}'")
    print(f"Дата: '{search_date}'")

    # Если дата не указана — берём из кэша
    if not search_date:
        if global_schedules.last_groups_df is not None:
            search_date = global_schedules.last_groups_date
        elif global_schedules.last_teachers_df is not None:
            search_date = global_schedules.last_teachers_date
        else:
            await message.answer("⚠️ Нет загруженных расписаний.")
            return

    if is_group_name(search_name):
        schedule_type = "groups"
        search_name = search_name.upper()
    else:
        schedule_type = "teachers"
        if '.' not in search_name:
            p = search_name.split()
            if len(p) == 3:
                search_name = f"{p[0]} {p[1]}.{p[2]}."
            elif len(p) == 2 and len(p[1]) == 2:
                search_name = f"{p[0]} {p[1][0]}.{p[1][1]}."

    print(f"Тип расписания: {schedule_type}")
    print(f"Обработанное имя: '{search_name}'")

    # Ищем файл
    schedule_file = find_schedule_file(search_date, schedule_type)

    # Для преподавателей — fallback на файл групп
    if not schedule_file and schedule_type == "teachers":
        schedule_file = find_schedule_file(search_date, "groups")

    if not schedule_file:
        await message.answer(f"⚠️ Расписание на {search_date} не найдено.")
        return

    df = _load_df(schedule_file, search_date)
    if df is None:
        await message.answer(f"⚠️ Не удалось прочитать файл расписания.")
        return

    try:
        if schedule_type == "groups":
            lines = group_schedule.get_schedule_for_group(df, search_name)
            if lines:
                lines_timed = add_pair_times(lines, search_date)
                display = lines_timed if is_saturday(search_date) else lines
                msg_text = f"{get_mood_emoji(lines)} Расписание группы {search_name} на {search_date}:\n\n" + "\n".join(display)
                await message.answer(
                    msg_text + "\n\n💡 Воспользуйтесь приложением для удобного поиска расписания: /app.",
                    parse_mode="HTML"
                )
            else:
                await message.answer(f"❌ Не найдено расписание для группы {search_name} на {search_date}.")
        else:
            lines = teacher_schedule.get_schedule_for_teacher(df, search_name)
            if lines:
                msg_text = f"📆 Расписание преподавателя {search_name} на {search_date}:\n\n" + "\n".join(lines)
                await message.answer(
                    msg_text + "\n\n💡 Воспользуйтесь приложением для удобного поиска расписания: /app.",
                    parse_mode="HTML"
                )
            else:
                await message.answer(f"❌ Не найдено расписание для преподавателя {search_name} на {search_date}.")

    except Exception as e:
        await message.answer(f"⚠️ Ошибка при чтении расписания: {str(e)}")
