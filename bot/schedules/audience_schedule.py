# audience_schedule.py

import pandas as pd
import numpy as np
import re

# ─── PDF structure ──────────────────────────────────────────────────────────
_PDF_DISC_COLS = [2, 6, 10, 14]   # 2 + gi*4  (sg1)
_PDF_AUD_COLS  = [3, 7, 11, 15]   # 2 + gi*4 + 1  (sg1)
_PDF_SG2_DISC_COLS = [4, 8, 12, 16]   # 2 + gi*4 + 2  (sg2)
_PDF_SG2_AUD_COLS  = [5, 9, 13, 17]   # 2 + gi*4 + 3  (sg2)
# Все пары (aud_col, disc_col, teacher_col) для поиска: (aud, disc, teacher)
_PDF_AUD_SEARCH = list(zip(_PDF_AUD_COLS, _PDF_DISC_COLS, _PDF_DISC_COLS)) + \
                  list(zip(_PDF_SG2_AUD_COLS, _PDF_SG2_DISC_COLS, _PDF_SG2_DISC_COLS))


def normalize_audience(val: str) -> str:
    val = val.strip()
    try:
        f = float(val)
        i = int(f)
        if float(i) == f:
            return str(i)
    except Exception:
        pass
    return val


def is_audience_token(token: str) -> bool:
    token = token.strip().lower()
    if not token:
        return False
    token_norm = normalize_audience(token)
    if re.match(r'^\d+$', token_norm):
        return True
    pattern = r'(?i)(тир|вц|иц|с/з|спортзал|спорт\.зал|с/з_\d+|\d+)'
    return bool(re.search(pattern, token_norm))


def _is_pdf_schedule(df: pd.DataFrame) -> bool:
    """True если df — расписание из PDF (структура 18 колонок с группами)."""
    from schedules.group_schedule import is_group_name
    if df.shape[1] < 15:
        return False
    for col in _PDF_DISC_COLS:
        if col < df.shape[1]:
            val = df.iat[0, col]
            if isinstance(val, str) and is_group_name(val):
                return True
    return False


# ─── PDF реализация ─────────────────────────────────────────────────────────

def _get_schedule_for_audience_pdf(df: pd.DataFrame, audience_number: str) -> list[str] | None:
    from schedules.group_schedule import is_group_name
    from schedules.teacher_schedule import is_likely_teacher_name

    normalized = normalize_audience(audience_number)

    # Карта заголовков групп: {row: {gi: group_name}}
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

    pairs_found = []  # (pair_int, group_name, disc_str, teacher_str)

    for row in range(df.shape[0]):
        for aud_col, disc_col, teacher_col in _PDF_AUD_SEARCH:
            if aud_col >= df.shape[1]:
                continue
            aud_val = df.iat[row, aud_col]
            if aud_val is None:
                continue
            if isinstance(aud_val, float) and pd.isna(aud_val):
                continue
            aud_str = str(aud_val).strip()
            if not aud_str or normalize_audience(aud_str) != normalized:
                continue

            # Номер пары (ffilled в col 0)
            pair_raw = df.iat[row, 0]
            if pair_raw is None or (isinstance(pair_raw, float) and pd.isna(pair_raw)):
                continue
            try:
                pair_int = int(str(pair_raw).strip())
            except ValueError:
                continue

            # gi определяем по aud_col
            if aud_col in _PDF_AUD_COLS:
                gi = _PDF_AUD_COLS.index(aud_col)
            else:
                gi = _PDF_SG2_AUD_COLS.index(aud_col)

            disc_raw = df.iat[row, disc_col] if disc_col < df.shape[1] else None
            disc_str = (
                str(disc_raw).strip()
                if disc_raw is not None and not (isinstance(disc_raw, float) and pd.isna(disc_raw))
                else ""
            )
            # Пропускаем строки-заголовки
            if is_group_name(disc_str):
                continue

            group_name = get_group(row, gi)

            # Преподаватель — следующая строка, тот же teacher_col
            teacher_str = ""
            if row + 1 < df.shape[0] and teacher_col < df.shape[1]:
                t_raw = df.iat[row + 1, teacher_col]
                if t_raw is not None and not (isinstance(t_raw, float) and pd.isna(t_raw)):
                    t_s = str(t_raw).strip()
                    if is_likely_teacher_name(t_s):
                        teacher_str = t_s

            pairs_found.append((pair_int, group_name, disc_str, teacher_str))

    if not pairs_found:
        return None

    pairs_found.sort(key=lambda x: x[0])

    lines = []
    for pair_int, group_name, disc_str, teacher_str in pairs_found:
        line = f"▪️{pair_int} пара"
        if group_name:
            line += f" – {group_name}"
        if disc_str:
            line += f" – {disc_str}"
        if teacher_str:
            line += f" – {teacher_str}"
        lines.append(line)

    return lines


