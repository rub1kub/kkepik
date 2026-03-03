# commands/start.py
import sqlite3
import config
from aiogram import types
from aiogram.filters import Command
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.types.input_file import BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import re
from aiogram.enums import ParseMode
from difflib import get_close_matches
import datetime
import pandas as pd
import os
from zoneinfo import ZoneInfo

import global_schedules
from schedules import group_schedule, teacher_schedule, parser_all
from schedules.schedule_mood import get_mood_emoji
from schedules.pair_times import add_pair_times, is_saturday
import config


def _find_latest_schedule(schedule_type: str):
    """Ищет последний файл расписания (xlsx/pdf) в DATA_DIR. Возвращает (df, date) или (None, None)."""
    type_kw = "ГРУПП" if schedule_type == "groups" else "ПРЕПОДАВАТЕЛИ"
    data_dir = config.DATA_DIR

    files = []
    for filename in os.listdir(data_dir):
        if filename.startswith('~$'):
            continue
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ('.xlsx', '.pdf'):
            continue
        if type_kw not in filename.upper():
            continue
        file_path = os.path.join(data_dir, filename)
        try:
            mtime = os.path.getmtime(file_path)
            files.append((filename, file_path, ext, mtime))
        except OSError:
            continue

    files.sort(key=lambda x: x[3], reverse=True)

    for filename, file_path, ext, mtime in files:
        try:
            mm = re.search(r"(\d{1,2}\.\d{1,2}\.\d{4})", filename)
            if not mm:
                mm = re.search(r"(\d{1,2}_\d{1,2}_\d{4})", filename)
            if mm:
                date_str = mm.group(1).replace("_", ".")
            elif ext == '.pdf':
                from schedules.pdf_to_df import extract_date_from_pdf_content
                date_str = extract_date_from_pdf_content(file_path)
                if not date_str:
                    continue
            else:
                continue

            if ext == '.pdf':
                from schedules.pdf_to_df import pdf_to_dataframe
                df = pdf_to_dataframe(file_path)
            else:
                df = pd.read_excel(file_path, sheet_name=date_str, header=None)
                if df is not None and 0 in df.columns:
                    df[0] = df[0].ffill()

            if df is not None:
                return df, date_str
        except Exception:
            continue

    return None, None


# Определяем состояния для регистрации
class RegistrationStates(StatesGroup):
    choosing_role = State()
    waiting_name = State()
    is_class_teacher = State()
    waiting_group = State()
    
webapp_url = "https://kkepik.ru/"

def create_schedule_keyboard(schedule_date: str, schedule_type: str = "groups") -> types.InlineKeyboardMarkup:
    """
    Создает клавиатуру с кнопками для расписания
    
    Args:
        schedule_date: Дата в формате dd.mm.yyyy
        schedule_type: Тип расписания ("groups" или "teachers")
    """
    # Создаем инлайн кнопку для открытия веб-приложения
    webapp_button = types.InlineKeyboardButton(
        text="📱 Открыть приложение",
        web_app=types.WebAppInfo(url=webapp_url)
    )
    
    # Создаем кнопку для скачивания файла
    api_port = config.get_api_port()
    download_url = f"https://kkepik.ru/api/schedule/download/{schedule_type}/{schedule_date}"
    download_button = types.InlineKeyboardButton(
        text="📥 Скачать файлом",
        url=download_url
    )
    
    # Возвращаем клавиатуру с двумя кнопками (скачивание сверху)
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [download_button],
        [webapp_button]
    ])

# Создаем базовую клавиатуру (для случаев, когда нет расписания)
webapp_button = types.InlineKeyboardButton(
        text="📱 Открыть приложение",
        web_app=types.WebAppInfo(url=webapp_url)
)
    
keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[webapp_button]])

