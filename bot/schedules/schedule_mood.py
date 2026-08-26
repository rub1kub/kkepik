# schedules/schedule_mood.py
"""
Оценка сложности учебного дня и выбор кастомного Telegram-эмодзи.
"""

import re

# Telegram requires the fallback to match the sticker's own emoji field.
# Comments preserve the visual grouping supplied for the schedule design.
REST_EMOJIS = [
    (5449372007432985754, "🌴"),
    (5343919586674750560, "😴"),  # visual: 🛌
    (5886606502868816489, "☕"),
    (5253555484811610534, "🧘"),
]
EASY_EMOJIS = [
    (5262515430836163289, "🎃"),  # visual: 🥳
    (5319066444883828666, "😎"),
    (5345810437436879174, "😌"),
    (5352693122528724986, "✨"),
]
NORMAL_EMOJIS = [
    (5262941654800687250, "🧙"),  # visual: 🙂
    (5336933568964734298, "🤓"),
    (5346234148845532752, "🙂"),  # visual: 📚
    (5402477260982731644, "☀️"),  # visual: 🌤
]
MEDIUM_EMOJIS = [
    (5262507798679280566, "🐸"),  # visual: 😐
    (6206233738494347353, "😐"),  # visual: 🫠
    (5262845357338941000, "⚰️"),  # visual: 😵‍💫
    (5260681883527699649, "🔥"),
]
HARD_EMOJIS = [
    (5260404531719594367, "😱"),  # visual: 😰
    (5260563484164251010, "😫"),  # visual: 😩
    (5262588247711702277, "🥺"),  # visual: 🥲
    (5390858197626006383, "🌧"),
]
CRITICAL_EMOJIS = [
    (5262487238670832805, "💀"),
    (5260635566600379633, "👺"),  # visual: 💀
    (5420475901257195805, "⚰️"),  # visual: 🪦
    (5363925144907554991, "🆘"),
]

# Each supplied emoji is a separate step on the difficulty scale. Scores above
# the scale stay on the final SOS level.
DIFFICULTY_EMOJIS = [
    *REST_EMOJIS,
    *EASY_EMOJIS,
    *NORMAL_EMOJIS,
    *MEDIUM_EMOJIS,
    *HARD_EMOJIS,
    *CRITICAL_EMOJIS,
]

# Фамилии со штрафом (+3 к каждой паре, в которой они встречаются)
PENALTY_TEACHERS = ["халезин", "шульга", "каркиван", "зиманин"]

# Фамилии с бонусом (−2 если хотя бы одна пара с этим преподавателем)
BONUS_TEACHERS = ["воробьева"]

# Список (keyword_lowercase, weight), проверяется от высшего к низшему весу
DISCIPLINE_WEIGHTS = [
    # Weight 4 — жесть
    ("физик",                 4),  # Физика (НЕ физкультура — обрабатывается отдельно)
    ("русский",               4),  # Русский язык
    ("криптограф",            4),  # Криптографические средства
    # Weight 3 — тяжело
    ("дискретн",              3),  # Дискретная математика
    ("математик",             3),  # Математика
    ("теори вероятн",         3),  # Теория вероятностей
    ("компьютерн сет",        3),  # Компьютерные сети
    ("хими",                  3),  # Химия
    ("электроник",            3),  # Электроника
    ("микропроцессор",        3),  # Микропроцессоры
    ("защит информаци",       3),  # Защита информации
    ("информационн безопасн", 3),  # Информационная безопасность
    ("программирован",        3),  # Программирование
    ("структур данн",         3),  # Структуры данных
    ("баз данн",              3),  # Базы данных
    ("операционн систем",     3),  # Операционные системы
    ("алгоритм",              3),
    ("сети и систем",         3),
    ("цифров электрон",       3),  # Цифровая электроника
    # Weight 2 — нормально
    ("биологи",               2),
    ("литератур",             2),
    ("иностранн",             2),  # Иностранный язык
    ("английск",              2),
    ("немецк",                2),
    ("бжд",                   2),
    ("безопасн жизн",         2),
    ("экономик",              2),
    ("менеджмент",            2),
    ("правоведен",            2),
    ("документооборот",       2),
    ("делопроизвод",          2),
    ("информатик",            2),
    ("веб",                   2),  # веб-приложения, разработка веб
    ("архитектур",            2),
    ("метрологи",             2),
    ("стандартизаци",         2),
    ("тестирован",            2),  # Тестирование ПО
    # Weight 1 — лёгкие
    ("физкультур",            1),  # Физкультура (также покрыта спецпроверкой выше)
    ("психолог",              1),
    ("индивидуальн проект",   1),
    ("философи",              1),
    ("грамотност",            1),  # Финансовая грамотность (грамотность→грамотност)
    ("история",               1),
    ("обществознани",         1),
    ("разговор о важном",     1),
]


