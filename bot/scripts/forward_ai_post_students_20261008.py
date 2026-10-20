"""Authorized Telegram forward of perehodnikkepa/12; read-only by default.

Only --apply starts the campaign. Telegram has no send idempotency key: a
timeout or crash after the durable attempting record must never be replayed.
The original channel post is forwarded untouched, without keyboard edits.
"""
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
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import (
    TelegramAPIError, TelegramBadRequest, TelegramForbiddenError,
    TelegramNetworkError, TelegramRetryAfter, TelegramUnauthorizedError,
)
from dotenv import dotenv_values
from message_registry import record_message

DATABASE = ROOT / 'data/database.db'
AUDIT = ROOT / 'audits/forward-ai-post-students-20261008'
SOURCE_CHAT = -1004435380193
SOURCE_MESSAGE = 12
SOURCE_URL = 'https://t.me/perehodnikkepa/12'
SOURCE_USERNAME = 'perehodnikkepa'
ROLE = 'Я студент'
STATUSES = frozenset(('attempting', 'rate_limited', 'sent', 'already_delivered',
    'excluded_role_changed', 'unreachable', 'unknown_outcome', 'unauthorized', 'source_error'))


def private_id(value):
    return type(value) is int and value > 0


def current_students(database=DATABASE):
    with closing(sqlite3.connect(f'file:{database}?mode=ro', uri=True)) as db:
        return [uid for (uid,) in db.execute(
            "SELECT DISTINCT user_id FROM users WHERE role=? AND typeof(user_id)='integer' "
            "AND user_id>0 ORDER BY user_id", (ROLE,)) if private_id(uid)]


def is_student(uid, database=DATABASE):
    if not private_id(uid):
        return False
    with closing(sqlite3.connect(f'file:{database}?mode=ro', uri=True)) as db:
        return bool(db.execute(
            "SELECT 1 FROM users WHERE user_id=? AND role=? AND typeof(user_id)='integer'",
            (uid, ROLE)).fetchone())


def registered_delivery(uid, registry):
    with closing(sqlite3.connect(f'file:{registry}?mode=ro', uri=True)) as db:
        row = db.execute(
            "SELECT message_id FROM message_registry WHERE platform='telegram' "
            "AND direction='outgoing' AND chat_id=? AND entity=? LIMIT 1",
            (str(uid), SOURCE_URL)).fetchone()
    if row is None:
        return None
    try:
        mid = int(row[0])
    except (TypeError, ValueError):
        raise ValueError('Invalid existing delivery record') from None
    if not private_id(mid):
        raise ValueError('Invalid existing delivery record')
    return mid


def read_journal(path):
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    for row in rows:
        if not isinstance(row, dict) or not private_id(row.get('user_id')) or row.get('status') not in STATUSES:
            raise ValueError('Journal requires manual review')
        if row['status'] == 'sent' and not private_id(row.get('message_id')):
            raise ValueError('Invalid confirmed delivery in journal')
    return rows


def latest_statuses(rows):
    return {row['user_id']: row['status'] for row in rows}


def already_attempted(rows):
    # Only an explicit latest 429 rejection is safe to retry after a restart.
    return {uid for uid, status in latest_statuses(rows).items() if status != 'rate_limited'}


def summary(targets, rows):
    target_set = set(targets)
    latest = {uid: status for uid, status in latest_statuses(rows).items() if uid in target_set}
    return {'post': SOURCE_URL, 'planned_students': len(targets), 'processed': len(latest),
            'counts': dict(Counter(latest.values())),
            'complete': len(latest) == len(targets) and all(
                status not in ('attempting', 'rate_limited') for status in latest.values())}


def validate_snapshot(plan):
    if (plan.get('source') != SOURCE_URL or plan.get('source_chat') != SOURCE_CHAT
            or plan.get('source_message') != SOURCE_MESSAGE or plan.get('role') != ROLE):
        raise ValueError('Campaign identity differs from the authorized post/audience')
    targets = plan.get('user_ids')
    if not isinstance(targets, list) or any(not private_id(uid) for uid in targets) or len(targets) != len(set(targets)):
        raise ValueError('Invalid private student snapshot')
    return targets


