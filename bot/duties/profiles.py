"""Read Telegram profile names for already registered group members.

Only getChat with existing numeric IDs is used. This never sends messages,
enrols students, or looks up arbitrary private accounts by @username.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from copy import deepcopy
from dataclasses import dataclass, field

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

from duties.store import clock, validate_people

log = logging.getLogger(__name__)


@dataclass
class EnrollmentPlan:
    pairs: list
    reserves: list
    reason: str = ''
    issues: list = field(default_factory=list)

    @property
    def ids(self):
        if self.reason:
            return None
        return [p['telegram_id'] for pair in self.pairs for p in pair] + [p['telegram_id'] for p in self.reserves]


class ProfileDirectory:
    def __init__(self, store, profile_db):
        self.store, self.profile_db = store, str(profile_db)
        self.lock = asyncio.Lock()
        self.checked = {}
        self.retry_after = 0

    def enrollment(self, creator, group, pairs, reserves, existing=()):
        """All-or-nothing, based on current registration, not cached group hints.

        Bare profile names need a complete, recent directory of this group.
        Partial lookups must never make a namesake appear unique. No writes.
        """
        result = EnrollmentPlan(deepcopy(pairs), deepcopy(reserves))
        try:
            with sqlite3.connect(f'file:{self.profile_db}?mode=ro', uri=True) as db:
                students = {r[0] for r in db.execute(
                    "SELECT user_id FROM users WHERE role='Я студент' AND name_or_group=?", (group,))
                    if type(r[0]) is int and r[0] > 0}
            with self.store.connect() as db:
                identities = {r['uid']: dict(r) for r in db.execute(
                    'SELECT * FROM identities WHERE group_hint=? AND seen>?', (group, clock().timestamp()-600))
                    if r['uid'] in students}
        except sqlite3.Error:
            result.reason = 'Сейчас не удалось проверить группы участников.'
            return result
        if creator not in students:
            result.reason = 'Без приглашений можно добавить только студентов своей группы.'
            return result
        complete = students <= identities.keys()
        entries = [(None, p) for p in deepcopy(list(existing))]
        entries += [(['pairs', i, j], p) for i, pair in enumerate(result.pairs) for j, p in enumerate(pair)]
        entries += [(['reserves', i], p) for i, p in enumerate(result.reserves)]
        all_people = [p for _, p in entries]
        unresolved = []
        for path, item in entries:
            target, username = item.get('telegram_id') or item.get('uid'), item.get('username')
            if target:
                matched = target in students
                # A formerly resolved handle must still belong to that ID.
                if username and not item.get('uid'):
                    matched = matched and identities.get(target, {}).get('username') == username.casefold()
            elif username:
                matches = [p['uid'] for p in identities.values() if p['username'] == username.casefold()]
                target = matches[0] if len(matches) == 1 else None
                matched = target is not None
            else:
                name = ' '.join(item['name'].split()).casefold()
                matches = [p['uid'] for p in identities.values() if ' '.join(p['name'].split()).casefold() == name]
                target = matches[0] if complete and len(matches) == 1 else None
                matched = target is not None
            if matched:
                item['telegram_id'] = target
            else:
                unresolved.append(item['name'])
                if path is not None:
                    result.issues.append(dict(path=path, name=item['name']))
        if unresolved:
            names = ', '.join(n[:35] for n in unresolved[:3]) + ('…' if len(unresolved) > 3 else '')
            result.reason = f'Не удалось проверить аккаунт и группу у: {names}. Проверьте @username или укажите Telegram user ID.'
        else:
            validate_people(all_people)
        return result

    async def refresh(self, bot, group, target_ids=()):
        # Serialize refreshes: simultaneous setup messages must not multiply
        # API calls. An unavailable Telegram profile must not block setup.
        async with self.lock:
            now = time.monotonic()
            if now < self.retry_after or now < self.checked.get(group, 0):
                return
            try:
                with sqlite3.connect(f'file:{self.profile_db}?mode=ro', uri=True) as db:
                    ids = [row[0] for row in db.execute(
                        "SELECT user_id FROM users WHERE role='Я студент' AND name_or_group=? ORDER BY user_id LIMIT 120",
                        (group,)) if type(row[0]) is int and row[0] > 0]
                with self.store.connect() as db:
                    fresh = {row[0] for row in db.execute('SELECT uid FROM identities WHERE group_hint=? AND seen>?',
                        (group, clock().timestamp()-600))}
            except sqlite3.Error:
                log.warning('Duty profile directory unavailable')
                return
            self.checked[group] = now + 60
            # Look up explicitly supplied IDs first, still restricting reads
            # to registered students of this group.
            requested = set(target_ids)
            ids.sort(key=lambda uid: uid not in requested)
            deadline = now + 15
            complete = True
            for uid in ids:
                if uid in fresh:
                    continue
                if time.monotonic() >= deadline:
                    complete = False
                    break
                try:
                    chat = await bot.get_chat(uid, request_timeout=3)
                    if chat.type != 'private' or chat.id != uid:
                        continue
                    name = ' '.join(p.strip() for p in (chat.first_name, chat.last_name) if p and p.strip())
                    if name:
                        self.store.observe(uid, name, chat.username, group)
                except TelegramRetryAfter as exc:
                    self.retry_after = time.monotonic() + exc.retry_after + 1
                    complete = False
                    break
                except (TelegramAPIError, TimeoutError, OSError):
                    # Removed or unreachable accounts remain manual entries.
                    continue
            if complete:
                self.checked[group] = time.monotonic() + 600