# ─── XLSX реализация (сохранена без изменений) ──────────────────────────────

def find_audience_columns(df) -> list[int]:
    audience_columns = []
    try:
        header_row = df.iloc[3]
        for col_idx, value in enumerate(header_row):
            if isinstance(value, str) and 'Ауд.' in value:
                audience_columns.append(col_idx)
        return audience_columns
    except Exception:
        return [7, 12, 17, 22]


def _get_schedule_for_audience_xlsx(df: pd.DataFrame, audience_number: str) -> list[str] | None:
    normalized_audience = normalize_audience(audience_number)
    audience_cols = find_audience_columns(df)
    discipline_cols = [4, 9, 14, 19]

    pairs_dict = {}
    max_pair = 0

    for row in range(df.shape[0]):
        pair_val = df.iat[row, 0]
        if pd.isna(pair_val):
            pair_val = ""
        pair_val = str(pair_val).strip()
        if not pair_val:
            continue
        try:
            current_pair = int(pair_val)
            max_pair = max(max_pair, current_pair)
        except ValueError:
            continue

        for i, aud_col in enumerate(audience_cols):
            if aud_col >= df.shape[1]:
                continue
            aud_val = df.iat[row, aud_col]
            if pd.isna(aud_val):
                aud_val = ""
            aud_val = str(aud_val).strip()
            if normalize_audience(aud_val) != normalized_audience:
                continue

            if i < len(discipline_cols):
                disc_col = discipline_cols[i]
                if disc_col < df.shape[1]:
                    disc_val = df.iat[row, disc_col]
                    if pd.isna(disc_val):
                        disc_val = ""
                    disc_val = str(disc_val).strip()

                    group_val = ""
                    for search_row in range(max(0, row - 10), row):
                        group_candidate = df.iat[search_row, disc_col]
                        if pd.notna(group_candidate):
                            group_str = str(group_candidate).strip()
                            if '-' in group_str and 'Д9' in group_str:
                                group_val = group_str
                                break

                    if disc_val:
                        line = f"▪️{pair_val} пара – {group_val} – {disc_val}"
                        if row + 1 < df.shape[0]:
                            teacher_val = df.iat[row + 1, disc_col]
                            if pd.isna(teacher_val):
                                teacher_val = ""
                            teacher_val = str(teacher_val).strip()
                            if teacher_val and len(teacher_val.split()) >= 2:
                                parts = teacher_val.split()
                                if len(parts) >= 2 and parts[0][0].isupper() and parts[1][0].isupper():
                                    line += f" – {teacher_val}"
                        pairs_dict[current_pair] = line

    schedule_lines = []
    if max_pair > 0:
        for pair_num in range(1, max_pair + 1):
            if pair_num in pairs_dict:
                schedule_lines.append(pairs_dict[pair_num])
            else:
                schedule_lines.append(f"▪️{pair_num} пара – Нет")

    return schedule_lines if schedule_lines else None


# ─── Публичный API ──────────────────────────────────────────────────────────

def get_schedule_for_audience(df, audience_number: str) -> list[str] | None:
    """Ищет все занятия в указанной аудитории. Поддерживает PDF и XLSX."""
    if _is_pdf_schedule(df):
        return _get_schedule_for_audience_pdf(df, audience_number)
    return _get_schedule_for_audience_xlsx(df, audience_number)
