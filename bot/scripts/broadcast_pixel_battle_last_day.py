"""Approved, one-time, student-only reminder with durable no-duplicate auditing."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError, TelegramBadRequest, TelegramForbiddenError,
    TelegramNetworkError, TelegramRetryAfter, TelegramUnauthorizedError,
)
from dotenv import dotenv_values
from message_registry import record_message
from scripts.preview_pixel_battle_last_day import (
    AUDIT, PROPOSED_SEND_AT, WEBAPP_URL, build_message, preview_keyboard, read_deadline,
)

DATABASE = ROOT / 'data/database.db'
ROLE = 'Я студент'
ENTITY = 'pixel-battle-last-day-students-20261007'


def current_students(database=DATABASE):
    with closing(sqlite3.connect(f'file:{database}?mode=ro', uri=True)) as db:
        return [uid for (uid,) in db.execute(
            "SELECT user_id FROM users WHERE role=? AND typeof(user_id)='integer' "
            "AND user_id>0 ORDER BY user_id", (ROLE,))]


def is_student(uid, database=DATABASE):
    if type(uid) is not int or uid <= 0:
        return False
    with closing(sqlite3.connect(f'file:{database}?mode=ro', uri=True)) as db:
        return bool(db.execute('SELECT 1 FROM users WHERE user_id=? AND role=?',
                               (uid, ROLE)).fetchone())


def validate_approval(plan, deadline):
    if plan.get('approved') is not True or plan.get('status') != 'approved':
        raise ValueError('Owner approval is required')
    if plan.get('broadcast_role') != ROLE or plan.get('timezone_requires_confirmation') is not False:
        raise ValueError('Student-only audience and confirmed timezone are required')
    if datetime.fromisoformat(plan['proposed_send_at']) != PROPOSED_SEND_AT:
        raise ValueError('Only the approved send time is allowed')
    if datetime.fromisoformat(plan['event_ends_at']) != deadline:
        raise ValueError('The event deadline has changed; obtain new approval')
    if plan.get('text_html') != build_message(deadline, PROPOSED_SEND_AT):
        raise ValueError('The message differs from the approved draft')
    if (plan.get('button_web_app_url') != WEBAPP_URL
            or plan.get('button_text') != '🎨 Открыть пиксель-батл'):
        raise ValueError('The opening button differs from the approved draft')


def validate_start(now, deadline):
    if now < PROPOSED_SEND_AT:
        raise ValueError('Broadcast cannot start before 11:44 Moscow')
    if now >= deadline:
        raise ValueError('The event has ended; do not broadcast an outdated announcement')


def already_attempted(rows):
    latest = {row['user_id']: row['status'] for row in rows}
    # Only a definitive rate-limit rejection may be retried. An uncertain result
    # or a crash after "attempting" must never cause a duplicate Telegram send.
    return {uid for uid, status in latest.items() if status != 'rate_limited'}


def read_journal(path):
    if not path.exists():
        return []
    # A damaged journal requires review, never blind replay of Telegram sends.
    return [json.loads(line) for line in path.read_text().splitlines() if line]


async def deliver_student(bot, uid, deadline, append, database=DATABASE):
    while True:
        # Recheck the CURRENT role before every API call, including after 429 waits.
        if not is_student(uid, database):
            return {'user_id': uid, 'status': 'excluded_role_changed'}
        now = datetime.now(timezone.utc)
        if (deadline - now).total_seconds() < 60:
            return {'user_id': uid, 'status': 'event_ended'}
        text = build_message(deadline, now)
        append({'user_id': uid, 'status': 'attempting', 'at': now.isoformat()})
        try:
            msg = await bot.send_message(chat_id=uid, text=text, parse_mode='HTML',
                                         reply_markup=preview_keyboard())
        except TelegramRetryAfter as exc:
            append({'user_id': uid, 'status': 'rate_limited', 'retry_after': exc.retry_after})
            remaining = exc.retry_after + 1
            while remaining > 0:
                delay = min(remaining, 30)
                await asyncio.sleep(delay)
                remaining -= delay
            continue
        except TelegramForbiddenError:
            return {'user_id': uid, 'status': 'unreachable'}
        except TelegramUnauthorizedError:
            append({'user_id': uid, 'status': 'unauthorized'})
            raise
        except TelegramBadRequest as exc:
            row = {'user_id': uid, 'status': 'failed', 'error_type': type(exc).__name__}
            if 'chat not found' not in exc.message.lower():
                append(row)
                raise
            return row
        except (TelegramNetworkError, TelegramAPIError) as exc:
            return {'user_id': uid, 'status': 'unknown_outcome', 'error_type': type(exc).__name__}
        else:
            if msg.chat.id != uid or msg.chat.type != 'private':
                raise RuntimeError('Unexpected Telegram destination')
            return {'user_id': uid, 'status': 'sent', 'message_id': msg.message_id,
                    'text_html': text}


async def main(check_only=False):
    plan = json.loads((AUDIT / 'moderation.json').read_text())
    deadline = read_deadline()
    validate_approval(plan, deadline)
    if check_only:
        print(json.dumps({'approval_valid': True, 'role': ROLE,
                          'current_students': len(current_students()),
                          'scheduled_at': PROPOSED_SEND_AT.isoformat(),
                          'scheduled_at_utc': PROPOSED_SEND_AT.astimezone(timezone.utc).isoformat(),
                          'no_messages_sent': True}, ensure_ascii=False))
        return
    validate_start(datetime.now(timezone.utc), deadline)
    env = dotenv_values(ROOT / '.env')
    registry = env.get('MESSAGE_REGISTRY_PATH') or ROOT / 'data/message_registry.db'
    with (AUDIT / 'broadcast.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        async with Bot(env['BOT_TOKEN']) as bot:
            me = await bot.get_me()
            if me.username != 'kkepik_bot':
                raise RuntimeError('Wrong Telegram bot')
            snapshot = AUDIT / 'broadcast_students.json'
            if not snapshot.exists():
                with snapshot.open('x') as record:
                    json.dump({'role': ROLE, 'user_ids': current_students()}, record)
                    record.flush()
                    os.fsync(record.fileno())
            targets = json.loads(snapshot.read_text())['user_ids']
            journal = AUDIT / 'broadcast_results.jsonl'
            rows = read_journal(journal)
            attempted = already_attempted(rows)
            with journal.open('a', buffering=1) as log:
                def append(row):
                    log.write(json.dumps(row, ensure_ascii=False) + '\n')
                    log.flush()
                    os.fsync(log.fileno())
                    rows.append(row)

                print(json.dumps({'planned_students': len(targets),
                                  'already_attempted': len(attempted)}), flush=True)
                for index, uid in enumerate(targets, 1):
                    if uid in attempted:
                        continue
                    row = await deliver_student(bot, uid, deadline, append)
                    append(row)
                    if row['status'] == 'sent':
                        # The durable journal is authoritative even if registry writes fail.
                        try:
                            record_message(platform='telegram', direction='outgoing', chat_id=uid,
                                           message_id=row['message_id'],
                                           message_kind='pixel_battle_last_day_student_reminder',
                                           telegram_method='sendMessage', entity=ENTITY,
                                           content=row['text_html'], db_path=registry)
                        except Exception as exc:
                            print(json.dumps({'registry_error': type(exc).__name__,
                                              'user_id': uid}), flush=True)
                    if row['status'] == 'event_ended':
                        break
                    if index % 100 == 0:
                        print(json.dumps({'processed': index, 'total': len(targets)}), flush=True)
                    # At most 10 requests/s, with room for the normal bot's traffic.
                    await asyncio.sleep(0.1)
            latest = {row['user_id']: row['status'] for row in rows}
            summary = {'planned_students': len(targets), 'processed': len(latest),
                       'counts': dict(Counter(latest.values())),
                       'complete': len(latest) == len(targets),
                       'finished_at': datetime.now(timezone.utc).isoformat()}
            with (AUDIT / 'broadcast_summary.json').open('w') as record:
                json.dump(summary, record, ensure_ascii=False, indent=2)
                record.flush()
                os.fsync(record.fileno())
            print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Validate without sending or scheduling')
    asyncio.run(main(check_only=parser.parse_args().check))
