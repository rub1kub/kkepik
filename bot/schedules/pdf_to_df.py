# schedules/pdf_to_df.py
"""
Конвертер PDF-расписания в pandas DataFrame.

Используем посимвольное извлечение (character-level), потому что
pdfplumber.extract_tables() НЕ справляется с этим PDF:
текст дисциплин перетекает за границы ячеек и перемешивается
с соседними группами.

Алгоритм:
1. Находим позиции групп (названия) — определяем блоки по 4 группы
2. Находим номера пар (x < 52, цифра) — якоря строк
3. Для каждой пары выделяем 3 визуальных уровня:
   - дисциплина: top ≈ pair_top - 0.5
   - аудитория:  top ≈ pair_top  (совпадает с номером пары)
   - преподаватель: top ≈ pair_top + 8..10
4. На каждом уровне text-run привязываем к группе по x-start
"""

import re
import pdfplumber
import pandas as pd
from typing import Optional, List, Dict, Tuple
from collections import defaultdict


# ──────────────────────────────────────────────
# Утилиты
# ──────────────────────────────────────────────

def _normalize_dashes(text: str) -> str:
    return text.replace("\u2013", "-").replace("\u2014", "-").replace("\u2011", "-")


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip("_")  # PDF иногда добавляет подчёркивания
    return _normalize_dashes(text) if text else ""


# ──────────────────────────────────────────────
# Извлечение структуры
# ──────────────────────────────────────────────

def _find_group_positions(page) -> List[Dict]:
    """Находит названия групп и их (x, y)."""
    words = page.extract_words(x_tolerance=1, y_tolerance=1)
    groups = []
    for w in words:
        t = _normalize_dashes(w["text"])
        if ("-Д9-" in t or "-КД9-" in t) and re.match(r"^\d{2,3}-", t):
            groups.append({"name": t, "x": w["x0"], "y": w["top"]})
    return groups


def _find_pair_numbers(chars: list, max_x: float = 52) -> List[Dict]:
    """
    Находит номера пар: одиночные цифры при x < max_x.
    В PDF бывают 2 колонки цифр (пары и уроки) — берём только ЛЕВУЮ (пары).
    Возвращает [{num: str, top: float}] отсортированные по top.
    """
    candidates = []
    for c in chars:
        if c["x0"] < max_x and c["text"].strip().isdigit() and len(c["text"].strip()) == 1:
            candidates.append({"num": c["text"].strip(), "top": c["top"], "x": c["x0"]})
    if not candidates:
        return []
    # Берём только левую колонку цифр (tolerance 5pt)
    leftmost_x = min(c["x"] for c in candidates)
    pairs = [p for p in candidates if p["x"] - leftmost_x < 5]
    pairs.sort(key=lambda p: p["top"])
    return [{"num": p["num"], "top": p["top"]} for p in pairs]


# ──────────────────────────────────────────────
# Text-run extraction
# ──────────────────────────────────────────────

def _chars_to_runs(chars: list, gap: float = 12.0,
                    group_starts: Optional[List[float]] = None) -> List[Dict]:
    """
    Группирует символы в text-run'ы (по x-близости + смена шрифта в content-stream).
    Разделяет подгруппы, у которых дисциплина и аудитория идут разными шрифтами.

    Если передан group_starts, дополнительно разрезает run'ы на границах групп —
    когда текст одной группы «перетекает» в столбец следующей.

    Возвращает [{text, x_start}].
    """
    if not chars:
        return []

    runs = []
    current = [chars[0]]
    for c in chars[1:]:
        prev_x = current[-1]["x0"]
        curr_x = c["x0"]
        prev_font = current[-1].get("fontname", "")
        curr_font = c.get("fontname", "")
        prev_text = current[-1].get("text", "")
        curr_text = c.get("text", "")
        # Новый run: большой разрыв вперёд, откат назад (≥1.5pt), ИЛИ смена шрифта
        split = (curr_x - prev_x > gap or
                 curr_x < prev_x - 1.5 or
                 (prev_font and curr_font and prev_font != curr_font))
        # Split: точка + backward uppercase (граница между ФИО преподавателей)
        if not split and prev_text == "." and curr_text.isupper() and curr_x < prev_x:
            split = True
        # Split: lowercase→UPPERCASE (склеенные дисциплины "проектИстория", "86-бПрактика")
        if not split and prev_text.islower() and curr_text.isupper():
            split = True
        if split:
            runs.append(current)
            current = []
        current.append(c)
    if current:
        runs.append(current)

    # Разрезаем run'ы на границах групп, но ТОЛЬКО если на границе есть
    # реальный x-gap (≥ min_boundary_gap). Это позволяет тексту, который
    # намеренно перетекает в соседний столбец, оставаться цельным.
    if group_starts and len(group_starts) > 1:
        min_boundary_gap = 7.0  # минимальный gap для разрезки на границе
        split_runs = []
        for run in runs:
            sorted_run = sorted(run, key=lambda c: c["x0"])
            first_gi = _assign_to_group(sorted_run[0]["x0"], group_starts)
            last_gi = _assign_to_group(sorted_run[-1]["x0"], group_starts)
            if first_gi == last_gi or first_gi < 0:
                split_runs.append(run)
            else:
                # Run пересекает границу — ищем точки разрезки
                # Разрез делаем только там, где x-gap совпадает с границей группы
                sub = []
                for i, ch in enumerate(sorted_run):
                    sub.append(ch)
                    if i < len(sorted_run) - 1:
                        next_ch = sorted_run[i + 1]
                        x_gap = next_ch["x0"] - ch.get("x1", ch["x0"])
                        if x_gap >= min_boundary_gap:
                            gi_cur = _assign_to_group(ch["x0"], group_starts)
                            gi_next = _assign_to_group(next_ch["x0"], group_starts)
                            if gi_cur != gi_next and gi_next > gi_cur:
                                split_runs.append(sub)
                                sub = []
                if sub:
                    split_runs.append(sub)
        runs = split_runs

    result = []
    for run in runs:
        sorted_run = sorted(run, key=lambda c: c["x0"])
        text = _clean("".join(c["text"] for c in sorted_run))
        if text:
            result.append({"text": text, "x_start": sorted_run[0]["x0"]})
    return result


