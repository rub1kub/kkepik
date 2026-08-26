"""Date rules for automatically shown schedules."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo


COLLEGE_TIMEZONE = ZoneInfo("Asia/Krasnoyarsk")


def current_college_date() -> dt.date:
    return dt.datetime.now(COLLEGE_TIMEZONE).date()


def is_current_schedule(
    date_str: str | None,
    *,
    today: dt.date | None = None,
) -> bool:
    if not date_str:
        return False
    try:
        schedule_date = dt.datetime.strptime(date_str, "%d.%m.%Y").date()
    except (TypeError, ValueError):
        return False

    current_date = today or current_college_date()
    days_ahead = (schedule_date - current_date).days
    return schedule_date.weekday() != 6 and 0 <= days_ahead <= 2
