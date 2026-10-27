"""Transactional duty queue. No Telegram or application imports/side effects.

Permanent pairs own a turn together. A completed substitution gives its actual
participant one credit; the partner's uncompleted half remains pending. Credits
are consumed only when both halves of the permanent pair's turn are complete.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from pathlib import Path
import hashlib
import json
import re
import secrets
import sqlite3

MSK = ZoneInfo("Europe/Moscow")
DEFAULTS = dict(time="08:40", cutoff="09:10", report="16:00", days=[0, 1, 2, 3, 4, 5],
                owner_daily=True, audience="all", requests=True, owner_events=True,
                mode="reserve", place="", tasks="", wait_minutes=10)


class DutyError(ValueError):
    pass


class AmbiguousPerson(DutyError):
    """An unresolved roster entry, with its position and the resolved draft."""
    def __init__(self, name, path, pairs, reserves):
        super().__init__(f'В группе несколько профилей с именем «{name}».')
        self.name, self.path = name, path
        self.pairs, self.reserves = pairs, reserves


def clock():
    return datetime.now(MSK)


def at(day, value):
    return datetime.fromisoformat(f"{day}T{value}:00").replace(tzinfo=MSK)


def settings(values=None):
    result = {**DEFAULTS, **(values or {})}
    for key in ("time", "cutoff", "report"):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(result[key])):
            raise DutyError("Укажите время в формате 08:00.")
    if not result['time'] < result['cutoff'] < result['report']:
        raise DutyError("Проверьте время: сначала утренняя рассылка, затем окончание поиска замен, потом итоги.")
    if result['audience'] not in ('all', 'assigned', 'off') or result['mode'] not in ('reserve', 'pair'):
        raise DutyError("Неизвестный режим.")
    if not result['days'] or any(type(d) is not int or d not in range(7) for d in result['days']):
        raise DutyError("Выберите хотя бы один день недели.")
    if not 2 <= result['wait_minutes'] <= 30:
        raise DutyError("Ожидание ответа: от 2 до 30 минут.")
    if len(result['place']) > 120 or len(result['tasks']) > 500:
        raise DutyError("Место — до 120 символов, обязанности — до 500.")
    return result


def telegram_user_id(text):
    """A positive Telegram user ID, not a group/channel's negative chat ID."""
    raw = text.strip()
    tagged = bool(re.match(r'^id(?:\s|:)', raw, re.I))
    number = re.sub(r'^id\s*:?\s*', '', raw, flags=re.I) if tagged else raw
    if not re.fullmatch(r'-?[0-9]+', number):
        if tagged:
            raise DutyError('Telegram user ID должен быть целым положительным числом.')
        return None
    if len(number) > 16 or not 0 < int(number) < 2**52:
        raise DutyError('Нужен положительный Telegram user ID. ID группы или канала не подойдёт.')
    return int(number)


def person(text):
    text = re.sub(r'^\s*\d+[.)]\s*', '', text).strip().lstrip('•▪▫ ')
    target = telegram_user_id(text)
    if target is not None:
        return dict(name=f'ID {target}', username=None, telegram_id=target)
    usernames = re.findall(r'(?<!\w)@([A-Za-z0-9_]{5,32})\b', text)
    if len(usernames) > 1:
        raise DutyError("У одного человека может быть только один @username.")
    # Punctuation can be the entire Telegram profile name, e.g. "-".
    # Trim separators only when removing an actual username from a label.
    name = re.sub(r'(?<!\w)@[A-Za-z0-9_]{5,32}\b', '', text).strip(' \t—–-,()') if usernames else text
    if not name:
        name = '@' + usernames[0] if usernames else ''
    if not name or len(name) > 80 or '\n' in name or any(ord(c) < 32 for c in name):
        raise DutyError("Укажите @username, Telegram user ID или имя из Telegram — до 80 символов.")
    return dict(name=name, username=usernames[0].lower() if usernames else None)


def parse_people(text, *, reserves=False):
    if len(text) > 8000:
        raise DutyError("Слишком длинный список. Максимум 60 человек.")
    lines = [(n, line.strip()) for n, line in enumerate(text.splitlines(), 1) if line.strip()]
    def read(value, number):
        try:
            return person(value)
        except DutyError as exc:
            raise DutyError(f'Строка {number}: {exc}') from None
    if reserves:
        return [read(line, n) for n, line in lines]
    explicit = any(re.search(r'[+|;]', line) for _, line in lines)
    if explicit:
        pairs = []
        for n, line in lines:
            pair = re.split(r'\s*[+|;]\s*', line)
            if len(pair) != 2:
                raise DutyError(f'Строка {n}: нужно двое через +, например @ivan_ivanov + @anna_petrova.')
            pairs.append([read(item, n) for item in pair])
        return pairs
    if len(lines) % 2:
        raise DutyError("Один человек остался без пары. Добавьте ему напарника или уберите из этого списка — запасных добавим дальше.")
    return [[read(lines[i][1], lines[i][0]), read(lines[i+1][1], lines[i+1][0])] for i in range(0, len(lines), 2)]


def validate_roster(pairs, reserves):
    people = [p for pair in pairs for p in pair] + reserves
    if not pairs or len(people) > 60 or any(len(pair) != 2 for pair in pairs):
        raise DutyError("Добавьте хотя бы одну пару. В списке может быть до 60 человек.")
    validate_people(people)


def validate_people(people):
    names, usernames, ids = set(), set(), set()
    for item in people:
        name = item['name'].casefold()
        username = item.get('username')
        target = item.get('telegram_id')
        if target is not None and (type(target) is not int or not 0 < target < 2**52):
            raise DutyError('Нужен положительный Telegram user ID.')
        accounts = {value for value in (target, item.get('uid')) if value is not None}
        if name in names or username and username in usernames or ids & accounts:
            raise DutyError("Кто-то указан дважды. Уберите повтор. Если это тёзки, укажите разные @username или Telegram user ID.")
        names.add(name)
        ids.update(accounts)
        if username:
            usernames.add(username)


