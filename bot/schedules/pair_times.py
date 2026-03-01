# schedules/pair_times.py
"""
Расписание звонков и добавление времени пар к строкам расписания.
"""

import re
import datetime

# Расписание звонков: будние дни (пн–пт)
WEEKDAY_TIMES = {
    1: "8:45–10:05",
    2: "10:25–11:45",
    3: "12:05–13:25",
    4: "13:35–14:55",
}

# Расписание звонков: суббота
SATURDAY_TIMES = {
    1: "8:45–10:00",
    2: "10:10–11:25",
    3: "11:35–12:50",
    4: "13:00–14:15",
}


def is_saturday(date_str: str) -> bool:
    """Проверяет, является ли дата субботой. Формат: dd.mm.yyyy."""
    try:
        dt = datetime.datetime.strptime(date_str, "%d.%m.%Y")
        return dt.weekday() == 5  # 5 = суббота
    except (ValueError, TypeError):
        return False


def get_pair_time(pair_num: int, date_str: str) -> str:
    """Возвращает строку времени для номера пары с учётом дня недели."""
    times = SATURDAY_TIMES if is_saturday(date_str) else WEEKDAY_TIMES
    return times.get(pair_num, "")


def add_pair_times(lines: list[str], date_str: str) -> list[str]:
    """
    Добавляет время к каждой строке расписания.

    Вход:  '▪️1 пара – Математика – Иванов И.И. – 101'
    Выход: '▪️1 пара (8:45–10:05) – Математика – Иванов И.И. – 101'
    """
    if not lines or not date_str:
        return lines

    result = []
    for line in lines:
        m = re.match(r'^(▪️\s*(\d+)\s*пара)\s*([–—]\s*)', line)
        if m:
            prefix = m.group(1)       # '▪️1 пара'
            pair_num = int(m.group(2))  # 1
            sep = m.group(3)           # '– '
            time_str = get_pair_time(pair_num, date_str)
            if time_str:
                new_line = f"{prefix} ({time_str}) {sep}{line[m.end():]}"
            else:
                new_line = line
            result.append(new_line)
        else:
            result.append(line)
    return result
