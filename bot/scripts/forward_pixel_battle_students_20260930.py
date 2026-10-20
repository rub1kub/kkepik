"""Authorized student-only forwarding of perehodnikkepa/11, with a durable journal."""
import asyncio
from collections import Counter
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
from aiogram.exceptions import (
    TelegramAPIError, TelegramBadRequest, TelegramForbiddenError,
    TelegramNetworkError, TelegramRetryAfter, TelegramUnauthorizedError,
)
from dotenv import dotenv_values
from message_registry import record_message

DATABASE = ROOT / 'data/database.db'
AUDIT = ROOT / 'audits/forward-pixel-battle-students-20260930'
SOURCE_CHAT = -1004435380193
SOURCE_MESSAGE = 11
SOURCE_URL = 'https://t.me/perehodnikkepa/11'
ROLE = 'Я студент'
BUTTON = {'inline_keyboard': [[{'text': 'Начать рисовать', 'url': 'https://t.me/kkepik_bot?startapp'}]]}


def current_students():
    with sqlite3.connect(f'file:{DATABASE}?mode=ro', uri=True) as db:
        return [uid for (uid,) in db.execute(
            "SELECT user_id FROM users WHERE role=? AND typeof(user_id)='integer' "
            "AND user_id>0 ORDER BY user_id", (ROLE,))]


def is_student(uid):
    with sqlite3.connect(f'file:{DATABASE}?mode=ro', uri=True) as db:
        return type(uid) is int and uid > 0 and bool(db.execute(
            'SELECT 1 FROM users WHERE user_id=? AND role=?', (uid, ROLE)).fetchone())


async def main():
    env = dotenv_values(ROOT / '.env')
    registry = env.get('MESSAGE_REGISTRY_PATH') or str(ROOT / 'data/message_registry.db')
    AUDIT.mkdir(mode=0o700, exist_ok=True)
    with (AUDIT / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = AUDIT / 'plan.json'
        if not plan.exists():
            plan.write_text(json.dumps({'source': SOURCE_URL, 'role': ROLE,
                                       'user_ids': current_students()}, ensure_ascii=False, indent=2))
        targets = json.loads(plan.read_text())['user_ids']
        journal = AUDIT / 'results.jsonl'
        previous = [json.loads(line) for line in journal.read_text().splitlines()] if journal.exists() else []
        attempted = {row['user_id'] for row in previous if row['status'] != 'rate_limited'}
        counts = Counter(row['status'] for row in previous if row['status'] not in ('attempting', 'rate_limited', 'button_restored'))

        def register(uid, mid):
            record_message(platform='telegram', direction='outgoing', chat_id=uid,
                           message_id=mid, message_kind='student_forwarded_post',
                           telegram_method='forwardMessage', entity=SOURCE_URL, db_path=registry)

        for row in previous:
            if row['status'] == 'sent':
                register(row['user_id'], row['message_id'])
        with sqlite3.connect(f'file:{registry}?mode=ro', uri=True) as db:
            delivered = {int(uid) for (uid,) in db.execute(
                "SELECT chat_id FROM message_registry WHERE platform='telegram' "
                "AND direction='outgoing' AND entity=?", (SOURCE_URL,))}
        with journal.open('a', buffering=1) as log:
            def append(row):
                log.write(json.dumps(row, ensure_ascii=False) + '\n')
                log.flush()
                os.fsync(log.fileno())

            print(json.dumps({'planned_students': len(targets), 'previously_attempted': len(attempted)}), flush=True)
            last_progress = time.monotonic()
            async with Bot(env['BOT_TOKEN']) as bot:
                me = await bot.get_me()
                assert me.username == 'kkepik_bot'
                for index, uid in enumerate(targets, 1):
                    if uid in attempted:
                        continue
                    if uid in delivered:
                        append({'user_id': uid, 'status': 'already_delivered'})
                        counts['already_delivered'] += 1
                        continue
                    # A snapshot never overrides the student's current role.
                    if not is_student(uid):
                        append({'user_id': uid, 'status': 'excluded_role_changed'})
                        counts['excluded_role_changed'] += 1
                        continue
                    append({'user_id': uid, 'status': 'attempting'})
                    while True:
                        try:
                            if not is_student(uid):
                                append({'user_id': uid, 'status': 'excluded_role_changed'})
                                counts['excluded_role_changed'] += 1
                                break
                            msg = await bot.forward_message(chat_id=uid, from_chat_id=SOURCE_CHAT,
                                                            message_id=SOURCE_MESSAGE)
                        except TelegramRetryAfter as exc:
                            append({'user_id': uid, 'status': 'rate_limited', 'retry_after': exc.retry_after})
                            print(json.dumps({'rate_limit_seconds': exc.retry_after, 'counts': dict(counts)}), flush=True)
                            remaining = exc.retry_after + 1
                            while remaining > 0:
                                delay = min(remaining, 30)
                                await asyncio.sleep(delay)
                                remaining -= delay
                            continue
                        except TelegramForbiddenError as exc:
                            append({'user_id': uid, 'status': 'unreachable', 'error': exc.message})
                            counts['unreachable'] += 1
                        except TelegramBadRequest as exc:
                            append({'user_id': uid, 'status': 'failed', 'error': exc.message})
                            counts['failed'] += 1
                            if 'chat not found' not in exc.message.lower():
                                raise
                        except TelegramUnauthorizedError:
                            append({'user_id': uid, 'status': 'unauthorized'})
                            raise
                        except (TelegramNetworkError, TelegramAPIError) as exc:
                            # Do not resend after an uncertain response: Telegram has no idempotency key.
                            append({'user_id': uid, 'status': 'unknown_outcome', 'error_type': type(exc).__name__})
                            counts['unknown_outcome'] += 1
                        else:
                            assert msg.chat.type == 'private' and msg.chat.id == uid
                            origin = msg.forward_origin
                            assert origin and origin.type == 'channel' and origin.chat.id == SOURCE_CHAT and origin.message_id == SOURCE_MESSAGE
                            append({'user_id': uid, 'status': 'sent', 'message_id': msg.message_id,
                                    'button_preserved': bool(msg.reply_markup)})
                            counts['sent'] += 1
                            register(uid, msg.message_id)
                            if not msg.reply_markup:
                                try:
                                    await bot.edit_message_reply_markup(chat_id=uid, message_id=msg.message_id,
                                                                        reply_markup=BUTTON)
                                except TelegramAPIError as exc:
                                    append({'user_id': uid, 'status': 'button_error', 'message_id': msg.message_id,
                                            'error_type': type(exc).__name__})
                                    counts['button_error'] += 1
                                else:
                                    append({'user_id': uid, 'status': 'button_restored', 'message_id': msg.message_id})
                        break
                    if time.monotonic() - last_progress >= 20:
                        print(json.dumps({'processed': index, 'total': len(targets), 'counts': dict(counts)}), flush=True)
                        last_progress = time.monotonic()
                    await asyncio.sleep(0.10)
            summary = {'planned_students': len(targets), 'counts': dict(counts), 'complete': True}
            (AUDIT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
            print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