def matches_member(member, text, uid=None):
    """Preserve input aliases and require the intended Telegram account."""
    target = dict(member).get('telegram_id')
    if target is not None and uid is not None and target != uid:
        return False
    text = text.strip().casefold()
    try:
        if target and telegram_user_id(text) == target:
            return True
    except DutyError:
        pass
    return text == member['name'].strip().casefold() or bool(
        member['username'] and text == '@' + member['username'].casefold())


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS spaces(
                    id INTEGER PRIMARY KEY, owner INTEGER NOT NULL, group_name TEXT NOT NULL,
                    settings TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                    first_date TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS access(
                    space INTEGER NOT NULL REFERENCES spaces(id), uid INTEGER NOT NULL,
                    role TEXT NOT NULL, muted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(space,uid));
                CREATE TABLE IF NOT EXISTS pairs(
                    id INTEGER PRIMARY KEY, space INTEGER NOT NULL REFERENCES spaces(id),
                    rank INTEGER NOT NULL, rounds INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS members(
                    id INTEGER PRIMARY KEY, space INTEGER NOT NULL REFERENCES spaces(id),
                    pair INTEGER REFERENCES pairs(id), name TEXT NOT NULL, username TEXT,
                    uid INTEGER, active INTEGER NOT NULL DEFAULT 1,
                    exempt_until TEXT, credit INTEGER NOT NULL DEFAULT 0,
                    pending_since TEXT, telegram_id INTEGER);
                CREATE UNIQUE INDEX IF NOT EXISTS member_account ON members(space,uid) WHERE uid IS NOT NULL;
                CREATE TABLE IF NOT EXISTS identities(
                    uid INTEGER PRIMARY KEY, username TEXT, name TEXT NOT NULL,
                    group_hint TEXT, seen REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS identity_username ON identities(username);
                CREATE TABLE IF NOT EXISTS sessions(
                    uid INTEGER PRIMARY KEY, step TEXT NOT NULL, payload TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS invites(
                    token_hash TEXT PRIMARY KEY, space INTEGER NOT NULL REFERENCES spaces(id),
                    member INTEGER REFERENCES members(id), expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS claims(
                    id INTEGER PRIMARY KEY, space INTEGER NOT NULL REFERENCES spaces(id),
                    uid INTEGER NOT NULL, member INTEGER REFERENCES members(id), name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_claim ON claims(space,uid) WHERE status='pending';
                CREATE TABLE IF NOT EXISTS days(
                    id INTEGER PRIMARY KEY, space INTEGER NOT NULL REFERENCES spaces(id), date TEXT NOT NULL,
                    status TEXT NOT NULL, pair INTEGER REFERENCES pairs(id), revision INTEGER NOT NULL DEFAULT 0,
                    attempted TEXT NOT NULL DEFAULT '[]', excluded TEXT NOT NULL DEFAULT '[]',
                    source_state TEXT NOT NULL DEFAULT 'unknown', source_revision INTEGER,
                    cutoff REAL NOT NULL, report_at REAL NOT NULL, reported INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(space,date));
                CREATE TABLE IF NOT EXISTS assignments(
                    id INTEGER PRIMARY KEY, day INTEGER NOT NULL REFERENCES days(id),
                    member INTEGER NOT NULL REFERENCES members(id), state TEXT NOT NULL,
                    UNIQUE(day,member));
                CREATE TABLE IF NOT EXISTS offers(
                    id INTEGER PRIMARY KEY, day INTEGER NOT NULL REFERENCES days(id), kind TEXT NOT NULL,
                    host INTEGER NOT NULL REFERENCES members(id), guest INTEGER NOT NULL REFERENCES members(id),
                    host_yes INTEGER NOT NULL DEFAULT 0, guest_yes INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL DEFAULT 'pending', expires REAL NOT NULL,
                    reminded INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_offer ON offers(day) WHERE state='pending';
                CREATE TABLE IF NOT EXISTS outbox(
                    id INTEGER PRIMARY KEY, space INTEGER NOT NULL REFERENCES spaces(id), uid INTEGER NOT NULL,
                    kind TEXT NOT NULL, ref INTEGER NOT NULL, dedup TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'pending', not_before REAL NOT NULL DEFAULT 0,
                    message_id INTEGER, attempts INTEGER NOT NULL DEFAULT 0, error TEXT);
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY, space INTEGER NOT NULL REFERENCES spaces(id),
                    actor INTEGER, action TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS assignment_member ON assignments(member,state);
                CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(status,not_before);
            ''')
            # Additive v1 -> v2 migration: the requested identity is separate
            # from uid, which is set after approval or a same-group check.
            db.execute('BEGIN IMMEDIATE')
            if 'telegram_id' not in {r['name'] for r in db.execute('PRAGMA table_info(members)')}:
                db.execute('ALTER TABLE members ADD COLUMN telegram_id INTEGER')
            if 'daily_muted' not in {r['name'] for r in db.execute('PRAGMA table_info(access)')}:
                db.execute('ALTER TABLE access ADD COLUMN daily_muted INTEGER NOT NULL DEFAULT 0')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS member_telegram_target ON members(space,telegram_id) WHERE telegram_id IS NOT NULL')
            if db.execute('PRAGMA user_version').fetchone()[0] < 3:
                db.execute("UPDATE outbox SET status='cancelled' WHERE status='pending' AND kind IN ('daily','update','source_change') "
                           "AND ref IN (SELECT id FROM days WHERE status='no_duty')")
            if db.execute('PRAGMA user_version').fetchone()[0] < 4:
                for row in db.execute("SELECT id,settings FROM spaces WHERE status!='archived'").fetchall():
                    opts = settings(json.loads(row['settings']))
                    opts['time'] = '08:40'
                    if opts['cutoff'] <= opts['time']:
                        opts['cutoff'] = '09:10'
                    if opts['report'] <= opts['cutoff']:
                        opts['report'] = '16:00'
                    if opts['audience'] != 'off':
                        opts['audience'] = 'all'
                    db.execute('UPDATE spaces SET settings=? WHERE id=?', (json.dumps(settings(opts), ensure_ascii=False), row['id']))
                    self.event(db, row['id'], None, 'notification_migration', 'daily_0840_personal_opt_out')
            db.execute('PRAGMA user_version=4')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def event(db, sid, actor, action, detail='', now=None):
        db.execute('INSERT INTO events(space,actor,action,detail,created) VALUES(?,?,?,?,?)',
                   (sid, actor, action, str(detail)[:1000], (now or clock()).timestamp()))

    @staticmethod
    def permission(db, sid, uid, manage=False, owner=False):
        row = db.execute('SELECT * FROM access WHERE space=? AND uid=?', (sid, uid)).fetchone()
        if not row or (manage and row['role'] not in ('owner', 'manager')) or (owner and row['role'] != 'owner'):
            raise DutyError("Этот список вам недоступен.")
        return row

    @staticmethod
    def space(db, sid):
        row = db.execute('SELECT * FROM spaces WHERE id=?', (sid,)).fetchone()
        if not row:
            raise DutyError("Список не найден.")
        return dict(row)

    def observe(self, uid, name, username=None, group_hint=None, now=None):
        if type(uid) is not int or uid <= 0:
            return
        username = username.lower() if username else None
        with self.connect(True) as db:
            if username:
                db.execute('UPDATE identities SET username=NULL WHERE username=? AND uid!=?', (username, uid))
            db.execute('INSERT INTO identities VALUES(?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET '
                       'username=excluded.username,name=excluded.name,group_hint=excluded.group_hint,seen=excluded.seen',
                       (uid, username, name[:100], group_hint, (now or clock()).timestamp()))

    def session(self, uid):
        with self.connect() as db:
            row = db.execute('SELECT * FROM sessions WHERE uid=?', (uid,)).fetchone()
        return (row['step'], json.loads(row['payload'])) if row else None

    def fill_profile_names(self, group, pairs, reserves):
        """Resolve names/handles/IDs, never enrol or send to matched accounts.

        Names are read only from recent Telegram observations in this group.
        Keeping the handle in the label distinguishes identical profile names.
        """
        with self.connect() as db:
            identities = [dict(r) for r in db.execute('SELECT uid,name,username FROM identities WHERE group_hint=? AND seen>?',
                (group, clock().timestamp()-600))]
            ambiguous = []
            def fill(item, path):
                item = dict(item)
                username = item.get('username')
                target = item.get('telegram_id')
                if target is not None:
                    matches = [r for r in identities if r['uid'] == target]
                elif username:
                    matches = [r for r in identities if r['username'] == username.casefold()]
                else:
                    name = ' '.join(item['name'].split()).casefold()
                    matches = [r for r in identities if ' '.join(r['name'].split()).casefold() == name]
                    if len(matches) > 1:
                        ambiguous.append((item['name'], path))
                    # A display name is not an account identifier. Even a
                    # single cached match may have an uncached namesake.
                    # Leave these entries for the normal invitation approval.
                    return item
                if len(matches) == 1:
                    identity = matches[0]
                    item['telegram_id'] = identity['uid']
                    name = ' '.join(identity['name'].split())
                    if target and item['name'] == f'ID {target}':
                        suffix = f' (ID {target})'
                        item['name'] = name[:80-len(suffix)].rstrip() + suffix
                    elif username and item['name'].casefold() == '@' + username.casefold():
                        suffix = f' (@{username})'
                        item['name'] = name[:80-len(suffix)].rstrip() + suffix
                return item
            pairs = [[fill(p, ['pairs', i, j]) for j, p in enumerate(pair)] for i, pair in enumerate(pairs)]
            reserves = [fill(p, ['reserves', i]) for i, p in enumerate(reserves)]
            if ambiguous:
                raise AmbiguousPerson(*ambiguous[0], pairs, reserves)
            return pairs, reserves

    def set_session(self, uid, step=None, payload=None):
        with self.connect(True) as db:
            if step is None:
                db.execute('DELETE FROM sessions WHERE uid=?', (uid,))
            else:
                db.execute('INSERT INTO sessions VALUES(?,?,?,?) ON CONFLICT(uid) DO UPDATE SET '
                           'step=excluded.step,payload=excluded.payload,updated=excluded.updated',
                           (uid, step, json.dumps(payload or {}, ensure_ascii=False), clock().timestamp()))

    def create(self, uid, group, pairs, reserves, options=None, now=None, *, auto_link=False):
        now = now or clock()
        validate_roster(pairs, reserves)
        opts = settings(options)
        first = now.date() if now < at(now.date(), opts['time']) else now.date() + timedelta(days=1)
        with self.connect(True) as db:
            if db.execute("SELECT 1 FROM spaces WHERE owner=? AND status!='archived'", (uid,)).fetchone():
                raise DutyError("Дежурства у вас уже настроены. Откройте /duty, чтобы изменить пары или настройки.")
            sid = db.execute('INSERT INTO spaces(owner,group_name,settings,first_date,created) VALUES(?,?,?,?,?)',
                             (uid, group, json.dumps(opts, ensure_ascii=False), first.isoformat(), now.timestamp())).lastrowid
            db.execute("INSERT INTO access(space,uid,role) VALUES(?,?,'owner')", (sid, uid))
            added = self._add_people(db, sid, pairs, reserves)
            if auto_link:
                self._link_added(db, sid, uid, added)
            db.execute('DELETE FROM sessions WHERE uid=?', (uid,))
            self.event(db, sid, uid, 'created', now=now)
        return sid

    @staticmethod
    def _add_people(db, sid, pairs, reserves):
        added = []
        rank = db.execute('SELECT coalesce(max(rank),0) FROM pairs WHERE space=?', (sid,)).fetchone()[0]
        for offset, pair in enumerate(pairs, 1):
            pid = db.execute('INSERT INTO pairs(space,rank) VALUES(?,?)', (sid, rank + offset)).lastrowid
            for item in pair:
                added.append(db.execute('INSERT INTO members(space,pair,name,username,telegram_id) VALUES(?,?,?,?,?)',
                           (sid, pid, item['name'], item.get('username'), item.get('telegram_id'))).lastrowid)
        for item in reserves:
            added.append(db.execute('INSERT INTO members(space,name,username,telegram_id) VALUES(?,?,?,?)',
                       (sid, item['name'], item.get('username'), item.get('telegram_id'))).lastrowid)
        return added

    def _link_added(self, db, sid, actor, added):
        """Caller checked the entire roster's registration; commit with notices.

        Only new entries are linked. This never re-enrols somebody who left,
        unmutes an existing account, or downgrades the creator to a member.
        """
        for mid in added:
            member = self._member(db, mid)
            uid = member['telegram_id']
            if not uid or member['space'] != sid or member['uid'] is not None:
                raise DutyError('Не удалось проверить всех участников. Вернитесь к списку и проверьте аккаунты.')
            db.execute('UPDATE members SET uid=? WHERE id=?', (uid, mid))
            db.execute("INSERT INTO access(space,uid,role) VALUES(?,?,'member') ON CONFLICT(space,uid) DO NOTHING", (sid, uid))
            self._job(db, sid, uid, 'added', mid, f'added:{mid}')
        self.event(db, sid, actor, 'members_linked', len(added))

    def lists(self, uid):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT s.*,a.role,a.muted FROM spaces s JOIN access a ON a.space=s.id "
                                                   "WHERE a.uid=? AND s.status!='archived' ORDER BY s.id", (uid,))]

    def view(self, sid, uid):
        with self.connect() as db:
            access = dict(self.permission(db, sid, uid))
            space = self.space(db, sid)
            members = [dict(row) for row in db.execute('SELECT m.*,p.rank,'
                "(SELECT count(*) FROM assignments a WHERE a.member=m.id AND a.state='done') completed "
                'FROM members m LEFT JOIN pairs p ON p.id=m.pair WHERE m.space=? AND m.active=1 '
                'ORDER BY m.pair IS NULL,p.rank,m.id', (sid,))]
            days = [dict(row) for row in db.execute("SELECT * FROM days WHERE space=? AND (id IN "
                "(SELECT id FROM days WHERE space=? ORDER BY date DESC LIMIT 14) OR id IN "
                "(SELECT day FROM assignments WHERE state IN ('assigned','waiting','reported'))) ORDER BY date DESC", (sid, sid))]
            claims = [dict(row) for row in db.execute("SELECT c.*,i.name profile_name,i.username profile_username,i.group_hint "
                "FROM claims c LEFT JOIN identities i ON i.uid=c.uid WHERE c.space=? AND c.status='pending'", (sid,))] if access['role'] != 'member' else []
        return dict(space=space, access=access, members=members, days=days, claims=claims)

    def update_settings(self, sid, uid, changes):
        with self.connect(True) as db:
            self.permission(db, sid, uid, manage=True)
            opts = settings({**json.loads(self.space(db, sid)['settings']), **changes})
            db.execute('UPDATE spaces SET settings=? WHERE id=?', (json.dumps(opts, ensure_ascii=False), sid))
            self.event(db, sid, uid, 'settings', ','.join(changes))

    def invite(self, sid, uid, member=None, now=None):
        now = now or clock()
        token = secrets.token_urlsafe(18)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connect(True) as db:
            self.permission(db, sid, uid, manage=True)
            if member and not db.execute('SELECT 1 FROM members WHERE id=? AND space=? AND active=1 AND uid IS NULL', (member, sid)).fetchone():
                raise DutyError("Человек уже подключился или его больше нет в списке. Обновите состав группы.")
            db.execute('UPDATE invites SET used=1 WHERE space=? AND member IS ?', (sid, member))
            db.execute('INSERT INTO invites VALUES(?,?,?,?,0)', (digest, sid, member, (now + timedelta(days=7)).timestamp()))
        return token

    def invitation(self, token, now=None):
        with self.connect() as db:
            row = db.execute("SELECT i.*,s.group_name,m.name FROM invites i JOIN spaces s ON s.id=i.space "
                "LEFT JOIN members m ON m.id=i.member WHERE token_hash=? AND used=0 AND expires>? AND s.status!='archived'",
                (hashlib.sha256(token.encode()).hexdigest(), (now or clock()).timestamp())).fetchone()
        if not row:
            raise DutyError("Приглашение истекло или отозвано. Попросите старосту прислать новое.")
        return dict(row)

    def claim(self, token, uid, name, now=None):
        now = now or clock()
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connect(True) as db:
            invite = db.execute("SELECT i.* FROM invites i JOIN spaces s ON s.id=i.space WHERE token_hash=? "
                "AND used=0 AND expires>? AND s.status!='archived'", (digest, now.timestamp())).fetchone()
            if not invite:
                raise DutyError("Приглашение истекло или отозвано.")
            sid = invite['space']
            if db.execute('SELECT 1 FROM members WHERE space=? AND uid=?', (sid, uid)).fetchone():
                raise DutyError("Вы уже подключились к этому списку. Откройте /duty.")
            if db.execute("SELECT 1 FROM claims WHERE space=? AND uid=? AND status='pending'", (sid, uid)).fetchone():
                raise DutyError("Заявка уже отправлена. Решение появится в /duty.")
            if db.execute('SELECT count(*) FROM claims WHERE uid=? AND created>?', (uid, now.timestamp()-86400)).fetchone()[0] >= 10:
                raise DutyError("Слишком много заявок за день. Попробуйте завтра.")
            member = invite['member']
            if member:
                row = db.execute('SELECT * FROM members WHERE id=? AND uid IS NULL AND active=1', (member,)).fetchone()
                if not row:
                    raise DutyError("Эта запись уже занята.")
                if row['telegram_id'] is not None and row['telegram_id'] != uid:
                    raise DutyError('Это приглашение для другого Telegram-аккаунта. Попросите старосту проверить ID.')
                name = row['name']
                db.execute('UPDATE invites SET used=1 WHERE token_hash=?', (digest,))
            if not name or len(name) > 80:
                raise DutyError("Пришлите @username, Telegram user ID или имя из списка. До 80 символов.")
            if not member:
                claimed_id = telegram_user_id(name)
                if claimed_id is not None and claimed_id != uid:
                    raise DutyError('Этот Telegram user ID не ваш. Пришлите свой ID или попросите старосту личное приглашение.')
            cid = db.execute('INSERT INTO claims(space,uid,member,name,created) VALUES(?,?,?,?,?)',
                             (sid, uid, member, name, now.timestamp())).lastrowid
            self._managers_job(db, sid, 'claim', cid, f'claim:{cid}')
            self.event(db, sid, uid, 'claim', cid, now)
        return cid

    def resolve_claim(self, cid, actor, member=None):
        with self.connect(True) as db:
            claim = db.execute("SELECT * FROM claims WHERE id=? AND status='pending'", (cid,)).fetchone()
            if not claim:
                raise DutyError("Заявка уже обработана.")
            sid, uid = claim['space'], claim['uid']
            self.permission(db, sid, actor, manage=True)
            if member is not None:
                m = db.execute('SELECT * FROM members WHERE space=? AND id=? AND active=1 AND uid IS NULL', (sid, member)).fetchone()
                if not m or db.execute('SELECT 1 FROM members WHERE space=? AND uid=?', (sid, uid)).fetchone():
                    raise DutyError("Эту запись уже подтвердили. Обновите список.")
                if m['telegram_id'] is not None and m['telegram_id'] != uid:
                    raise DutyError('Telegram user ID участника не совпадает с ID в списке. Проверьте приглашение.')
                if claim['member'] and member != claim['member']:
                    raise DutyError("Человек открыл приглашение к другой записи. Отправьте ему личное приглашение к нужной.")
                if not claim['member'] and not matches_member(m, claim['name'], uid):
                    raise DutyError("Имя или username не совпадает со списком. Отправьте человеку личное приглашение.")
                db.execute('UPDATE members SET uid=? WHERE id=?', (uid, member))
                db.execute("INSERT INTO access(space,uid,role) VALUES(?,?,'member') ON CONFLICT(space,uid) DO NOTHING", (sid, uid))
            db.execute('UPDATE claims SET status=? WHERE id=?', ('approved' if member else 'rejected', cid))
            self._job(db, sid, uid, 'claim_result', cid, f'claim_result:{cid}')
            self.event(db, sid, actor, 'claim_approved' if member else 'claim_rejected', cid)

    @staticmethod
    def _job(db, sid, uid, kind, ref, key):
        db.execute('INSERT OR IGNORE INTO outbox(space,uid,kind,ref,dedup) VALUES(?,?,?,?,?)',
                   (sid, uid, kind, ref, f'{key}:{uid}'))

    def _managers_job(self, db, sid, kind, ref, key, force=False):
        opts = json.loads(self.space(db, sid)['settings'])
        if force or opts['owner_events']:
            for row in db.execute("SELECT uid FROM access WHERE space=? AND role IN ('owner','manager') AND muted=0", (sid,)):
                self._job(db, sid, row['uid'], kind, ref, key)

    @staticmethod
    def _day(db, did):
        row = db.execute('SELECT * FROM days WHERE id=?', (did,)).fetchone()
        if not row:
            raise DutyError("Дежурство не найдено.")
        return dict(row)

    @staticmethod
    def _member(db, mid):
        row = db.execute('SELECT * FROM members WHERE id=?', (mid,)).fetchone()
        if not row:
            raise DutyError("Участник не найден.")
        return dict(row)

    def _daily_targets(self, db, day):
        if day['status'] == 'no_duty':
            return set()
        opts = json.loads(self.space(db, day['space'])['settings'])
        targets = set()
        if opts['audience'] == 'all':
            targets.update(row[0] for row in db.execute("SELECT DISTINCT a.uid FROM access a JOIN members m "
                "ON m.space=a.space AND m.uid=a.uid WHERE a.space=? AND a.muted=0 AND a.daily_muted=0 "
                "AND m.active=1", (day['space'],)))
        elif opts['audience'] == 'assigned':
            targets.update(row[0] for row in db.execute("SELECT DISTINCT m.uid FROM assignments x JOIN members m ON m.id=x.member "
                "JOIN access a ON a.space=m.space AND a.uid=m.uid WHERE x.day=? AND a.muted=0 AND a.daily_muted=0 "
                "AND x.state IN ('assigned','waiting','reported','done') AND m.active=1", (day['id'],)))
        if opts['owner_daily']:
            targets.update(row[0] for row in db.execute("SELECT uid FROM access WHERE space=? AND role IN ('owner','manager') AND muted=0 AND daily_muted=0", (day['space'],)))
        return targets - {None}

    def _changed(self, db, did, *, initial=False):
        db.execute('UPDATE days SET revision=revision+1 WHERE id=?', (did,))
        day = self._day(db, did)
        if day['status'] == 'no_duty':
            return
        # Update previous morning cards too, including somebody whose assignment
        # was just cancelled. Settings/membership are rechecked before delivery.
        targets = self._daily_targets(db, day)
        if not initial:
            targets.update(row[0] for row in db.execute("SELECT DISTINCT uid FROM outbox WHERE ref=? "
                "AND kind IN ('daily','update') AND status='sent'", (did,)))
        for uid in targets:
            if not initial and not db.execute("SELECT 1 FROM outbox WHERE ref=? AND uid=? AND kind IN ('daily','update') AND status='sent'",
                                              (did, uid)).fetchone() and not db.execute(
                    "SELECT 1 FROM assignments x JOIN members m ON m.id=x.member WHERE x.day=? AND m.uid=? "
                    "AND x.state IN ('assigned','waiting','reported')", (did, uid)).fetchone():
                # Informational subscriptions start with the next morning;
                # a newly assigned duty can still require a personal message.
                continue
            self._job(db, day['space'], uid, 'daily' if initial else 'update', did,
                      f"daily:{did}" if initial else f"update:{did}:{day['revision']}")

    def _available(self, db, member, day):
        if not member['active'] or (member['exempt_until'] and member['exempt_until'] >= day['date']):
            return False
        if member['id'] in json.loads(day['excluded']):
            return False
        # An unfinished past result is held for review, never silently charged
        # or used to call the same person for another shift.
        if db.execute("SELECT 1 FROM assignments a JOIN days d ON d.id=a.day WHERE a.member=? "
                      "AND d.date!=? AND a.state IN ('assigned','waiting','reported')", (member['id'], day['date'])).fetchone():
            return False
        # One Telegram account can join several lists, but cannot be silently
        # scheduled for simultaneous daily duties in two of them.
        if member['uid'] and db.execute("SELECT 1 FROM assignments a JOIN members m ON m.id=a.member "
                "JOIN days d ON d.id=a.day WHERE m.uid=? AND d.date=? AND d.id!=? "
                "AND a.state IN ('assigned','waiting','reported','done')", (member['uid'], day['date'], day['id'])).fetchone():
            return False
        return True

    @staticmethod
    def _assign(db, did, mid, state):
        db.execute('INSERT INTO assignments(day,member,state) VALUES(?,?,?) ON CONFLICT(day,member) '
                   'DO UPDATE SET state=excluded.state', (did, mid, state))

    def _offer(self, db, day, kind, host, guest, now):
        opts = json.loads(self.space(db, day['space'])['settings'])
        expires = min(day['cutoff'], now.timestamp() + opts['wait_minutes'] * 60)
        oid = db.execute('INSERT INTO offers(day,kind,host,guest,expires,created) VALUES(?,?,?,?,?,?)',
                         (day['id'], kind, host['id'], guest['id'], expires, now.timestamp())).lastrowid
        db.execute("UPDATE days SET status='searching' WHERE id=?", (day['id'],))
        self._assign(db, day['id'], host['id'], 'waiting')
        if kind == 'pair':
            self._assign(db, day['id'], guest['id'], 'waiting')
        if opts['requests']:
            for m in (host, guest):
                if m['uid']:
                    self._job(db, day['space'], m['uid'], 'offer', oid, f'offer:{oid}')
        self.event(db, day['space'], None, 'offer_created', oid, now)
        return oid

    def _replacement(self, db, did, host_id, now):
        day = self._day(db, did)
        host = self._member(db, host_id)
        opts = json.loads(self.space(db, day['space'])['settings'])
        if now.timestamp() >= day['cutoff'] or opts['mode'] == 'pair' or not host['uid']:
            self._skip_pair(db, did, now)
            return
        used = {row[0] for row in db.execute('SELECT guest FROM offers WHERE day=?', (did,))}
        reserves = [dict(r) for r in db.execute("SELECT m.* FROM members m WHERE m.space=? AND m.pair IS NULL "
            "AND m.active=1 AND m.uid IS NOT NULL ORDER BY (SELECT count(*) FROM assignments a WHERE a.member=m.id "
            "AND a.state='done'),m.id", (day['space'],))]
        for candidate in reserves:
            if candidate['id'] not in used and self._available(db, candidate, day):
                self._offer(db, day, 'reserve', host, candidate, now)
                return
        self._skip_pair(db, did, now)

    def _choose_pair(self, db, did, now, *, promoted=False):
        day = self._day(db, did)
        if now.timestamp() >= day['cutoff']:
            self._unfilled(db, did, now)
            return
        attempted = set(json.loads(day['attempted']))
        pairs = db.execute('SELECT p.* FROM pairs p WHERE space=? ORDER BY '
            '(SELECT min(pending_since) FROM members WHERE pair=p.id) IS NULL,'
            '(SELECT min(pending_since) FROM members WHERE pair=p.id),rounds,rank,id', (day['space'],)).fetchall()
        for pair in pairs:
            if pair['id'] in attempted:
                continue
            attempted.add(pair['id'])
            db.execute('UPDATE days SET attempted=?,pair=? WHERE id=?', (json.dumps(sorted(attempted)), pair['id'], did))
            people = [dict(r) for r in db.execute('SELECT * FROM members WHERE pair=? ORDER BY id', (pair['id'],))]
            if len(people) != 2:
                continue
            due = [m for m in people if m['credit'] == 0 and self._available(db, m, day)]
            if len(due) == 2:
                if promoted:
                    if not all(m['uid'] for m in due):
                        continue
                    self._offer(db, self._day(db, did), 'pair', due[0], due[1], now)
                else:
                    for m in due:
                        self._assign(db, did, m['id'], 'assigned')
                    db.execute("UPDATE days SET status='assigned' WHERE id=?", (did,))
                return
            if len(due) == 1:
                # Previously completed partner keeps their credit. Only the
                # outstanding person is offered a voluntary reserve pairing.
                self._assign(db, did, due[0]['id'], 'waiting')
                self._replacement(db, did, due[0]['id'], now)
                return
        self._unfilled(db, did, now)

    def _unfilled(self, db, did, now):
        day = self._day(db, did)
        db.execute("UPDATE days SET status='unfilled' WHERE id=?", (did,))
        db.execute("UPDATE offers SET state='cancelled' WHERE day=? AND state='pending'", (did,))
        db.execute("UPDATE assignments SET state='released' WHERE day=? AND state='waiting'", (did,))
        self._managers_job(db, day['space'], 'alert', did, f"unfilled:{did}")
        self.event(db, day['space'], None, 'unfilled', did, now)

    def _skip_pair(self, db, did, now):
        day = self._day(db, did)
        db.execute("UPDATE offers SET state='cancelled' WHERE day=? AND state='pending'", (did,))
        for row in db.execute("SELECT member FROM assignments WHERE day=? AND state IN ('assigned','waiting')", (did,)).fetchall():
            db.execute('UPDATE members SET pending_since=coalesce(pending_since,?) WHERE id=?', (day['date'], row['member']))
        db.execute("UPDATE assignments SET state='released' WHERE day=? AND state IN ('assigned','waiting')", (did,))
        self._choose_pair(db, did, now, promoted=True)

    def ensure_day(self, sid, now=None, source_state='unknown', source_revision=None):
        now = now or clock()
        with self.connect(True) as db:
            space = self.space(db, sid)
            opts = json.loads(space['settings'])
            day = now.date().isoformat()
            if space['status'] != 'active' or day < space['first_date'] or now < at(day, opts['time']):
                return None
            existing = db.execute('SELECT id FROM days WHERE space=? AND date=?', (sid, day)).fetchone()
            if existing:
                return existing['id']
            inactive = now.weekday() not in opts['days'] or source_state == 'no_lessons'
            did = db.execute('INSERT INTO days(space,date,status,source_state,source_revision,cutoff,report_at) VALUES(?,?,?,?,?,?,?)',
                (sid, day, 'no_duty' if inactive else 'searching', source_state, source_revision,
                 at(day, opts['cutoff']).timestamp(), at(day, opts['report']).timestamp())).lastrowid
            if not inactive:
                self._choose_pair(db, did, now)
            self._changed(db, did, initial=True)
            self.event(db, sid, None, 'day_created', did, now)
            return did

    def decline(self, did, uid, now=None):
        now = now or clock()
        with self.connect(True) as db:
            day = self._day(db, did)
            self.permission(db, day['space'], uid)
            if day['date'] != now.date().isoformat():
                raise DutyError("Эта кнопка относится к другому дню. Откройте /duty.")
            member = db.execute('SELECT * FROM members WHERE space=? AND uid=? AND active=1', (day['space'], uid)).fetchone()
            assignment = db.execute('SELECT * FROM assignments WHERE day=? AND member=?', (did, member['id'])).fetchone() if member else None
            if not assignment or assignment['state'] not in ('assigned', 'waiting'):
                raise DutyError("Сегодня вы не назначены или ваш ответ уже учтён.")
            mid = member['id']
            excluded = set(json.loads(day['excluded'])) | {mid}
            db.execute('UPDATE days SET excluded=? WHERE id=?', (json.dumps(sorted(excluded)), did))
            db.execute("UPDATE assignments SET state='declined' WHERE id=?", (assignment['id'],))
            db.execute('UPDATE members SET pending_since=coalesce(pending_since,?) WHERE id=?', (day['date'], mid))
            db.execute("UPDATE offers SET state='cancelled' WHERE day=? AND state='pending'", (did,))
            remaining = db.execute("SELECT member FROM assignments WHERE day=? AND state IN ('assigned','waiting')", (did,)).fetchall()
            if now.timestamp() >= day['cutoff']:
                # Do not retroactively release the partner who may already be
                # working. Their assignment remains available for completion.
                self._unfilled(db, did, now)
            elif len(remaining) == 1:
                self._replacement(db, did, remaining[0]['member'], now)
            else:
                self._skip_pair(db, did, now)
            self._changed(db, did)
            self._managers_job(db, day['space'], 'change', did, f"decline:{did}:{mid}")
            self.event(db, day['space'], uid, 'declined', mid, now)

    def reply_offer(self, oid, uid, accept, now=None):
        now = now or clock()
        with self.connect(True) as db:
            offer = db.execute('SELECT * FROM offers WHERE id=?', (oid,)).fetchone()
            if not offer:
                raise DutyError("Предложение не найдено.")
            day = self._day(db, offer['day'])
            self.permission(db, day['space'], uid)
            host, guest = self._member(db, offer['host']), self._member(db, offer['guest'])
            side = 'host' if host['uid'] == uid else 'guest' if guest['uid'] == uid else None
            if not side:
                raise DutyError("Это предложение адресовано другому человеку.")
            if offer['state'] != 'pending' or now.timestamp() >= offer['expires'] or day['date'] != now.date().isoformat():
                raise DutyError("Предложение уже закрыто. Актуальное дежурство — в /duty.")
            member = host if side == 'host' else guest
            if not accept:
                db.execute("UPDATE offers SET state='declined' WHERE id=?", (oid,))
                excluded = set(json.loads(day['excluded'])) | {member['id']}
                db.execute('UPDATE days SET excluded=? WHERE id=?', (json.dumps(sorted(excluded)), day['id']))
                if offer['kind'] == 'reserve' and side == 'guest':
                    self._replacement(db, day['id'], host['id'], now)
                else:
                    self._skip_pair(db, day['id'], now)
            else:
                db.execute(f'UPDATE offers SET {side}_yes=1 WHERE id=?', (oid,))
                latest = db.execute('SELECT * FROM offers WHERE id=?', (oid,)).fetchone()
                if latest['host_yes'] and latest['guest_yes']:
                    fresh = self._day(db, day['id'])
                    if not all(self._available(db, m, fresh) for m in (host, guest)):
                        db.execute("UPDATE offers SET state='cancelled' WHERE id=?", (oid,))
                        self._skip_pair(db, day['id'], now)
                    else:
                        db.execute("UPDATE offers SET state='accepted' WHERE id=?", (oid,))
                        for m in (host, guest):
                            self._assign(db, day['id'], m['id'], 'assigned')
                            self._job(db, day['space'], m['uid'], 'accepted', day['id'], f'accepted:{oid}')
                        db.execute("UPDATE days SET status='assigned' WHERE id=?", (day['id'],))
                        self._managers_job(db, day['space'], 'change', day['id'], f'accepted_owner:{oid}')
            self.event(db, day['space'], uid, 'offer_yes' if accept else 'offer_no', oid, now)
            self._changed(db, day['id'])

    def tick(self, now=None):
        now = now or clock()
        with self.connect(True) as db:
            pending = db.execute("SELECT o.* FROM offers o JOIN days d ON d.id=o.day JOIN spaces s ON s.id=d.space "
                                 "WHERE o.state='pending' AND s.status='active'").fetchall()
            for offer in pending:
                day = self._day(db, offer['day'])
                if now.timestamp() >= offer['expires']:
                    db.execute("UPDATE offers SET state='expired' WHERE id=?", (offer['id'],))
                    if offer['kind'] == 'reserve' and offer['host_yes'] and now.date().isoformat() == day['date']:
                        self._replacement(db, day['id'], offer['host'], now)
                    else:
                        self._skip_pair(db, day['id'], now)
                    self._changed(db, day['id'])
                elif not offer['reminded'] and now.timestamp() >= (offer['created'] + offer['expires']) / 2:
                    db.execute('UPDATE offers SET reminded=1 WHERE id=?', (offer['id'],))
                    opts = json.loads(self.space(db, day['space'])['settings'])
                    if opts['requests']:
                        for side in ('host', 'guest'):
                            m = self._member(db, offer[side])
                            if m['uid'] and not offer[side + '_yes']:
                                self._job(db, day['space'], m['uid'], 'offer_reminder', offer['id'], f"remind:{offer['id']}")
            for day in db.execute("SELECT d.* FROM days d JOIN spaces s ON s.id=d.space WHERE s.status='active' "
                "AND d.reported=0 AND d.report_at<=?", (now.timestamp(),)).fetchall():
                db.execute('UPDATE days SET reported=1 WHERE id=?', (day['id'],))
                if day['status'] != 'no_duty':
                    self._managers_job(db, day['space'], 'report', day['id'], f"report:{day['id']}")

    def report_done(self, did, uid, now=None):
        now = now or clock()
        with self.connect(True) as db:
            day = self._day(db, did)
            self.permission(db, day['space'], uid)
            if now.timestamp() < day['cutoff']:
                raise DutyError("Сообщить о выполнении можно после окончания подбора дежурных.")
            row = db.execute("SELECT a.id FROM assignments a JOIN members m ON m.id=a.member WHERE a.day=? "
                             "AND m.uid=? AND a.state='assigned'", (did, uid)).fetchone()
            if not row:
                raise DutyError("Отметка уже передана или вы не назначены.")
            db.execute("UPDATE assignments SET state='reported' WHERE id=?", (row['id'],))
            self._managers_job(db, day['space'], 'report', did, f"reported:{did}:{uid}")
            self.event(db, day['space'], uid, 'reported', did, now)
            self._changed(db, did)

    def finish(self, did, actor, mid=None, outcome='done', now=None):
        now = now or clock()
        if outcome not in ('done', 'missed', 'excused'):
            raise DutyError("Неизвестный результат.")
        with self.connect(True) as db:
            day = self._day(db, did)
            self.permission(db, day['space'], actor, manage=True)
            if now.timestamp() < day['cutoff']:
                raise DutyError("Дождитесь окончания подбора дежурных.")
            rows = db.execute("SELECT a.*,m.pair FROM assignments a JOIN members m ON m.id=a.member WHERE a.day=? "
                "AND a.state IN ('assigned','reported') AND (? IS NULL OR a.member=?)", (did, mid, mid)).fetchall()
            if not rows:
                raise DutyError("Отмечать больше некого: результаты уже сохранены или дежурных не назначали.")
            for row in rows:
                db.execute('UPDATE assignments SET state=? WHERE id=?', (outcome, row['id']))
                if outcome == 'done':
                    db.execute('UPDATE members SET pending_since=NULL WHERE id=?', (row['member'],))
                elif outcome == 'missed':
                    db.execute('UPDATE members SET pending_since=coalesce(pending_since,?) WHERE id=?', (day['date'], row['member']))
                self.event(db, day['space'], actor, outcome, row['member'], now)
            self._recalculate(db, day['space'])
            if not db.execute("SELECT 1 FROM assignments WHERE day=? AND state IN ('assigned','waiting','reported')", (did,)).fetchone():
                db.execute("UPDATE days SET status='closed' WHERE id=?", (did,))
            self._changed(db, did)

    def day_view(self, did, uid):
        with self.connect() as db:
            day = self._day(db, did)
            access = dict(self.permission(db, day['space'], uid))
            space = self.space(db, day['space'])
            assignments = [dict(r) for r in db.execute('SELECT a.*,m.name,m.uid,m.pair FROM assignments a '
                'JOIN members m ON m.id=a.member WHERE a.day=? ORDER BY a.id', (did,))]
            offers = [dict(r) for r in db.execute("SELECT o.*,h.name host_name,g.name guest_name,h.uid host_uid,g.uid guest_uid "
                "FROM offers o JOIN members h ON h.id=o.host JOIN members g ON g.id=o.guest "
                "WHERE o.day=? AND o.state='pending'", (did,))]
        return dict(day=day, access=access, space=space, assignments=assignments, offers=offers)

    @staticmethod
    def _recalculate(db, sid):
        for pair in db.execute('SELECT id FROM pairs WHERE space=?', (sid,)).fetchall():
            members = db.execute("SELECT m.id,(SELECT count(*) FROM assignments a WHERE a.member=m.id AND a.state='done') n "
                                 'FROM members m WHERE m.pair=?', (pair['id'],)).fetchall()
            if len(members) == 2:
                rounds = min(m['n'] for m in members)
                db.execute('UPDATE pairs SET rounds=? WHERE id=?', (rounds, pair['id']))
                for m in members:
                    db.execute('UPDATE members SET credit=? WHERE id=?', (m['n'] - rounds, m['id']))

    def correct_result(self, did, mid, actor, outcome, now=None):
        if outcome not in ('done', 'missed', 'excused'):
            raise DutyError("Неизвестный результат.")
        with self.connect(True) as db:
            day = self._day(db, did)
            self.permission(db, day['space'], actor, manage=True)
            row = db.execute("SELECT * FROM assignments WHERE day=? AND member=? AND state IN ('done','missed','excused')", (did, mid)).fetchone()
            if not row:
                raise DutyError("Для этой записи ещё нет результата.")
            db.execute('UPDATE assignments SET state=? WHERE id=?', (outcome, row['id']))
            if outcome == 'done':
                db.execute('UPDATE members SET pending_since=NULL WHERE id=?', (mid,))
            elif outcome == 'missed':
                db.execute('UPDATE members SET pending_since=coalesce(pending_since,?) WHERE id=?', (day['date'], mid))
            self._recalculate(db, day['space'])
            self.event(db, day['space'], actor, 'result_corrected', f'{mid}:{row["state"]}->{outcome}', now)

    def manage(self, sid, uid, action, value=None):
        with self.connect(True) as db:
            access = self.permission(db, sid, uid, manage=action not in ('mute', 'silence', 'leave', 'daily_notifications'),
                                     owner=action in ('transfer', 'manager'))
            if action == 'mute':
                db.execute('UPDATE access SET muted=1-muted WHERE space=? AND uid=?', (sid, uid))
            elif action == 'silence':
                db.execute('UPDATE access SET muted=1 WHERE space=? AND uid=?', (sid, uid))
            elif action == 'daily_notifications':
                if type(value) is not bool:
                    raise DutyError('Укажите, получать ли уведомления о дежурных.')
                db.execute('UPDATE access SET daily_muted=? WHERE space=? AND uid=?', (int(not value), sid, uid))
            elif action == 'leave':
                if access['role'] == 'owner':
                    raise DutyError("Сначала передайте управление или архивируйте список.")
                member = db.execute('SELECT id FROM members WHERE space=? AND uid=?', (sid, uid)).fetchone()
                if member:
                    offers = db.execute("SELECT * FROM offers WHERE state='pending' AND (host=? OR guest=?)",
                                        (member['id'], member['id'])).fetchall()
                    for offer in offers:
                        db.execute("UPDATE offers SET state='cancelled' WHERE id=?", (offer['id'],))
                        self._skip_pair(db, offer['day'], clock())
                        self._changed(db, offer['day'])
                    for assignment in db.execute("SELECT day FROM assignments WHERE member=? AND state IN ('assigned','reported')", (member['id'],)).fetchall():
                        self._managers_job(db, sid, 'change', assignment['day'], f"left:{member['id']}:{assignment['day']}")
                db.execute('UPDATE members SET uid=NULL WHERE space=? AND uid=?', (sid, uid))
                db.execute('DELETE FROM access WHERE space=? AND uid=?', (sid, uid))
            elif action in ('pause', 'resume', 'archive'):
                status = {'pause': 'paused', 'resume': 'active', 'archive': 'archived'}[action]
                space = self.space(db, sid)
                if status != 'archived' and (action == 'resume' or space['status'] == 'archived'):
                    if db.execute("SELECT 1 FROM spaces WHERE owner=? AND status!='archived' AND id!=?",
                                  (space['owner'], sid)).fetchone():
                        raise DutyError('У создателя уже есть другой список дежурств. Сначала нужно архивировать его.')
                db.execute('UPDATE spaces SET status=? WHERE id=?', (status, sid))
                if action != 'resume':
                    db.execute("UPDATE offers SET state='cancelled' WHERE state='pending' AND day IN (SELECT id FROM days WHERE space=?)", (sid,))
                    db.execute("UPDATE assignments SET state='released' WHERE state='waiting' AND day IN (SELECT id FROM days WHERE space=?)", (sid,))
                    db.execute("UPDATE days SET status='unfilled' WHERE space=? AND status='searching'", (sid,))
                    db.execute("UPDATE outbox SET status='cancelled' WHERE space=? AND status='pending' "
                               "AND (?='archive' OR kind!='added')", (sid, action))
            elif action in ('transfer', 'manager'):
                target = db.execute('SELECT * FROM access WHERE space=? AND uid=?', (sid, value)).fetchone()
                if not target or value == uid:
                    raise DutyError("Выберите другого участника, который уже вступил в список.")
                if action == 'transfer':
                    if self.space(db, sid)['status'] != 'archived' and db.execute(
                            "SELECT 1 FROM spaces WHERE owner=? AND status!='archived' AND id!=?", (value, sid)).fetchone():
                        raise DutyError('Этот участник уже ведёт свои дежурства. Выберите другого участника.')
                    db.execute("UPDATE access SET role='manager' WHERE space=? AND uid=?", (sid, uid))
                    db.execute("UPDATE access SET role='owner' WHERE space=? AND uid=?", (sid, value))
                    db.execute('UPDATE spaces SET owner=? WHERE id=?', (value, sid))
                else:
                    db.execute('UPDATE access SET role=? WHERE space=? AND uid=?',
                               ('member' if target['role'] == 'manager' else 'manager', sid, value))
            else:
                raise DutyError("Неизвестное действие.")
            self.event(db, sid, uid, action, value or '')

    def edit_member(self, mid, actor, field, value):
        if field not in ('name', 'exempt_until', 'active', 'uid'):
            raise DutyError("Неизвестное поле.")
        with self.connect(True) as db:
            member = self._member(db, mid)
            self.permission(db, member['space'], actor, manage=True)
            if field != 'name' and db.execute("SELECT 1 FROM assignments WHERE member=? AND state IN ('assigned','waiting','reported')", (mid,)).fetchone():
                raise DutyError("Сначала отметьте результат дежурства этого участника в разделе «Кто отдежурил».")
            if field != 'name' and db.execute("SELECT 1 FROM offers WHERE state='pending' AND (host=? OR guest=?)", (mid, mid)).fetchone():
                raise DutyError("Ждём ответа этого участника на замену. Дождитесь ответа или отмените дежурство.")
            if field == 'name':
                value = person(value)['name']
            if field == 'exempt_until' and value:
                try:
                    value = date.fromisoformat(value).isoformat()
                except ValueError:
                    raise DutyError("Укажите дату в формате ДД.ММ.ГГГГ.") from None
            if field == 'uid':
                if value is not None:
                    raise DutyError("Чтобы подключить человека, отправьте ему приглашение и подтвердите его заявку.")
                if member['uid']:
                    db.execute("DELETE FROM access WHERE space=? AND uid=? AND role='member'", (member['space'], member['uid']))
            db.execute(f'UPDATE members SET {field}=? WHERE id=?', (value, mid))
            self.event(db, member['space'], actor, 'member_' + field, mid)

    def append_roster(self, sid, actor, pairs=None, reserves=None, *, auto_link=False):
        pairs, reserves = pairs or [], reserves or []
        with self.connect(True) as db:
            self.permission(db, sid, actor, manage=True)
            old = [dict(r) for r in db.execute('SELECT name,username,telegram_id,uid FROM members WHERE space=?', (sid,))]
            new = [p for pair in pairs for p in pair] + reserves
            if not new or len(old) + len(new) > 60:
                raise DutyError("В списке может быть до 60 человек.")
            validate_people(old + new)
            added = self._add_people(db, sid, pairs, reserves)
            if auto_link:
                self._link_added(db, sid, actor, added)
            self.event(db, sid, actor, 'members_added', len(new))

    def manual_pair(self, did, actor, pid, now=None):
        now = now or clock()
        with self.connect(True) as db:
            day = self._day(db, did)
            self.permission(db, day['space'], actor, manage=True)
            if day['date'] != now.date().isoformat():
                raise DutyError("Вручную назначить пару можно только на сегодня.")
            if db.execute("SELECT 1 FROM assignments WHERE day=? AND state IN ('done','reported')", (did,)).fetchone():
                raise DutyError("Кто-то уже отметил дежурство. Проверьте результаты кнопкой «Отметить каждого отдельно».")
            people = [dict(r) for r in db.execute('SELECT * FROM members WHERE pair=? AND space=? AND active=1', (pid, day['space']))]
            if len(people) != 2 or not all(self._available(db, p, day) for p in people):
                raise DutyError("Кто-то из пары отказался, освобождён или занят другим дежурством. Проверьте участников и отметки за прошлые дни.")
            if any(p['credit'] for p in people):
                raise DutyError("Один из напарников в этот раз уже отдежурил. Проверьте результаты и назначьте второму запасного.")
            db.execute("UPDATE offers SET state='cancelled' WHERE day=? AND state='pending'", (did,))
            db.execute("UPDATE assignments SET state='released' WHERE day=? AND state IN ('assigned','waiting')", (did,))
            for p in people:
                self._assign(db, did, p['id'], 'assigned')
            db.execute("UPDATE days SET pair=?,status='assigned' WHERE id=?", (pid, did))
            self._changed(db, did)
            self.event(db, day['space'], actor, 'manual_pair_confirmed', pid, now)

    def history(self, sid, uid):
        with self.connect() as db:
            self.permission(db, sid, uid, manage=True)
            return [dict(r) for r in db.execute('SELECT e.*,i.name actor_name FROM events e LEFT JOIN identities i ON i.uid=e.actor '
                                               'WHERE e.space=? ORDER BY e.id DESC LIMIT 20', (sid,))]

    def reconsider(self, did, uid, now=None):
        now = now or clock()
        with self.connect(True) as db:
            day = self._day(db, did)
            self.permission(db, day['space'], uid)
            member = db.execute('SELECT * FROM members WHERE space=? AND uid=? AND active=1', (day['space'], uid)).fetchone()
            excluded = set(json.loads(day['excluded']))
            if day['date'] != now.date().isoformat() or not member or member['id'] not in excluded:
                raise DutyError("Для сегодняшнего дня нет отказа, который можно изменить.")
            excluded.remove(member['id'])
            db.execute('UPDATE days SET excluded=? WHERE id=?', (json.dumps(sorted(excluded)), did))
            db.execute("UPDATE assignments SET state='released' WHERE day=? AND member=? AND state='declined'", (did, member['id']))
            # Do not undo a confirmed replacement. If no team was found yet,
            # the original pair can be offered again with fresh consent.
            if day['status'] == 'unfilled' and now.timestamp() < day['cutoff'] and member['pair']:
                attempted = set(json.loads(day['attempted'])) - {member['pair']}
                db.execute('UPDATE days SET attempted=? WHERE id=?', (json.dumps(sorted(attempted)), did))
                self._choose_pair(db, did, now, promoted=True)
            self._managers_job(db, day['space'], 'change', did, f'reconsider:{did}:{member["id"]}')
            self._changed(db, did)
            self.event(db, day['space'], uid, 'reconsidered', member['id'], now)

    def cancel_day(self, did, actor, now=None):
        with self.connect(True) as db:
            day = self._day(db, did)
            self.permission(db, day['space'], actor, manage=True)
            if db.execute("SELECT 1 FROM assignments WHERE day=? AND state='reported'", (did,)).fetchone():
                raise DutyError("Сначала проверьте сообщения о выполнении. Уже выполненную работу нужно учесть.")
            db.execute("UPDATE offers SET state='cancelled' WHERE day=? AND state='pending'", (did,))
            db.execute("UPDATE assignments SET state='cancelled' WHERE day=? AND state IN ('assigned','waiting')", (did,))
            db.execute("UPDATE days SET status='cancelled' WHERE id=?", (did,))
            self._changed(db, did)
            self.event(db, day['space'], actor, 'day_cancelled', did, now)

    def manual_reserve(self, did, actor, host_id, guest_id, now=None):
        now = now or clock()
        with self.connect(True) as db:
            day = self._day(db, did)
            self.permission(db, day['space'], actor, manage=True)
            people = [self._member(db, mid) for mid in (host_id, guest_id)]
            if day['date'] != now.date().isoformat() or any(p['space'] != day['space'] for p in people):
                raise DutyError("Выберите участников этого списка для сегодняшнего дня.")
            if not people[0]['pair'] or people[1]['pair'] or people[0]['credit']:
                raise DutyError("Выберите ещё не отработавшего участника пары и запасного.")
            if not all(self._available(db, p, day) for p in people):
                raise DutyError("Кто-то отказался, освобождён или занят другим дежурством. Проверьте участников и отметки за прошлые дни.")
            if db.execute("SELECT 1 FROM assignments WHERE day=? AND (state='reported' OR (state='done' AND member IN (?,?)))",
                          (did, host_id, guest_id)).fetchone():
                raise DutyError("Проверьте результаты: нельзя повторно назначить уже отдежурившего человека.")
            db.execute("UPDATE offers SET state='cancelled' WHERE day=? AND state='pending'", (did,))
            db.execute("UPDATE assignments SET state='released' WHERE day=? AND state IN ('assigned','waiting')", (did,))
            for p in people:
                self._assign(db, did, p['id'], 'assigned')
            db.execute("UPDATE days SET pair=?,status='assigned' WHERE id=?", (people[0]['pair'], did))
            self._changed(db, did)
            self.event(db, day['space'], actor, 'manual_reserve_confirmed', f'{host_id},{guest_id}', now)

    def swap_partners(self, first, second, actor):
        with self.connect(True) as db:
            a, b = self._member(db, first), self._member(db, second)
            self.permission(db, a['space'], actor, manage=True)
            if a['space'] != b['space'] or not a['active'] or not b['active'] or a['pair'] == b['pair']:
                raise DutyError("Выберите участника другой пары или запасного из этого списка.")
            involved = db.execute('SELECT * FROM members WHERE id IN (?,?) OR pair IN (?,?)',
                                  (first, second, a['pair'], b['pair'])).fetchall()
            for m in involved:
                if m['credit'] or db.execute("SELECT 1 FROM assignments WHERE member=? AND state IN ('assigned','waiting','reported')", (m['id'],)).fetchone():
                    raise DutyError("Сначала отметьте незакрытые дежурства этих участников. Если один напарник уже отдежурил за пару, дождитесь второго — так очередь останется справедливой.")
            db.execute('UPDATE members SET pair=? WHERE id=?', (b['pair'], a['id']))
            db.execute('UPDATE members SET pair=? WHERE id=?', (a['pair'], b['id']))
            db.execute('UPDATE members SET credit=0 WHERE id IN (?,?) AND pair IS NULL', (first, second))
            self._recalculate(db, a['space'])
            self.event(db, a['space'], actor, 'partners_changed', f'{first},{second}')

    def active_spaces(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM spaces WHERE status='active'")]

    def pending_jobs(self, now=None):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM outbox WHERE status='pending' AND not_before<=? ORDER BY id LIMIT 30",
                                               ((now or clock()).timestamp(),))]

    def recover_outbox(self):
        # sendMessage has no idempotency key. Never replay an interrupted send.
        with self.connect(True) as db:
            db.execute("UPDATE outbox SET status='unknown',error='interrupted' WHERE status='sending'")

    def delivery_allowed(self, db, job, now):
        sid, uid, kind = job['space'], job['uid'], job['kind']
        space = self.space(db, sid)
        if space['status'] != 'active' and (kind != 'added' or space['status'] == 'archived'):
            return False
        opts = json.loads(space['settings'])
        if kind == 'claim_result':
            return bool(db.execute('SELECT 1 FROM claims WHERE id=? AND uid=? AND space=?', (job['ref'], uid, sid)).fetchone())
        access = db.execute('SELECT * FROM access WHERE space=? AND uid=? AND muted=0', (sid, uid)).fetchone()
        if not access:
            return False
        if kind == 'added':
            return bool(db.execute('SELECT 1 FROM members WHERE id=? AND space=? AND uid=? AND active=1',
                                   (job['ref'], sid, uid)).fetchone())
        if kind in ('offer', 'offer_reminder'):
            offer = db.execute("SELECT * FROM offers WHERE id=? AND state='pending' AND expires>?", (job['ref'], now.timestamp())).fetchone()
            return bool(opts['requests'] and offer and uid in (self._member(db, offer['host'])['uid'], self._member(db, offer['guest'])['uid']))
        if kind == 'accepted':
            return opts['requests'] and bool(db.execute("SELECT 1 FROM assignments a JOIN members m ON m.id=a.member "
                "WHERE a.day=? AND m.uid=? AND a.state='assigned'", (job['ref'], uid)).fetchone())
        if kind == 'claim':
            return opts['owner_events'] and access['role'] in ('owner', 'manager') and bool(db.execute("SELECT 1 FROM claims WHERE id=? AND status='pending'", (job['ref'],)).fetchone())
        if kind == 'delivery_issue':
            return access['role'] in ('owner', 'manager') and opts['owner_events']
        if kind in ('report', 'alert', 'change', 'source_change'):
            day = self._day(db, job['ref'])
            return day['status'] != 'no_duty' and access['role'] in ('owner', 'manager') and opts['owner_events']
        day = self._day(db, job['ref'])
        if day['status'] == 'no_duty' or day['date'] != now.date().isoformat():
            return False
        if access['daily_muted']:
            return False
        if uid in self._daily_targets(db, day):
            return True
        # Remove stale assignment buttons from an existing card without sending
        # another daily announcement to somebody outside today's audience.
        return kind == 'update' and opts['audience'] != 'off' and bool(db.execute(
            'SELECT 1 FROM assignments a JOIN members m ON m.id=a.member WHERE a.day=? AND m.uid=?',
            (day['id'], uid)).fetchone()) and bool(db.execute(
            "SELECT 1 FROM outbox WHERE kind='daily' AND ref=? AND uid=? AND status='sent'", (day['id'], uid)).fetchone())

    def begin_delivery(self, jid, now=None):
        now = now or clock()
        with self.connect(True) as db:
            row = db.execute("SELECT * FROM outbox WHERE id=? AND status='pending' AND not_before<=?", (jid, now.timestamp())).fetchone()
            if not row:
                return None
            job = dict(row)
            if not self.delivery_allowed(db, job, now):
                db.execute("UPDATE outbox SET status='cancelled' WHERE id=?", (jid,))
                return None
            db.execute("UPDATE outbox SET status='sending',attempts=attempts+1 WHERE id=?", (jid,))
            return job

    def delivery_result(self, jid, status, message_id=None, retry_at=0, error=None):
        if status not in ('sent', 'failed', 'unknown', 'pending', 'cancelled'):
            raise ValueError(status)
        with self.connect(True) as db:
            db.execute('UPDATE outbox SET status=?,message_id=?,not_before=?,error=? WHERE id=?',
                       (status, message_id, retry_at, error, jid))

    def previous_card(self, did, uid):
        with self.connect() as db:
            row = db.execute("SELECT message_id FROM outbox WHERE ref=? AND uid=? AND kind IN ('daily','update') "
                "AND status='sent' AND message_id IS NOT NULL ORDER BY id DESC LIMIT 1", (did, uid)).fetchone()
        return row[0] if row else None

    def offer_view(self, oid, uid):
        with self.connect() as db:
            row = db.execute('SELECT * FROM offers WHERE id=?', (oid,)).fetchone()
            if not row:
                raise DutyError("Предложение не найдено.")
            offer = dict(row)
            day = self._day(db, offer['day'])
            self.permission(db, day['space'], uid)
            host, guest = self._member(db, offer['host']), self._member(db, offer['guest'])
            if uid not in (host['uid'], guest['uid']):
                raise DutyError("Это предложение адресовано другому человеку.")
            return dict(offer=offer, day=day, host=host, guest=guest, space=self.space(db, day['space']))

    def delivery_problem(self, job, now=None):
        now = now or clock()
        if job['kind'] == 'delivery_issue':
            return
        with self.connect(True) as db:
            opts = json.loads(self.space(db, job['space'])['settings'])
            if opts['owner_events']:
                for manager in db.execute("SELECT uid FROM access WHERE space=? AND role IN ('owner','manager') AND muted=0 AND uid!=?",
                                          (job['space'], job['uid'])):
                    self._job(db, job['space'], manager['uid'], 'delivery_issue', job['id'],
                              f"delivery_issue:{job['space']}:{job['uid']}:{now.date()}")