def _assign_to_group(x_start: float, group_starts: List[float], tolerance: float = 10.0) -> int:
    """Определяет индекс группы (0..N-1) по x_start. Возвращает -1 если не подходит."""
    best = -1
    for i, gx in enumerate(group_starts):
        if x_start >= gx - tolerance:
            best = i
    return best



def _looks_like_aud(text: str) -> bool:
    """
    Определяет, является ли текст номером аудитории.
    Аудитория: числа, "ВЦ-N", "с/з_N", "ТИР" и т.п.
    """
    text = text.strip()
    if not text or len(text) > 10:
        return False
    if re.match(r"^\d{1,4}$", text):
        return True
    # Обрабатываем float-значения вроде ".0", "93.0" → аудитория
    try:
        f = float(text)
        i = int(f)
        if float(i) == f and 0 <= i <= 9999:
            return True
    except (ValueError, OverflowError):
        pass
    if re.match(r"^[ВвЦцИиТтЕеРр/зс_\d\.\-]+$", text):
        return True
    # "0_0", "0-0", "0/0" — дистанционно / без аудитории
    if re.match(r"^\d[_\-/]\d$", text):
        return True
    # "86-б", "89а" — число + необязательный дефис + одна буква
    if re.match(r"^\d{1,4}[- ]?[а-яА-Яa-zA-Z]$", text):
        return True
    return False


def _split_merged_aud_disc(runs: List[Dict]) -> List[Dict]:
    """
    Разбивает run'ы, в которых номер аудитории склеился с названием дисциплины.

    В расписаниях практик PDF рендерит аудиторию (напр. "95") и дисциплину
    ("Практика по...") в одном text-потоке с маленьким зазором (~9pt).
    _chars_to_runs объединяет их в один run: "95Практика по...".

    Эта функция разделяет такие run'ы на два: "95" + "Практика по...".
    """
    result = []
    for run in runs:
        text = run["text"]
        # Паттерн: aud (цифры, возможно через . _ - 0) + кириллическая буква
        # Покрывает: "95Практика", "0.0Практика", "0_0Практика", "0-0Практика"
        m = re.match(r"^(\d[\d._\-/]*\d?)([А-Яа-яЁё].{3,})", text)
        if m:
            aud_part = m.group(1)
            disc_part = m.group(2)
            result.append({"text": aud_part, "x_start": run["x_start"]})
            # Оценка x_start для дисциплины: ~6pt на цифру + зазор ~9pt
            est_x = run["x_start"] + len(aud_part) * 6 + 9
            result.append({"text": disc_part, "x_start": est_x})
            continue
        # Паттерн: ВЦ-N или аналог + кириллическая буква
        m = re.match(r"^((?:ВЦ|ИЦ|ТИР|с/з)-?\d*)[_\s]*([А-Яа-яЁё].{3,})", text, re.IGNORECASE)
        if m:
            aud_part = m.group(1)
            disc_part = m.group(2)
            result.append({"text": aud_part, "x_start": run["x_start"]})
            est_x = run["x_start"] + len(aud_part) * 6 + 9
            result.append({"text": disc_part, "x_start": est_x})
            continue
        result.append(run)
    return result


# ──────────────────────────────────────────────
# Извлечение блока
# ──────────────────────────────────────────────

