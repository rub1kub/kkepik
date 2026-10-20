"""Explicitly authorized private-user forward of perehodnikkepa/4; dry-run by default."""
import argparse
import asyncio
from collections import Counter
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

SOURCE = '@perehodnikkepa'
POST_ID = 4
SOURCE_URL = 'https://t.me/perehodnikkepa/4'
AUDIT = ROOT / 'audits/forward-perehodnikkepa-4-20260921'
DATABASE = ROOT / 'data/database.db'


def private_ids(rows):
    return sorted({uid for (uid,) in rows if type(uid) is int and uid > 0})


def recipients():
    with sqlite3.connect(f'file:{DATABASE}?mode=ro', uri=True) as db:
        return private_ids(db.execute('SELECT user_id FROM users').fetchall())


async def deliver(bot, uid):
    if type(uid) is not int or uid <= 0:
        raise ValueError('Only private user IDs are permitted')
    for attempt in range(4):
        try:
            return await bot.forward_message(chat_id=uid, from_chat_id=SOURCE, message_id=POST_ID)
        except TelegramRetryAfter as exc:
            if attempt == 3:
                raise
            await asyncio.sleep(exc.retry_after + 1)


def save_id(uid, mid, registry):
    record_message(platform='telegram', direction='outgoing', chat_id=uid,
                   message_id=mid, message_kind='forwarded_post',
                   telegram_method='forwardMessage', entity=SOURCE_URL, db_path=registry)


async def main(apply):
    env = dotenv_values(ROOT / '.env')
    registry = env.get('MESSAGE_REGISTRY_PATH') or str(ROOT / 'data/message_registry.db')
    targets = recipients()
    with sqlite3.connect(f'file:{registry}?mode=ro', uri=True) as db:
        delivered = {int(uid): int(mid) for uid, mid in db.execute(
            "SELECT chat_id,message_id FROM message_registry WHERE platform='telegram' "
            "AND direction='outgoing' AND entity=?", (SOURCE_URL,))}
    print(json.dumps({'registered': len(targets), 'already_delivered': len(set(targets) & delivered.keys()),
                      'new_recipients': len(set(targets) - delivered.keys()), 'apply': apply}), flush=True)
    if not apply:
        return
    if not env.get('BOT_TOKEN'):
        raise RuntimeError('BOT_TOKEN is not configured')
    AUDIT.mkdir(exist_ok=True, mode=0o700)
    with (AUDIT / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = AUDIT / 'plan.json'
        if plan.exists():
            targets = json.loads(plan.read_text())['user_ids']
        else:
            plan.write_text(json.dumps({'source': SOURCE_URL, 'user_ids': targets}, indent=2))
        journal = AUDIT / 'results.jsonl'
        previous = [json.loads(line) for line in journal.read_text().splitlines()] if journal.exists() else []
        # Reconcile a successful forward if registry writing was interrupted.
        for row in previous:
            if row['status'] == 'sent':
                save_id(row['user_id'], row['message_id'], registry)
        attempted = {row['user_id'] for row in previous}
        counts = Counter()
        with journal.open('a', buffering=1) as log:
            def append(row):
                log.write(json.dumps(row, ensure_ascii=False) + '\n')
                log.flush()
                os.fsync(log.fileno())

            async with Bot(env['BOT_TOKEN']) as bot:
                for uid in targets:
                    if uid in attempted:
                        counts['previously_attempted'] += 1
                        continue
                    if uid in delivered:
                        append({'user_id': uid, 'status': 'already_delivered', 'message_id': delivered[uid]})
                        counts['already_delivered'] += 1
                        continue
                    with sqlite3.connect(f'file:{DATABASE}?mode=ro', uri=True) as db:
                        exists = db.execute('SELECT 1 FROM users WHERE user_id=?', (uid,)).fetchone()
                    if not exists:
                        append({'user_id': uid, 'status': 'unregistered'})
                        counts['unregistered'] += 1
                        continue
                    # Never retry uncertain outcomes: Telegram has no send idempotency key.
                    append({'user_id': uid, 'status': 'attempting'})
                    try:
                        msg = await deliver(bot, uid)
                    except TelegramRetryAfter as exc:
                        append({'user_id': uid, 'status': 'rate_limited', 'retry_after': exc.retry_after})
                        counts['rate_limited'] += 1
                        break
                    except TelegramUnauthorizedError:
                        append({'user_id': uid, 'status': 'unauthorized'})
                        counts['unauthorized'] += 1
                        break
                    except TelegramForbiddenError as exc:
                        append({'user_id': uid, 'status': 'unreachable', 'error': exc.message})
                        counts['unreachable'] += 1
                    except TelegramBadRequest as exc:
                        append({'user_id': uid, 'status': 'failed', 'error': exc.message})
                        counts['failed'] += 1
                        if 'chat not found' not in exc.message.lower():
                            break
                    except (TelegramNetworkError, TelegramAPIError) as exc:
                        append({'user_id': uid, 'status': 'unknown_outcome', 'error_type': type(exc).__name__})
                        counts['unknown_outcome'] += 1
                        break
                    else:
                        assert msg.chat.type == 'private' and msg.chat.id == uid
                        append({'user_id': uid, 'status': 'sent', 'message_id': msg.message_id})
                        save_id(uid, msg.message_id, registry)
                        counts['sent'] += 1
                    if sum(counts.values()) % 100 == 0:
                        print(json.dumps(dict(counts)), flush=True)
                    await asyncio.sleep(0.10)
        print(json.dumps({'result': dict(counts), 'audit': str(AUDIT)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    asyncio.run(main(parser.parse_args().apply))