def _get_discipline_weight(disc: str) -> int:
    """Возвращает вес дисциплины (0–4). 0 — пустая / «Нет», 2 — по умолчанию."""
    if not disc:
        return 0
    dl = disc.lower().strip()
    if not dl or dl == "нет":
        return 0

    # Физическая культура / Физкультура → всегда weight 1
    if "физкультур" in dl or ("физическ" in dl and "культур" in dl):
        return 1

    # Проверяем ключевые слова (список уже отсортирован от weight 4 к weight 1)
    for keyword, weight in DISCIPLINE_WEIGHTS:
        if keyword in dl:
            return weight

    # Практики: тяжёлые (по ЗИ / криптографии) → 4, остальные → 2
    if "практик" in dl:
        if any(x in dl for x in ("защит", "криптограф", "безопасн")):
            return 4
        return 2

    return 2  # по умолчанию


def _looks_like_audience(val: str) -> bool:
    """True если строка похожа на аудиторию, а не на имя преподавателя."""
    v = val.strip()
    if not v:
        return True
    # Префикс «ауд.» (расписание преподавателей из PDF групп)
    if v.lower().startswith("ауд"):
        return True
    # Содержит цифры → скорее всего аудитория
    if re.search(r'\d', v):
        return True
    # Специальные слова-аудитории
    if re.search(r'(?i)тир|вц|иц|с/з|спортзал|спорт\.зал|акт\.зал|актзал|акт зал', v):
        return True
    return False


def _parse_line(line: str) -> tuple[str, list[str]]:
    """
    Разбирает строку расписания, возвращает (discipline, [teachers]).

    Поддерживает форматы:
      • ▪️N пара – Дисциплина – Преподаватель – Аудитория   (студент / XLSX)
      • ▪️N пара – ГруппаXXX – Дисциплина – ауд. N          (преподаватель из PDF групп)
    """
    # Убираем «▪️N пара (время) – »
    text = re.sub(r'^▪️\s*\d+\s*пара\s*(?:\([^)]*\))?\s*[–—]\s*', '', line.strip())
    if not text or text.strip() == "Нет":
        return "", []

    parts = re.split(r'\s*[–—]\s*', text)
    if not parts:
        return "", []

    # Если первая часть выглядит как код группы (преподавательское расписание из PDF)
    disc_start = 0
    first = parts[0].strip()
    if re.match(r'^\d{2,3}-[А-ЯЁA-Z]', first):
        disc_start = 1

    if disc_start >= len(parts):
        return "", []

    disc = parts[disc_start].strip()
    teachers: list[str] = []

    for part in parts[disc_start + 1:]:
        part = part.strip()
        if not part or _looks_like_audience(part):
            continue
        if "/" in part:
            for sub in part.split("/"):
                sub = sub.strip()
                if sub and not _looks_like_audience(sub):
                    teachers.append(sub)
        else:
            teachers.append(part)

    return disc, teachers


def _tg_emoji(emoji_id: int, fallback: str) -> str:
    """Возвращает HTML-тег кастомного Telegram-эмодзи."""
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def _emoji_for_score(total_score: int) -> tuple[int, str]:
    level = min(max(total_score, 0), len(DIFFICULTY_EMOJIS) - 1)
    return DIFFICULTY_EMOJIS[level]


def get_mood_emoji(schedule_lines: list[str]) -> str:
    """
    По списку строк расписания возвращает HTML кастомного Telegram-эмодзи
    в зависимости от сложности дня.

    Args:
        schedule_lines: строки вида «▪️N пара – Дисциплина – Преподаватель – Ауд»

    Returns:
        HTML строка: <tg-emoji emoji-id="...">🙂</tg-emoji>
    """
    if not schedule_lines:
        return _tg_emoji(*_emoji_for_score(0))

    real_pairs = [l for l in schedule_lines if l.strip() and "пара – Нет" not in l]

    if not real_pairs:
        return _tg_emoji(*_emoji_for_score(0))

    total_score = 0
    has_bonus = False

    for line in real_pairs:
        disc, teachers = _parse_line(line)
        total_score += _get_discipline_weight(disc)

        for teacher in teachers:
            tl = teacher.lower()
            for penalty in PENALTY_TEACHERS:
                if tl.startswith(penalty):
                    total_score += 3
                    break
            if not has_bonus:
                for bonus in BONUS_TEACHERS:
                    if tl.startswith(bonus):
                        has_bonus = True
                        break

    if has_bonus:
        total_score = max(0, total_score - 2)

    return _tg_emoji(*_emoji_for_score(total_score))
