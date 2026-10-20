"""Send one owner-only moderation preview; never schedules or broadcasts."""
from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aiogram import Bot, types
from dotenv import dotenv_values
from message_registry import record_message

OWNER = 1084693264
MOSCOW = ZoneInfo("Europe/Moscow")
PROPOSED_SEND_AT = datetime(2026, 10, 7, 11, 44, tzinfo=MOSCOW)
CANVAS_DATABASE = Path('/var/lib/kkepik-pixels/canvas.sqlite3')
AUDIT = ROOT / 'audits/pixel-battle-last-day-20261007'
WEBAPP_URL = 'https://kkepik.rub1kub.ru/'


def read_deadline(database=CANVAS_DATABASE):
    with closing(sqlite3.connect(f'file:{database}?mode=ro', uri=True)) as db:
        row = db.execute('SELECT ends_at_ms FROM canvas_event WHERE id=1').fetchone()
    if not row or not row[0]:
        raise ValueError('The event must have a persisted deadline')
    return datetime.fromtimestamp(row[0] / 1000, timezone.utc)


def build_message(deadline, send_at=PROPOSED_SEND_AT):
    if deadline.tzinfo is None or send_at.tzinfo is None:
        raise ValueError('Explicit time zones are required')
    if deadline.astimezone(MOSCOW).date() != send_at.astimezone(MOSCOW).date():
        raise ValueError('The announcement is only valid on the last day')
    remaining = int((deadline - send_at).total_seconds()) // 60
    if remaining < 1:
        raise ValueError('The event must still be active at send time')
    hours, minutes = divmod(remaining, 60)
    countdown = f'{hours} ч {minutes} мин' if hours else f'{minutes} мин'
    return (
        '🎨 <b>Сегодня — последний день пиксель-батла!</b>\n\n'
        f'⏳ До завершения осталось <b>{countdown}</b>. '
        f'Рисование закроется сегодня в <b>{deadline.astimezone(MOSCOW):%H:%M} мск</b>.\n\n'
        'Залетай на общее полотно: дорисуй своё, поддержи рисунок своей группы '
        'и оставь след в ККЭПике. Успей!'
    )


def preview_keyboard():
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(
            text='🎨 Открыть пиксель-батл',
            style='primary',
            web_app=types.WebAppInfo(url=WEBAPP_URL),
        ),
    ]])


async def send_owner_preview(bot, text):
    return await bot.send_message(
        chat_id=OWNER,
        text=text,
        parse_mode='HTML',
        reply_markup=preview_keyboard(),
    )


async def main():
    deadline = read_deadline()
    text = build_message(deadline)
    if datetime.now(timezone.utc) >= PROPOSED_SEND_AT:
        raise SystemExit('Proposed send time has passed; obtain a new time first')
    env = dotenv_values(ROOT / '.env')
    async with Bot(env['BOT_TOKEN']) as bot:
        me = await bot.get_me()
        owner = await bot.get_chat(OWNER)
        assert me.username == 'kkepik_bot'
        assert owner.id == OWNER and owner.type == 'private' and owner.username == 'rub1kub'
        AUDIT.mkdir(mode=0o700, exist_ok=True)
        # A durable exclusive marker prevents a duplicate after an uncertain API response.
        with (AUDIT / 'moderation.json').open('x') as record:
            json.dump({
                'status': 'pending_owner_approval',
                'approved': False,
                'broadcast_scheduled': False,
                'preview_recipient': OWNER,
                'broadcast_role': 'Я студент',
                'proposed_send_at': PROPOSED_SEND_AT.isoformat(),
                'timezone_requires_confirmation': True,
                'event_ends_at': deadline.isoformat(),
                'text_html': text,
                'button_text': '🎨 Открыть пиксель-батл',
                'button_web_app_url': WEBAPP_URL,
            }, record, ensure_ascii=False, indent=2)
            record.flush()
            os.fsync(record.fileno())
        result = await send_owner_preview(bot, text)
        assert result.chat.id == OWNER and result.chat.type == 'private'
        with (AUDIT / 'preview_result.json').open('x') as record:
            record.write(result.model_dump_json(exclude_none=True))
            record.flush()
            os.fsync(record.fileno())
        record_message(
            platform='telegram', direction='outgoing', chat_id=OWNER,
            message_id=result.message_id, message_kind='pixel_battle_last_day_preview',
            telegram_method='sendMessage', entity='pixel-battle-last-day-20261007',
            content=text,
            db_path=env.get('MESSAGE_REGISTRY_PATH') or ROOT / 'data/message_registry.db',
        )
        print(json.dumps({'recipient': OWNER, 'message_id': result.message_id,
                          'status': 'pending_owner_approval', 'broadcast_scheduled': False}))


if __name__ == '__main__':
    asyncio.run(main())
