"""Collect the schedule's existing emoji in an owner-managed Telegram pack."""
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputSticker
from PIL import Image, ImageChops, ImageStat
from schedules.schedule_mood import DIFFICULTY_EMOJIS

OWNER = 1084693264
NAME = 'kkepik_mood_by_kkepik_bot'


async def main():
    load_dotenv(ROOT / '.env')
    audit = ROOT / 'audits/schedule-emoji-pack'
    audit.mkdir(exist_ok=True, mode=0o700)
    originals = audit / 'originals.json'
    if not originals.exists():
        originals.write_text(json.dumps(DIFFICULTY_EMOJIS, ensure_ascii=False, indent=2))
    source = json.loads(originals.read_text())
    async with Bot(os.environ['BOT_TOKEN']) as bot:
        stickers = await bot.get_custom_emoji_stickers(custom_emoji_ids=[str(i) for i, _ in source])
        by_id = {s.custom_emoji_id: s for s in stickers}
        assert len(by_id) == len(source) == 24
        ordered = [by_id[str(i)] for i, _ in source]
        (audit / 'source_stickers.json').write_text(json.dumps(
            [s.model_dump(mode='json', exclude_none=True) for s in ordered], ensure_ascii=False, indent=2))
        try:
            pack = await bot.get_sticker_set(NAME)
        except TelegramBadRequest as exc:
            if 'STICKERSET_INVALID' not in exc.message:
                raise
            await bot.create_new_sticker_set(
                user_id=OWNER, name=NAME, title='ККЭПик · Настроение дня', sticker_type='custom_emoji',
                stickers=[InputSticker(sticker=s.file_id,
                                       format='animated' if s.is_animated else 'video' if s.is_video else 'static',
                                       emoji_list=[s.emoji]) for s in ordered],
            )
            pack = await bot.get_sticker_set(NAME)
        assert pack.sticker_type == 'custom_emoji' and len(pack.stickers) == 24
        mapping = []
        for (old_id, fallback), old, new in zip(source, ordered, pack.stickers):
            assert new.set_name == NAME and new.custom_emoji_id
            assert (old.is_animated, old.is_video, old.emoji) == (new.is_animated, new.is_video, new.emoji)
            # Verify the returned order by content identity, not by emoji (several repeat).
            original_bytes = io.BytesIO()
            copied_bytes = io.BytesIO()
            await bot.download(old.file_id, destination=original_bytes)
            await bot.download(new.file_id, destination=copied_bytes)
            original_hash = hashlib.sha256(original_bytes.getvalue()).hexdigest()
            copied_hash = hashlib.sha256(copied_bytes.getvalue()).hexdigest()
            if not old.is_animated and not old.is_video:
                original_image = Image.open(original_bytes).convert('RGBA')
                copied_image = Image.open(copied_bytes).convert('RGBA')
                assert original_image.size == copied_image.size
                # Telegram re-encodes static WebP; allow small compression loss.
                delta = ImageStat.Stat(ImageChops.difference(original_image, copied_image)).mean
                assert delta[3] == 0 and max(delta[:3]) < 3, delta
            else:
                assert original_hash == copied_hash
            mapping.append({'source_id': old_id, 'custom_emoji_id': int(new.custom_emoji_id),
                            'emoji': new.emoji, 'source_pack': old.set_name, 'sha256': original_hash})
            print(f'Verified emoji {len(mapping)}/24', flush=True)
        (audit / 'pack.json').write_text(pack.model_dump_json(indent=2, exclude_none=True))
        (audit / 'mapping.json').write_text(json.dumps(mapping, ensure_ascii=False, indent=2))
        print(json.dumps({'pack': NAME, 'mapping': mapping}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