def _extract_block(page_chars: list, block_groups: List[Dict],
                   y_start: float, y_end: float) -> List[list]:
    """
    Извлекает данные блока (до 4 групп) и возвращает строки DataFrame.

    Символы сначала разделяются по группам через midpoint-границы,
    затем для каждой группы строятся text-run'ы через _chars_to_runs.

    Disc/aud классификация — по содержимому (_looks_like_aud).
    Subgroups (sg1/sg2) определяются расстоянием от начала группы.
    """
    bg = sorted(block_groups, key=lambda g: g["x"])
    group_starts = [g["x"] for g in bg]
    n_groups = len(bg)
    n_cols = 2 + n_groups * 4

    # Символы в области блока
    block_chars = [c for c in page_chars if y_start <= c["top"] <= y_end]
    if not block_chars:
        return []

    # Номера пар в этом блоке (порог x определяем по первой группе)
    pair_num_max_x = group_starts[0] - 8 if group_starts else 52
    pair_nums = _find_pair_numbers(block_chars, max_x=pair_num_max_x)
    if not pair_nums:
        return []

    # ── Собираем строки ──

    output = []

    # Header с названиями групп
    header = [None] * n_cols
    for gi, g in enumerate(bg):
        header[2 + gi * 4] = g["name"]
    output.append(header)

    # Для каждой пары
    for pi, pn in enumerate(pair_nums):
        pair_top = pn["top"]

        # Определяем вертикальный диапазон для этой пары
        if pi + 1 < len(pair_nums):
            pair_y_end = pair_nums[pi + 1]["top"] - 1
        else:
            pair_y_end = y_end

        # Собираем символы этой пары (исключая x левее пар-колонки)
        content_min_x = group_starts[0] - 10 if group_starts else 62
        pair_chars = [c for c in block_chars
                      if pair_top - 2 <= c["top"] <= pair_y_end and c["x0"] >= content_min_x]

        if not pair_chars:
            # Пустая пара
            disc_row = [None] * n_cols
            disc_row[0] = pn["num"]
            output.append(disc_row)
            output.append([None] * n_cols)
            continue

        # Группируем по top (с tolerance 0.3pt)
        top_buckets = []
        for c in sorted(pair_chars, key=lambda c: c["top"]):
            placed = False
            for bucket in top_buckets:
                if abs(c["top"] - bucket[0]) <= 0.3:
                    bucket[1].append(c)
                    placed = True
                    break
            if not placed:
                top_buckets.append((c["top"], [c]))

        # Классифицируем y-уровни: content (disc+aud) vs teacher
        content_chars = []
        teacher_chars = []

        for bucket_top, bucket_chars in top_buckets:
            offset = bucket_top - pair_top
            if -2 <= offset <= 7:
                content_chars.extend(bucket_chars)
            elif 7 < offset < 14:
                teacher_chars.extend(bucket_chars)

        # ── Disc/Aud: run'ы строим в порядке content-stream, разрезаем на границах групп ──

        disc_per_group = defaultdict(list)     # gi -> [(dist, text)]
        aud_per_group = defaultdict(list)

        content_runs = _chars_to_runs(content_chars, group_starts=group_starts)
        content_runs = _split_merged_aud_disc(content_runs)
        for run in content_runs:
            gi = _assign_to_group(run["x_start"], group_starts)
            if gi >= 0:
                d = run["x_start"] - group_starts[gi]
                if _looks_like_aud(run["text"]):
                    aud_per_group[gi].append((d, run["text"]))
                else:
                    disc_per_group[gi].append((d, run["text"]))

        # ── Teacher: run'ы в порядке content-stream (backward-x разделяет sg1/sg2) ──

        teacher_per_group = defaultdict(list)
        teacher_runs = _chars_to_runs(teacher_chars, group_starts=group_starts)
        for run in teacher_runs:
            gi = _assign_to_group(run["x_start"], group_starts)
            if gi >= 0:
                d = run["x_start"] - group_starts[gi]
                teacher_per_group[gi].append((d, run["text"]))

        # Фильтр overflow: disc начинающийся с lowercase — мусор из соседней группы
        for gi in list(disc_per_group.keys()):
            entries = disc_per_group[gi]
            filtered = [(d, t) for d, t in entries if not t or not t[0].islower()]
            disc_per_group[gi] = filtered if filtered else []

        # Фильтр мусора: disc из одной пунктуации (апострофы, точки и т.п.)
        for gi in list(disc_per_group.keys()):
            entries = disc_per_group[gi]
            filtered = [(d, t) for d, t in entries if t and any(c.isalnum() for c in t) and len(t) > 1]
            disc_per_group[gi] = filtered if filtered else []

        # Фильтр overflow: teacher начинающийся с "." или lowercase — фрагмент ФИО из соседней группы
        for gi in list(teacher_per_group.keys()):
            entries = teacher_per_group[gi]
            filtered = [(d, t) for d, t in entries if t and t[0].isalpha() and t[0].isupper()]
            teacher_per_group[gi] = filtered if filtered else []

        # Фильтр: teacher = число (аудитория, попавшая в teacher y-зону)
        for gi in list(teacher_per_group.keys()):
            entries = teacher_per_group[gi]
            filtered = [(d, t) for d, t in entries if t and not re.match(r"^\d+$", t)]
            teacher_per_group[gi] = filtered if filtered else []

        # Сортируем по расстоянию от начала группы
        for gi in disc_per_group:
            disc_per_group[gi].sort()
        for gi in aud_per_group:
            aud_per_group[gi].sort()
        for gi in teacher_per_group:
            teacher_per_group[gi].sort()

        # Строим disc row (sg1 + sg2)
        disc_row = [None] * n_cols
        disc_row[0] = pn["num"]
        for gi in range(n_groups):
            d = disc_per_group.get(gi, [])
            a = aud_per_group.get(gi, [])
            if d:
                disc_row[2 + gi * 4] = d[0][1]           # sg1 disc
            if len(d) >= 2:
                disc_row[2 + gi * 4 + 2] = d[1][1]       # sg2 disc
            if a:
                disc_row[2 + gi * 4 + 1] = a[0][1]       # sg1 aud
            if len(a) >= 2:
                disc_row[2 + gi * 4 + 3] = a[1][1]       # sg2 aud
        output.append(disc_row)

        # Строим teacher row (sg1 + sg2)
        teacher_row = [None] * n_cols
        for gi in range(n_groups):
            t = teacher_per_group.get(gi, [])
            if t:
                teacher_row[2 + gi * 4] = t[0][1]        # sg1 teacher
            if len(t) >= 2:
                teacher_row[2 + gi * 4 + 2] = t[1][1]    # sg2 teacher
        output.append(teacher_row)

    return output


