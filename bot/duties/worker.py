"""Persistent local scheduler and Telegram outbox for duty lists."""
from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import sqlite3
from html import escape as h

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramAPIError

from duties.store import clock, DutyError
from commands.duty import keyboard

log = logging.getLogger(__name__)


def schedule_state(path, group, day):
    """Read only published structured data; missing source is never a day off."""
    if not path.exists():
        return 'unknown', None
    try:
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5) as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM revisions WHERE date=? ORDER BY revision DESC LIMIT 1', (str(day),)).fetchone()
            if not row:
                return 'unknown', None
            if row['status'] != 'parsed':
                return 'invalid_source', row['revision']
            groups = json.loads(row['groups_json'])
            if group not in groups:
                return 'entity_not_found', row['revision']
            exists = db.execute('SELECT 1 FROM lessons WHERE revision_id=? AND group_name=? LIMIT 1', (row['id'], group)).fetchone()
            return ('available' if exists else 'no_lessons'), row['revision']
    except (sqlite3.Error, ValueError):
        return 'unknown', None


class DutyWorker:
    def __init__(self, app, bot):
        self.app, self.store, self.bot = app, app.store, bot
        self.lock = None
        self.task = None

    def start(self):
        # A second polling process must not create duplicate scheduled sends.
        self.lock = open(str(self.store.path) + '.worker.lock', 'a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            self.lock = None
            raise RuntimeError('Duty scheduler is already running') from None
        self.store.recover_outbox()
        self.task = asyncio.create_task(self.run(), name='duty-scheduler')

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.lock:
            self.lock.close()
            self.lock = None

    async def run(self):
        while True:
            try:
                await self.once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error('Duty scheduler pass failed (%s)', type(exc).__name__)
            await asyncio.sleep(5)

    async def once(self, now=None):
        now = now or clock()
        for space in self.store.active_spaces():
            state, revision = schedule_state(self.app.data_dir / 'knowledge.db', space['group_name'], now.date())
            did = self.store.ensure_day(space['id'], now, state, revision)
            if did:
                with self.store.connect(True) as db:
                    day = self.store._day(db, did)
                    if (day['source_state'], day['source_revision']) != (state, revision):
                        # Publish a warning, not a silent change to today's pair.
                        db.execute('UPDATE days SET source_state=?,source_revision=? WHERE id=?', (state, revision, did))
                        self.store._managers_job(db, space['id'], 'source_change', did, f'source:{did}:{state}:{revision}')
                        self.store._changed(db, did)
        self.store.tick(now)
        for job in self.store.pending_jobs(now):
            await self.deliver(job['id'], now)
            await asyncio.sleep(0.08)

    def render(self, job):
        kind, uid, ref = job['kind'], job['uid'], job['ref']
        if kind in ('offer','offer_reminder'):
            text, rows = self.app.offer_panel(ref, uid)
            if kind == 'offer_reminder':
                text = 'Ещё ждём твой ответ.\n\n' + text
            return text, rows
        if kind == 'claim':
            return self.app.claim_panel(ref, uid)
        if kind == 'added':
            data = self.store.view(job['space'], uid)
            space = data['space']
            member = next((m for m in data['members'] if m['id'] == ref and m['uid'] == uid), None)
            if not member:
                raise DutyError('Участник уже вышел из списка.')
            with self.store.connect() as db:
                owner = db.execute('SELECT name,username FROM identities WHERE uid=?', (space['owner'],)).fetchone()
            creator = ('@' + h(owner['username']) if owner['username'] else h(owner['name'])) if owner else 'староста'
            text = f'<b>Вас добавили в список дежурств группы {h(space["group_name"])}.</b>\n\nСписок создал {creator}.\n'
            partner = next((m for m in data['members'] if member['pair'] and m['pair'] == member['pair'] and m['id'] != ref), None)
            if partner:
                text += 'Ваш напарник: ' + h(partner['name']) + '.\n'
            elif member['pair']:
                text += 'Уточните напарника у создателя списка.\n'
            else:
                text += 'Вы в списке запасных.\n'
            opts = json.loads(space['settings'])
            if opts['audience'] == 'off':
                text += '\nУтренняя рассылка в этом списке выключена.'
            elif opts['audience'] == 'all':
                text += f'\nВ дни дежурств в {opts["time"]} МСК бот будет сообщать, кто дежурит.'
            else:
                text += f'\nКогда придёт ваша очередь, бот напишет в {opts["time"]} МСК.'
            text += '\n\nПосмотреть список и настроить уведомления можно в /duty.'
            rows = [[('Открыть дежурства', f'open:{space["id"]}')], [('Отключить уведомления', f'silence:{space["id"]}')]]
            if data['access']['role'] != 'owner':
                rows.append([('Выйти из списка', f'leaveask:{space["id"]}')])
            return text, rows
        if kind == 'claim_result':
            with self.store.connect() as db:
                claim = db.execute('SELECT c.*,s.group_name FROM claims c JOIN spaces s ON s.id=c.space WHERE c.id=? AND c.uid=?', (ref, uid)).fetchone()
            if not claim:
                raise DutyError('Заявка не найдена.')
            if claim['status'] == 'approved':
                return f"Староста подтвердил вашу заявку в {h(claim['group_name'])}. Посмотреть дежурных и настроить уведомления: /duty.", [[('Открыть дежурства', f'open:{claim["space"]}')]]
            return 'Староста отклонил заявку. Проверьте с ним имя в списке и попросите новое приглашение.', [[('Мои дежурства', 'home')]]
        if kind == 'delivery_issue':
            return ('Одно из уведомлений могло не дойти. Свяжитесь с участниками напрямую. '
                    'Бот не считает это отказом от дежурства.'), [[('Открыть список', f'open:{job["space"]}')]]
        text, rows = self.app.day_panel(ref, uid, notification=kind in ('daily', 'update'))
        if kind == 'report':
            text = ('Отметьте, кто отдежурил: всех сразу или каждого отдельно кнопками ниже. '
                    'Если человек не ответил боту, сначала уточните у него.\n\n') + text
        elif kind == 'accepted':
            text = 'Вы оба согласились. Замена подтверждена.\n\n' + text
        elif kind == 'source_change':
            text = 'Обновились данные расписания. Проверьте, нужны ли сегодня дежурные. Бот пока оставил прежний состав.\n\n' + text
        return text, rows

    async def deliver(self, jid, now=None):
        now = now or clock()
        job = self.store.begin_delivery(jid, now)
        if not job:
            return
        editing = False
        try:
            text, rows = self.render(job)
            previous = self.store.previous_card(job['ref'], job['uid']) if job['kind'] == 'update' else None
            if previous:
                editing = True
                message = await self.bot.edit_message_text(text=text, chat_id=job['uid'], message_id=previous,
                    reply_markup=keyboard(rows), parse_mode='HTML', disable_web_page_preview=True)
                mid = previous
            else:
                # Recheck just before the network call; queued notifications obey
                # the latest membership, preferences and proposal expiration.
                with self.store.connect() as db:
                    if not self.store.delivery_allowed(db, job, clock()):
                        self.store.delivery_result(jid, 'cancelled')
                        return
                message = await self.bot.send_message(chat_id=job['uid'], text=text, reply_markup=keyboard(rows),
                                                     parse_mode='HTML', disable_web_page_preview=True)
                if message.chat.id != job['uid'] or message.chat.type != 'private':
                    self.store.delivery_result(jid, 'unknown', error='unexpected_chat')
                    self.store.delivery_problem(job)
                    return
                mid = message.message_id
            self.store.delivery_result(jid, 'sent', mid)
        except TelegramRetryAfter as exc:
            self.store.delivery_result(jid, 'pending', retry_at=clock().timestamp()+exc.retry_after+1, error='rate_limit')
        except TelegramBadRequest as exc:
            if editing and 'message is not modified' in exc.message.lower():
                self.store.delivery_result(jid, 'sent', previous)
            else:
                self.store.delivery_result(jid, 'failed', error='bad_request')
                self.store.delivery_problem(job)
        except TelegramForbiddenError:
            self.store.delivery_result(jid, 'failed', error='unreachable')
            self.store.delivery_problem(job)
        except DutyError:
            self.store.delivery_result(jid, 'cancelled', error='stale')
        except asyncio.CancelledError:
            self.store.delivery_result(jid, 'pending' if editing else 'unknown', error='interrupted')
            raise
        except (TelegramAPIError, TimeoutError, OSError):
            # Editing the same message is idempotent; sending a new one isn't.
            self.store.delivery_result(jid, 'pending' if editing else 'unknown',
                                       retry_at=clock().timestamp()+30, error='network')
            self.store.delivery_problem(job)
        except Exception as exc:
            self.store.delivery_result(jid, 'unknown', error=type(exc).__name__)
            log.error('Duty delivery failed (%s)', type(exc).__name__)
            self.store.delivery_problem(job)
