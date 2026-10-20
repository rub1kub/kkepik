"""One-off edit of existing student captions; dry-run unless --apply is supplied."""

import argparse
import asyncio
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError
from schedules.schedule_formatter import build_group_schedule_message
from schedules.schedule_keyboard import create_schedule_keyboard

GROUP = '103-Д9-3ИНС'
DATE = '10.09.2026'
OLD = 'Тестирование информационных систем'
NEW = 'Торнадо 🌪🌪🌪'
LINES = [
    '▪️1 пара – Графический дизайн и мультимедиа – Евтушенко Е.Д. – 57',
    '▪️2 пара – Оптимизация веб-приложений – Дмитриева А.С. – 48',
    '▪️3 пара – Средства разработки веб-приложений – Копылова А.И. – 104',
    '▪️4 пара – Тестирование информационных систем – Зиманина Т.Н. – 63',
]


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


async def main(apply):
    load_dotenv(ROOT / '.env')
    registry = Path(os.getenv('MESSAGE_REGISTRY_PATH') or ROOT / 'data/message_registry.db')
    with sqlite3.connect(f'file:{registry}?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('ATTACH DATABASE ? AS usersdb', (f'file:{ROOT}/data/database.db?mode=ro',))
        rows = [dict(row) for row in conn.execute('''
            SELECT m.* FROM message_registry m JOIN usersdb.users u
                ON CAST(m.chat_id AS INTEGER) = u.user_id
            WHERE m.platform = 'telegram' AND m.direction = 'outgoing'
                AND m.message_kind = 'schedule' AND m.schedule_type = 'groups'
                AND m.entity = ? AND m.schedule_date = ?
                AND u.role = 'Я студент' AND u.name_or_group = ?
            ORDER BY (m.chat_id = '1084693264') DESC, m.id
        ''', (GROUP, DATE, GROUP))]

    caption = build_group_schedule_message(LINES, GROUP, DATE, show_group=False)
    plain = html.unescape(re.sub(r'<[^>]+>', '', caption))
    changed = plain.replace(OLD, NEW)
    assert caption.count(OLD) == 1
    targets = []
    for row in rows:
        assert int(row['chat_id']) > 0
        assert row['telegram_method'] == 'SendPhoto'
        assert digest(row['content_preview']) == row['content_hash']
        if row['content_preview'] == changed:
            continue
        assert row['content_preview'] == plain, f"Unexpected content: registry {row['id']}"
        targets.append(row)
    print(json.dumps({'matching_messages': len(rows), 'pending_edits': len(targets),
                      'replacement': NEW, 'apply': apply}, ensure_ascii=False), flush=True)
    if not apply or not targets:
        return

    audit_dir = ROOT / 'audits' / ('caption-edit-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    audit_dir.mkdir(mode=0o700)
    (audit_dir / 'before.json').write_text(json.dumps(targets, ensure_ascii=False, indent=2))
    (audit_dir / 'caption.html').write_text(caption.replace(OLD, NEW))
    keyboard = create_schedule_keyboard(DATE, 'groups')
    successful = 0
    failed = 0
    async with Bot(token=os.environ['BOT_TOKEN']) as bot:
        for row in targets:
            with sqlite3.connect(f'file:{ROOT}/data/database.db?mode=ro', uri=True) as users:
                role = users.execute('SELECT role,name_or_group FROM users WHERE user_id=?',
                                     (int(row['chat_id']),)).fetchone()
            assert role == ('Я студент', GROUP), 'Recipient changed since preflight'
            result = None
            for attempt in range(3):
                try:
                    result = await bot.edit_message_caption(
                        chat_id=int(row['chat_id']), message_id=int(row['message_id']),
                        caption=caption.replace(OLD, NEW), parse_mode='HTML', reply_markup=keyboard)
                    break
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(exc.retry_after + 1)
                except TelegramAPIError as exc:
                    failed += 1
                    with (audit_dir / 'results.jsonl').open('a') as log:
                        log.write(json.dumps({'registry_id': row['id'], 'status': 'error',
                                              'error': exc.message}, ensure_ascii=False) + '\n')
                    break
            else:
                raise RuntimeError('Retry limit reached')
            if result is None:
                continue
            assert result.caption == changed
            assert result.photo and result.reply_markup
            assert result.chat.id == int(row['chat_id']) and result.chat.type == 'private'
            assert result.message_id == int(row['message_id'])
            with (audit_dir / 'results.jsonl').open('a') as log:
                log.write(json.dumps({'registry_id': row['id'], 'status': 'edited',
                                      'message': result.model_dump(mode='json', exclude_none=True)},
                                     ensure_ascii=False) + '\n')
            with sqlite3.connect(registry, timeout=10) as conn:
                conn.execute('''UPDATE message_registry SET content_preview=?,content_hash=?,
                                updated_at=CURRENT_TIMESTAMP WHERE id=? AND content_hash=?''',
                             (changed, digest(changed), row['id'], row['content_hash']))
            successful += 1
            await asyncio.sleep(0.15)
    print(json.dumps({'edited': successful, 'failed': failed, 'audit': str(audit_dir)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    asyncio.run(main(parser.parse_args().apply))
