# group.py

import pandas as pd
import numpy as np
import re

def is_group_name(name: str) -> bool:
    """
    Проверяет, является ли строка номером группы.
    Поддерживаемые форматы:
    - 103-Д9-1ИНС (обычные группы)
    - 89-КД9-2ИСП (короткие группы)
    - 103-Д9-3ССА (группы 3 курса)
    - 56-Д9-4ССА (группы 4 курса)
    - 103-Д9-1ИНС-1 (группы с подгруппами)
    - 103-Д9-1ИНС-2
    """
    # Паттерн для номера группы:
    # ^\d{2,3} - начало с 2-3 цифр
    # -[А-Я]?К?Д9- - дефис, опционально буква, опционально К, Д9, дефис
    # [1-4][А-Я]{2,} - цифра от 1 до 4 и 2 или более букв
    # (-\d)?$ - опционально дефис и цифра в конце
    pattern = r'^\d{2,3}-[А-Я]?К?Д9-[1-4][А-Я]{2,}(-\d)?$'
    name = name.upper()  # Приводим к верхнему регистру перед проверкой
    return bool(re.match(pattern, name))

def unify_known_phrases(text: str) -> str:
    """
    Не даём 'РАЗГОВОР О ВАЖНОМ' дробиться на три слова.
    Если встречаем подряд ['РАЗГОВОР','О','ВАЖНОМ'], склеиваем в одну фразу.
    """
    if not isinstance(text, str):
        return ""
    words = text.split()
    new_words = []
    i = 0
    while i < len(words):
        if i + 2 < len(words):
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
    Считаем 'token' аудиторией, если:
      - это число,
      - или содержит (без учёта регистра) 'ТИР', 'ВЦ', 'ИЦ', 'с/з', 'спортзал', 'спорт.зал', 'с/з_3' или любую цифру.
    """
    token = token.strip().lower()
    if not token:
        return False

    token_norm = normalize_audience(token)
    if re.match(r'^\d+$', token_norm):
        return True

    # Паттерны для специальных аудиторий (спортзал, тир, актовый зал и т.д.)
    pattern = r'(?i)(тир|вц|иц|с/з|спортзал|спорт\.зал|акт\.зал|актзал|акт зал|с/з_\d+|\d+)'
    return bool(re.search(pattern, token_norm))

def parse_two_columns(df, row_idx: int, col_idx: int) -> tuple[str, str]:
    """
    Считываем две ячейки: (row_idx, col_idx) и (row_idx, col_idx+1).
    Если во второй ячейке — аудитория, возвращаем (discipline, audience).
    Иначе всё вместе — дисциплина.
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

