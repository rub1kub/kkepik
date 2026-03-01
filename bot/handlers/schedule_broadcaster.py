# handlers/schedule_broadcaster.py
"""
Общая логика обработки файла расписания и рассылки через Telegram.
Используется из upload_schedule.py (Telegram) и max_bot (MAX messenger).
"""

import re
import sqlite3
import os
import shutil
import logging
import pandas as pd
from aiogram import types
from aiogram.types.input_file import BufferedInputFile
from aiogram.client.bot import Bot

import config
import global_schedules
from schedules import group_schedule, teacher_schedule
from schedules.schedule_comparator import get_changed_users, load_existing_schedule
from schedules.schedule_image import generate_schedule_image
from schedules.schedule_mood import get_mood_emoji
from schedules.pair_times import add_pair_times, is_saturday


WEBAPP_URL = "https://kkepik.ru/"


def create_schedule_keyboard(schedule_date: str, schedule_type: str = "groups") -> types.InlineKeyboardMarkup:
    """Создает клавиатуру с кнопками для расписания."""
    webapp_button = types.InlineKeyboardButton(
        text="📱 Открыть в приложении",
        web_app=types.WebAppInfo(url=WEBAPP_URL)
    )
    download_url = f"https://kkepik.ru/api/schedule/download/{schedule_type}/{schedule_date}"
    download_button = types.InlineKeyboardButton(
        text="📥 Скачать файлом",
        url=download_url
    )
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [download_button],
        [webapp_button]
    ])


def _get_cached_image(cache: dict, lines: list, group_name: str, date_str: str):
    """Генерирует и кэширует изображение расписания по группе."""
    if not config.SEND_PREVIEW:
        return None
    key = group_name.upper()
    if key not in cache:
        try:
            cache[key] = generate_schedule_image(lines, group_name, date_str)
        except Exception:
            cache[key] = None
    return cache[key]


