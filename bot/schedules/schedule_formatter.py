"""Mobile-first formatting for student schedule messages."""

from __future__ import annotations

import datetime as dt
from html import escape, unescape
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
    subject_aliases: dict[str, str] | None = None,
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

    display_subject = (subject_aliases or {}).get(_plain(subject), subject)
    safe_subject = _safe(display_subject)
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


def format_group_schedule(
    lines: list[str], group_name: str, date_str: str, *,
    subject_aliases: dict[str, str] | None = None,
) -> str:
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
                        subject_aliases=subject_aliases,
                    )
                )
    return "\n\n".join(blocks)


def build_group_schedule_message(
    lines: list[str],
    group_name: str,
    date_str: str,
    *,
    show_group: bool = True,
    subject_aliases: dict[str, str] | None = None,
    preferences=None,
) -> str:
    from schedules.message_constructor import render_schedule_message
    return render_schedule_message(lines, group_name, date_str, preferences=preferences,
                                   show_group=show_group, subject_aliases=subject_aliases)


def telegram_text_length(text: str) -> int:
    """Count visible UTF-16 units, including astral emoji and HTML entities."""
    return len(unescape(_HTML_TAG_RE.sub("", text)).encode("utf-16-le")) // 2


def fit_photo_caption(text: str, limit: int = 1024) -> str:
    if telegram_text_length(text) <= limit:
        return text

    compact = text.replace("аудитория", "ауд.")
    compact = compact.replace("\n\n▪️", "\n▪️")
    return compact
