"""Private, explicitly requested advertising-post capture and exact preview."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path

from aiogram import BaseMiddleware, types

log = logging.getLogger(__name__)
OWNER = 1084693264
CAMPAIGN_ID = 'ad-20261029'


def write_private_json(path: Path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def post_digest(post):
    return hashlib.sha256(json.dumps(post, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


async def send_saved_post(bot, chat_id, post):
    """Use original Telegram entities, file IDs and preview options, never HTML."""
    messages = [types.Message.model_validate(value) for value in post['messages']]
    if not 1 <= len(messages) <= 10:
        raise ValueError('A post must have 1–10 messages')
    if len(messages) > 1:
        media = []
        for message in messages:
            common = dict(caption=message.caption, caption_entities=message.caption_entities,
                          parse_mode=None)
            if message.photo:
                media.append(types.InputMediaPhoto(media=message.photo[-1].file_id,
                    show_caption_above_media=bool(message.show_caption_above_media),
                    has_spoiler=bool(message.has_media_spoiler), **common))
            elif message.document:
                media.append(types.InputMediaDocument(media=message.document.file_id, **common))
            else:
                raise ValueError('Unsupported album content')
        return await bot.send_media_group(chat_id=chat_id, media=media)
    message = messages[0]
    common = dict(chat_id=chat_id, parse_mode=None,
                  reply_markup=message.reply_markup if isinstance(
                      message.reply_markup, types.InlineKeyboardMarkup) else None)
    if message.photo:
        result = await bot.send_photo(photo=message.photo[-1].file_id,
            caption=message.caption, caption_entities=message.caption_entities,
            show_caption_above_media=bool(message.show_caption_above_media),
            has_spoiler=bool(message.has_media_spoiler), **common)
    elif message.document:
        result = await bot.send_document(document=message.document.file_id,
            caption=message.caption, caption_entities=message.caption_entities, **common)
    elif message.text:
        result = await bot.send_message(text=message.text, entities=message.entities,
            link_preview_options=message.link_preview_options, **common)
    else:
        raise ValueError('Unsupported advertising post')
    return [result]


class AdminPostCapture(BaseMiddleware):
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = asyncio.Lock()
        self.task = None

    @property
    def campaign_path(self):
        return self.directory / 'campaign.json'

    def campaign(self):
        if not self.campaign_path.exists():
            return None
        value = json.loads(self.campaign_path.read_text())
        if value.get('id') != CAMPAIGN_ID or value.get('owner') != OWNER:
            raise ValueError('Unexpected campaign identity')
        return value

    async def __call__(self, handler, event, data):
        if (not isinstance(event, types.Message) or event.chat.type != 'private'
                or event.chat.id != OWNER or not event.from_user
                or event.from_user.id != OWNER):
            return await handler(event, data)
        async with self.lock:
            campaign = self.campaign()
            phase = campaign.get('phase') if campaign else None
            same_album = (phase == 'collecting' and event.media_group_id
                          and event.media_group_id == campaign.get('media_group_id'))
            reply_matches = (phase == 'awaiting_post' and campaign.get('prompt_message_id')
                and event.reply_to_message
                and event.reply_to_message.message_id == campaign['prompt_message_id'])
            # Forwarding a photo can clear Telegram's ForceReply selection.
            # Only this explicitly awaited owner's private media is accepted.
            awaited_media = (phase == 'awaiting_post' and campaign.get('prompt_message_id')
                and (event.photo or event.document) and campaign.get('prompt_attempted_at')
                and event.date.timestamp() >= int(datetime.fromisoformat(
                    campaign['prompt_attempted_at']).timestamp()))
            if not (same_album or reply_matches or awaited_media):
                return await handler(event, data)
            if not (event.photo or event.document or event.text and not event.text.startswith('/')):
                await data['bot'].send_message(chat_id=OWNER,
                    text='Пришли пост с фотографией или текстом ответом на запрос выше.', parse_mode=None)
                return
            source = self.directory / f'source-{event.message_id}.json'
            write_private_json(source, event.model_dump(mode='json', exclude_none=True, exclude_defaults=True))
            ids = list(campaign.get('source_message_ids', []))
            if event.message_id not in ids:
                ids.append(event.message_id)
            campaign.update(phase='collecting', source_message_ids=sorted(ids),
                            media_group_id=event.media_group_id)
            write_private_json(self.campaign_path, campaign)
            if self.task:
                self.task.cancel()
            self.task = asyncio.create_task(self.finish_after_delay(data['bot'], 2 if event.media_group_id else 0))
        return

    async def resume(self, bot):
        campaign = self.campaign()
        if campaign and campaign.get('phase') == 'collecting':
            self.task = asyncio.create_task(self.finish_after_delay(bot, 2))

    async def finish_after_delay(self, bot, delay):
        try:
            await asyncio.sleep(delay)
            async with self.lock:
                campaign = self.campaign()
                if not campaign or campaign.get('phase') != 'collecting':
                    return
                messages = [json.loads((self.directory / f'source-{mid}.json').read_text())
                            for mid in campaign['source_message_ids']]
                post = dict(version=1, messages=messages)
                digest = post_digest(post)
                write_private_json(self.directory / 'post.json', post)
                campaign.update(phase='preview_attempting', post_sha256=digest,
                                preview_attempted_at=datetime.now(timezone.utc).isoformat())
                write_private_json(self.campaign_path, campaign)
                previews = await send_saved_post(bot, OWNER, post)
                if len(previews) != len(messages) or any(m.chat.id != OWNER for m in previews):
                    raise ValueError('Unexpected preview destination')
                write_private_json(self.directory / 'preview.json',
                    [m.model_dump(mode='json', exclude_none=True, exclude_defaults=True) for m in previews])
                campaign.update(phase='ready', preview_message_ids=[m.message_id for m in previews],
                                saved_at=datetime.now(timezone.utc).isoformat())
                write_private_json(self.campaign_path, campaign)
                # A separate confirmation never alters the actual advertising post.
                await bot.send_message(chat_id=OWNER, parse_mode=None,
                    text='Пост сохранён. Выше — тестовая копия для проверки.\n\n'
                         'Рассылка всем студентам и преподавателям: 29 октября, 09:00 МСК.')
                backups = []
                for index, message in enumerate(messages):
                    item = types.Message.model_validate(message)
                    media = item.photo[-1] if item.photo else item.document
                    if media:
                        try:
                            await bot.download(media.file_id, destination=self.directory / f'media-{index}.bin')
                            backups.append(dict(index=index, downloaded=True))
                        except Exception as error:
                            backups.append(dict(index=index, downloaded=False, error_type=type(error).__name__))
                write_private_json(self.directory / 'media-backups.json', backups)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Do not replay an uncertain preview send on restart.
            log.error('Advertising post capture stopped: %s', type(error).__name__)
            write_private_json(self.directory / 'capture-error.json',
                               dict(error_type=type(error).__name__, at=datetime.now(timezone.utc).isoformat()))
