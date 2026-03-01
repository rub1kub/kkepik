# teacher.py

import pandas as pd
import numpy as np
import re
from collections import defaultdict

# ─── PDF structure ──────────────────────────────────────────────────────────
_PDF_DISC_COLS = [2, 6, 10, 14]   # 2 + gi*4  (sg1)
_PDF_SG2_COLS  = [4, 8, 12, 16]   # 2 + gi*4 + 2  (sg2)
_PDF_ALL_TEACHER_COLS = _PDF_DISC_COLS + _PDF_SG2_COLS  # все столбцы с преподавателями


def _is_pdf_schedule(df: pd.DataFrame) -> bool:
    """True если df — расписание из PDF (структура с группами в колонках 2,6,10,14)."""
    from schedules.group_schedule import is_group_name
    if df.shape[1] < 15:
        return False
    for col in _PDF_DISC_COLS:
        if col < df.shape[1]:
            val = df.iat[0, col]
            if isinstance(val, str) and is_group_name(val):
                return True
    return False

def normalize_teacher_name(name: str) -> str:
    """
    Нормализует имя преподавателя:
    - Убирает лишние пробелы
    - Убирает точки между инициалами
    - Убирает точку в конце
    - Оставляет только один пробел между фамилией и инициалами
    - Приводит к нижнему регистру для регистронезависимого сравнения
    """
    if not isinstance(name, str):
        return ""
    
    # Убираем лишние пробелы и точки
    parts = name.strip().split()
    if len(parts) < 2:
        return name.strip().lower()
    
    # Получаем фамилию
    surname = parts[0]
    
    # Собираем инициалы, убирая точки и пробелы
    initials = ''.join(p.replace('.', '') for p in parts[1:])
    
    return f"{surname} {initials}".lower()

def unify_known_phrases(text: str) -> str:
    """
    Не даём 'РАЗГОВОР О ВАЖНОМ' дробиться на три слова.
    """
    if not isinstance(text, str):
        return ""
    words = text.split()
    new_words = []
    i = 0
    while i < len(words):
        if i+2 < len(words):
            triple = [words[i].upper(), words[i+1].upper(), words[i+2].upper()]
            if triple == ["РАЗГОВОР", "О", "ВАЖНОМ"]:
                new_words.append("РАЗГОВОР О ВАЖНОМ")
                i += 3
                continue
        new_words.append(words[i])
        i += 1
    return " ".join(new_words)

def normalize_audience(val: str) -> str:
    """
    Если val выглядит как число (даже с .0), пробуем привести к int и вернуть строку.
    """
    val = val.strip()
    try:
        f = float(val)
        i = int(f)
        if float(i) == f:
            return str(i)
    except:
        pass
    return val

def is_audience_token(token: str) -> bool:
    """
    Считаем 'token' аудиторией, если есть число, или 'ТИР','ВЦ','ИЦ', 'с/з', 'спортзал', 'спорт.зал', 'с/з_3' и т.д.
    """
    token = token.strip().lower()
    if not token:
        return False
    token_norm = normalize_audience(token)
    if re.match(r'^\d+$', token_norm):
        return True
    pattern = r'(?i)(тир|вц|иц|с/з|спортзал|спорт\.зал|с/з_\d+|\d+)'
    return bool(re.search(pattern, token_norm))

def is_likely_teacher_name(text: str) -> bool:
    """
    Проверяем различные форматы имён преподавателей независимо от регистра.
    Поддерживаемые форматы:
    - "Иванов ИИ" -> "Иванов И.И."
    - "Иванов И.И." -> "Иванов И.И."
    - "Иванов_И.И." -> "Иванов И.И."
    - "Иванов И. И." -> "Иванов И.И."
    """
    text = text.strip().replace('_', ' ')
    if not text:
        return False
    
    # Нормализуем имя перед проверкой (приводим к нижнему регистру)
    normalized = normalize_teacher_name(text)
    parts = normalized.split()
    
    if len(parts) != 2:
        return False
    
    surname, initials = parts
    
    # Проверяем фамилию (должна начинаться с буквы)
    if not surname[0].isalpha():
        return False
    
    # Проверяем инициалы (должно быть 2 буквы, возможно с точками)
    # Убираем все точки и пробелы из инициалов
    clean_initials = re.sub(r'[.\s]', '', initials)
    if len(clean_initials) != 2 or not clean_initials.isalpha():
        return False
    
    return True

