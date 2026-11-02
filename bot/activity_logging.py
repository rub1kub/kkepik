"""Telegram action metadata without the contents of personal messages."""
from contextlib import closing
import logging
import re
import sqlite3
import time

from aiogram import BaseMiddleware, types
from aiogram.dispatcher.event.bases import UNHANDLED
from activity_store import ActivityStore, GROUP

log = logging.getLogger(__name__)


class BotActivityMiddleware(BaseMiddleware):
    def __init__(self, database, activity=None):
        self.database = str(database)
        self.activity = activity or ActivityStore()

    def profile(self, uid):
        with closing(sqlite3.connect(f'file:{self.database}?mode=ro', uri=True, timeout=.25)) as db:
            user = db.execute('SELECT role,name_or_group FROM users WHERE user_id=?', (uid,)).fetchone()
            groups = [g for g, in db.execute('SELECT group_name FROM tracked_groups WHERE user_id=? ORDER BY slot', (uid,)) if GROUP.fullmatch(g)]
            return dict(role=user[0] if user else None, groups=groups)

    async def __call__(self, handler, event, data):
        item = event.message or event.edited_message or event.callback_query or event.inline_query or event.chosen_inline_result
        user = getattr(item, 'from_user', None)
        if not user or user.is_bot:
            return await handler(event, data)
        if isinstance(item, types.Message) and item.chat.type != 'private':
            return await handler(event, data)
        started, outcome, details = time.monotonic(), 'ok', {}
        before = None
        try: before = self.profile(user.id)
        except sqlite3.Error: pass
        if isinstance(item, types.Message):
            command = re.match(r'^/([a-zA-Z0-9_]{1,32})(?:@\w+)?(?:\s|$)', item.text or item.caption or '')
            action = 'bot.command' if command else 'bot.input'
            if command: details['command'] = command[1].lower()
            details['content_type'] = item.content_type
        elif isinstance(item, types.CallbackQuery):
            action = 'bot.callback'
            parts = (item.data or '').split(':')
            details['callback'] = ':'.join(parts[:2]) if all(re.fullmatch('[a-z_]{1,40}', p) for p in parts[:2]) else 'other'
        else:
            action = 'bot.inline'
        state = data.get('state')
        if state:
            try: details['state'] = await state.get_state()
            except Exception: pass
        try:
            result = await handler(event, data)
            if result is UNHANDLED:
                outcome = 'unhandled'
            return result
        except Exception as error:
            outcome = 'error'; details['error_type'] = type(error).__name__
            raise
        finally:
            try:
                after = self.profile(user.id)
                if before is not None and before != after:
                    details.update(groups_before=before['groups'], groups_after=after['groups'],
                                   role_before=before['role'], role_after=after['role'])
                details['duration_ms'] = int((time.monotonic() - started) * 1000)
                self.activity.append(user.id, 'telegram', 'server', action, outcome=outcome,
                                     event_id='update:' + str(event.update_id), details=details)
            except Exception as error:
                log.warning('Activity journal write skipped (%s)', type(error).__name__)
