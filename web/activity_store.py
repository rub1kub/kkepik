"""Bounded operational event journal shared by the bot and Mini App.

Only explicit fields enter the journal. No headers, message bodies, search input,
IP addresses, session credentials or exception strings are accepted.
"""
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import time

DEFAULT_PATH = '/var/lib/kkepik-activity/events.sqlite3'
RETENTION_DAYS = 30
META_KEYS = {'page', 'endpoint', 'method', 'http_status', 'command', 'callback',
             'content_type', 'state', 'error_type', 'duration_ms', 'tab', 'control',
             'reason', 'is_favorite', 'transport', 'space', 'day', 'member',
             'action', 'groups_before', 'groups_after', 'role_before', 'role_after',
             'schedule_type', 'file_type', 'delivery', 'download_id', 'source_id',
             'bytes_sent', 'bytes_expected', 'partial', 'attribution'}
GROUP = re.compile(r'^\d{2,3}-[А-ЯЁ]{1,3}\d{1,2}-[1-4][А-ЯЁ]{2,}(?:-\d)?$')


class RateLimited(ValueError):
    pass


def entity(kind, value):
    if kind is None and value is None:
        return None, None
    if kind not in ('group', 'teacher', 'audience') or not isinstance(value, str):
        raise ValueError('Invalid entity')
    value = value.strip()
    if not value or len(value) > 160 or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid entity')
    if kind == 'group':
        value = value.upper().replace('–', '-').replace('—', '-').replace('‑', '-')
        if not GROUP.fullmatch(value):
            raise ValueError('Invalid group')
    elif not re.fullmatch(r'[\w\s.,/()\-–—]+', value, re.UNICODE):
        raise ValueError('Invalid schedule entity')
    return kind, value


def schedule_date(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('Invalid date')
    for fmt in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError('Invalid date')


def metadata(values):
    result = {}
    for key, value in (values or {}).items():
        if key not in META_KEYS:
            continue
        if key in ('groups_before', 'groups_after'):
            if isinstance(value, list):
                result[key] = [entity('group', item)[1] for item in value[:2]]
        elif value is None or type(value) in (bool, int):
            result[key] = value
        elif isinstance(value, str) and len(value) <= 160 and re.fullmatch(r'[\w\s./:{}<>\-]+', value):
            result[key] = value
    return result


class ActivityStore:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('KKEPIK_ACTIVITY_DB') or DEFAULT_PATH)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(mode=0o770, parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=0.25)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=NORMAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS activity_events(
                    id INTEGER PRIMARY KEY, received_at_ms INTEGER NOT NULL,
                    client_at_ms INTEGER, actor_id INTEGER NOT NULL,
                    channel TEXT NOT NULL, origin TEXT NOT NULL, action TEXT NOT NULL,
                    outcome TEXT NOT NULL, entity_type TEXT, entity_id TEXT,
                    schedule_date TEXT, event_id TEXT NOT NULL, metadata TEXT NOT NULL,
                    UNIQUE(actor_id,channel,origin,event_id));
                CREATE INDEX IF NOT EXISTS activity_by_actor ON activity_events(actor_id,received_at_ms);
                CREATE INDEX IF NOT EXISTS activity_by_entity ON activity_events(entity_type,entity_id,received_at_ms);
                CREATE TABLE IF NOT EXISTS activity_meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS activity_limits(actor_id INTEGER PRIMARY KEY,minute INTEGER NOT NULL,count INTEGER NOT NULL);
            ''')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def append(self, actor_id, channel, origin, action, *, outcome='ok', event_id,
               entity_type=None, entity_id=None, date=None, details=None,
               client_at_ms=None):
        return self.append_batch(actor_id, channel, origin, [dict(action=action,
            outcome=outcome, event_id=event_id, entity_type=entity_type,
            entity_id=entity_id, date=date, details=details, client_at_ms=client_at_ms)])

    def append_batch(self, actor_id, channel, origin, events, *, rate_limit=False):
        anonymous = actor_id == 0 and channel == 'web' and origin == 'server'
        if type(actor_id) is not int or actor_id < 0 or (actor_id == 0 and not anonymous) or channel not in ('telegram', 'miniapp', 'web') or origin not in ('server', 'client'):
            raise ValueError('Invalid actor or origin')
        if not 1 <= len(events) <= 20:
            raise ValueError('Invalid batch')
        now = int(time.time() * 1000)
        rows = []
        for event in events:
            action, eid = event['action'], event['event_id']
            if not isinstance(action, str) or not re.fullmatch(r'[a-z][a-z0-9_.]{0,63}', action):
                raise ValueError('Invalid action')
            if anonymous and not action.startswith('schedule.download.'):
                raise ValueError('Invalid anonymous action')
            if not isinstance(eid, str) or not re.fullmatch(r'[A-Za-z0-9:_\-]{1,100}', eid):
                raise ValueError('Invalid event ID')
            outcome = event.get('outcome', 'ok')
            if outcome not in ('ok', 'error', 'rejected', 'observed', 'unhandled'):
                raise ValueError('Invalid outcome')
            kind, value = entity(event.get('entity_type'), event.get('entity_id'))
            at = event.get('client_at_ms')
            if at is not None and (type(at) is not int or not now - 172800000 <= at <= now + 60000):
                # A bad device clock must not lose the event; server time is authoritative.
                at = None
            rows.append((now, at, actor_id, channel, origin, action, outcome, kind,
                value, schedule_date(event.get('date')), eid,
                json.dumps(metadata(event.get('details')), ensure_ascii=False, separators=(',', ':'))))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._prune(db, now)
            if rate_limit:
                minute = now // 60000
                row = db.execute('SELECT minute,count FROM activity_limits WHERE actor_id=?', (actor_id,)).fetchone()
                count = row['count'] if row and row['minute'] == minute else 0
                if count + len(rows) > 180:
                    raise RateLimited('Too many events')
                db.execute('INSERT INTO activity_limits VALUES(?,?,?) ON CONFLICT(actor_id) '
                           'DO UPDATE SET minute=excluded.minute,count=excluded.count', (actor_id, minute, count + len(rows)))
            before = db.total_changes
            db.executemany('INSERT OR IGNORE INTO activity_events(received_at_ms,client_at_ms,actor_id,channel,origin,action,outcome,'
                           'entity_type,entity_id,schedule_date,event_id,metadata) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', rows)
            return db.total_changes - before

    @staticmethod
    def _prune(db, now):
        previous = db.execute("SELECT value FROM activity_meta WHERE key='pruned_at'").fetchone()
        if previous and now - previous[0] < 21600000:
            return
        db.execute('DELETE FROM activity_events WHERE received_at_ms<?', (now - RETENTION_DAYS * 86400000,))
        db.execute('DELETE FROM activity_limits WHERE minute<?', (now // 60000 - 1440,))
        db.execute("INSERT INTO activity_meta VALUES('pruned_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (now,))
