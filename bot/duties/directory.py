"""Group-scoped, minimal people picker. Tokens never contain Telegram IDs."""
from collections import Counter
import asyncio
import hashlib
import hmac
import json
import secrets
import sqlite3
import time

from fastapi import HTTPException
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.utils.token import TokenValidationError

from duties.store import DutyError, clock, validate_people, validate_roster

TOKEN_TTL = 30 * 60
GROUP_LOCK = 7 * 86400
MAX_PEOPLE = 120


class Directory:
    def __init__(self, store, profile_db, bot_token):
        self.store, self.profile_db, self.bot_token = store, str(profile_db), bot_token
        self.lock = asyncio.Lock()
        self.checked = {}
        with store.connect(True) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS picker_access(
                    uid INTEGER PRIMARY KEY, group_name TEXT NOT NULL, locked_until REAL NOT NULL,
                    minute INTEGER NOT NULL DEFAULT 0, minute_requests INTEGER NOT NULL DEFAULT 0,
                    day INTEGER NOT NULL DEFAULT 0, day_requests INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS picker_choices(
                    token_hash TEXT PRIMARY KEY, requester INTEGER NOT NULL, group_name TEXT NOT NULL,
                    space INTEGER, target INTEGER NOT NULL, label TEXT NOT NULL, expires REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS picker_expiry ON picker_choices(expires);
            ''')

    def primary_group(self, uid):
        with sqlite3.connect(f'file:{self.profile_db}?mode=ro', uri=True) as db:
            row = db.execute('SELECT role,name_or_group FROM users WHERE user_id=?', (uid,)).fetchone()
        if not row or row[0] != 'Я студент' or not row[1]:
            raise HTTPException(403, 'Выбор участников доступен студентам для своей группы')
        return row[1].strip().upper()

    def scope(self, uid, sid=None, *, charge=False):
        group = self.primary_group(uid)
        now = clock().timestamp()
        with self.store.connect(True) as db:
            if sid is not None:
                try:
                    self.store.permission(db, sid, uid, manage=True)
                    space = self.store.space(db, sid)
                except DutyError:
                    raise HTTPException(403, 'Нет доступа к выбору участников этого списка') from None
                if space['group_name'].upper() != group or space['status'] == 'archived':
                    raise HTTPException(403, 'Добавлять участников можно только из своей группы')
            row = db.execute('SELECT * FROM picker_access WHERE uid=?', (uid,)).fetchone()
            if row and row['group_name'] != group and now < row['locked_until']:
                raise HTTPException(403, 'После смены группы выбор новых участников временно недоступен. Это ограничение защищает списки студентов от перебора.')
            if not row or row['group_name'] != group:
                db.execute('INSERT INTO picker_access(uid,group_name,locked_until) VALUES(?,?,?) '
                           'ON CONFLICT(uid) DO UPDATE SET group_name=excluded.group_name,locked_until=excluded.locked_until',
                           (uid, group, now + GROUP_LOCK))
                row = db.execute('SELECT * FROM picker_access WHERE uid=?', (uid,)).fetchone()
            if charge:
                minute, day = int(now // 60), int(now // 86400)
                mc = row['minute_requests'] if row['minute'] == minute else 0
                dc = row['day_requests'] if row['day'] == day else 0
                if mc >= 5 or dc >= 30:
                    raise HTTPException(429, 'Список обновляли слишком часто. Попробуйте позже.', headers={'Retry-After': '60'})
                db.execute('UPDATE picker_access SET minute=?,minute_requests=?,day=?,day_requests=? WHERE uid=?',
                           (minute, mc+1, day, dc+1, uid))
        return group

    def registered(self, group):
        with sqlite3.connect(f'file:{self.profile_db}?mode=ro', uri=True) as db:
            tracked = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracked_groups'").fetchone()
            secondary = " OR EXISTS(SELECT 1 FROM tracked_groups t WHERE t.user_id=u.user_id AND t.group_name=? COLLATE NOCASE)" if tracked else ''
            rows = db.execute("SELECT u.user_id,u.name_or_group FROM users u WHERE u.role='Я студент' AND "
                              '(u.name_or_group=? COLLATE NOCASE' + secondary + ') ORDER BY u.user_id LIMIT ?',
                              (group, group, MAX_PEOPLE) if tracked else (group, MAX_PEOPLE)).fetchall()
        return {r[0]: r[1] for r in rows if type(r[0]) is int and r[0] > 0}

    async def refresh(self, group, students):
        async with self.lock:
            current = time.monotonic()
            if self.checked.get(group, 0) > current:
                return
            self.checked[group] = current + 60
            with self.store.connect() as db:
                fresh = {r['uid'] for r in db.execute('SELECT uid FROM identities WHERE seen>?', (clock().timestamp()-600,))}
            try:
                bot = Bot(self.bot_token())
            except TokenValidationError:
                return
            complete = True
            try:
                for uid, primary in students.items():
                    if uid in fresh:
                        continue
                    if time.monotonic() > current + 15:
                        complete = False
                        break
                    try:
                        chat = await bot.get_chat(uid, request_timeout=3)
                        if chat.type == 'private' and chat.id == uid:
                            name = ' '.join(p.strip() for p in (chat.first_name, chat.last_name) if p and p.strip())
                            if name:
                                self.store.observe(uid, name, chat.username, primary)
                    except TelegramRetryAfter as exc:
                        self.checked[group] = time.monotonic()+max(60, exc.retry_after+1)
                        complete = False
                        break
                    except (TelegramAPIError, OSError, TimeoutError):
                        continue
            finally:
                await bot.session.close()
            if complete:
                self.checked[group] = time.monotonic()+600

    async def choices(self, uid, sid=None):
        group = self.scope(uid, sid, charge=True)
        students = self.registered(group)
        await self.refresh(group, students)
        # Membership and rights may have changed during Telegram profile reads.
        if self.scope(uid, sid) != group:
            raise HTTPException(403, 'Группа изменилась. Откройте выбор участников заново.')
        students = self.registered(group)
        with self.store.connect(True) as db:
            existing = {r[0] for r in db.execute('SELECT coalesce(uid,telegram_id) FROM members WHERE space=?', (sid,))} if sid else set()
            ids = list(students)
            rows = [dict(r) for r in db.execute('SELECT uid,name,username FROM identities WHERE uid IN (' + ','.join('?' for _ in ids) + ')', ids)
                    if r['uid'] not in existing] if ids else []
            names = Counter(' '.join(r['name'].split())[:80].casefold() for r in rows)
            unnamed = Counter(' '.join(r['name'].split())[:80].casefold() for r in rows if not r['username'])
            result = []
            now = clock().timestamp()
            db.execute('DELETE FROM picker_choices WHERE expires<=?', (now,))
            for r in sorted(rows, key=lambda x: x['name'].casefold()):
                label = ' '.join(r['name'].split())[:80]
                if names[label.casefold()] > 1:
                    if r['username']:
                        suffix = ' (@' + r['username'] + ')'
                        label = label[:80-len(suffix)] + suffix
                    elif unnamed[label.casefold()] > 1:
                        # Never silently guess between indistinguishable profiles.
                        continue
                    else:
                        label = label[:64] + ' (без username)'
                token = secrets.token_urlsafe(24)
                db.execute('INSERT INTO picker_choices VALUES(?,?,?,?,?,?,?)',
                           (hashlib.sha256(token.encode()).hexdigest(), uid, group, sid, r['uid'], label, now+TOKEN_TTL))
                result.append(dict(ref=token, name=label))
        return dict(people=result, expires_at=int(now+TOKEN_TTL))

    def resolve(self, uid, pair_refs, reserve_refs, sid=None):
        group = self.scope(uid, sid)
        students = self.registered(group)
        refs = [ref for pair in pair_refs for ref in pair] + reserve_refs
        people = []
        with self.store.connect() as db:
            for token in refs:
                row = db.execute('SELECT * FROM picker_choices WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
                if (not row or row['requester'] != uid or row['group_name'] != group or row['space'] != sid
                        or row['expires'] <= clock().timestamp() or row['target'] not in students):
                    raise DutyError('Выбор участника устарел. Обновите список группы и выберите его заново.')
                people.append(dict(name=row['label'], username=None, telegram_id=row['target']))
            if sid:
                existing = [dict(r) for r in db.execute('SELECT name,username,uid,telegram_id FROM members WHERE space=?', (sid,))]
                validate_people(existing + people)
        pairs = [people[i:i+2] for i in range(0, len(pair_refs)*2, 2)]
        reserves = people[len(pair_refs)*2:]
        if sid:
            if not people:
                raise DutyError('Выберите хотя бы одного участника')
        else:
            validate_roster(pairs, reserves)
        return group, pairs, reserves

    def signature(self, uid, group, pairs, reserves, options, *, expires=None):
        expires = int(clock().timestamp()+TOKEN_TTL) if expires is None else expires
        value = json.dumps([uid, group, [[p['telegram_id'] for p in pair] for pair in pairs],
                            [p['telegram_id'] for p in reserves], options, expires], sort_keys=True, separators=(',', ':'))
        digest = hmac.new(self.bot_token().encode(), ('duty-preview:' + value).encode(), hashlib.sha256).hexdigest()
        return str(expires) + '.' + digest

    def verify_preview(self, token, uid, group, pairs, reserves, options):
        try:
            expiry = int(token.split('.', 1)[0])
        except (ValueError, AttributeError):
            raise DutyError('Сначала проверьте список перед запуском дежурств') from None
        if not clock().timestamp() < expiry <= clock().timestamp()+TOKEN_TTL+1 or not hmac.compare_digest(
                token, self.signature(uid, group, pairs, reserves, options, expires=expiry)):
            raise DutyError('Список изменился или устарел. Проверьте его перед запуском ещё раз.')
