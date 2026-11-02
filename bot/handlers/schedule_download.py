"""Send the original schedule to the actual user who presses the button."""
import logging
from pathlib import Path
import re
import uuid
import time

from aiogram import types
from aiogram.types import FSInputFile
from aiogram.exceptions import TelegramAPIError

from activity_store import ActivityStore, schedule_date
from commands.find import find_schedule_file

log = logging.getLogger(__name__)
CALLBACK = re.compile(r'^schedule_pdf:(groups|teachers):(\d{2}\.\d{2}\.\d{4})$')


async def download_schedule(query: types.CallbackQuery):
    match = CALLBACK.fullmatch(query.data or '')
    if not match:
        await query.answer('Не удалось прочитать дату. Открой расписание заново.', show_alert=True)
        return
    try:
        date = schedule_date(match[2])
    except ValueError:
        await query.answer('Не удалось прочитать дату. Открой расписание заново.', show_alert=True)
        return
    activity = ActivityStore()
    uid, download_id = query.from_user.id, uuid.uuid4().hex
    details = dict(download_id=download_id, schedule_type=match[1], delivery='telegram', attribution='telegram_callback')

    def record(phase, outcome='ok', **extra):
        try:
            return activity.append(uid, 'telegram', 'server', 'schedule.download.' + phase,
                outcome=outcome, event_id='pdf:' + phase + ':' + query.id, date=date,
                details={**details, **extra})
        except Exception as error:
            log.warning('Download journal write skipped (%s)', type(error).__name__)
            return None

    # Telegram update retries must not send the same file twice.
    if record('request') == 0:
        await query.answer('Этот запрос уже обработан.')
        return
    try:
        with activity.connect() as db:
            count = db.execute("SELECT count(*) FROM activity_events WHERE actor_id=? AND channel='telegram' "
                "AND action='schedule.download.request' AND received_at_ms>=?",
                (uid, int(time.time()*1000)-60000)).fetchone()[0]
        if count > 10:
            record('error', 'rejected', reason='rate_limited')
            await query.answer('Слишком много запросов. Попробуй через минуту.', show_alert=True)
            return
    except Exception as error:
        log.warning('Download rate check skipped (%s)', type(error).__name__)
    try:
        await query.answer('Отправляю файл в чат с ботом.')
    except TelegramAPIError:
        pass
    try:
        path = find_schedule_file(match[2], match[1])
        file = Path(path) if path else None
        size = file.stat().st_size if file and file.is_file() else None
    except OSError as error:
        record('error', 'error', error_type=type(error).__name__)
        return
    if size is None:
        record('error', 'rejected', reason='file_not_found')
        await query.bot.send_message(uid, 'Файла на эту дату пока нет. Попробуй позже.')
        return
    details.update(file_type=file.suffix.lstrip('.').lower(), bytes_expected=size)
    record('started', 'observed')
    try:
        await query.bot.send_document(uid, FSInputFile(file))
    except (TelegramAPIError, OSError) as error:
        record('error', 'error', error_type=type(error).__name__)
        # Sending failure is retained in the journal, without the API's text/token.
        log.warning('Schedule file could not be sent (%s)', type(error).__name__)
        return
    record('complete', bytes_sent=details['bytes_expected'])
