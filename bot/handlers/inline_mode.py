# handlers/inline_mode.py
import re
import sqlite3
import os
import pandas as pd
from aiogram import types
from aiogram.types import InlineQueryResultArticle, InputTextMessageContent
import config
from schedules import group_schedule, teacher_schedule, parser_all

import datetime
from difflib import get_close_matches
# Убираем импорт global_schedules, так как будем загружать файлы по дате
# import global_schedules

def parse_date_from_query(query_text: str) -> tuple[str, datetime.date | None]:
    """
    Пытается извлечь дату из запроса. Возвращает текст без даты и саму дату (или None).
    Форматы дат: дд.мм.гггг, дд/мм/гггг, дд_мм_гггг
    """
    date_pattern = r'\b(\d{1,2}[./_]\d{1,2}[./_]\d{4})\b'
    match = re.search(date_pattern, query_text)
    if match:
        date_str = match.group(1).replace('_', '.').replace('/', '.')
        try:
            date_obj = datetime.datetime.strptime(date_str, '%d.%m.%Y').date()
            text_without_date = query_text.replace(match.group(0), '').strip()
            return text_without_date, date_obj
        except ValueError:
            pass
    return query_text.strip(), None

def get_available_schedule_dates() -> list[datetime.date]:
    """
    Сканирует директорию data и возвращает список уникальных дат из названий файлов.
    """
    dates = set()
    date_pattern = r'(\d{1,2}[._]\d{1,2}[._]\d{4})'
    
    try:
        # Проверяем, существует ли директория
        if not os.path.exists(config.DATA_DIR):
            return []
            
        files_in_dir = os.listdir(config.DATA_DIR)
        print(f"Files in DATA_DIR: {files_in_dir}")  # Логирование
        
        if not files_in_dir:
            return []
            
        for filename in files_in_dir:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in (".xlsx", ".pdf"):
                continue
            match = re.search(date_pattern, filename)
            if match:
                date_str_raw = match.group(1)
                date_str = date_str_raw.replace('_', '.').replace('/', '.')
                try:
                    date_obj = datetime.datetime.strptime(date_str, '%d.%m.%Y').date()
                    dates.add(date_obj)
                except ValueError:
                    continue
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"Error scanning data directory: {e}")
        return []
    
    sorted_dates = sorted(list(dates), reverse=True)
    return sorted_dates

def load_schedule_file(target_date: datetime.date, schedule_type: str) -> tuple[pd.DataFrame | None, str | None]:
    """
    Загружает DataFrame из файла расписания для конкретной даты и типа.
    Поддерживает .xlsx и .pdf.
    """
    date_str = target_date.strftime('%d.%m.%Y')
    date_str_underscore = target_date.strftime('%d_%m_%Y')
    type_str = "ГРУППЫ" if schedule_type == "groups" else "ПРЕПОДАВАТЕЛИ"

    if not os.path.exists(config.DATA_DIR):
        return None, None

    # Сканируем директорию — ищем совпадение по дате и типу
    for ext_pref in (".xlsx", ".pdf"):
        for filename in os.listdir(config.DATA_DIR):
            if filename.startswith("~$"):
                continue
            if os.path.splitext(filename)[1].lower() != ext_pref:
                continue
            if type_str not in filename.upper():
                continue
            if date_str not in filename and date_str_underscore not in filename:
                continue

            file_path = os.path.join(config.DATA_DIR, filename)
            try:
                if ext_pref == ".pdf":
                    from schedules.pdf_to_df import pdf_to_dataframe
                    df_temp = pdf_to_dataframe(file_path)
                    if df_temp is not None and not df_temp.empty:
                        return df_temp, date_str
                else:
                    df_temp = pd.read_excel(file_path, sheet_name=0, header=None)
                    if df_temp is not None and not df_temp.empty and 0 in df_temp.columns:
                        df_temp[0] = df_temp[0].ffill()
                        return df_temp, date_str
            except Exception:
                continue

    return None, None


def create_inline_result(result_id: str, title: str, description: str, message_text: str, parse_mode: str = "HTML") -> InlineQueryResultArticle:
    """Создает объект InlineQueryResultArticle."""
    return InlineQueryResultArticle(
        id=result_id,
        title=title,
        description=description,
        input_message_content=InputTextMessageContent(message_text=message_text, parse_mode=parse_mode)
    )

