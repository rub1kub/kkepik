"""One owner-only preview on the real September 30 schedule, no broadcast."""
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aiogram.types import BufferedInputFile
from dotenv import dotenv_values
from message_registry import TrackedBot, message_tracking_context
from schedules.pdf_to_df import pdf_to_dataframe
from schedules.pdf_crop import crop_group_screenshots
from schedules.group_schedule import get_schedule_for_group
from schedules.schedule_formatter import build_group_schedule_message, fit_photo_caption, telegram_text_length
from schedules.schedule_keyboard import create_schedule_keyboard
from schedules.schedule_notices import PIXEL_BATTLE_NOTICE

OWNER = 1084693264
GROUP = '103-Д9-3ИНС'
DATE = '30.09.2026'


def verify_notice_entities(result):
    entities = result.caption_entities or []
    emoji = next(e for e in entities if e.type == 'custom_emoji' and e.custom_emoji_id == '5431456208487716895')
    end = len(result.caption.encode('utf-16-le')) // 2
    for kind in ('bold', 'spoiler'):
        # Telegram splits formatting at custom emoji boundaries; verify full coverage.
        covered = emoji.offset
        for entity in sorted((e for e in entities if e.type == kind), key=lambda e: e.offset):
            if entity.offset <= covered:
                covered = max(covered, entity.offset + entity.length)
        assert covered >= end, kind


async def main():
    audit = ROOT / 'audits/pixel-battle-notice-preview'
    audit.mkdir(exist_ok=True, mode=0o700)
    if (audit / 'attempt.json').exists():
        raise SystemExit('Preview already attempted; refusing duplicate')
    path = ROOT / 'data' / f'Расписание на {DATE}_ГРУППЫ.pdf'
    frame = pdf_to_dataframe(str(path))
    lines = get_schedule_for_group(frame, GROUP)
    assert lines, 'Real group schedule is required for the preview'
    crops = crop_group_screenshots(str(path))
    assert GROUP in crops
    message = build_group_schedule_message(
        lines, GROUP, DATE, show_group=False,
        subject_aliases={'Тестирование информационных систем': 'Торнадо 🌪🌪🌪'},
    )
    # Preview only: retain the source PDF's actual date; production has strict date gates.
    caption = fit_photo_caption(message + '\n\n' + PIXEL_BATTLE_NOTICE)
    assert telegram_text_length(caption) <= 1024, 'Preview must fit exactly one photo message'
    assert caption.endswith('\n\n' + PIXEL_BATTLE_NOTICE)
    (audit / 'caption.html').write_text(caption)
    async with TrackedBot(dotenv_values(ROOT / '.env')['BOT_TOKEN']) as bot:
        with (audit / 'attempt.json').open('x') as record:
            json.dump({'recipient': OWNER, 'status': 'attempting'}, record)
            record.flush()
            os.fsync(record.fileno())
        with message_tracking_context(message_kind='pixel_battle_notice_preview',
                                      schedule_date=DATE, schedule_type='groups', entity=GROUP):
            result = await bot.send_photo(
                chat_id=OWNER, photo=BufferedInputFile(crops[GROUP], filename='schedule.png'),
                caption=caption, parse_mode='HTML', reply_markup=create_schedule_keyboard(DATE),
            )
        (audit / 'result.json').write_text(result.model_dump_json(exclude_none=True))
        verify_notice_entities(result)
        print(json.dumps({'recipient': result.chat.id, 'message_id': result.message_id,
                          'bold_spoiler_and_emoji_verified': True}), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