def parse_schedule_for_group(df, row_g: int, col_g: int, group_name: str) -> list[str]:
    """
    Парсим расписание для группы. col_g — это столбец, где найдена группа.
    Теперь для всех пар от 1 до максимальной, если пары нет — добавляем строку '▪️N пара – Нет'.
    """
    pairs_dict = {}
    max_pair = 0
    i = row_g + 1
    end_row = min(row_g + 50, df.shape[0])

    while i < end_row:
        val_in_col = df.iat[i, col_g]

        if isinstance(val_in_col, str):
            # Если встретили другую группу
            if ("-Д9-" in val_in_col or "-КД9-" in val_in_col) and val_in_col != group_name:
                break

        pair_val = df.iat[i, 0]
        if pd.isna(pair_val):
            pair_val = ""
        pair_val = str(pair_val).strip()

        try:
            current_pair = int(pair_val)
            max_pair = max(max_pair, current_pair)
        except ValueError:
            i += 2
            continue

        # Подгруппа 1
        disc1, aud1 = parse_two_columns(df, i, col_g)

        # Преподаватель
        teacher1, aud1t = "", ""
        if i + 1 < end_row:
            teacher1, aud1t = parse_two_columns(df, i+1, col_g)

        # Подгруппа 2 (col_g+2)
        disc2, aud2 = "", ""
        teacher2, aud2t = "", ""
        if (col_g + 2) < df.shape[1]:
            disc2, aud2 = parse_two_columns(df, i, col_g+2)
            if i+1 < end_row:
                teacher2, aud2t = parse_two_columns(df, i+1, col_g+2)

        # Если disc2 — аудитория (например "АКТ.ЗАЛ"), а не дисциплина — переносим в aud2
        if disc2.strip() and is_audience_token(disc2.strip()) and not teacher2.strip():
            if not aud2.strip():
                aud2 = disc2.strip()
            disc2 = ""

        # ── Объединение подгрупп: одна дисциплина, разные преподаватели ──
        if (disc1.strip() and disc2.strip()
                and disc1.strip().upper() == disc2.strip().upper()):
            line = f"▪️{pair_val} пара – {disc1.strip()}"

            t1, t2 = teacher1.strip(), teacher2.strip()
            if t1 and t2 and t1 != t2:
                line += f" – {t1} / {t2}"
            elif t1:
                line += f" – {t1}"
            elif t2:
                line += f" – {t2}"

            auds = []
            for a in [aud1, aud2, aud1t, aud2t]:
                a_s = a.strip()
                if a_s and a_s != "0" and a_s not in auds:
                    auds.append(a_s)
            if auds:
                line += f" – {', '.join(auds)}"

            pairs_dict[current_pair] = line
            i += 2
            continue

        # ── Разные дисциплины или одна подгруппа ──

        # Если во второй подгруппе ничего нет, но aud2 есть — считаем ауд2 аудиторией для первой
        extra_audiences = []
        if not disc2.strip() and not teacher2.strip():
            if aud2.strip():
                extra_audiences.append(aud2)
            if aud2t.strip():
                extra_audiences.append(aud2t)

        # Формируем строку для первой подгруппы
        if disc1.strip():
            line1 = f"▪️{pair_val} пара – {disc1}"
            if teacher1.strip():
                line1 += f" – {teacher1}"
            if aud1.strip():
                line1 += f" – {aud1}"
            if aud1t.strip():
                line1 += f" – {aud1t}"
            # Добавляем «лишние» аудитории
            for ea in extra_audiences:
                line1 += f" – {ea}"
            pairs_dict[current_pair] = line1

        # Если во второй подгруппе есть что-то реальное
        if disc2.strip() or teacher2.strip():
            line2 = f"▪️{pair_val} пара – {disc2}"
            if teacher2.strip():
                line2 += f" – {teacher2}"
            if aud2.strip():
                line2 += f" – {aud2}"
            if aud2t.strip():
                line2 += f" – {aud2t}"
            # Если уже есть строка для этой пары (от первой подгруппы), объединяем
            if current_pair in pairs_dict:
                pairs_dict[current_pair] += f"\n{line2}"
            else:
                pairs_dict[current_pair] = line2

        i += 2

    # Формируем итоговый список: для всех пар от 1 до max_pair, если пары нет — 'Нет'
    schedule_lines = []
    for pair_num in range(1, max_pair + 1):
        if pair_num in pairs_dict:
            schedule_lines.append(pairs_dict[pair_num])
        else:
            schedule_lines.append(f"▪️{pair_num} пара – Нет")

    # Удаляем все подряд идущие 'Нет' в конце списка
    while schedule_lines and schedule_lines[-1].endswith('пара – Нет'):
        schedule_lines.pop()

    return schedule_lines

def get_schedule_for_group(df, group_name: str) -> list[str] | None:
    """
    Ищем ячейку (row_g, col_g), где group_name, парсим.
    """
    coords = np.where(df.values == group_name)
    if len(coords[0]) == 0:
        return None

    row_g = coords[0][-1]
    col_g = coords[1][-1]
    lines = parse_schedule_for_group(df, row_g, col_g, group_name)
    return lines