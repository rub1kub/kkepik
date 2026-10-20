"""Date-scoped additions to automatic schedule deliveries."""

import datetime as dt

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


def get_schedule_settings_notice(schedule_date: str) -> str | None:
    return SETTINGS_NOTICE if schedule_date == SETTINGS_SCHEDULE_DATE else None


def append_broadcast_schedule_notice(message: str, recipient_role: str | None, schedule_date: str) -> str:
    message = append_student_schedule_notice(message, recipient_role, schedule_date)
    notice = get_schedule_settings_notice(schedule_date)
    if notice and not message.endswith(notice):
        message += '\n\n' + notice
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