def parse_two_columns(df, row_idx: int, col_idx: int) -> tuple[str, str]:
    """
    Считываем (row_idx, col_idx) и (row_idx, col_idx+1).
    Если val2 — аудитория, возвращаем (val1, val2). Иначе склеиваем.
    """
    ncols = df.shape[1]

    if row_idx < 0 or row_idx >= df.shape[0] or (col_idx + 1) >= ncols:
        return ("", "")

    val1 = df.iat[row_idx, col_idx]
    val2 = df.iat[row_idx, col_idx+1]

    if pd.isna(val1):
        val1 = ""
    if pd.isna(val2):
        val2 = ""

    val1 = unify_known_phrases(str(val1).strip())
    val2 = unify_known_phrases(str(val2).strip())

    if is_audience_token(val2):
        aud = normalize_audience(val2)
        return (val1, aud)
    else:
        combined = (val1 + " " + val2).strip()
        return (combined, "")

def find_audience_columns(df) -> list[int]:
    """
    Находит все столбцы с аудиториями в таблице.
    Ищет по заголовку 'Ауд.' в 4-й строке (индекс 3).
    """
    audience_columns = []
    try:
        # Ищем в строке с заголовками (обычно 4-я строка)
        header_row = df.iloc[3]
        for col_idx, value in enumerate(header_row):
            if isinstance(value, str) and 'Ауд.' in value:
                audience_columns.append(col_idx)
        return audience_columns
    except Exception as e:
        return [7, 12, 17, 22]  # Возвращаем стандартные индексы столбцов с аудиториями

def get_audience(df, row_idx: int, group_col: int) -> str:
    """
    Получаем номер аудитории для конкретной группы.
    Ищет аудиторию в соответствующем столбце аудитории для группы.
    """
    try:
        # Находим все столбцы с аудиториями
        audience_cols = find_audience_columns(df)
        
        # Определяем, в каком блоке находится группа
        group_block = -1
        for i, col in enumerate([4, 9, 14, 19]):  # Столбцы с группами
            if col == group_col:
                group_block = i
                break
        
        if group_block == -1:
            return ""
            
        # Берем соответствующий столбец с аудиторией
        if group_block < len(audience_cols):
            aud_col = audience_cols[group_block]
            val = df.iat[row_idx, aud_col]
            
            if pd.isna(val):
                return ""
                
            result = str(val).strip()
            # Проверяем на пустую аудиторию или одинарную кавычку
            if result in ["'", ""] or result.isspace():
                return ""
                
            return result
            
    except Exception as e:
        pass
    return ""

