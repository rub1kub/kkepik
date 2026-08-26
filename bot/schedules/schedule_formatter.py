"""Mobile-first formatting for student schedule messages."""

from __future__ import annotations

import datetime as dt
from html import escape
import re

from schedules.group_schedule import is_audience_token
from schedules.pair_times import get_pair_time, is_saturday
from schedules.schedule_mood import get_mood_emoji


_PAIR_RE = re.compile(r"^▪️\s*(\d+)\s*пара\s*[–—-]\s*(.*)$")
_BOLD_TAG_RE = re.compile(r"</?b>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def get_group_course(group_name: str) -> int | None:
    try:
        course = int(group_name.split("-")[2][0])
    except (AttributeError, IndexError, TypeError, ValueError):
        return None
    return course if 1 <= course <= 4 else None


def should_show_pair_times(group_name: str, date_str: str) -> bool:
    if is_saturday(date_str):
        return True
    try:
        schedule_date = dt.datetime.strptime(date_str, "%d.%m.%Y").date()
    except (TypeError, ValueError):
        return False
    return get_group_course(group_name) == 1 and schedule_date.month == 9


def _plain(value: str) -> str:
    return _BOLD_TAG_RE.sub("", value).strip()


def _safe(value: str) -> str:
    return escape(_plain(value), quote=False)


def _split_lesson(line: str) -> tuple[int, str, str, str] | None:
    match = _PAIR_RE.match(line.strip())
    if not match:
        return None

    pair = int(match.group(1))
    parts = [part.strip() for part in re.split(r"\s+[–—]\s+", match.group(2))]
    if not parts:
        return pair, "", "", ""

    subject = parts.pop(0)
    audiences = []
    while parts and is_audience_token(_plain(parts[-1])):
        audiences.insert(0, parts.pop())

    teacher = " – ".join(parts)
    audience = ", ".join(_plain(item) for item in audiences)
    return pair, subject, teacher, audience


def _format_lesson(
    line: str,
    *,
    first_course: bool,
    date_str: str,
    show_time: bool,
) -> str:
    parsed = _split_lesson(line)
    if parsed is None:
        return _safe(line)

    pair, subject, teacher, audience = parsed
    pair_header = f"▪️<b>{pair} пара</b>"
    if show_time:
        pair_time = get_pair_time(pair, date_str)
        if pair_time:
            pair_header += f" · <i>{pair_time}</i>"

    safe_subject = _safe(subject)
    if safe_subject.casefold() == "нет":
        return f"{pair_header} – Нет"

    if first_course:
        first_line = f"{pair_header} – <b>{safe_subject}</b>"
    else:
        first_line = f"{pair_header} – {safe_subject}"

    details = []
    if teacher:
        details.append(_safe(teacher))
    if audience:
        room = f"аудитория <b>{_safe(audience)}</b>" if first_course else f"аудитория {_safe(audience)}"
        details.append(room)

    if not details:
        return first_line

    detail_text = " · ".join(details)
    if not first_course:
        detail_text = f"<b>{detail_text}</b>"
    return f"{first_line}\n   {detail_text}"


def format_group_schedule(lines: list[str], group_name: str, date_str: str) -> str:
    first_course = get_group_course(group_name) == 1
    show_time = should_show_pair_times(group_name, date_str)
    blocks = []
    for item in lines:
        for line in item.splitlines():
            if line.strip():
                blocks.append(
                    _format_lesson(
                        line,
                        first_course=first_course,
                        date_str=date_str,
                        show_time=show_time,
                    )
                )
    return "\n\n".join(blocks)


def build_group_schedule_message(
    lines: list[str],
    group_name: str,
    date_str: str,
    *,
    show_group: bool = True,
) -> str:
    schedule = format_group_schedule(lines, group_name, date_str)
    mood = get_mood_emoji(lines)
    header = f"{mood} Расписание на <b>{escape(date_str)}</b>"
    if show_group:
        header += f"\n\nГруппа <b>{escape(group_name)}</b>:"
    return f"{header}\n\n{schedule}"


def telegram_text_length(text: str) -> int:
    """Approximate Telegram's post-parse text length for HTML messages."""
    return len(_HTML_TAG_RE.sub("", text))


def fit_photo_caption(text: str, limit: int = 1024) -> str:
    if telegram_text_length(text) <= limit:
        return text

    compact = text.replace("аудитория", "ауд.")
    compact = compact.replace("\n\n▪️", "\n▪️")
    return compact
