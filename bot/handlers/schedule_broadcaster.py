# handlers/schedule_broadcaster.py
"""
Общая логика обработки файла расписания и рассылки через Telegram.
Используется из upload_schedule.py (Telegram) и max_bot (MAX messenger).
"""

import re
import datetime as dt
import sqlite3
import os
import shutil
import logging
from collections import Counter
from html import escape
import pandas as pd
from aiogram import types
from aiogram.types.input_file import BufferedInputFile
from aiogram.client.bot import Bot

import config
import global_schedules
from schedules import group_schedule, teacher_schedule
from schedules.schedule_comparator import (
    compare_group_schedules,
    compare_teacher_schedules,
    get_changed_users,
    load_existing_schedule,
)
from schedules.schedule_formatter import (
    build_group_schedule_message,
    fit_photo_caption,
    telegram_text_length,
)
from schedules.schedule_keyboard import create_schedule_keyboard
from schedules.schedule_dates import is_current_schedule
from schedules.schedule_notices import append_broadcast_schedule_notice, get_schedule_settings_notice
from schedules.pdf_crop import crop_group_screenshots
from tracked_groups import get_all_tracked_groups
from message_registry import message_tracking_context
from schedule_preferences import get_preferences, get_all_preferences, SchedulePreferences, wants_broadcast
from schedules.message_constructor import render_schedule_message, personal_subject_aliases
from schedules.personal_delivery import send_personal_schedule

