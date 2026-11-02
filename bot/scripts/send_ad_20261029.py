"""Deliver the owner's saved post at 09:00 Moscow, once per registered user."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
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
from aiogram.exceptions import (TelegramAPIError, TelegramBadRequest,
    TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter,
    TelegramUnauthorizedError)
import config
from admin_post_capture import (CAMPAIGN_ID, OWNER, post_digest, send_saved_post,
                                write_private_json)
from message_registry import record_message

DIRECTORY = Path(config.DATA_DIR) / 'admin_campaigns' / CAMPAIGN_ID
ROLES = ('Я студент', 'Я преподаватель')
SEND_AT = datetime.fromisoformat('2026-10-29T09:00:00+03:00')


def load_campaign(require_ready=True):
    campaign = json.loads((DIRECTORY / 'campaign.json').read_text())
    if (campaign.get('id') != CAMPAIGN_ID or campaign.get('owner') != OWNER
            or campaign.get('roles') != list(ROLES)
            or campaign.get('send_at') != SEND_AT.isoformat()):
        raise ValueError('Campaign identity, audience or time differs')
    if require_ready and campaign.get('phase') not in ('ready', 'sending', 'complete'):
        raise ValueError('No saved and previewed post; no broadcast allowed')
    post_path = DIRECTORY / 'post.json'
    post = json.loads(post_path.read_text()) if post_path.exists() else None
    if require_ready and (not post or post_digest(post) != campaign.get('post_sha256')):
        raise ValueError('Saved post differs from the preview')
    return campaign, post


def current_recipients(database=None):
    with closing(sqlite3.connect(f'file:{database or config.get_db_path()}?mode=ro', uri=True)) as db:
        return [dict(user_id=uid, role=role) for uid, role in db.execute(
            "SELECT user_id,role FROM users WHERE role IN (?,?) "
            "AND typeof(user_id)='integer' AND user_id>0 ORDER BY user_id", ROLES)]


def eligible(uid, database=None):
    if type(uid) is not int or uid <= 0:
        return False
    with closing(sqlite3.connect(f'file:{database or config.get_db_path()}?mode=ro', uri=True)) as db:
        return bool(db.execute('SELECT 1 FROM users WHERE user_id=? AND role IN (?,?)',
                               (uid, *ROLES)).fetchone())


def append(log, rows, value):
    log.write(json.dumps(value, ensure_ascii=False) + '\n')
    log.flush()
    os.fsync(log.fileno())
    rows.append(value)


def report_for(targets, rows):
    allowed = {r['user_id'] for r in targets}
    latest = {r['user_id']: r for r in rows if r['user_id'] in allowed}
    counts = Counter(r['status'] for r in latest.values())
    return dict(campaign=CAMPAIGN_ID, planned=len(targets), processed=len(latest),
        audience=dict(Counter(r['role'] for r in targets)), counts=dict(counts),
        complete=len(latest) == len(targets) and not counts['attempting'] and not counts['rate_limited'])


async def deliver(bot, uid, post, record, retry_at=0):
    while True:
        if retry_at > time.time():
            await asyncio.sleep(min(retry_at - time.time(), 20))
            continue
        if not eligible(uid):
            return dict(user_id=uid, status='excluded_registration_changed')
        record(dict(user_id=uid, status='attempting', at=datetime.now(timezone.utc).isoformat()))
        try:
            messages = await send_saved_post(bot, uid, post)
        except TelegramRetryAfter as exc:
            retry_at = time.time() + max(0, exc.retry_after) + 1
            record(dict(user_id=uid, status='rate_limited', retry_not_before=retry_at))
            continue
        except TelegramForbiddenError:
            return dict(user_id=uid, status='unreachable')
        except TelegramUnauthorizedError:
            return dict(user_id=uid, status='unauthorized', stop=True)
        except TelegramBadRequest as exc:
            missing = any(s in exc.message.lower() for s in
                          ('chat not found', 'user not found', 'user is deactivated'))
            return dict(user_id=uid, status='unreachable' if missing else 'payload_error', stop=not missing)
        except (TelegramNetworkError, TelegramAPIError):
            # Telegram provides no idempotency key. Never retry an uncertain send.
            return dict(user_id=uid, status='unknown_outcome')
        if len(messages) != len(post['messages']) or any(
                m.chat.id != uid or m.chat.type != 'private' for m in messages):
            raise ValueError('Unexpected destination or partial Telegram result')
        return dict(user_id=uid, status='sent', message_ids=[m.message_id for m in messages])


async def run(send=False):
    campaign, post = load_campaign(require_ready=send)
    if not send:
        recipients = current_recipients()
        print(json.dumps(dict(mode='check', no_messages_sent=True, phase=campaign['phase'],
            send_at=SEND_AT.isoformat(), send_at_utc=SEND_AT.astimezone(timezone.utc).isoformat(),
            current_audience=dict(Counter(r['role'] for r in recipients)),
            post_saved=post is not None, ready=campaign['phase'] == 'ready'), ensure_ascii=False), flush=True)
        return
    now = datetime.now(timezone.utc)
    if now < SEND_AT or now > SEND_AT + timedelta(hours=2):
        raise ValueError('Outside the approved broadcast time window')
    os.umask(0o077)
    with (DIRECTORY / 'broadcast.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        targets_path = DIRECTORY / 'recipients.json'
        if not targets_path.exists():
            write_private_json(targets_path, current_recipients())
        targets = json.loads(targets_path.read_text())
        ids = {r['user_id'] for r in targets}
        if len(ids) != len(targets) or any(type(uid) is not int or uid <= 0 for uid in ids) or any(
                r['role'] not in ROLES for r in targets):
            raise ValueError('Invalid private recipient snapshot')
        journal_path = DIRECTORY / 'results.jsonl'
        rows = [json.loads(line) for line in journal_path.read_text().splitlines()] if journal_path.exists() else []
        if any(r['user_id'] not in ids for r in rows):
            raise ValueError('Unexpected journal recipient')
        campaign['phase'] = 'sending'
        write_private_json(DIRECTORY / 'campaign.json', campaign)
        async with Bot(config.get_bot_token(), session=AiohttpSession(timeout=20)) as bot:
            me = await bot.get_me()
            if me.id != 7446409382 or not me.is_bot:
                raise ValueError('Unexpected Telegram bot')
            stopped, registry_errors, consecutive_unknown = False, 0, 0
            with journal_path.open('a') as journal:
                def record(row):
                    append(journal, rows, row)
                latest = {r['user_id']: r for r in rows}
                for row in list(latest.values()):
                    if row['status'] == 'attempting':
                        record(dict(user_id=row['user_id'], status='unknown_outcome'))
                latest = {r['user_id']: r for r in rows}
                last_report = time.monotonic()
                print(json.dumps(report_for(targets, rows), ensure_ascii=False), flush=True)
                for target in targets:
                    uid = target['user_id']
                    old = latest.get(uid)
                    if old and old['status'] != 'rate_limited':
                        continue
                    result = await deliver(bot, uid, post, record,
                                           old.get('retry_not_before', 0) if old else 0)
                    record(result)
                    consecutive_unknown = consecutive_unknown + 1 if result['status'] == 'unknown_outcome' else 0
                    if result['status'] == 'sent':
                        for index, mid in enumerate(result['message_ids']):
                            source = post['messages'][index]
                            try:
                                record_message(platform='telegram', direction='outgoing', chat_id=uid,
                                    message_id=mid, message_kind='advertisement', telegram_method='savedPost',
                                    entity=CAMPAIGN_ID, content=source.get('caption') or source.get('text'))
                            except Exception:
                                registry_errors += 1
                    if time.monotonic() - last_report >= 20:
                        print(json.dumps(report_for(targets, rows), ensure_ascii=False), flush=True)
                        last_report = time.monotonic()
                    if result.get('stop') or consecutive_unknown >= 5:
                        stopped = True
                        break
                    await asyncio.sleep(0.12)
            report = {**report_for(targets, rows), 'stopped': stopped, 'registry_errors': registry_errors,
                      'finished_at': datetime.now(timezone.utc).isoformat()}
            write_private_json(DIRECTORY / 'summary.json', report)
            campaign['phase'] = 'complete' if report['complete'] else 'incomplete'
            write_private_json(DIRECTORY / 'campaign.json', campaign)
            print(json.dumps(report, ensure_ascii=False), flush=True)
            marker = DIRECTORY / 'report-attempt.json'
            if not marker.exists():
                write_private_json(marker, dict(at=datetime.now(timezone.utc).isoformat()))
                counts = report['counts']
                await bot.send_message(chat_id=OWNER, parse_mode=None,
                    text=('Рассылка завершена.' if report['complete'] else 'Рассылка остановлена.')
                         + f"\nОтправлено: {counts.get('sent', 0)}."
                         + f"\nНедоступны: {counts.get('unreachable', 0)}."
                         + (f"\nРезультат не подтверждён: {counts['unknown_outcome']}."
                            if counts.get('unknown_outcome') else '')
                         + (f"\nОсталось обработать: {report['planned'] - report['processed']}."
                            if not report['complete'] else ''))
            if stopped:
                raise RuntimeError('Broadcast stopped; review its durable journal')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send', action='store_true')
    try:
        asyncio.run(run(send=parser.parse_args().send))
    except KeyboardInterrupt:
        print(json.dumps(dict(status='interrupted')), flush=True)
        sys.exit(130)
    except Exception as error:
        print(json.dumps(dict(status='stopped', error_type=type(error).__name__)), flush=True)
        sys.exit(1)