def save_delivery(uid, mid, registry):
    record_message(platform='telegram', direction='outgoing', chat_id=uid,
                   message_id=mid, message_kind='ai_post_student_forward',
                   telegram_method='forwardMessage', entity=SOURCE_URL, db_path=registry)


async def validate_bot_and_source(bot):
    me = await bot.get_me()
    if me.username != 'kkepik_bot' or me.is_bot is not True:
        raise ValueError('Unexpected Telegram bot')
    source = await bot.get_chat(SOURCE_CHAT)
    if (source.id != SOURCE_CHAT or source.type != 'channel'
            or source.username != SOURCE_USERNAME or source.has_protected_content is True):
        raise ValueError('Unexpected or protected source channel')


async def deliver_student(bot, uid, append, registry, database=DATABASE, progress=None):
    while True:
        if not is_student(uid, database):
            return {'user_id': uid, 'status': 'excluded_role_changed'}
        delivered = registered_delivery(uid, registry)
        if delivered is not None:
            return {'user_id': uid, 'status': 'already_delivered', 'message_id': delivered}
        append({'user_id': uid, 'status': 'attempting', 'at': datetime.now(timezone.utc).isoformat()})
        try:
            msg = await bot.forward_message(chat_id=uid, from_chat_id=SOURCE_CHAT, message_id=SOURCE_MESSAGE)
        except TelegramRetryAfter as exc:
            append({'user_id': uid, 'status': 'rate_limited', 'retry_after': exc.retry_after})
            remaining = exc.retry_after + 1
            while remaining > 0:
                if progress:
                    progress('rate_limit_wait', remaining)
                delay = min(remaining, 30)
                await asyncio.sleep(delay)
                remaining -= delay
            continue
        except TelegramUnauthorizedError:
            return {'user_id': uid, 'status': 'unauthorized'}
        except TelegramForbiddenError as exc:
            # A loss of source-channel access is not a blocked recipient.
            status = 'source_error' if any(part in exc.message.lower() for part in ('channel', 'not a member', 'kicked')) else 'unreachable'
            return {'user_id': uid, 'status': status, 'error_type': type(exc).__name__}
        except TelegramBadRequest as exc:
            status = 'unreachable' if any(part in exc.message.lower() for part in (
                'chat not found', 'user not found', 'user is deactivated')) else 'source_error'
            return {'user_id': uid, 'status': status, 'error_type': type(exc).__name__}
        except (TelegramNetworkError, TelegramAPIError) as exc:
            return {'user_id': uid, 'status': 'unknown_outcome', 'error_type': type(exc).__name__}
        else:
            origin = msg.forward_origin
            if (msg.chat.id != uid or msg.chat.type != 'private' or origin is None
                    or origin.type != 'channel' or origin.chat.id != SOURCE_CHAT
                    or origin.message_id != SOURCE_MESSAGE):
                return {'user_id': uid, 'status': 'unknown_outcome',
                        'error_type': 'UnexpectedForwardResult', 'stop': True}
            return {'user_id': uid, 'status': 'sent', 'message_id': msg.message_id,
                    'original_buttons_present': bool(msg.reply_markup)}


def private_file(path, flags):
    fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, 'a' if flags & os.O_APPEND else 'w', encoding='utf-8')


def durable_json(path, payload, exclusive=False):
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    with private_file(path, flags) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