async def _send_with_image(bot: Bot, uid: int, msg_text: str, img_bytes, keyboard):
    """Отправляет расписание: фото + caption если есть картинка, иначе текст."""
    if img_bytes:
        photo = BufferedInputFile(img_bytes, filename="schedule.png")
        caption = fit_photo_caption(msg_text)
        if telegram_text_length(caption) <= 1024:
            await bot.send_photo(uid, photo=photo, caption=caption, parse_mode="HTML", reply_markup=keyboard)
        else:
            await bot.send_photo(uid, photo=photo)
            await bot.send_message(uid, text=msg_text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await bot.send_message(uid, text=msg_text, parse_mode="HTML", reply_markup=keyboard)


async def _send_tracked_group_schedule(
    bot: Bot,
    uid: int,
    df: pd.DataFrame,
    group_name: str,
    schedule_date: str,
    pdf_crop_cache: dict[str, bytes],
    *,
    is_update: bool,
    tracked_count: int,
    always_show_group: bool,
    recipient_role: str | None = None,
    preferences=None,
) -> bool:
    with message_tracking_context(
        message_kind="schedule",
        schedule_date=schedule_date,
        schedule_type="groups",
        entity=group_name.upper(),
    ):
        return await _send_tracked_group_schedule_impl(
            bot,
            uid,
            df,
            group_name,
            schedule_date,
            pdf_crop_cache,
            is_update=is_update,
            tracked_count=tracked_count,
            always_show_group=always_show_group,
            recipient_role=recipient_role,
            preferences=preferences,
        )


async def _send_tracked_group_schedule_impl(
    bot: Bot,
    uid: int,
    df: pd.DataFrame,
    group_name: str,
    schedule_date: str,
    pdf_crop_cache: dict[str, bytes],
    *,
    is_update: bool,
    tracked_count: int,
    always_show_group: bool,
    recipient_role: str | None = None,
    preferences=None,
) -> bool:
    lines = group_schedule.get_schedule_for_group(df, group_name.upper())
    keyboard = create_schedule_keyboard(schedule_date, "groups")
    if lines is None:
        logging.warning(
            "[broadcast] Группа %s отсутствует в файле; отправка uid=%s пропущена",
            group_name,
            uid,
        )
        return False

    if not lines:
        lines = [f"▪️{pair} пара – Нет" for pair in range(1, 5)]

    image = pdf_crop_cache.get(group_name.upper())
    preferences = preferences or get_preferences(uid)
    # Only personalize this cohort's student captions, never source data or teacher copies.
    subject_aliases = personal_subject_aliases(recipient_role, group_name)
    message = build_group_schedule_message(
        lines,
        group_name,
        schedule_date,
        show_group=always_show_group or tracked_count > 1 or not image,
        subject_aliases=subject_aliases,
        preferences=preferences,
    )
    if is_update:
        message = "‼️ РАСПИСАНИЕ ИЗМЕНИЛОСЬ\n\n" + message
    message = append_broadcast_schedule_notice(message, recipient_role, schedule_date)
    await send_personal_schedule(bot, uid, message, image, keyboard, preferences,
                                 notice=get_schedule_settings_notice(schedule_date))
    return True


def find_missing_tracked_groups(
    tracked_by_user: dict[int, list[str]],
    available_groups: set[str],
) -> Counter[str]:
    available = {group.upper() for group in available_groups}
    return Counter(
        group.upper()
        for groups in tracked_by_user.values()
        for group in groups
        if group.upper() not in available
    )


def find_missing_tracked_groups_by_user(
    tracked_by_user: dict[int, list[str]],
    available_groups: set[str],
) -> dict[int, list[str]]:
    available = {group.upper() for group in available_groups}
    return {
        user_id: list(
            dict.fromkeys(
                group.upper()
                for group in groups
                if group.upper() not in available
            )
        )
        for user_id, groups in tracked_by_user.items()
        if any(group.upper() not in available for group in groups)
    }


async def _notify_missing_tracked_groups(
    bot: Bot,
    missing: Counter[str],
    schedule_date: str,
) -> None:
    if not missing:
        return

    from handlers.schedule_alerts import OWNER_ID

    rows = [
        f"• <code>{escape(group)}</code> — {count}"
        for group, count in missing.most_common(40)
    ]
    if len(missing) > len(rows):
        rows.append(f"• и ещё {len(missing) - len(rows)}")
    text = (
        "⚠️ <b>Группы из базы не найдены в расписании</b>\n"
        f"Дата: <b>{escape(schedule_date)}</b>\n"
        "Отправка этим подпискам пропущена:\n\n"
        + "\n".join(rows)
    )
    try:
        with message_tracking_context(
            message_kind="schedule_warning",
            schedule_date=schedule_date,
            schedule_type="groups",
        ):
            await bot.send_message(OWNER_ID, text, parse_mode="HTML")
    except Exception as exc:
        logging.warning("[broadcast] Не удалось уведомить владельца: %s", exc)


async def _notify_users_about_missing_groups(
    bot: Bot,
    missing_by_user: dict[int, list[str]],
    schedule_date: str,
    preferences_by_user=None,
) -> int:
    notified = 0
    for user_id, groups in missing_by_user.items():
        rows = "\n".join(f"• <code>{escape(group)}</code>" for group in groups)
        text = (
            "⚠️ <b>Группа не найдена в расписании</b>\n\n"
            f"В файле на <b>{escape(schedule_date)}</b> нет отслеживаемой группы:\n"
            f"{rows}\n\n"
            "Проверьте номер в /groups. Если он указан верно, значит группы нет "
            "в исходном файле колледжа."
        )
        text = append_broadcast_schedule_notice(text, None, schedule_date)
        try:
            with message_tracking_context(
                message_kind="schedule_missing_group",
                schedule_date=schedule_date,
                schedule_type="groups",
                entity=", ".join(groups),
            ):
                preferences = (preferences_by_user or {}).get(user_id, SchedulePreferences())
                await bot.send_message(user_id, text, parse_mode="HTML", disable_notification=preferences.silent)
            notified += 1
        except Exception as exc:
            logging.warning(
                "[broadcast] Не удалось сообщить uid=%s об отсутствующей группе: %s",
                user_id,
                exc,
            )
    return notified


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


def validate_schedule_df(df: pd.DataFrame, schedule_type: str) -> str | None:
    """Reject empty or structurally unrecognized files before any side effects."""
    if df is None or df.empty or df.shape[1] < 2:
        return "Файл прочитан, но таблица расписания пуста."

    try:
        from schedules.schedule_catalog import extract_catalog_entries

        entries = extract_catalog_entries(df)
    except Exception as exc:
        return f"Не удалось проверить структуру расписания: {exc}"

    if schedule_type == "groups" and not entries["groups"]:
        return "В файле групп не найдено ни одного корректного номера группы."
    if schedule_type == "teachers" and not entries["teachers"]:
        return "В файле преподавателей не найдено ни одного корректного ФИО."
    return None


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
    is_active_schedule = is_current_schedule(schedule_date)
    if broadcast and not is_active_schedule:
        return (
            False,
            f"Расписание на {schedule_date} неактуально. "
            "Для сохранения без рассылки загрузите файл с подписью /silent.",
        )

    # 2. Проверяем существование ДО сохранения
    old_df = load_existing_schedule(schedule_date, schedule_type)
    is_update = old_df is not None

    # 3. Читаем DataFrame
    df, err = load_schedule_df(file_path, schedule_date)
    if err:
        return False, err
    validation_error = validate_schedule_df(df, schedule_type)
    if validation_error:
        return False, validation_error

    # Knowledge revisions retain source bytes BEFORE the legacy filename is
    # overwritten. This hook is isolated from delivery and never reads users.
    if schedule_type == "groups":
        try:
            from schedules.knowledge_store import KnowledgeStore

            knowledge = KnowledgeStore(config.DATA_DIR)
            previous_path = os.path.join(config.DATA_DIR, file_name)
            if os.path.isfile(previous_path) and os.path.abspath(previous_path) != os.path.abspath(file_path):
                previous_df, previous_error = load_schedule_df(previous_path, schedule_date)
                knowledge.ingest(previous_path, schedule_date, previous_df,
                                 error=previous_error, archived_import=True,
                                 received_at=dt.datetime.fromtimestamp(
                                     os.path.getmtime(previous_path), dt.timezone.utc
                                 ).isoformat(timespec="seconds"))
            knowledge_result = knowledge.ingest(file_path, schedule_date, df)
            if knowledge_result["status"] != "parsed":
                logging.warning("[knowledge] Source structure could not be indexed for %s", schedule_date)
        except Exception:
            logging.exception("[knowledge] Unable to snapshot schedule %s", schedule_date)

    # 4. Сохраняем файл в DATA_DIR
    try:
        data_file_path = os.path.join(config.DATA_DIR, file_name)
        shutil.copy2(file_path, data_file_path)
    except Exception as e:
        return False, f"Ошибка сохранения файла: {e}"

    try:
        from schedules.schedule_catalog import update_schedule_catalog

        catalog_result = update_schedule_catalog(
            df,
            schedule_date,
            schedule_type,
            source_name=file_name,
        )
        if catalog_result.get("updated"):
            await _log(
                "Каталог обновлён: "
                f"{catalog_result['groups']} групп, "
                f"{catalog_result['teachers']} преподавателей, "
                f"{catalog_result['audiences']} аудиторий"
            )
    except Exception as e:
        logging.exception("[catalog] update failed: %s", e)

    # 4b. Генерируем кропы из PDF для рассылки студентам
    pdf_crop_cache: dict[str, bytes] = {}
    if is_active_schedule and data_file_path.lower().endswith(".pdf") and schedule_type == "groups":
        try:
            pdf_crop_cache = crop_group_screenshots(data_file_path)
            await _log(f"PDF кропы: {len(pdf_crop_cache)} групп")
        except Exception as e:
            logging.warning(f"[broadcast] PDF crop failed: {e}")

    # Архивный /silent-файл сохраняется на диске, но не становится активным.
    if is_active_schedule:
        if schedule_type == "groups":
            global_schedules.last_groups_df = df
            global_schedules.last_groups_date = schedule_date
            global_schedules.last_groups_crop_cache = pdf_crop_cache
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
    tracked_by_user = get_all_tracked_groups(config.DB_PATH)
    personal_preferences = get_all_preferences(config.DB_PATH)

    for uid, role, namegrp, is_class_teacher, class_group in rows:
        if uid in tracked_by_user:
            continue
        if role == config.ROLE_STUDENT and namegrp:
            tracked_by_user[uid] = [namegrp.upper()]
        elif role == config.ROLE_TEACHER and is_class_teacher and class_group:
            tracked_by_user[uid] = [class_group.upper()]

    available_groups: set[str] = set()
    if schedule_type == "groups":
        from schedules.schedule_catalog import extract_catalog_entries

        available_groups = set(extract_catalog_entries(df)["groups"])
        missing_tracked_groups = find_missing_tracked_groups(
            tracked_by_user,
            available_groups,
        )
        await _notify_missing_tracked_groups(bot, missing_tracked_groups, schedule_date)
        missing_by_user = find_missing_tracked_groups_by_user(
            tracked_by_user,
            available_groups,
        )
        missing_by_user = {uid: groups for uid, groups in missing_by_user.items()
                           if wants_broadcast(personal_preferences.get(uid, SchedulePreferences()), is_update=is_update)}
        missing_notice_count = await _notify_users_about_missing_groups(
            bot,
            missing_by_user,
            schedule_date,
            preferences_by_user=personal_preferences,
        )
        if missing_notice_count:
            await _log(
                "Об отсутствующей группе уведомлены "
                f"{missing_notice_count} пользователей"
            )

    # 6. Определяем изменённых пользователей
    if is_update:
        changed_user_ids = get_changed_users(old_df, df, schedule_type, rows)
        changed_group_keys: set[tuple[int, str]] = set()
        changed_teacher_ids: set[int] = set()
        if schedule_type == "groups":
            for uid, groups in tracked_by_user.items():
                for group_name in groups:
                    if compare_group_schedules(old_df, df, group_name):
                        changed_group_keys.add((uid, group_name))
                        changed_user_ids.add(uid)
            for uid, role, namegrp, _, _ in rows:
                if role == config.ROLE_TEACHER and compare_teacher_schedules(old_df, df, namegrp):
                    changed_teacher_ids.add(uid)
                    changed_user_ids.add(uid)
        await _log(f"Обновление расписания: {len(changed_user_ids)} пользователей с изменениями")
    else:
        changed_user_ids = {row[0] for row in rows}
        changed_group_keys = {
            (uid, group_name)
            for uid, groups in tracked_by_user.items()
            for group_name in groups
        }
        changed_teacher_ids = {
            uid for uid, role, _, _, _ in rows if role == config.ROLE_TEACHER
        }
        await _log(f"Новое расписание: отправляем {len(changed_user_ids)} пользователям")


    # 8. Рассылка
    success_count = 0
    notified_user_ids: set[int] = set()

    for (uid, role, namegrp, is_class_teacher, class_group) in rows:
        if uid not in changed_user_ids:
            continue
        preferences = personal_preferences.get(uid, SchedulePreferences())
        if not wants_broadcast(preferences, is_update=is_update):
            continue

        if schedule_type == "groups":
            groups = tracked_by_user.get(uid, [])
            for group_name in groups:
                if group_name.upper() not in available_groups:
                    continue
                if is_update and (uid, group_name) not in changed_group_keys:
                    continue
                try:
                    sent = await _send_tracked_group_schedule(
                        bot,
                        uid,
                        df,
                        group_name,
                        schedule_date,
                        pdf_crop_cache,
                        is_update=is_update,
                        tracked_count=len(groups),
                        always_show_group=role == config.ROLE_TEACHER,
                        recipient_role=role,
                        preferences=preferences,
                    )
                    if sent:
                        success_count += 1
                        notified_user_ids.add(uid)
                except Exception as e:
                    logging.warning(
                        f"[broadcast] Не удалось отправить uid={uid}, group={group_name}: {e}"
                    )

            # ── Преподаватели (из таблицы групп) ──
            if role == config.ROLE_TEACHER and uid in changed_teacher_ids:
                lines_raw = teacher_schedule.get_schedule_for_teacher(df, namegrp)
                if lines_raw:
                    msg_text = render_schedule_message(lines_raw, namegrp, schedule_date, preferences=preferences, teacher=True)
                    if is_update:
                        msg_text = "‼️ РАСПИСАНИЕ ИЗМЕНИЛОСЬ\n\n" + msg_text
                    msg_text = append_broadcast_schedule_notice(msg_text, role, schedule_date)
                    try:
                        with message_tracking_context(
                            message_kind="schedule",
                            schedule_date=schedule_date,
                            schedule_type="groups",
                            entity=namegrp,
                        ):
                            await send_personal_schedule(bot, uid, msg_text, None,
                                create_schedule_keyboard(schedule_date, "groups"), preferences)
                        success_count += 1
                        notified_user_ids.add(uid)
                    except Exception as e:
                        logging.warning(f"[broadcast] Не удалось отправить uid={uid}: {e}")

        elif schedule_type == "teachers":
            if role == config.ROLE_TEACHER:
                lines_raw = teacher_schedule.get_schedule_for_teacher(df, namegrp)
                if not lines_raw:
                    msg_error = f"🔔 Пришло расписание!\n\nНо бот не нашёл ФИО '{namegrp}' в таблице."
                    if is_update:
                        msg_error = "‼️ РАСПИСАНИЕ ИЗМЕНИЛОСЬ\n\n" + msg_error
                    msg_error = append_broadcast_schedule_notice(msg_error, role, schedule_date)
                    try:
                        with message_tracking_context(
                            message_kind="schedule",
                            schedule_date=schedule_date,
                            schedule_type="teachers",
                            entity=namegrp,
                        ):
                            await bot.send_message(uid, msg_error, disable_notification=preferences.silent)
                        success_count += 1
                        notified_user_ids.add(uid)
                    except Exception as e:
                        logging.warning(f"[broadcast] Не удалось отправить uid={uid}: {e}")
                else:
                    msg_text = render_schedule_message(lines_raw, namegrp, schedule_date, preferences=preferences, teacher=True)
                    if is_update:
                        msg_text = "‼️ РАСПИСАНИЕ ИЗМЕНИЛОСЬ\n\n" + msg_text
                    msg_text = append_broadcast_schedule_notice(msg_text, role, schedule_date)
                    try:
                        schedule_keyboard = create_schedule_keyboard(schedule_date, "teachers")
                        with message_tracking_context(
                            message_kind="schedule",
                            schedule_date=schedule_date,
                            schedule_type="teachers",
                            entity=namegrp,
                        ):
                            await send_personal_schedule(bot, uid, msg_text, None, schedule_keyboard, preferences)
                        success_count += 1
                        notified_user_ids.add(uid)
                    except Exception as e:
                        logging.warning(f"[broadcast] Не удалось отправить uid={uid}: {e}")

    if is_update:
        result_msg = f"Обновление завершено! Уведомлены {len(notified_user_ids)} пользователей ({success_count} сообщений)."
    else:
        result_msg = f"Новое расписание разослано {len(notified_user_ids)} пользователям ({success_count} сообщений)."

    await _log(result_msg)
    return True, result_msg
