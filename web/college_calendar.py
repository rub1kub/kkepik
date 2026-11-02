"""Confirmed exceptions to the college calendar (Europe/Moscow).

The web copy and browser rules are built by scripts/build_college_calendar.py.
Unlisted dates keep their usual weekday rules; missing PDFs are not days off.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

EXCEPTIONS = {
    "2026-11-03": {"kind": "saturday_hours", "note": "3 ноября — пары по субботнему времени."},
    "2026-11-04": {"kind": "holiday", "note": "4 ноября — выходной. Занятий нет."},
}


def calendar_date(value: date | str) -> date | None:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(ZoneInfo("Europe/Moscow"))
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                pass
    return None


def day_rule(value: date | str) -> dict | None:
    day = calendar_date(value)
    rule = EXCEPTIONS.get(day.isoformat()) if day else None
    return rule.copy() if rule else None


def is_holiday(value: date | str) -> bool:
    return (day_rule(value) or {}).get("kind") == "holiday"


def uses_saturday_hours(value: date | str) -> bool:
    day = calendar_date(value)
    return bool(day and not is_holiday(day) and (
        day.weekday() == 5 or (day_rule(day) or {}).get("kind") == "saturday_hours"))