async def main(apply=False):
    env = dotenv_values(ROOT / '.env')
    registry = env.get('MESSAGE_REGISTRY_PATH') or ROOT / 'data/message_registry.db'
    snapshot = AUDIT / 'plan.json'
    targets = validate_snapshot(json.loads(snapshot.read_text())) if snapshot.exists() else current_students()
    rows = read_journal(AUDIT / 'results.jsonl')
    attempted = already_attempted(rows)
    if not apply:
        delivered = sum(registered_delivery(uid, registry) is not None for uid in targets)
        print(json.dumps({'mode': 'dry-run', 'post': SOURCE_URL, 'role': ROLE,
            'planned_students': len(targets), 'current_students': len(current_students()),
            'already_delivered': delivered, 'previously_attempted': len(set(targets) & attempted),
            'no_messages_sent': True}, ensure_ascii=False), flush=True)
        return
    if not env.get('BOT_TOKEN'):
        raise ValueError('Bot credentials are missing')
    if AUDIT.is_symlink():
        raise ValueError('Campaign directory cannot be a symlink')
    async with Bot(env['BOT_TOKEN'], session=AiohttpSession(timeout=20)) as bot:
        await validate_bot_and_source(bot)
        AUDIT.mkdir(mode=0o700, exist_ok=True)
        AUDIT.chmod(0o700)
        sync_directory(AUDIT.parent)
        with private_file(AUDIT / 'lock', os.O_WRONLY | os.O_CREAT | os.O_APPEND) as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Reload under the lock; earlier reads were only a dry-run view.
            if not snapshot.exists():
                durable_json(snapshot, {'source': SOURCE_URL, 'source_chat': SOURCE_CHAT,
                    'source_message': SOURCE_MESSAGE, 'role': ROLE, 'user_ids': current_students()}, exclusive=True)
            targets = validate_snapshot(json.loads(snapshot.read_text()))
            rows = read_journal(AUDIT / 'results.jsonl')
            target_set = set(targets)
            if any(row['user_id'] not in target_set for row in rows):
                raise ValueError('Journal contains an unexpected recipient')
            registry_errors = 0
            for row in rows:
                if row['status'] == 'sent':
                    try:
                        save_delivery(row['user_id'], row['message_id'], registry)
                    except Exception:
                        registry_errors += 1
            attempted = already_attempted(rows)
            last_progress = time.monotonic()
            stopped = False
            with private_file(AUDIT / 'results.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND) as journal:
                sync_directory(AUDIT)
                def append(row):
                    journal.write(json.dumps(row, ensure_ascii=False) + '\n')
                    journal.flush()
                    os.fsync(journal.fileno())
                    rows.append(row)

                def progress(phase='forwarding', wait=None, force=False):
                    nonlocal last_progress
                    if force or wait is not None or time.monotonic() - last_progress >= 20:
                        report = {**summary(targets, rows), 'phase': phase}
                        if wait is not None:
                            report['wait_seconds_remaining'] = wait
                        print(json.dumps(report, ensure_ascii=False), flush=True)
                        last_progress = time.monotonic()

                progress(force=True)
                for uid in targets:
                    if uid in attempted:
                        continue
                    row = await deliver_student(bot, uid, append, registry, progress=progress)
                    append(row)
                    if row['status'] == 'sent':
                        try:
                            save_delivery(uid, row['message_id'], registry)
                        except Exception:
                            registry_errors += 1
                    progress()
                    if row['status'] in ('source_error', 'unauthorized') or row.get('stop'):
                        stopped = True
                        break
                    await asyncio.sleep(0.10)
            report = {**summary(targets, rows), 'stopped': stopped,
                      'registry_errors': registry_errors, 'finished_at': datetime.now(timezone.utc).isoformat()}
            durable_json(AUDIT / 'summary.json', report)
            print(json.dumps(report, ensure_ascii=False), flush=True)
            if stopped:
                raise RuntimeError('Campaign stopped safely')


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true', help='Send the exact authorized post to current students')
    mode.add_argument('--dry-run', action='store_true', help='Read-only audience/deduplication summary (default)')
    return parser.parse_args(argv)


if __name__ == '__main__':
    try:
        asyncio.run(main(apply=arguments().apply))
    except KeyboardInterrupt:
        print(json.dumps({'status': 'stopped', 'post': SOURCE_URL, 'reason': 'interrupted'}), flush=True)
        sys.exit(130)
    except Exception as exc:
        # Telegram transport exceptions can contain credential-bearing URLs.
        print(json.dumps({'status': 'stopped', 'post': SOURCE_URL, 'error_type': type(exc).__name__}), flush=True)
        sys.exit(1)
