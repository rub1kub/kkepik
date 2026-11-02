import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from aiogram import types
from admin_post_capture import (AdminPostCapture, OWNER, post_digest,
                                send_saved_post, write_private_json)
from scripts import send_ad_20261029 as delivery


def message(**changes):
    values = dict(message_id=20, date=datetime.now(timezone.utc),
        chat=types.Chat(id=OWNER, type='private'),
        from_user=types.User(id=OWNER, first_name='Admin', is_bot=False),
        photo=[types.PhotoSize(file_id='original-photo', file_unique_id='unique-photo',
                               width=100, height=100)],
        caption='🔥 Пост со ссылкой', caption_entities=[
            types.MessageEntity(type='bold', offset=3, length=4),
            types.MessageEntity(type='text_link', offset=11, length=7, url='https://example.com')])
    values.update(changes)
    return types.Message(**values)


class SavedPostTest(unittest.IsolatedAsyncioTestCase):
    async def test_photo_preserves_entities_caption_and_markup(self):
        bot = AsyncMock()
        original = message(reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text='Сайт', url='https://example.com')]]))
        post = {'messages': [original.model_dump(mode='json', exclude_defaults=True)]}
        await send_saved_post(bot, 123, post)
        args = bot.send_photo.await_args.kwargs
        self.assertEqual(args['photo'], 'original-photo')
        self.assertEqual(args['caption'], original.caption)
        self.assertEqual(args['caption_entities'], original.caption_entities)
        self.assertEqual(args['reply_markup'], original.reply_markup)
        self.assertIsNone(args['parse_mode'])
        self.assertEqual(args['chat_id'], 123)

    async def test_text_preserves_preview_settings_and_literal_markup(self):
        bot = AsyncMock()
        original = message(photo=None, caption=None, caption_entities=None,
            text='<Это текст> https://example.com', entities=[],
            link_preview_options=types.LinkPreviewOptions(is_disabled=True))
        await send_saved_post(bot, 123, {'messages': [original.model_dump(mode='json', exclude_defaults=True)]})
        args = bot.send_message.await_args.kwargs
        self.assertEqual(args['text'], original.text)
        self.assertTrue(args['link_preview_options'].is_disabled)
        self.assertIsNone(args['parse_mode'])

    async def test_album_preserves_caption_entities_and_order(self):
        bot = AsyncMock()
        originals = [message(message_id=20, media_group_id='album'),
                     message(message_id=21, media_group_id='album', caption=None, caption_entities=None)]
        await send_saved_post(bot, 123, {'messages': [m.model_dump(mode='json') for m in originals]})
        media = bot.send_media_group.await_args.kwargs['media']
        self.assertEqual(len(media), 2)
        self.assertEqual(media[0].caption_entities, originals[0].caption_entities)
        self.assertIsNone(media[0].parse_mode)
        self.assertIsNone(media[1].caption)

    async def test_owner_reply_saved_before_exact_owner_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = AdminPostCapture(directory)
            write_private_json(capture.campaign_path, dict(id='ad-20261029', owner=OWNER,
                phase='awaiting_post', prompt_message_id=10))
            bot = AsyncMock()
            original = message(reply_to_message=message(message_id=10))
            bot.send_photo.return_value = message(message_id=30)
            await capture(AsyncMock(), original, {'bot': bot})
            await capture.task
            campaign = capture.campaign()
            self.assertEqual(campaign['phase'], 'ready')
            post = json.loads((Path(directory) / 'post.json').read_text())
            self.assertEqual(campaign['post_sha256'], post_digest(post))
            self.assertEqual(campaign['preview_message_ids'], [30])
            self.assertEqual(bot.send_photo.await_args.kwargs['chat_id'], OWNER)
            self.assertEqual(bot.send_photo.await_args.kwargs['caption'], original.caption)

    async def test_unrelated_and_non_owner_messages_are_not_captured(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = AdminPostCapture(directory)
            write_private_json(capture.campaign_path, dict(id='ad-20261029', owner=OWNER,
                phase='awaiting_post', prompt_message_id=10))
            handler = AsyncMock()
            await capture(handler, message(), {'bot': AsyncMock()})
            await capture(handler, message(chat=types.Chat(id=123, type='private'),
                from_user=types.User(id=123, first_name='Other', is_bot=False),
                reply_to_message=message(message_id=10)), {'bot': AsyncMock()})
            self.assertEqual(handler.await_count, 2)
            self.assertFalse((Path(directory) / 'post.json').exists())

    async def test_send_before_due_date_cannot_create_bot_or_send(self):
        with patch.object(delivery, 'load_campaign', return_value=({}, {})), \
             patch.object(delivery, 'Bot') as bot, \
             patch.object(delivery, 'datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 28, tzinfo=timezone.utc)
            with self.assertRaises(ValueError):
                await delivery.run(send=True)
            bot.assert_not_called()

    async def test_awaited_owner_photo_does_not_require_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = AdminPostCapture(directory)
            write_private_json(capture.campaign_path, dict(id='ad-20261029', owner=OWNER,
                phase='awaiting_post', prompt_message_id=10,
                prompt_attempted_at='2026-10-28T20:00:00+00:00'))
            bot = AsyncMock()
            bot.send_photo.return_value = message(message_id=31)
            handler = AsyncMock()
            await capture(handler, message(), {'bot': bot})
            await capture.task
            handler.assert_not_awaited()
            self.assertEqual(capture.campaign()['phase'], 'ready')


class CampaignAudienceTest(unittest.TestCase):
    def test_both_registered_roles_only_and_live_recheck(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'users.db'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE users(user_id INTEGER PRIMARY KEY,role TEXT)')
                db.executemany('INSERT INTO users VALUES(?,?)', [
                    (1, 'Я студент'), (2, 'Я преподаватель'), (3, 'Unknown'), (-10, 'Я студент')])
            self.assertEqual(delivery.current_recipients(path), [
                {'user_id': 1, 'role': 'Я студент'}, {'user_id': 2, 'role': 'Я преподаватель'}])
            self.assertTrue(delivery.eligible(2, path))
            self.assertFalse(delivery.eligible(3, path))
            self.assertFalse(delivery.eligible(-10, path))
            with sqlite3.connect(path) as db:
                db.execute('DELETE FROM users WHERE user_id=2')
            self.assertFalse(delivery.eligible(2, path))

    def test_modified_post_cannot_pass_digest_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_private_json(root / 'campaign.json', dict(id='ad-20261029', owner=OWNER,
                roles=list(delivery.ROLES), send_at=delivery.SEND_AT.isoformat(),
                phase='ready', post_sha256='wrong'))
            write_private_json(root / 'post.json', {'messages': [message().model_dump(mode='json')]})
            with patch.object(delivery, 'DIRECTORY', root), self.assertRaises(ValueError):
                delivery.load_campaign()


if __name__ == '__main__':
    unittest.main()
