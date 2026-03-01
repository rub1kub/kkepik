# schedules/schedule_image.py
"""
Генерация PNG-карточки с расписанием группы для отправки в Telegram.
"""

import io
import os
import re

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    _PIL_OK = True
except ImportError:
    _PIL_OK = False


# ─── Пути к шрифтам ─────────────────────────────────────────────────────────

_FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fonts")


def _font(size: int, weight: str = "Regular"):
    """Загружает шрифт Montserrat нужного начертания."""
    # Montserrat из локальной папки fonts/
    local = os.path.join(_FONTS_DIR, f"Montserrat-{weight}.ttf")
    try:
        return ImageFont.truetype(local, size)
    except (OSError, IOError):
        pass
    # Fallback на системные шрифты
    fallbacks = {
        "Bold":     ["C:/Windows/Fonts/arialbd.ttf",
                     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
        "SemiBold": ["C:/Windows/Fonts/arialbd.ttf",
                     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
        "Medium":   ["C:/Windows/Fonts/arial.ttf",
                     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
        "Regular":  ["C:/Windows/Fonts/arial.ttf",
                     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    }
    for p in fallbacks.get(weight, fallbacks["Regular"]):
        try:
            return ImageFont.truetype(p, size)
        except (OSError, IOError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


# ─── Цветовая схема ─────────────────────────────────────────────────────────

_BG          = (248, 249, 252)   # фон карточки (слегка голубоватый)
_HEADER_BG   = (37, 99, 235)    # синий заголовок
_HEADER_BG2  = (29, 78, 216)    # градиент (нижняя часть)
_WHITE       = (255, 255, 255)
_ROW_WHITE   = (255, 255, 255)
_ROW_ALT     = (243, 244, 246)  # серый чередования
_TEXT_PRIMARY = (17, 24, 39)    # основной текст
_TEXT_SECOND  = (107, 114, 128) # преподаватель
_TEXT_MUTED   = (156, 163, 175) # «Нет»
_ACCENT       = (37, 99, 235)   # номер пары
_AUD_BG       = (239, 246, 255) # фон бейджа аудитории
_AUD_TEXT     = (37, 99, 235)   # текст бейджа аудитории
_DIVIDER      = (229, 231, 235) # разделитель строк
_CARD_SHADOW  = (0, 0, 0, 25)  # тень
_COL_DIVIDER  = (209, 213, 219) # вертикальный разделитель колонок


# ─── Парсинг строк расписания ───────────────────────────────────────────────

def _is_aud_like(val: str) -> bool:
    val = val.strip()
    if not val:
        return False
    # Comma-separated audiences like "95, 93"
    if ',' in val:
        parts = [p.strip() for p in val.split(',')]
        return len(parts) > 0 and all(_is_aud_like(p) for p in parts if p)
    try:
        f = float(val)
        i = int(f)
        if float(i) == f:
            val = str(i)
    except Exception:
        pass
    if re.match(r'^\d{1,4}$', val):
        return True
    return bool(re.match(
        r'(?i)^(тир|вц|иц|с/з|спортзал|спорт\.зал|акт\.зал|актзал|акт зал|с/з_?\d*|вц-?\d*)$', val
    ))


def _parse_line(line: str) -> dict:
    line = line.replace("▪️", "").strip()
    parts = [p.strip() for p in line.split(" – ")]
    r = {"pair": "", "time": "", "disc": "", "teacher": "", "aud": ""}
    if not parts:
        return r

    # Извлекаем номер пары и время: "1 пара (8:45–10:05)" или "1 пара"
    m = re.match(r"(\d+)\s*пара\s*(?:\(([^)]+)\))?", parts[0])
    if m:
        r["pair"] = m.group(1)
        r["time"] = m.group(2) or ""
    else:
        r["pair"] = parts[0]

    if len(parts) >= 2:
        r["disc"] = parts[1]
    auds = []
    for p in parts[2:]:
        if _is_aud_like(p):
            auds.append(p)
        elif not r["teacher"]:
            r["teacher"] = p
    r["aud"] = ", ".join(auds)
    return r


# ─── Перенос текста ─────────────────────────────────────────────────────────

def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    if not text:
        return []
    if draw.textbbox((0, 0), text, font=font)[2] <= max_w:
        return [text]
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [text]


# ─── Рисование скруглённого прямоугольника ───────────────────────────────────

def _rounded_rect(draw, xy, radius, fill):
    """Рисует прямоугольник со скруглёнными углами."""
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


# ─── Группировка по парам (для двухколоночного режима) ────────────────────────

def _group_by_pair(parsed: list[dict]) -> list[list[dict]]:
    """
    Группирует последовательные строки с одинаковым номером пары.
    Возвращает список групп (1 или 2 элемента в каждой группе = подгруппы).
    """
    if not parsed:
        return []

    groups = []
    current_group = [parsed[0]]

    for p in parsed[1:]:
        if p["pair"] == current_group[0]["pair"] and len(current_group) < 2:
            current_group.append(p)
        else:
            groups.append(current_group)
            current_group = [p]

    groups.append(current_group)
    return groups


# ─── Генерация изображения ──────────────────────────────────────────────────

def generate_schedule_image(
    lines: list[str], group_name: str, date_str: str
) -> bytes | None:
    """
    Рисует PNG-карточку расписания группы.

    Returns:
        PNG-изображение (bytes) или None при ошибке.
    """
    if not _PIL_OK or not lines:
        return None

    try:
        # Развернуть подгруппы
        flat: list[str] = []
        for l in lines:
            flat.extend(l.split("\n"))

        # Шрифты Montserrat
        f_title  = _font(26, "Bold")
        f_group  = _font(17, "Medium")
        f_pair   = _font(24, "Bold")
        f_time   = _font(11, "Regular")
        f_disc   = _font(16, "Medium")
        f_teach  = _font(14, "Regular")
        f_aud    = _font(13, "SemiBold")

        # Размеры
        PAD      = 28
        HEADER_H = 88
        COL_P    = 60       # колонка номера пары (шире для времени)
        COL_A    = 90       # колонка аудитории (бейдж)
        W        = 720
        COL_D    = W - PAD * 2 - COL_P - COL_A - 16  # ширина колонки дисциплины
        LH       = 21      # высота строки текста
        RP       = 14      # padding внутри ячейки
        BOTTOM   = 16      # нижний отступ

        # Для двухколоночного режима
        CONTENT_W = W - PAD * 2 - COL_P - 8  # вся область контента после номера пары
        COL_HALF = (CONTENT_W - 12) // 2     # ширина одной колонки (с зазором)
        COL_D_HALF = COL_HALF - COL_A - 8    # ширина дисциплины в половинке

        parsed = [_parse_line(l) for l in flat]
        pair_groups = _group_by_pair(parsed)

        # ── Вычисляем высоты строк ──
        tmp = Image.new("RGB", (1, 1))
        td  = ImageDraw.Draw(tmp)

        group_heights: list[int] = []
        for pg in pair_groups:
            if len(pg) == 1:
                # Одиночная пара — как раньше
                p = pg[0]
                disc_lines = _wrap(td, p["disc"] or "—", f_disc, COL_D)
                n = len(disc_lines) + (1 if p["teacher"] else 0)
                group_heights.append(max(RP * 2 + n * LH, 52))
            else:
                # Две подгруппы — высота = max из двух колонок
                heights = []
                for p in pg:
                    disc_lines = _wrap(td, p["disc"] or "—", f_disc, COL_D_HALF)
                    n = len(disc_lines) + (1 if p["teacher"] else 0)
                    heights.append(max(RP * 2 + n * LH, 52))
                group_heights.append(max(heights))

        H = HEADER_H + sum(group_heights) + BOTTOM

        # ── Рисуем ──
        img = Image.new("RGB", (W, H), _BG)
        d   = ImageDraw.Draw(img)

        # ── Заголовок (градиент) ──
        for row_y in range(HEADER_H):
            t = row_y / HEADER_H
            r = int(_HEADER_BG[0] * (1 - t) + _HEADER_BG2[0] * t)
            g = int(_HEADER_BG[1] * (1 - t) + _HEADER_BG2[1] * t)
            b = int(_HEADER_BG[2] * (1 - t) + _HEADER_BG2[2] * t)
            d.line([(0, row_y), (W, row_y)], fill=(r, g, b))

        # Текст заголовка
        d.text((PAD, 20), f"Расписание на {date_str}", fill=_WHITE, font=f_title)
        d.text((PAD, 54), group_name, fill=(200, 220, 255), font=f_group)

        # ── Строки расписания ──
        y = HEADER_H
        for gi, pg in enumerate(pair_groups):
            rh = group_heights[gi]

            # Фон строки
            bg = _ROW_ALT if gi % 2 == 1 else _ROW_WHITE
            d.rectangle([0, y, W, y + rh], fill=bg)

            # Разделитель
            if gi > 0:
                d.line([(PAD, y), (W - PAD, y)], fill=_DIVIDER, width=1)

            # ── Номер пары ──
            pair_text = pg[0]["pair"]
            pair_bbox = d.textbbox((0, 0), pair_text, font=f_pair)
            pair_w = pair_bbox[2] - pair_bbox[0]
            pair_x = PAD + (COL_P - pair_w) // 2
            d.text((pair_x, y + RP - 1), pair_text, fill=_ACCENT, font=f_pair)

            # ── Время пары (под номером) ──
            time_text = pg[0].get("time", "")
            if time_text:
                time_bbox = d.textbbox((0, 0), time_text, font=f_time)
                time_w = time_bbox[2] - time_bbox[0]
                time_x = PAD + (COL_P - time_w) // 2
                d.text((time_x, y + RP + 26), time_text, fill=_TEXT_SECOND, font=f_time)

            if len(pg) == 1:
                # ── Одиночная пара (полная ширина) ──
                p = pg[0]
                x_d  = PAD + COL_P + 8
                disc = p["disc"] or "—"
                is_empty = disc in ("Нет", "—", "")
                color = _TEXT_MUTED if is_empty else _TEXT_PRIMARY
                disc_lines = _wrap(d, disc, f_disc, COL_D)
                ty = y + RP
                for dl in disc_lines:
                    d.text((x_d, ty), dl, fill=color, font=f_disc)
                    ty += LH

                # Преподаватель
                if p["teacher"]:
                    d.text((x_d, ty), p["teacher"], fill=_TEXT_SECOND, font=f_teach)

                # Бейдж аудитории
                if p["aud"] and p["aud"] != "0":
                    aud_text = p["aud"]
                    aud_bbox = d.textbbox((0, 0), aud_text, font=f_aud)
                    aud_tw = aud_bbox[2] - aud_bbox[0]
                    aud_th = aud_bbox[3] - aud_bbox[1]
                    badge_w = aud_tw + 16
                    badge_h = aud_th + 10
                    badge_x = W - PAD - badge_w
                    badge_y = y + RP
                    _rounded_rect(d, (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
                                  radius=6, fill=_AUD_BG)
                    d.text((badge_x + 8, badge_y + 4), aud_text,
                           fill=_AUD_TEXT, font=f_aud)

            else:
                # ── Две подгруппы (двухколоночный режим) ──
                x_content = PAD + COL_P + 8

                for ci, p in enumerate(pg):
                    col_x = x_content + ci * (COL_HALF + 12)

                    disc = p["disc"] or "—"
                    is_empty = disc in ("Нет", "—", "")
                    color = _TEXT_MUTED if is_empty else _TEXT_PRIMARY
                    disc_lines = _wrap(d, disc, f_disc, COL_D_HALF)
                    ty = y + RP
                    for dl in disc_lines:
                        d.text((col_x, ty), dl, fill=color, font=f_disc)
                        ty += LH

                    # Преподаватель
                    if p["teacher"]:
                        d.text((col_x, ty), p["teacher"], fill=_TEXT_SECOND, font=f_teach)
                        ty += LH

                    # Бейдж аудитории
                    if p["aud"] and p["aud"] != "0":
                        aud_text = p["aud"]
                        aud_bbox = d.textbbox((0, 0), aud_text, font=f_aud)
                        aud_tw = aud_bbox[2] - aud_bbox[0]
                        aud_th = aud_bbox[3] - aud_bbox[1]
                        badge_w = aud_tw + 16
                        badge_h = aud_th + 10
                        badge_x = col_x + COL_D_HALF + 4
                        badge_y = y + RP
                        _rounded_rect(d, (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
                                      radius=6, fill=_AUD_BG)
                        d.text((badge_x + 8, badge_y + 4), aud_text,
                               fill=_AUD_TEXT, font=f_aud)

                # Вертикальный разделитель между колонками
                div_x = x_content + COL_HALF + 5
                d.line([(div_x, y + 6), (div_x, y + rh - 6)], fill=_COL_DIVIDER, width=1)

            y += rh

        # Нижняя полоска-акцент
        d.rectangle([0, H - 3, W, H], fill=_HEADER_BG)

        # ── Сохраняем PNG ──
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf.getvalue()

    except Exception:
        return None