def parse_schedule_for_teacher(df, row_t: int, col_t: int, teacher_name: str, is_combined: bool = False) -> list[str]:
    """
    Парсим расписание для преподавателя. col_t — это столбец, где найдено имя преподавателя.
    Учитываем два формата имени:
    1. С точками (приоритетный): "Ермолов И.А."
    2. Без точек: "Ермолов ИА"
    """
    normalized_teacher_name = normalize_teacher_name(teacher_name)
    
    schedule_lines = []
    pairs_dict = {}
    max_pair = 0
    
    i = row_t + 1
    end_row = min(row_t + 50, df.shape[0])
    found_next_teacher = False

    while i < end_row and not found_next_teacher:
        pair_val = df.iat[i, 0]
        if pd.isna(pair_val):
            pair_val = ""
        pair_val = str(pair_val).strip()
        
        if not pair_val:
            break

        try:
            current_pair = int(pair_val)
            max_pair = max(max_pair, current_pair)
        except ValueError:
            i += 2
            continue

        # Дисциплина col_t
        disc1, aud1 = parse_two_columns(df, i, col_t)
        
        # Получаем аудиторию для текущей группы
        room = get_audience(df, i, col_t)

        # Проверяем следующую строку на наличие другого преподавателя
        teacher_extra = ""
        if i + 1 < end_row:
            raw_val_next = df.iat[i+1, col_t]
            if isinstance(raw_val_next, str):
                teacher_extra = raw_val_next.strip()

            if teacher_extra and is_likely_teacher_name(teacher_extra):
                if normalize_teacher_name(teacher_extra) != normalized_teacher_name:
                    found_next_teacher = True

        # Проверяем текущее значение на имя преподавателя
        if disc1.strip() and is_likely_teacher_name(disc1.strip()):
            current_teacher_normalized = normalize_teacher_name(disc1.strip())
            if current_teacher_normalized != normalized_teacher_name:
                break
            i += 2
            continue

        # Формируем строку расписания
        if disc1.strip():
            line = f"▪️{pair_val} пара"
            line += f" – {disc1.strip()}"
            # Добавляем аудиторию, если она есть
            if room:
                line += f" – {room}"
            if teacher_extra and not is_likely_teacher_name(teacher_extra):
                line += f" – {teacher_extra}"
            pairs_dict[current_pair] = line

        i += 2

    # Если нашли хотя бы одну пару, формируем полное расписание
    if max_pair > 0:
        if is_combined:
            # Для совмещенного расписания выводим только непустые пары
            for pair_num in range(1, max_pair + 1):
                if pair_num in pairs_dict:
                    schedule_lines.append(pairs_dict[pair_num])
        else:
            # Для основного расписания выводим все пары
            for pair_num in range(1, max_pair + 1):
                if pair_num in pairs_dict:
                    schedule_lines.append(pairs_dict[pair_num])
                else:
                    schedule_lines.append(f"▪️{pair_num} пара – Нет")

    return schedule_lines

def get_schedule_for_teacher_in_groups(df: pd.DataFrame, teacher_name: str) -> list[str] | None:
    """
    Ищет расписание преподавателя в PDF расписании групп.
    Teacher rows: колонки 2+gi*4 (sg1) и 2+gi*4+2 (sg2) содержат ФИО.
    Дисциплина и аудитория берутся из предыдущей (disc) строки.
    """
    from schedules.group_schedule import is_group_name

    normalized = normalize_teacher_name(teacher_name)

    # Карта заголовков: {row: {gi: group_name}}
    header_rows: dict = {}
    for row in range(df.shape[0]):
        for gi, disc_col in enumerate(_PDF_DISC_COLS):
            if disc_col >= df.shape[1]:
                continue
            val = df.iat[row, disc_col]
            if isinstance(val, str) and is_group_name(val):
                header_rows.setdefault(row, {})[gi] = val

    sorted_hr = sorted(header_rows)

    def get_group(row: int, gi: int) -> str:
        for hr in reversed(sorted_hr):
            if hr <= row and gi in header_rows[hr]:
                return header_rows[hr][gi]
        return ""

    pairs_found = []  # (pair_int, group_name, disc_str, aud_str)

    for row in range(df.shape[0]):
        for tcol in _PDF_ALL_TEACHER_COLS:
            if tcol >= df.shape[1]:
                continue
            val = df.iat[row, tcol]
            if not isinstance(val, str):
                continue
            if not is_likely_teacher_name(val):
                continue
            if normalize_teacher_name(val) != normalized:
                continue

            # Определяем gi (индекс группы) и смещение sg
            # sg1 cols: 2,6,10,14 → gi = (col-2)//4
            # sg2 cols: 4,8,12,16 → gi = (col-4)//4
            if tcol in _PDF_DISC_COLS:
                gi = _PDF_DISC_COLS.index(tcol)
            else:
                gi = _PDF_SG2_COLS.index(tcol)

            # Номер пары (ffilled в col 0)
            pair_raw = df.iat[row, 0]
            if pair_raw is None or (isinstance(pair_raw, float) and pd.isna(pair_raw)):
                continue
            try:
                pair_int = int(str(pair_raw).strip())
            except ValueError:
                continue

            group_name = get_group(row, gi)

            # Дисциплина и аудитория — предыдущая строка, тот же столбец
            disc_str = ""
            aud_str = ""
            if row > 0:
                prev_disc = df.iat[row - 1, tcol]
                if prev_disc is not None and not (isinstance(prev_disc, float) and pd.isna(prev_disc)):
                    s = str(prev_disc).strip()
                    if s and not is_group_name(s) and not is_likely_teacher_name(s):
                        disc_str = s

                aud_col = tcol + 1
                if aud_col < df.shape[1]:
                    prev_aud = df.iat[row - 1, aud_col]
                    if prev_aud is not None and not (isinstance(prev_aud, float) and pd.isna(prev_aud)):
                        a = str(prev_aud).strip()
                        if a:
                            aud_str = normalize_audience(a)

            pairs_found.append((pair_int, group_name, disc_str, aud_str))

    if not pairs_found:
        return None

    by_pair: dict = defaultdict(list)
    max_pair = 0
    for pair_int, group_name, disc_str, aud_str in pairs_found:
        by_pair[pair_int].append((group_name, disc_str, aud_str))
        max_pair = max(max_pair, pair_int)

    lines = []
    for pair_int in range(1, max_pair + 1):
        if pair_int in by_pair:
            for group_name, disc_str, aud_str in by_pair[pair_int]:
                line = f"▪️{pair_int} пара"
                if group_name:
                    line += f" – {group_name}"
                if disc_str:
                    line += f" – {disc_str}"
                if aud_str:
                    line += f" – ауд. {aud_str}"
                lines.append(line)
        else:
            lines.append(f"▪️{pair_int} пара – Нет")

    # Убираем хвостовые "Нет"
    while lines and lines[-1].endswith("– Нет"):
        lines.pop()

    return lines if lines else None


