from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock

from aiogram import types
from activity_logging import BotActivityMiddleware
from activity_store import ActivityStore


class BotActivityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Path(self.directory.name) / 'users.db'
        self.activity_path = Path(self.directory.name) / 'activity.db'
        with sqlite3.connect(self.db) as db:
            db.executescript('CREATE TABLE users(user_id INTEGER PRIMARY KEY,role TEXT,name_or_group TEXT);'
                'CREATE TABLE tracked_groups(user_id INTEGER,group_name TEXT,slot INTEGER);'
                "INSERT INTO users VALUES(42,'Я студент','103-Д9-3ИНС');"
                "INSERT INTO tracked_groups VALUES(42,'103-Д9-3ИНС',1);")
        self.middleware = BotActivityMiddleware(self.db, ActivityStore(self.activity_path))
        self.user = types.User(id=42, is_bot=False, first_name='Person')

    async def asyncTearDown(self): self.directory.cleanup()

    def rows(self):
        with sqlite3.connect(self.activity_path) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute('SELECT * FROM activity_events')]

    async def test_command_arguments_and_private_text_are_not_stored(self):
        message = types.Message(message_id=1, date=datetime.now(timezone.utc),
            chat=types.Chat(id=42, type='private'), from_user=self.user, text='/find SECRET')
        await self.middleware(AsyncMock(), types.Update(update_id=1, message=message), {})
        row = self.rows()[0]
        self.assertEqual(json.loads(row['metadata'])['command'], 'find')
        self.assertNotIn('SECRET', json.dumps(row))

    async def test_registration_and_subscription_change_are_recorded(self):
        message = types.Message(message_id=1, date=datetime.now(timezone.utc),
            chat=types.Chat(id=42, type='private'), from_user=self.user, text='158-КД9-1ИНБ')
        async def handler(event, data):
            with sqlite3.connect(self.db) as db:
                db.execute("INSERT INTO tracked_groups VALUES(42,'158-КД9-1ИНБ',2)")
        await self.middleware(handler, types.Update(update_id=2, message=message), {})
        meta = json.loads(self.rows()[0]['metadata'])
        self.assertEqual(meta['groups_before'], ['103-Д9-3ИНС'])
        self.assertEqual(meta['groups_after'], ['103-Д9-3ИНС', '158-КД9-1ИНБ'])

    async def test_callback_secret_suffix_is_not_recorded(self):
        callback = types.CallbackQuery(id='query', chat_instance='instance', from_user=self.user,
                                       data='du:claim:SECRET-INVITE-TOKEN')
        await self.middleware(AsyncMock(), types.Update(update_id=3, callback_query=callback), {})
        self.assertEqual(json.loads(self.rows()[0]['metadata'])['callback'], 'du:claim')
        self.assertNotIn('SECRET', json.dumps(self.rows()))

    async def test_original_failure_is_reraised_but_message_is_not_logged(self):
        message = types.Message(message_id=1, date=datetime.now(timezone.utc),
            chat=types.Chat(id=42, type='private'), from_user=self.user, text='PRIVATE')
        with self.assertRaises(ValueError):
            await self.middleware(AsyncMock(side_effect=ValueError('SECRET')), types.Update(update_id=4, message=message), {})
        row = self.rows()[0]
        self.assertEqual(row['outcome'], 'error')
        self.assertNotIn('SECRET', json.dumps(row))
        self.assertNotIn('PRIVATE', json.dumps(row))


if __name__ == '__main__': unittest.main()