# Обработчик команды /start
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    # Создаем таблицу, если её нет, и пытаемся найти пользователя в базе
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            role TEXT,
            name_or_group TEXT
        )
    """)
    cur.execute("SELECT role, name_or_group FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()

    # Если пользователь найден, отправляем актуальное расписание
    if row:
        role, typed_name = row
        typed_name = (typed_name or "").strip()
        if role == config.ROLE_STUDENT:
            typed_name = typed_name.upper()

            # 1. Кэш в памяти
            df = global_schedules.last_groups_df
            schedule_date = global_schedules.last_groups_date

            # 2. Fallback: поиск файла в DATA_DIR (xlsx + pdf)
            if df is None:
                df, schedule_date = _find_latest_schedule("groups")

            lines = None
            if df is not None:
                lines = group_schedule.get_schedule_for_group(df, typed_name)

            if lines:
                lines_timed = add_pair_times(lines, schedule_date)
                txt = "\n".join(lines_timed if is_saturday(schedule_date) else lines)
                msg_text = (
                    f"{get_mood_emoji(lines)} Ваше расписание (группа <b>{typed_name}</b>):\n\n"
                    f"<b>{schedule_date}</b>\n\n{txt}"
                )
                schedule_keyboard = create_schedule_keyboard(schedule_date, "groups")
                img_bytes = global_schedules.last_groups_crop_cache.get(typed_name)
                if img_bytes:
                    photo = BufferedInputFile(img_bytes, filename="schedule.png")
                    if len(msg_text) <= 1024:
                        await message.answer_photo(photo=photo, caption=msg_text, parse_mode="HTML", reply_markup=schedule_keyboard)
                    else:
                        await message.answer_photo(photo=photo)
                        await message.answer(msg_text, parse_mode="HTML", reply_markup=schedule_keyboard)
                else:
                    await message.answer(msg_text, parse_mode="HTML", reply_markup=schedule_keyboard)
            else:
                await message.answer(
                    f"Расписание не найдено для группы <b>{typed_name}</b>.",
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
            return
        elif role == config.ROLE_TEACHER:
            lines_raw = None
            schedule_date = None

            # 1. Кэш преподавателей
            if global_schedules.last_teachers_df is not None:
                lines_raw = teacher_schedule.get_schedule_for_teacher(
                    global_schedules.last_teachers_df, typed_name
                )
                schedule_date = global_schedules.last_teachers_date

            # 2. Кэш групп (преподаватель из PDF групп)
            if not lines_raw and global_schedules.last_groups_df is not None:
                lines_raw = teacher_schedule.get_schedule_for_teacher(
                    global_schedules.last_groups_df, typed_name
                )
                schedule_date = global_schedules.last_groups_date

            # 3. Fallback: поиск файла (teachers → groups)
            if not lines_raw:
                df, sd = _find_latest_schedule("teachers")
                if df is not None:
                    lines_raw = teacher_schedule.get_schedule_for_teacher(df, typed_name)
                    schedule_date = sd
            if not lines_raw:
                df, sd = _find_latest_schedule("groups")
                if df is not None:
                    lines_raw = teacher_schedule.get_schedule_for_teacher(df, typed_name)
                    schedule_date = sd

            if lines_raw:
                txt = "\n".join(lines_raw)
                msg_text = (
                    f"📆 Ваше расписание (преподаватель <b>{typed_name}</b>):\n\n"
                    f"<b>{schedule_date}</b>\n\n{txt}"
                )
                schedule_keyboard = create_schedule_keyboard(schedule_date, "groups")
                await message.answer(msg_text, parse_mode="HTML", reply_markup=schedule_keyboard)
            else:
                await message.answer(
                    f"Расписание не найдено для преподавателя <b>{typed_name}</b>.",
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
            return
        return

    await message.answer("👋")
    # Если пользователь не найден, предлагаем выбрать роль
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Я студент 🎓", callback_data="role_student"),
            InlineKeyboardButton(text="Я преподаватель 👨‍🏫", callback_data="role_teacher")
        ]
    ])
    await message.answer("❔Рассылка какого расписания Вас интересует?\nСтуденческое или Преподавательское?", reply_markup=kb)
    await state.set_state(RegistrationStates.choosing_role)

# Обработка выбора роли через callback
async def process_role_callback(callback: types.CallbackQuery, state: FSMContext):
    data = callback.data
    if data == "role_student":
        await state.update_data(role=config.ROLE_STUDENT)
        await callback.message.answer("✏️Введите номер Вашей группы (например: 103-Д9-1ИНС).")
        await state.set_state(RegistrationStates.waiting_name)
    elif data == "role_teacher":
        await state.update_data(role=config.ROLE_TEACHER)
        await callback.message.answer("✏️Введите Вашу Фамилию и инициалы через пробел (например: Иванов И.И.).")
        await state.set_state(RegistrationStates.waiting_name)

# Обработка ввода номера группы или ФИО
async def process_name(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, введите текст (номер группы или ФИО).")
        return
    
    name_or_group = message.text.strip()
    data = await state.get_data()
    role = data.get("role", "")

    if role == config.ROLE_STUDENT:
        # Проверка формата номера группы (например: 103-Д9-1ИНС)
        group_pattern = r'^\d{2,3}-[А-ЯЁа-яёA-Za-z]{1,2}\d{1,2}-\d{1,2}[А-ЯЁа-яёA-Za-z]{3}$'
        if not re.match(group_pattern, name_or_group):
            await message.answer(
                "❌ Неверный формат номера группы. Пожалуйста, введите номер группы в формате, похожем на: <code>103-Д9-1ИНС</code>", 
                parse_mode=ParseMode.HTML
            )
            return  # Остаемся в том же состоянии, ожидая корректного ввода
    elif role == config.ROLE_TEACHER:
        # Проверка формата ФИО (например: Иванов И.И.)
        teacher_pattern = r'^[А-ЯЁA-Z][а-яёa-z-]+ [А-ЯЁA-Z]\.[А-ЯЁA-Z]\.?$'
        # Дополнительно разрешаем форматы:
        # - без точек (Иванов ИИ)
        # - с пробелом (Иванов И. И.)
        # - с подчеркиванием (Иванов_И.И.)
        teacher_pattern_alt1 = r'^[А-ЯЁA-Z][а-яёa-z-]+ [А-ЯЁA-Z]{2}$'
        teacher_pattern_alt2 = r'^[А-ЯЁA-Z][а-яёa-z-]+ [А-ЯЁA-Z]\. [А-ЯЁA-Z]\.?$'
        teacher_pattern_alt3 = r'^[А-ЯЁA-Z][а-яёa-z-]+_[А-ЯЁA-Z]\.[А-ЯЁA-Z]\.?$'
        
        if not (re.match(teacher_pattern, name_or_group) or 
                re.match(teacher_pattern_alt1, name_or_group) or
                re.match(teacher_pattern_alt2, name_or_group) or
                re.match(teacher_pattern_alt3, name_or_group)):
            await message.answer(
                "❌ Неверный формат ФИО. Пожалуйста, введите Фамилию и инициалы в формате, похожем на: <code>Иванов И.И.</code> или <code>Иванов_И.И.</code>", 
                parse_mode=ParseMode.HTML
            )
            return  # Остаемся в том же состоянии, ожидая корректного ввода
        
        # Нормализуем имя преподавателя (заменяем подчеркивание на пробел)
        name_or_group = name_or_group.replace('_', ' ')
        
        # Проверяем наличие преподавателя в списке
        try:
            all_teachers = parser_all.get_all_teachers()
            if name_or_group not in all_teachers:
                # Ищем похожие варианты
                similar_teachers = get_close_matches(name_or_group, all_teachers, n=3, cutoff=0.6)
                error_message = f"❌ Преподаватель '{name_or_group}' не найден в расписании.\n\n"
                if similar_teachers:
                    error_message += "Может быть Вы имели ввиду:\n"
                    for teacher in similar_teachers:
                        error_message += f"• {teacher}\n"
                error_message += "\nУкажите ФИО такое же, как было указано в таблице с расписанием. Фамилию и инициалы."
                await message.answer(error_message)
                return
        except Exception as e:
            print(f"Ошибка при проверке списка преподавателей: {str(e)}")
            # В случае ошибки пропускаем проверку и продолжаем регистрацию
        
        # Сохраняем ФИО и переходим к вопросу о классном руководстве
        await state.update_data(teacher_name=name_or_group)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Да ✅", callback_data="is_class_teacher_yes"),
                InlineKeyboardButton(text="Нет ❌", callback_data="is_class_teacher_no")
            ]
        ])
        await message.answer("👨‍🏫 Вы являетесь классным руководителем (куратором)?", reply_markup=kb)
        await state.set_state(RegistrationStates.is_class_teacher)
        return

    # Если это студент, сохраняем данные и завершаем регистрацию
    user_id = message.from_user.id
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, role TEXT, name_or_group TEXT, is_class_teacher INTEGER, class_group TEXT)")
    
    if role == config.ROLE_STUDENT:
        cur.execute("INSERT OR REPLACE INTO users (user_id, role, name_or_group, is_class_teacher, class_group) VALUES (?, ?, ?, NULL, NULL)",
                   (user_id, role, name_or_group.upper()))
    conn.commit()
    conn.close()
    
    await message.answer(f"✅ Регистрация завершена! Теперь Вам будет приходить студенческое расписание.\n\nСброс: /reset", reply_markup=keyboard)
    await state.clear()

# Обработчик ответа на вопрос о классном руководстве
async def process_class_teacher_callback(callback: types.CallbackQuery, state: FSMContext):
    data = callback.data
    user_id = callback.from_user.id
    
    if data == "is_class_teacher_yes":
        await callback.message.answer("✏️ Введите номер группы, которую Вы ведете (например: 103-Д9-1ИНС).")
        await state.set_state(RegistrationStates.waiting_group)
    else:
        # Если не классный руководитель, завершаем регистрацию
        data = await state.get_data()
        teacher_name = data.get("teacher_name", "")
        
        conn = sqlite3.connect(config.DB_PATH)
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO users (user_id, role, name_or_group, is_class_teacher, class_group) VALUES (?, ?, ?, 0, NULL)", 
                   (user_id, config.ROLE_TEACHER, teacher_name))
        conn.commit()
        conn.close()

        await callback.message.answer(f"✅ Регистрация завершена! Теперь Вам будет приходить преподавательское расписание.\n\nСброс: /reset")
        await state.clear()

# Обработчик ввода номера группы для классного руководителя
async def process_class_group(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, введите номер группы.")
        return
    
    group = message.text.strip().upper()
    # Проверка формата номера группы
    group_pattern = r'^\d{2,3}-[А-ЯЁа-яёA-Za-z]{1,2}\d{1,2}-\d{1,2}[А-ЯЁа-яёA-Za-z]{3}$'
    if not re.match(group_pattern, group):
        await message.answer(
            "❌ Неверный формат номера группы. Пожалуйста, введите номер группы в формате, похожем на: <code>103-Д9-1ИНС</code>", 
            parse_mode=ParseMode.HTML
        )
        return
    
    # Сохраняем данные и завершаем регистрацию
    user_id = message.from_user.id
    data = await state.get_data()
    teacher_name = data.get("teacher_name", "")
    
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO users (user_id, role, name_or_group, is_class_teacher, class_group) VALUES (?, ?, ?, 1, ?)", 
               (user_id, config.ROLE_TEACHER, teacher_name, group))
    conn.commit()
    conn.close()
    
    await message.answer(f"✅ Регистрация завершена! Теперь Вам будет приходить преподавательское расписание.\n\nСброс: /reset")
    await state.clear()