async def _send_with_image(bot: Bot, uid: int, msg_text: str, img_bytes, keyboard):
    """Отправляет расписание: фото + caption если есть картинка, иначе текст."""
    if img_bytes and config.SEND_PREVIEW:
        photo = BufferedInputFile(img_bytes, filename="schedule.png")
        if len(msg_text) <= 1024:
            await bot.send_photo(uid, photo=photo, caption=msg_text, parse_mode="HTML", reply_markup=keyboard)
        else:
            await bot.send_photo(uid, photo=photo)
            await bot.send_message(uid, text=msg_text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await bot.send_message(uid, text=msg_text, parse_mode="HTML", reply_markup=keyboard)


def parse_schedule_file_metadata(file_path: str, file_name: str) -> tuple[str | None, str | None, str | None]:
    """
    Извлекает дату и тип расписания из имени/содержимого файла.

    Returns:
        (schedule_date, schedule_type, error_message)
        При ошибке schedule_date и schedule_type = None, error_message содержит описание.
    """
    is_pdf = file_name.lower().endswith(".pdf")

    # Извлекаем дату
    mm = re.search(r"(\d{1,2}\.\d{1,2}\.\d{4})", file_name)
    if not mm:
        mm = re.search(r"(\d{1,2}_\d{1,2}_\d{4})", file_name)
    if mm:
        schedule_date = mm.group(1).replace("_", ".")
    elif is_pdf:
        from schedules.pdf_to_df import extract_date_from_pdf_content
        content_date = extract_date_from_pdf_content(file_path)
        if content_date:
            schedule_date = content_date
        else:
            return None, None, "Не удалось извлечь дату из PDF."
    else:
        return None, None, "Не удалось извлечь дату из названия файла."

    # Определяем тип
    schedule_type = None
    if "ГРУППЫ" in file_name.upper():
        schedule_type = "groups"
    elif "ПРЕПОДАВАТЕЛИ" in file_name.upper():
        schedule_type = "teachers"
    if not schedule_type:
        return None, None, "Не удалось определить тип расписания (группы/преподаватели)."

    return schedule_date, schedule_type, None


def load_schedule_df(file_path: str, schedule_date: str) -> tuple[pd.DataFrame | None, str | None]:
    """
    Читает DataFrame из файла расписания.

    Returns:
        (df, error_message). При ошибке df = None.
    """
    is_pdf = file_path.lower().endswith(".pdf")
    try:
        if is_pdf:
            from schedules.pdf_to_df import pdf_to_dataframe
            df = pdf_to_dataframe(file_path)
            if df is None:
                return None, "Не удалось прочитать таблицы из PDF."
        else:
            df = pd.read_excel(file_path, sheet_name=schedule_date, header=None)
            if 0 in df.columns:
                df[0] = df[0].ffill()
        return df, None
    except Exception as e:
        return None, f"Ошибка чтения файла: {e}"


async def process_and_broadcast(
    file_path: str,
    file_name: str,
    bot: Bot = None,
    log_fn=None,
    broadcast: bool = True,
) -> tuple[bool, str]:
    """
    Обрабатывает файл расписания и рассылает через Telegram.

    Args:
        file_path: путь к файлу на диске (PDF/XLSX)
        file_name: оригинальное имя файла
        bot: экземпляр Telegram бота
        log_fn: async функция для логирования (опционально), вызывается как await log_fn(text)

    Returns:
        (success, message)
    """

    async def _log(text: str):
        if log_fn:
            try:
                await log_fn(text)
            except Exception:
                pass
        print(f"[broadcast] {text}")

    # 1. Парсим метаданные
    schedule_date, schedule_type, err = parse_schedule_file_metadata(file_path, file_name)
    if err:
        return False, err

    # 2. Проверяем существование ДО сохранения
    old_df = load_existing_schedule(schedule_date, schedule_type)
    is_update = old_df is not None

    # 3. Читаем DataFrame
    df, err = load_schedule_df(file_path, schedule_date)
    if err:
        return False, err

    # 4. Сохраняем файл в DATA_DIR
    try:
        data_file_path = os.path.join(config.DATA_DIR, file_name)
        shutil.copy2(file_path, data_file_path)
    except Exception as e:
        return False, f"Ошибка сохранения файла: {e}"

    # 5. Обновляем глобальный кэш (бот-процесс)
    if schedule_type == "groups":
        global_schedules.last_groups_df = df
        global_schedules.last_groups_date = schedule_date
    else:
        global_schedules.last_teachers_df = df
        global_schedules.last_teachers_date = schedule_date

    # Обновляем кэш API-процесса (он работает в отдельном процессе)
    try:
        import aiohttp as _aiohttp
        api_port = config.get_api_port()
        async with _aiohttp.ClientSession() as _session:
            async with _session.post(f"http://127.0.0.1:{api_port}/schedule/reload") as _resp:
                if _resp.status == 200:
                    print(f"[broadcast] API кэш обновлён")
    except Exception:
        pass  # API может быть недоступен — не критично

    if not broadcast:
        return True, f"Файл '{file_name}' обработан и сохранён (без рассылки)."

    # 5. Получаем пользователей
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, role, name_or_group, is_class_teacher, class_group FROM users")
    rows = cur.fetchall()
    conn.close()

    # 6. Определяем изменённых пользователей
    if is_update:
        changed_user_ids = get_changed_users(old_df, df, schedule_type, rows)
        await _log(f"Обновление расписания: {len(changed_user_ids)} пользователей с изменениями")
    else:
        changed_user_ids = {row[0] for row in rows}
        await _log(f"Новое расписание: отправляем {len(changed_user_ids)} пользователям")


    # 8. Рассылка
    success_count = 0
    group_image_cache = {}

    for (uid, role, namegrp, is_class_teacher, class_group) in rows:
        if uid not in changed_user_ids:
            continue

        if schedule_type == "groups":
            # ── Студенты ──
            if role == config.ROLE_STUDENT:
                lines = group_schedule.get_schedule_for_group(df, namegrp.upper())
                if lines is None:
                    msg_error = (
                        f"🔔 Пришло расписание!\n\n"
                        f"Но бот не нашёл группу '{namegrp}' в таблице.\n"
                        f"Либо Вы неправильно зарегистрировались, либо пар действительно нет 😳"
                    )
                    if is_update:
                        msg_error = "‼️ РАСПИСАНИЕ ИЗМЕНИЛОСЬ\n\n" + msg_error
                    try:
                        await bot.send_message(uid, msg_error, reply_markup=create_schedule_keyboard(schedule_date, "groups"))
                        success_count += 1
                    except Exception as e:
                        logging.warning(f"[broadcast] Не удалось отправить uid={uid}: {e}")
                elif not lines:
                    lines = [f"▪️{i} пара – Нет" for i in range(1, 5)]
                    lines_timed = add_pair_times(lines, schedule_date)
                    txt = "\n".join(lines_timed if is_saturday(schedule_date) else lines)
                    msg_text = f"{get_mood_emoji(lines)} Расписание на <b>{schedule_date}</b>\n\nГруппа <b>{namegrp}</b>:\n\n{txt}"
                    if is_update:
                        msg_text = "‼️ РАСПИСАНИЕ ИЗМЕНИЛОСЬ\n\n" + msg_text
                    try:
                        kb = create_schedule_keyboard(schedule_date, "groups")
                        img = _get_cached_image(group_image_cache, lines_timed, namegrp, schedule_date)
                        await _send_with_image(bot, uid, msg_text, img, kb)
                        success_count += 1
                    except Exception as e:
                        logging.warning(f"[broadcast] Не удалось отправить uid={uid}: {e}")
                else:
                    lines_timed = add_pair_times(lines, schedule_date)
                    txt = "\n".join(lines_timed if is_saturday(schedule_date) else lines)
                    msg_text = f"{get_mood_emoji(lines)} Расписание на <b>{schedule_date}</b>\n\nГруппа <b>{namegrp}</b>:\n\n{txt}"
                    if is_update:
                        msg_text = "‼️ РАСПИСАНИЕ ИЗМЕНИЛОСЬ\n\n" + msg_text
                    try:
                        kb = create_schedule_keyboard(schedule_date, "groups")
                        img = _get_cached_image(group_image_cache, lines_timed, namegrp, schedule_date)
                        await _send_with_image(bot, uid, msg_text, img, kb)
                        success_count += 1
                    except Exception as e:
                        logging.warning(f"[broadcast] Не удалось отправить uid={uid}: {e}")

            # ── Преподаватели (из таблицы групп) ──
            if role == config.ROLE_TEACHER:
                lines_raw = teacher_schedule.get_schedule_for_teacher(df, namegrp)
                if lines_raw:
                    txt = "\n".join(lines_raw)
                    msg_text = f"📆 <b>{schedule_date}</b>\n\nПреподаватель <b>{namegrp}</b>:\n\n{txt}"
                    if is_update:
                        msg_text = "‼️ РАСПИСАНИЕ ИЗМЕНИЛОСЬ\n\n" + msg_text
                    try:
                        await bot.send_message(uid, text=msg_text, parse_mode="HTML",
                                               reply_markup=create_schedule_keyboard(schedule_date, "groups"))
                        success_count += 1
                    except Exception as e:
                        logging.warning(f"[broadcast] Не удалось отправить uid={uid}: {e}")

            # ── Классные руководители ──
            if role == config.ROLE_TEACHER and is_class_teacher and class_group:
                lines = group_schedule.get_schedule_for_group(df, class_group.upper())
                if lines is None:
                    msg_error = (
                        f"🔔 Пришло расписание!\n\n"
                        f"Но бот не нашёл группу '{class_group}' в таблице.\n"
                        f"Либо Вы неправильно зарегистрировались, либо пар действительно нет 😳"
                    )
                    if is_update:
                        msg_error = "‼️ РАСПИСАНИЕ ИЗМЕНИЛОСЬ\n\n" + msg_error
                    try:
                        await bot.send_message(uid, msg_error, reply_markup=create_schedule_keyboard(schedule_date, "groups"))
                        success_count += 1
                    except Exception as e:
                        logging.warning(f"[broadcast] Не удалось отправить uid={uid}: {e}")
                elif not lines:
                    lines = [f"▪️{i} пара – Нет" for i in range(1, 5)]
                    lines_timed = add_pair_times(lines, schedule_date)
                    txt = "\n".join(lines_timed if is_saturday(schedule_date) else lines)
                    msg_text = f"👨‍🏫 Расписание Вашей группы <b>{class_group}</b> на <b>{schedule_date}</b>:\n\n{txt}"
                    if is_update:
                        msg_text = "‼️ РАСПИСАНИЕ ИЗМЕНИЛОСЬ\n\n" + msg_text
                    try:
                        kb = create_schedule_keyboard(schedule_date, "groups")
                        img = _get_cached_image(group_image_cache, lines_timed, class_group, schedule_date)
                        await _send_with_image(bot, uid, msg_text, img, kb)
                        success_count += 1
                    except Exception as e:
                        logging.warning(f"[broadcast] Не удалось отправить uid={uid}: {e}")
                else:
                    lines_timed = add_pair_times(lines, schedule_date)
                    txt = "\n".join(lines_timed if is_saturday(schedule_date) else lines)
                    msg_text = f"👨‍🏫 Расписание Вашей группы <b>{class_group}</b> на <b>{schedule_date}</b>:\n\n{txt}"
                    if is_update:
                        msg_text = "‼️ РАСПИСАНИЕ ИЗМЕНИЛОСЬ\n\n" + msg_text
                    try:
                        kb = create_schedule_keyboard(schedule_date, "groups")
                        img = _get_cached_image(group_image_cache, lines_timed, class_group, schedule_date)
                        await _send_with_image(bot, uid, msg_text, img, kb)
                        success_count += 1
                    except Exception as e:
                        logging.warning(f"[broadcast] Не удалось отправить uid={uid}: {e}")

        elif schedule_type == "teachers":
            if role == config.ROLE_TEACHER:
                lines_raw = teacher_schedule.get_schedule_for_teacher(df, namegrp)
                if not lines_raw:
                    msg_error = f"🔔 Пришло расписание!\n\nНо бот не нашёл ФИО '{namegrp}' в таблице."
                    if is_update:
                        msg_error = "‼️ РАСПИСАНИЕ ИЗМЕНИЛОСЬ\n\n" + msg_error
                    try:
                        await bot.send_message(uid, msg_error)
                        success_count += 1
                    except Exception as e:
                        logging.warning(f"[broadcast] Не удалось отправить uid={uid}: {e}")
                else:
                    txt = "\n".join(lines_raw)
                    msg_text = f"📆 <b>{schedule_date}</b>\n\nПреподаватель <b>{namegrp}</b>:\n\n{txt}"
                    if is_update:
                        msg_text = "‼️ РАСПИСАНИЕ ИЗМЕНИЛОСЬ\n\n" + msg_text
                    try:
                        schedule_keyboard = create_schedule_keyboard(schedule_date, "teachers")
                        await bot.send_message(uid, text=msg_text, parse_mode="HTML", reply_markup=schedule_keyboard)
                        success_count += 1
                    except Exception as e:
                        logging.warning(f"[broadcast] Не удалось отправить uid={uid}: {e}")

    if is_update:
        result_msg = f"Обновление завершено! Уведомлены {success_count} пользователей."
    else:
        result_msg = f"Новое расписание разослано {success_count} пользователям."

    await _log(result_msg)
    return True, result_msg
