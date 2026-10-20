"""Announce the approved PDF repair only to its proven affected recipients.

Read-only by default. --apply sends one plain-text notice per affected private
chat. Durable attempts are never replayed after an uncertain Telegram outcome.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import (TelegramAPIError, TelegramBadRequest,
    TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter,
    TelegramUnauthorizedError)
from dotenv import dotenv_values
from message_registry import record_message

DATABASE = ROOT / 'data/database.db'
REGISTRY = ROOT / 'data/message_registry.db'
REPAIR = ROOT / 'audits/schedule-repair-20261013'
AUDIT = ROOT / 'audits/schedule-repair-notice-20261013'
DATE = '13.10.2026'
SOURCE_SHA = '3f7970f6030289cac3c40d177aaf2aece6ebc48e6bfd9aa3110eac74455e1f30'
EXPECTED_MESSAGES, EXPECTED_RECIPIENTS = 197, 196
ENTITY = 'schedule-repair-notice-20261013'
KIND = 'schedule_repair_notice'
TEXT = ('Бот пропустил продолжение таблицы на следующей странице PDF. '
        'У части групп потерялись пары, а кому-то пришло сообщение, что группы нет в файле.\n\n'
        'Исправил. Предыдущее сообщение с расписанием уже обновлено — проверьте его ещё раз. '
        'Извините за путаницу\n\n'
        'Если заметите проблемы, пишите мне — @rub1kub.')
TEXT_HASH = hashlib.sha256(TEXT.encode()).hexdigest()
STATUSES = frozenset(('attempting', 'rate_limited', 'sent', 'already_delivered',
    'excluded_registration_changed', 'unreachable', 'unknown_outcome',
    'unauthorized', 'payload_error'))


def private_id(value):
    return type(value) is int and value > 0


def ro(path):
    connection = sqlite3.connect(f'file:{Path(path)}?mode=ro', uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def load_affected(repair=REPAIR, registry=REGISTRY):
    """Fail closed unless every planned edit is proven complete and registered."""
    evidence = {name: (Path(repair) / name).read_bytes()
                for name in ('plan.json', 'edits.jsonl', 'summary.json')}
    plan = json.loads(evidence['plan.json'])
    report = json.loads(evidence['summary.json'])
    if (plan.get('version') != 1 or plan.get('date') != DATE
            or plan.get('source_sha') != SOURCE_SHA or report.get('complete') is not True
            or report.get('finished') is not True or report.get('date') != DATE
            or report.get('source_sha') != SOURCE_SHA
            or report.get('messages') != EXPECTED_MESSAGES
            or len(plan.get('targets', [])) != EXPECTED_MESSAGES):
        raise ValueError('Repair is not completely verified')
    edits = {}
    for line in evidence['edits.jsonl'].decode().splitlines():
        event = json.loads(line)
        edits[event['registry_id']] = event
    recipients, seen = {}, set()
    with closing(ro(registry)) as db:
        for target in plan['targets']:
            row = target['row']
            uid = int(row['chat_id'])
            if not str(row['chat_id']).isdigit() or not private_id(uid) or row['id'] in seen:
                raise ValueError('Unsafe repair recipient or duplicate registry row')
            seen.add(row['id'])
            if edits.get(row['id'], {}).get('status') not in ('edited', 'not_modified'):
                raise ValueError('Repair edit is not confirmed')
            current = db.execute('SELECT * FROM message_registry WHERE id=?', (row['id'],)).fetchone()
            keys = ('platform', 'direction', 'chat_id', 'message_id', 'schedule_date',
                    'schedule_type', 'entity', 'telegram_method')
            if (current is None or any(current[key] != row[key] for key in keys)
                    or row['platform'] != 'telegram' or row['direction'] != 'outgoing'
                    or row['schedule_date'] != DATE or row['schedule_type'] != 'groups'
                    or current['content_hash'] != target['new_hash']
                    or current['message_kind'] != target['new_kind']
                    or target['new_kind'] != 'schedule'):
                raise ValueError('Fixed repair registry evidence differs')
            role = target['snapshot']['user']['role']
            if role not in ('Я студент', 'Я преподаватель'):
                raise ValueError('Unknown affected registration')
            if uid in recipients and recipients[uid] != role:
                raise ValueError('Affected recipient has inconsistent registration')
            recipients[uid] = role
    if len(recipients) != EXPECTED_RECIPIENTS:
        raise ValueError('Affected recipient count differs')
    return {'entity': ENTITY, 'text_sha256': TEXT_HASH,
            'repair_sha256': {name: hashlib.sha256(data).hexdigest() for name, data in evidence.items()},
            'recipients': [{'user_id': uid, 'role': role} for uid, role in sorted(recipients.items())]}


def same_registration(recipient, database=DATABASE):
    uid = recipient['user_id']
    if not private_id(uid):
        return False
    with closing(ro(database)) as db:
        return db.execute("SELECT 1 FROM users WHERE user_id=? AND role=? "
                          "AND typeof(user_id)='integer'", (uid, recipient['role'])).fetchone() is not None


def registered_delivery(uid, registry):
    with closing(ro(registry)) as db:
        rows = db.execute("SELECT message_id,message_kind,content_hash FROM message_registry "
                          "WHERE platform='telegram' AND direction='outgoing' AND chat_id=? AND entity=?",
                          (str(uid), ENTITY)).fetchall()
    if not rows:
        return None
    if (len(rows) != 1 or rows[0]['message_kind'] != KIND or rows[0]['content_hash'] != TEXT_HASH
            or not str(rows[0]['message_id']).isdigit() or not private_id(int(rows[0]['message_id']))):
        raise ValueError('Conflicting notice delivery evidence')
    return int(rows[0]['message_id'])


def read_journal(path):
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    for row in rows:
        if (not isinstance(row, dict) or not private_id(row.get('user_id'))
                or row.get('status') not in STATUSES
                or row['status'] in ('sent', 'already_delivered') and not private_id(row.get('message_id'))
                or row['status'] == 'rate_limited' and not isinstance(row.get('retry_not_before'), (int, float))):
            raise ValueError('Notice journal requires manual review')
    return rows


def latest_rows(rows):
    return {row['user_id']: row for row in rows}


def summary(recipients, rows):
    allowed = {recipient['user_id'] for recipient in recipients}
    latest = {uid: row for uid, row in latest_rows(rows).items() if uid in allowed}
    return {'campaign': ENTITY, 'planned_recipients': len(allowed), 'processed': len(latest),
            'counts': dict(Counter(row['status'] for row in latest.values())),
            'complete': len(latest) == len(allowed) and all(
                row['status'] not in ('attempting', 'rate_limited') for row in latest.values())}


async def validate_bot(bot):
    me = await bot.get_me()
    if me.username != 'kkepik_bot' or me.is_bot is not True:
        raise ValueError('Unexpected Telegram bot')


async def wait_until(deadline, progress=None):
    while (remaining := deadline - time.time()) > 0:
        if progress:
            progress('rate_limit_wait', round(remaining, 1))
        await asyncio.sleep(min(remaining, 20))


async def deliver(bot, recipient, append, registry, database=DATABASE,
                  progress=None, retry_not_before=0):
    uid = recipient['user_id']
    await wait_until(retry_not_before, progress)
    while True:
        delivered = registered_delivery(uid, registry)
        if delivered is not None:
            return {'user_id': uid, 'status': 'already_delivered', 'message_id': delivered}
        if not same_registration(recipient, database):
            return {'user_id': uid, 'status': 'excluded_registration_changed'}
        append({'user_id': uid, 'status': 'attempting', 'at': datetime.now(timezone.utc).isoformat()})
        try:
            message = await bot.send_message(chat_id=uid, text=TEXT, parse_mode=None)
        except TelegramRetryAfter as exc:
            deadline = time.time() + max(0, exc.retry_after) + 1
            append({'user_id': uid, 'status': 'rate_limited', 'retry_not_before': deadline})
            await wait_until(deadline, progress)
            continue
        except TelegramUnauthorizedError:
            return {'user_id': uid, 'status': 'unauthorized'}
        except TelegramForbiddenError:
            return {'user_id': uid, 'status': 'unreachable'}
        except TelegramBadRequest as exc:
            status = 'unreachable' if any(fragment in exc.message.lower() for fragment in (
                'chat not found', 'user not found', 'user is deactivated')) else 'payload_error'
            return {'user_id': uid, 'status': status, 'error_type': type(exc).__name__}
        except (TelegramNetworkError, TelegramAPIError, TimeoutError) as exc:
            return {'user_id': uid, 'status': 'unknown_outcome', 'error_type': type(exc).__name__}
        else:
            if (message.chat.id != uid or message.chat.type != 'private' or message.text != TEXT
                    or not private_id(message.message_id)):
                return {'user_id': uid, 'status': 'unknown_outcome',
                        'error_type': 'UnexpectedMessageResult', 'stop': True}
            return {'user_id': uid, 'status': 'sent', 'message_id': message.message_id}


def private_file(path, flags):
    fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, 'a' if flags & os.O_APPEND else 'w', encoding='utf-8')


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_json(path, payload, exclusive=False):
    with private_file(path, os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def save_delivery(row, registry):
    record_message(platform='telegram', direction='outgoing', chat_id=row['user_id'],
        message_id=row['message_id'], message_kind=KIND, telegram_method='SendMessage',
        schedule_date=DATE, entity=ENTITY, content=TEXT, db_path=registry)


async def main(apply=False):
    env = dotenv_values(ROOT / '.env')
    registry = Path(env.get('MESSAGE_REGISTRY_PATH') or REGISTRY)
    affected = load_affected(registry=registry)
    snapshot, journal_path = AUDIT / 'plan.json', AUDIT / 'results.jsonl'
    if snapshot.exists() and json.loads(snapshot.read_text()) != affected:
        raise ValueError('Notice snapshot differs from the proven affected cohort')
    recipients = affected['recipients']
    rows = read_journal(journal_path)
    if not apply:
        print(json.dumps({**summary(recipients, rows), 'mode': 'dry-run',
            'already_delivered': sum(registered_delivery(item['user_id'], registry) is not None for item in recipients),
            'current_registrations': sum(same_registration(item) for item in recipients),
            'no_messages_sent': True}, ensure_ascii=False), flush=True)
        return
    if not env.get('BOT_TOKEN') or AUDIT.is_symlink():
        raise ValueError('Missing credentials or unsafe notice directory')
    async with Bot(env['BOT_TOKEN'], session=AiohttpSession(timeout=20)) as bot:
        await validate_bot(bot)
        AUDIT.mkdir(mode=0o700, exist_ok=True)
        AUDIT.chmod(0o700)
        sync_directory(AUDIT.parent)
        with private_file(AUDIT / 'lock', os.O_WRONLY | os.O_CREAT | os.O_APPEND) as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            affected = load_affected(registry=registry)
            if snapshot.exists():
                if json.loads(snapshot.read_text()) != affected:
                    raise ValueError('Notice snapshot changed')
            else:
                durable_json(snapshot, affected, exclusive=True)
            recipients = affected['recipients']
            rows = read_journal(journal_path)
            allowed = {item['user_id'] for item in recipients}
            if any(row['user_id'] not in allowed for row in rows):
                raise ValueError('Unexpected notice journal recipient')
            registry_errors, stopped = 0, False
            for row in rows:
                if row['status'] == 'sent':
                    save_delivery(row, registry)
            last_progress = time.monotonic()
            with private_file(journal_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND) as journal:
                sync_directory(AUDIT)
                def append(row):
                    journal.write(json.dumps(row, ensure_ascii=False) + '\n')
                    journal.flush()
                    os.fsync(journal.fileno())
                    rows.append(row)

                def progress(phase='announcing', wait=None, force=False):
                    nonlocal last_progress
                    if force or wait is not None or time.monotonic() - last_progress >= 20:
                        report = {**summary(recipients, rows), 'phase': phase}
                        if wait is not None:
                            report['wait_seconds_remaining'] = wait
                        print(json.dumps(report, ensure_ascii=False), flush=True)
                        last_progress = time.monotonic()

                for row in list(latest_rows(rows).values()):
                    if row['status'] == 'attempting':
                        append({'user_id': row['user_id'], 'status': 'unknown_outcome',
                                'error_type': 'InterruptedRequest'})
                previous = latest_rows(rows)
                progress(force=True)
                for recipient in recipients:
                    old = previous.get(recipient['user_id'], {})
                    if old and old['status'] != 'rate_limited':
                        continue
                    row = await deliver(bot, recipient, append, registry, progress=progress,
                                        retry_not_before=old.get('retry_not_before', 0))
                    append(row)  # Durable success precedes registry writes; recoverable on resume.
                    if row['status'] == 'sent':
                        try:
                            save_delivery(row, registry)
                        except Exception:
                            registry_errors += 1
                    progress()
                    if row['status'] in ('payload_error', 'unauthorized') or row.get('stop'):
                        stopped = True
                        break
                    await asyncio.sleep(0.10)
            report = {**summary(recipients, rows), 'stopped': stopped,
                      'registry_errors': registry_errors, 'finished_at': datetime.now(timezone.utc).isoformat()}
            durable_json(AUDIT / 'summary.json', report)
            print(json.dumps(report, ensure_ascii=False), flush=True)
            if stopped:
                raise RuntimeError('Notice campaign stopped safely')


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true', help='Send the exact approved repair notice')
    mode.add_argument('--dry-run', action='store_true', help='Read-only evidence/audience summary (default)')
    return parser.parse_args(argv)


if __name__ == '__main__':
    try:
        asyncio.run(main(apply=arguments().apply))
    except KeyboardInterrupt:
        print(json.dumps({'status': 'stopped', 'campaign': ENTITY, 'reason': 'interrupted'}), flush=True)
        sys.exit(130)
    except Exception as exc:
        print(json.dumps({'status': 'stopped', 'campaign': ENTITY, 'error_type': type(exc).__name__}), flush=True)
        sys.exit(1)
