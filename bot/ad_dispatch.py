"""Send a saved advertising post after a durable primary-schedule receipt."""
import asyncio
from collections import Counter
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

import config
from admin_post_capture import AdminPostCapture, OWNER, post_digest, write_private_json
from message_registry import record_message
from scripts.send_ad_20261029 import current_recipients, deliver, append as append_result, ROLES


RETRYABLE_REJECTIONS = frozenset({'payload_error', 'unauthorized'})


def pending_campaign(data_dir=None, now=None, *, resume=False):
    base = Path(data_dir or config.DATA_DIR) / 'admin_campaigns'
    if not (base / 'active.json').exists():
        return None, 'no_active_campaign'
    capture = AdminPostCapture.active(base)
    campaign = capture.campaign()
    if not campaign or campaign.get('trigger', {}).get('type') != 'schedule_complete':
        return None, 'not_a_schedule_campaign'
    phase = campaign['phase']
    if phase in ('complete', 'cancelled', 'expired') or (phase == 'paused_error' and not resume):
        return None, campaign['phase']
    if campaign.get('roles') != list(ROLES):
        raise ValueError('Unexpected advertising audience')
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()
    expected_day = campaign['trigger']['broadcast_day']
    if day != expected_day:
        return None, 'expired' if day > expected_day else 'not_today'
    receipt_path = capture.directory / 'schedule-complete.json'
    if not receipt_path.exists():
        return None, 'waiting_schedule'
    receipt = json.loads(receipt_path.read_text())
    completed = datetime.fromisoformat(receipt['completed_at'])
    if (receipt.get('campaign') != campaign['id']
            or receipt.get('schedule_date') != campaign['trigger']['schedule_date']
            or receipt.get('schedule_type') != 'groups' or receipt.get('is_update') is not False
            or type(receipt.get('notified_count')) is not int or receipt['notified_count'] <= 0
            or completed.tzinfo is None or completed > now
            or completed.astimezone(ZoneInfo('Europe/Moscow')).date().isoformat() != expected_day):
        raise ValueError('Invalid schedule completion receipt')
    if phase not in ('ready', 'sending', 'incomplete', 'paused_error'):
        return None, 'waiting_post'
    post = json.loads((capture.directory / 'post.json').read_text())
    if not campaign.get('preview_message_ids') or post_digest(post) != campaign.get('post_sha256'):
        raise ValueError('Saved advertisement differs from its preview')
    return (capture, campaign, post), 'ready'


def report_for(campaign_id, targets, rows):
    allowed = {r['user_id'] for r in targets}
    latest = {r['user_id']: r for r in rows if r['user_id'] in allowed}
    counts = Counter(r['status'] for r in latest.values())
    unresolved = RETRYABLE_REJECTIONS | {'attempting', 'rate_limited'}
    return dict(campaign=campaign_id, planned=len(targets), processed=len(latest), counts=dict(counts),
        complete=len(latest) == len(targets) and not any(counts[status] for status in unresolved))


async def send_campaign(capture, campaign, post, bot, *, resume=False):
    folder = capture.directory
    targets_path = folder / 'recipients.json'
    if not targets_path.exists():
        write_private_json(targets_path, current_recipients())
    targets = json.loads(targets_path.read_text())
    ids = {r['user_id'] for r in targets}
    if len(ids) != len(targets) or any(type(uid) is not int or uid <= 0 for uid in ids) or any(r['role'] not in ROLES for r in targets):
        raise ValueError('Invalid advertising recipients')
    journal_path = folder / 'results.jsonl'
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()] if journal_path.exists() else []
    if any(r['user_id'] not in ids for r in rows):
        raise ValueError('Advertising journal contains an unexpected recipient')
    campaign['phase'] = 'sending'
    write_private_json(capture.campaign_path, campaign)
    stopped, unknown, registry_errors = False, 0, 0
    with journal_path.open('a') as journal:
        retryable = {'rate_limited'}
        if resume:
            retryable.update(RETRYABLE_REJECTIONS)

        def record(row):
            append_result(journal, rows, row)
        latest = {r['user_id']: r for r in rows}
        for row in list(latest.values()):
            if row['status'] == 'attempting':
                record(dict(user_id=row['user_id'], status='unknown_outcome'))
        latest = {r['user_id']: r for r in rows}
        last_report = time.monotonic()
        for target in targets:
            uid = target['user_id']
            old = latest.get(uid)
            if old and old['status'] not in retryable:
                continue
            result = await deliver(bot, uid, post, record, old.get('retry_not_before', 0) if old else 0)
            record(result)
            unknown = unknown + 1 if result['status'] == 'unknown_outcome' else 0
            if result['status'] == 'sent':
                for index, mid in enumerate(result['message_ids']):
                    source = post['messages'][index]
                    try:
                        record_message(platform='telegram', direction='outgoing', chat_id=uid,
                            message_id=mid, message_kind='advertisement', telegram_method='savedPost',
                            entity=campaign['id'], content=source.get('caption') or source.get('text'))
                    except Exception:
                        registry_errors += 1
            if time.monotonic() - last_report >= 20:
                print(json.dumps(report_for(campaign['id'], targets, rows)), flush=True)
                last_report = time.monotonic()
            if result.get('stop') or unknown >= 5:
                stopped = True
                break
            await asyncio.sleep(0.12)
    report = {**report_for(campaign['id'], targets, rows), 'stopped': stopped,
              'registry_errors': registry_errors, 'finished_at': datetime.now(timezone.utc).isoformat()}
    write_private_json(folder / 'summary.json', report)
    campaign['phase'] = 'complete' if report['complete'] else 'paused_error' if stopped else 'incomplete'
    write_private_json(capture.campaign_path, campaign)
    marker = folder / ('report-complete-attempt.json' if report['complete'] else 'report-attempt.json')
    if not marker.exists():
        write_private_json(marker, {'at': datetime.now(timezone.utc).isoformat()})
        counts = report['counts']
        await bot.send_message(OWNER, parse_mode=None,
            text=('Рекламная рассылка завершена.' if report['complete'] else 'Рекламная рассылка остановлена.')
            + f"\nОтправлено: {counts.get('sent',0)}.\nНедоступны: {counts.get('unreachable',0)}."
            + (f"\nНе подтверждено: {counts['unknown_outcome']}." if counts.get('unknown_outcome') else '')
            + (f"\nОсталось обработать: {report['planned']-report['processed']}." if not report['complete'] else ''))
    return report


async def run(send=False, *, resume=False):
    if not send:
        pending, reason = pending_campaign(resume=resume)
        print(json.dumps({'mode':'check','reason':reason,'ready':pending is not None,'no_messages_sent':True}), flush=True)
        return
    os.umask(0o077)
    base = Path(config.DATA_DIR) / 'admin_campaigns'
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (base / 'dispatch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        last_reason = None
        while True:
            pending, reason = pending_campaign(resume=resume)
            if reason != last_reason:
                print(json.dumps({'status':reason}), flush=True); last_reason = reason
            if reason in ('complete','cancelled','paused_error','expired','no_active_campaign','not_a_schedule_campaign'):
                return
            if pending:
                capture, campaign, post = pending
                async with Bot(config.get_bot_token(), session=AiohttpSession(timeout=20)) as bot:
                    me = await bot.get_me()
                    if me.id != 7446409382 or not me.is_bot:
                        raise ValueError('Unexpected Telegram bot')
                    report = await send_campaign(capture, campaign, post, bot, resume=resume)
                    print(json.dumps(report), flush=True)
                return
            await asyncio.sleep(1)