async def inline_schedule(inline_query: types.InlineQuery):
    query_text_raw = inline_query.query.strip()
    user_id = inline_query.from_user.id

    # Сначала извлекаем дату из запроса
    date_pattern = r'\b(\d{1,2}\.\d{1,2}\.\d{4})\b'
    date_match = re.search(date_pattern, query_text_raw)
    target_date = None
    try:
        if date_match:
            target_date = datetime.datetime.strptime(date_match.group(1), '%d.%m.%Y').date()
            query_text = query_text_raw.replace(date_match.group(1), '').strip()
        else:
            query_text = query_text_raw
    except ValueError:
        query_text = query_text_raw

    # Определяем имя/группу для поиска
    typed_name = ""
    user_role = None
    if not query_text:
        try:
            conn = sqlite3.connect(config.DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT role, name_or_group FROM users WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
            conn.close()
            if row:
                user_role, typed_name = row
                typed_name = (typed_name or "").strip()
            else:
                return await inline_query.answer(
                    results=[],
                    switch_pm_text="Введите группу/ФИО или зарегистрируйтесь (/start)",
                    switch_pm_parameter="inline_usage",
                    cache_time=1
                )
        except Exception:
            return await inline_query.answer(results=[], cache_time=1)
    else:
        typed_name = query_text

    results = []
    available_dates = get_available_schedule_dates()

    if not available_dates:
        results.append(create_inline_result("no_files", "⚠️ Нет загруженных расписаний", "", "Администратор ещё не загрузил файлы расписаний!"))
        return await inline_query.answer(results, cache_time=1)

    # Если запрос начинается с цифры, ищем группы
    if query_text and query_text[0].isdigit():
        try:
            all_groups = parser_all.get_all_groups()
            # Фильтруем группы, начинающиеся с введенного числа
            matching_groups = [group for group in all_groups if group.startswith(query_text)]
            # Если точных совпадений нет, ищем похожие
            if not matching_groups:
                matching_groups = get_close_matches(query_text, all_groups, n=5, cutoff=0.3)
            
            if matching_groups:
                # Проверяем, есть ли полное совпадение
                exact_match = any(group == query_text for group in matching_groups)
                
                if exact_match:
                    # Если есть полное совпадение, показываем расписания
                    group = query_text
                    # Если указана конкретная дата, используем только её
                    if target_date:
                        date_to_use = target_date
                        df_g, date_g = load_schedule_file(date_to_use, "groups")
                        if df_g is not None:
                            lines = group_schedule.get_schedule_for_group(df_g, group)
                            if lines and lines != ["❌ Группа не найдена в расписании"]:
                                schedule_str = "\n".join(lines)
                                msg_text = f"<b>📚 Расписание группы {group} на {date_g}</b>\n\n{schedule_str}"
                                results.append(create_inline_result(
                                    result_id=f"group_{group}_{date_g}",
                                    title=f"Группа {group} на {date_g}",
                                    description="Нажмите, чтобы отправить расписание",
                                    message_text=msg_text
                                ))
                    else:
                        # Берем последние 10 дат
                        for date_to_use in available_dates[:10]:
                            df_g, date_g = load_schedule_file(date_to_use, "groups")
                            if df_g is not None:
                                lines = group_schedule.get_schedule_for_group(df_g, group)
                                if lines and lines != ["❌ Группа не найдена в расписании"]:
                                    schedule_str = "\n".join(lines)
                                    msg_text = f"<b>📚 Расписание группы {group} на {date_g}</b>\n\n{schedule_str}"
                                    results.append(create_inline_result(
                                        result_id=f"group_{group}_{date_g}",
                                        title=f"Группа {group} на {date_g}",
                                        description="Нажмите, чтобы отправить расписание",
                                        message_text=msg_text
                                    ))
                else:
                    # Если нет полного совпадения, показываем список групп с расписанием на ближайший день
                    for group in matching_groups:
                        # Ищем расписание на ближайший день
                        for date_to_use in available_dates:
                            df_g, date_g = load_schedule_file(date_to_use, "groups")
                            if df_g is not None:
                                lines = group_schedule.get_schedule_for_group(df_g, group)
                                if lines and lines != ["❌ Группа не найдена в расписании"]:
                                    schedule_str = "\n".join(lines)
                                    msg_text = f"<b>📚 Расписание группы {group} на {date_g}</b>\n\n{schedule_str}"
                                    results.append(create_inline_result(
                                        result_id=f"group_{group}_{date_g}",
                                        title=f"Группа {group} на {date_g}",
                                        description="Нажмите, чтобы отправить расписание",
                                        message_text=msg_text
                                    ))
                                    break  # Нашли расписание на ближайший день, переходим к следующей группе
                if results:
                    return await inline_query.answer(results, cache_time=5)
        except Exception as e:
            print(f"Ошибка при поиске групп: {str(e)}")

    # Если запрос начинается с буквы, ищем преподавателей
    elif query_text and query_text[0].isalpha():
        try:
            all_teachers = parser_all.get_all_teachers()
            # Фильтруем преподавателей, начинающихся с введенного текста
            matching_teachers = [teacher for teacher in all_teachers if teacher.lower().startswith(query_text.lower())]
            # Если точных совпадений нет, ищем похожие
            if not matching_teachers:
                matching_teachers = get_close_matches(query_text, all_teachers, n=5, cutoff=0.3)
            
            if matching_teachers:
                # Проверяем, есть ли полное совпадение
                exact_match = any(teacher.lower() == query_text.lower() for teacher in matching_teachers)
                
                if exact_match:
                    # Если есть полное совпадение, показываем расписания
                    teacher = next(t for t in matching_teachers if t.lower() == query_text.lower())
                    # Если указана конкретная дата, используем только её
                    if target_date:
                        date_to_use = target_date
                        df_t, date_t = load_schedule_file(date_to_use, "teachers")
                        if df_t is None:
                            df_t, date_t = load_schedule_file(date_to_use, "groups")
                        if df_t is not None:
                            lines = teacher_schedule.get_schedule_for_teacher(df_t, teacher)
                            if lines and lines != ["❌ Преподаватель не найден в расписании"]:
                                cleaned_lines = []
                                pattern_fio = rf" – {re.escape(teacher)}"
                                for line in lines:
                                    cleaned_line = re.sub(pattern_fio, "", line, count=1, flags=re.IGNORECASE)
                                    cleaned_lines.append(cleaned_line)
                                schedule_str = "\n".join(cleaned_lines)
                                msg_text = f"<b>📚 Расписание преподавателя {teacher} на {date_t}</b>\n\n{schedule_str}"
                                results.append(create_inline_result(
                                    result_id=f"teacher_{teacher}_{date_t}",
                                    title=f"{teacher} на {date_t}",
                                    description="Нажмите, чтобы отправить расписание",
                                    message_text=msg_text
                                ))
                    else:
                        # Берем последние 10 дат
                        for date_to_use in available_dates[:10]:
                            df_t, date_t = load_schedule_file(date_to_use, "teachers")
                            if df_t is None:
                                df_t, date_t = load_schedule_file(date_to_use, "groups")
                            if df_t is not None:
                                lines = teacher_schedule.get_schedule_for_teacher(df_t, teacher)
                                if lines and lines != ["❌ Преподаватель не найден в расписании"]:
                                    cleaned_lines = []
                                    pattern_fio = rf" – {re.escape(teacher)}"
                                    for line in lines:
                                        cleaned_line = re.sub(pattern_fio, "", line, count=1, flags=re.IGNORECASE)
                                        cleaned_lines.append(cleaned_line)
                                    schedule_str = "\n".join(cleaned_lines)
                                    msg_text = f"<b>📚 Расписание преподавателя {teacher} на {date_t}</b>\n\n{schedule_str}"
                                    results.append(create_inline_result(
                                        result_id=f"teacher_{teacher}_{date_t}",
                                        title=f"{teacher} на {date_t}",
                                        description="Нажмите, чтобы отправить расписание",
                                        message_text=msg_text
                                    ))
                else:
                    # Если нет полного совпадения, показываем список преподавателей с расписанием на ближайший день
                    for teacher in matching_teachers:
                        # Ищем расписание на ближайший день
                        for date_to_use in available_dates:
                            df_t, date_t = load_schedule_file(date_to_use, "teachers")
                            if df_t is None:
                                df_t, date_t = load_schedule_file(date_to_use, "groups")
                            if df_t is not None:
                                lines = teacher_schedule.get_schedule_for_teacher(df_t, teacher)
                                if lines and lines != ["❌ Преподаватель не найден в расписании"]:
                                    cleaned_lines = []
                                    pattern_fio = rf" – {re.escape(teacher)}"
                                    for line in lines:
                                        cleaned_line = re.sub(pattern_fio, "", line, count=1, flags=re.IGNORECASE)
                                        cleaned_lines.append(cleaned_line)
                                    schedule_str = "\n".join(cleaned_lines)
                                    msg_text = f"<b>📚 Расписание преподавателя {teacher} на {date_t}</b>\n\n{schedule_str}"
                                    results.append(create_inline_result(
                                        result_id=f"teacher_{teacher}_{date_t}",
                                        title=f"{teacher} на {date_t}",
                                        description="Нажмите, чтобы отправить расписание",
                                        message_text=msg_text
                                    ))
                                    break  # Нашли расписание на ближайший день, переходим к следующему преподавателю
                if results:
                    return await inline_query.answer(results, cache_time=5)
        except Exception as e:
            print(f"Ошибка при поиске преподавателей: {str(e)}")

    # Если ничего не найдено, показываем стандартное расписание
    try:
        dates_to_check = [target_date] if target_date else available_dates[:10]  # Берем последние 10 дат
        result_id_prefix = f"explicit_{target_date.strftime('%Y%m%d')}" if target_date else "available"

        found_something = False
        group_pattern_check = r'^\d{2,3}-[А-ЯЁа-яёA-Za-z]{1,2}\d{1,2}-\d{1,2}[А-ЯЁа-яёA-Za-z]{3}$'

        for current_date in dates_to_check:
            lines = None
            found_type = None
            date_to_use = None
            df_g, date_g = None, None
            df_t, date_t = None, None

            # 1. Пытаемся найти расписание для группы
            df_g, date_g = load_schedule_file(current_date, "groups")
            if df_g is not None:
                g_lines = group_schedule.get_schedule_for_group(df_g, typed_name.upper())
                if g_lines and g_lines != ["❌ Группа не найдена в расписании"]:
                    lines = g_lines
                    found_type = "group"
                    date_to_use = date_g

            # 2. Если НЕ найдено для группы ИЛИ если запрос НЕ похож на группу, ищем для преподавателя
            looks_like_group = bool(re.match(group_pattern_check, typed_name))

            if not lines and not looks_like_group:
                df_t, date_t = load_schedule_file(current_date, "teachers")
                # Fallback: ищем в расписании групп (PDF содержит ФИО преподавателей)
                if df_t is None:
                    df_t, date_t = load_schedule_file(current_date, "groups")
                if df_t is not None:
                    t_lines = teacher_schedule.get_schedule_for_teacher(df_t, typed_name)
                    if t_lines and t_lines != ["❌ Преподаватель не найден в расписании"] and t_lines != ["ℹ️ У преподавателя нет пар в расписании"]:
                        cleaned_lines = []
                        pattern_fio = rf" – {re.escape(typed_name)}"
                        for line in t_lines:
                            cleaned_line = re.sub(pattern_fio, "", line, count=1, flags=re.IGNORECASE)
                            cleaned_lines.append(cleaned_line)
                        lines = cleaned_lines
                        found_type = "teacher"
                        date_to_use = date_t

            if lines:
                found_something = True
                date_str = date_to_use or "??.??.????"
                schedule_str = "\n".join(lines)

                if found_type == "group":
                    header_str = f"📚 Расписание группы {typed_name.upper()} на {date_str}"
                    title = f"Группа {typed_name.upper()} на {date_str}"
                else:
                    header_str = f"📚 Расписание преподавателя {typed_name} на {date_str}"
                    title = f"{typed_name} на {date_str}"

                msg_text = f"<b>{header_str}</b>\n\n{schedule_str}"
                result_id = f"{result_id_prefix}_{typed_name.replace(' ','_')}_{current_date.strftime('%Y%m%d')}_{found_type}"

                results.append(create_inline_result(
                    result_id=result_id,
                    title=title,
                    description="Нажмите, чтобы отправить расписание",
                    message_text=msg_text
                ))
                if target_date:
                    break

        if not found_something:
            title_not_found = f"❌ '{typed_name}' не найден"
            if target_date:
                title_not_found += f" на {target_date.strftime('%d.%m.%Y')}"
            else:
                title_not_found += " ни на одну дату"
            results.append(create_inline_result("not_found", title_not_found, "", "Расписание не найдено."))

        return await inline_query.answer(results, cache_time=5)
    except Exception as e:
        print(f"Ошибка при обработке стандартного расписания: {str(e)}")
        return await inline_query.answer([], cache_time=1)