# ──────────────────────────────────────────────
# Публичные функции
# ──────────────────────────────────────────────

def pdf_to_dataframe(file_path: str) -> Optional[pd.DataFrame]:
    """
    Конвертирует PDF-файл расписания в pandas DataFrame.
    """
    import logging
    logger = logging.getLogger(__name__)

    all_rows = []

    with pdfplumber.open(file_path) as pdf:
        logger.info(f"pdf_to_df: {file_path}, pages={len(pdf.pages)}")
        for page in pdf.pages:
            groups = _find_group_positions(page)
            if not groups:
                logger.warning(f"pdf_to_df: page {page.page_number} — групп не найдено "
                               f"(chars={len(page.chars)}, words={len(page.extract_words())})")
                continue
            logger.info(f"pdf_to_df: page {page.page_number} — {len(groups)} групп")

            page_chars = page.chars

            # Блоки: группы с одинаковым y (кластеризация с tolerance 3pt)
            blocks = defaultdict(list)
            sorted_groups = sorted(groups, key=lambda g: g["y"])
            cluster_y = None
            for g in sorted_groups:
                if cluster_y is None or abs(g["y"] - cluster_y) > 3:
                    cluster_y = g["y"]
                blocks[cluster_y].append(g)

            sorted_ys = sorted(blocks.keys())

            for bi, by in enumerate(sorted_ys):
                block_groups = blocks[by]
                y_start = by + 3  # Чуть ниже строки с названием группы
                if bi + 1 < len(sorted_ys):
                    y_end = sorted_ys[bi + 1] - 3
                else:
                    y_end = page.height - 5

                rows = _extract_block(page_chars, block_groups, y_start, y_end)
                all_rows.extend(rows)

    if not all_rows:
        logger.warning(f"pdf_to_df: all_rows пуст — ни одна группа не распознана в {file_path}")
        return None

    max_cols = max(len(r) for r in all_rows)
    all_rows = [r + [None] * (max_cols - len(r)) for r in all_rows]

    df = pd.DataFrame(all_rows)

    # Forward-fill номеров пар
    if 0 in df.columns:
        result = []
        last = None
        for val in df[0].tolist():
            if val is not None and re.match(r"^\d+$", str(val).strip()):
                last = val
            result.append(last)
        df[0] = result

    return df


def extract_date_from_pdf_content(file_path: str) -> Optional[str]:
    """Извлекает дату из текста первой страницы PDF."""
    try:
        with pdfplumber.open(file_path) as pdf:
            if pdf.pages:
                text = pdf.pages[0].extract_text() or ""
                match = re.search(r"\b(\d{1,2}\.\d{1,2}\.\d{4})\b", text)
                if match:
                    return match.group(1)
    except Exception:
        pass
    return None