def get_schedule_for_teacher(df, teacher_name: str) -> list[str] | None:
    """
    Ищет расписание преподавателя. Автоматически определяет формат DF (PDF или XLSX).
    """
    if _is_pdf_schedule(df):
        return get_schedule_for_teacher_in_groups(df, teacher_name)

    # ─── XLSX-логика ────────────────────────────────────────────────────────
    normalized_teacher_name = normalize_teacher_name(teacher_name)
    
    # Ищем все вхождения имени преподавателя
    coords = []
    for row in range(df.shape[0]):
        for col in range(df.shape[1]):
            val = df.iat[row, col]
            if isinstance(val, str) and is_likely_teacher_name(val):
                if normalize_teacher_name(val) == normalized_teacher_name:
                    coords.append((row, col))
    
    if not coords:
        return None
        
    # Сортируем координаты по приоритету формата имени
    # Приоритет отдаем формату с точками (например, "Ермолов И.А.")
    sorted_coords = []
    for row, col in coords:
        val = df.iat[row, col]
        if '.' in val:  # Формат с точками имеет приоритет
            sorted_coords.insert(0, (row, col))
        else:
            sorted_coords.append((row, col))
    
    # Собираем расписание из всех найденных координат
    all_schedule_lines = []
    has_priority_schedule = False
    has_combined_schedule = False
    
    for row_t, col_t in sorted_coords:
        val = df.iat[row_t, col_t]
        is_combined = '.' not in val  # Определяем, является ли это совмещенным расписанием
        
        lines = parse_schedule_for_teacher(df, row_t, col_t, teacher_name, is_combined)
        
        if not is_combined:  # Формат с точками
            all_schedule_lines.extend(lines)
            has_priority_schedule = True
        else:  # Формат без точек
            if lines:  # Если есть совмещенные пары
                has_combined_schedule = True
                if has_priority_schedule:  # Добавляем заголовок только если есть приоритетное расписание
                    all_schedule_lines.append("\nСовмещенные пары:")
                all_schedule_lines.extend(lines)
    
    return all_schedule_lines if all_schedule_lines else None