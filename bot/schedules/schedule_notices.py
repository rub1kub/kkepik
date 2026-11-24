"""Date-scoped additions to automatic schedule deliveries."""

import datetime as dt
import logging
from pathlib import Path

import config
from schedules.schedule_dates import current_college_date


PIXEL_BATTLE_SEND_DATE = dt.date(2026, 9, 30)
PIXEL_BATTLE_SCHEDULE_DATE = '01.10.2026'
PIXEL_BATTLE_NOTICE = (
    '<tg-spoiler><b><tg-emoji emoji-id="5431456208487716895">🎨</tg-emoji> '
    'Пиксель-батл в мини-аппе ККЭПика — общее полотно на весь колледж, '
    'рисовать может каждый, по пикселю в секунду.</b></tg-spoiler>'
)

SETTINGS_SCHEDULE_DATE = '07.10.2026'
SETTINGS_NOTICE = (
    '⚙️ Теперь расписание можно настроить под себя: '
    'шаблоны, поля и вид сообщений — /settings.'
)

DUTY_SCHEDULE_DATE = '29.10.2026'
DUTY_NOTICE = '🧹 Старостам и замам: теперь в боте есть учёт дежурств — /duty'


def take_next_schedule_notice(data_dir, schedule_date, schedule_type, *, is_update):
    """Atomically claim a moderated footer for one new schedule distribution."""
    if is_update:
        return None
    pending = Path(data_dir) / 'next_schedule_notice.html'
    claimed = pending.with_name(f'schedule_notice_{schedule_date}_{schedule_type}.html')
    try:
        notice = pending.read_text(encoding='utf-8').strip()
        pending.replace(claimed)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError):
        logging.exception('Could not load the one-time schedule notice')
        return None
    return notice or None


def get_schedule_settings_notice(schedule_date: str) -> str | None:
    return SETTINGS_NOTICE if schedule_date == SETTINGS_SCHEDULE_DATE else None


def get_broadcast_schedule_notice(schedule_date: str, recipient_role: str | None = None, *, extra_notice=None) -> str | None:
    notice = get_schedule_settings_notice(schedule_date)
    if recipient_role == config.ROLE_STUDENT and schedule_date == DUTY_SCHEDULE_DATE:
        notice = DUTY_NOTICE
    return '\n\n'.join(part for part in (notice, extra_notice) if part) or None


def append_broadcast_schedule_notice(message: str, recipient_role: str | None, schedule_date: str, *, extra_notice=None) -> str:
    message = append_student_schedule_notice(message, recipient_role, schedule_date)
    notice = get_broadcast_schedule_notice(schedule_date, recipient_role, extra_notice=extra_notice)
    if notice and not message.endswith(notice):
        message = message.rstrip() + '\n\n' + notice
    return message


def append_student_schedule_notice(
    message: str,
    recipient_role: str | None,
    schedule_date: str,
) -> str:
    # Both dates are intentional: neither late retries nor other schedules qualify.
    if (
        recipient_role == config.ROLE_STUDENT
        and schedule_date == PIXEL_BATTLE_SCHEDULE_DATE
        and current_college_date() == PIXEL_BATTLE_SEND_DATE
    ):
        return f'{message}\n\n{PIXEL_BATTLE_NOTICE}'
    return message
