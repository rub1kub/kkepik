"""Shared background canvas, isolated from the schedule app and its database."""

from __future__ import annotations

import math
import os
import sqlite3
import time
from contextlib import closing

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from telegram_auth import authenticated_telegram_user
from pixel_admin_auth import CANVAS_ADMIN_TELEGRAM_ID, valid_admin_token
from pixel_chat import initialize_chat, register_chat, chat_summary
from pixel_replay import register_replay


PALETTE = (
    '#faf8f3', '#dddcd6', '#92958f', '#343b3e',
    '#d6a2b3', '#bb716a', '#ce9b68', '#98785f',
    '#d4bf79', '#afbd8c', '#789b82', '#80aaa6',
    '#82a9c1', '#6f85af', '#b09bbe', '#887795',
)
COOLDOWN_MS = 1000
CANVAS_BOUNDS = {'x0': -64, 'y0': -128, 'x1': 64, 'y1': 128}
MAX_VIEW_AREA = 65_536
EVENT_DURATION_MS = 7 * 24 * 60 * 60 * 1000
PRESENCE_TTL_MS = 35_000
PRESENCE_RETENTION_MS = 5 * 60 * 1000


def connect(path):
    db = sqlite3.connect(path, timeout=0.5, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=500')
    db.execute('PRAGMA synchronous=NORMAL')
    return db


def initialize(path):
    with closing(connect(path)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS canvas_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                revision INTEGER NOT NULL
            );
            INSERT OR IGNORE INTO canvas_meta VALUES (1, 0);
            CREATE TABLE IF NOT EXISTS pixels (
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                color INTEGER NOT NULL,
                revision INTEGER NOT NULL,
                PRIMARY KEY (x, y)
            ) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS pixels_by_y ON pixels (y, x);
            CREATE INDEX IF NOT EXISTS pixels_by_revision ON pixels (revision);
            CREATE TABLE IF NOT EXISTS canvas_players (
                telegram_id INTEGER PRIMARY KEY,
                placed_at_ms INTEGER NOT NULL,
                request_id TEXT NOT NULL,
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                color INTEGER NOT NULL,
                revision INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS canvas_history (
                id INTEGER PRIMARY KEY,
                revision INTEGER NOT NULL,
                at_ms INTEGER NOT NULL,
                x0 INTEGER NOT NULL, y0 INTEGER NOT NULL,
                x1 INTEGER NOT NULL, y1 INTEGER NOT NULL,
                color INTEGER NOT NULL,
                source TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS history_by_time ON canvas_history (at_ms);
            CREATE INDEX IF NOT EXISTS history_by_revision ON canvas_history (revision);
            CREATE TABLE IF NOT EXISTS canvas_authorship_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1)
            );
            CREATE TABLE IF NOT EXISTS canvas_history_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1), started_at_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS canvas_event (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                started_at_ms INTEGER NOT NULL, ends_at_ms INTEGER NOT NULL
            );
            INSERT OR IGNORE INTO canvas_event VALUES (1, 0, 0);
            CREATE TABLE IF NOT EXISTS miniapp_presence (
                telegram_id INTEGER NOT NULL, session_id TEXT NOT NULL,
                sequence INTEGER NOT NULL, active INTEGER NOT NULL,
                last_seen_ms INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, session_id)
            ) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS presence_by_time ON miniapp_presence (last_seen_ms);
            CREATE TABLE IF NOT EXISTS canvas_bans (
                telegram_id INTEGER PRIMARY KEY, banned_at_ms INTEGER NOT NULL,
                actor_id INTEGER, actor_kind TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS canvas_moderation (
                id INTEGER PRIMARY KEY, target_id INTEGER NOT NULL,
                action TEXT NOT NULL, at_ms INTEGER NOT NULL,
                actor_id INTEGER, actor_kind TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS canvas_rollbacks (
                id INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                target_id INTEGER NOT NULL, through_id INTEGER NOT NULL,
                at_ms INTEGER NOT NULL, actor_id INTEGER, actor_kind TEXT NOT NULL,
                changed_cells INTEGER NOT NULL, undone_events INTEGER NOT NULL,
                revision INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS canvas_rollback_edits (
                history_id INTEGER PRIMARY KEY, rollback_id INTEGER NOT NULL
            );
        ''')
        initialize_chat(db)
        # Additive migration keeps every pixel and every history record.
        for table in ('pixels', 'canvas_history'):
            columns = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
            additions = {
                'actor_id': 'INTEGER', 'actor_name': "TEXT NOT NULL DEFAULT ''",
                'actor_username': "TEXT NOT NULL DEFAULT ''",
                'actor_kind': "TEXT NOT NULL DEFAULT 'unknown'",
            }
            if table == 'pixels':
                additions['placed_at_ms'] = 'INTEGER'
            for name, definition in additions.items():
                if name not in columns:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        db.execute('CREATE INDEX IF NOT EXISTS history_by_actor ON canvas_history (actor_kind,actor_id,id)')
        # One-time baseline, in the same transaction as its marker. Old edits
        # cannot be recovered; the existing board remains the first frame.
        db.execute('BEGIN IMMEDIATE')
        player_columns = {row['name'] for row in db.execute('PRAGMA table_info(canvas_players)')}
        if 'cooldown_ms' not in player_columns:
            db.execute('ALTER TABLE canvas_players ADD COLUMN cooldown_ms INTEGER NOT NULL DEFAULT 1000')
        if db.execute('SELECT 1 FROM canvas_history_meta').fetchone() is None:
            now = time.time_ns() // 1_000_000
            db.execute('INSERT INTO canvas_history_meta VALUES (1, ?)', (now,))
            db.execute(
                'INSERT INTO canvas_history (revision, at_ms, x0, y0, x1, y1, color, source) '
                "SELECT revision, ?, x, y, x+1, y+1, color, 'baseline' FROM pixels ORDER BY revision",
                (now,),
            )
        if db.execute('SELECT 1 FROM canvas_authorship_meta').fetchone() is None:
            # Only the most recent accepted request per player existed before
            # this feature. Match its full revision and coordinates exactly.
            for row in db.execute('SELECT * FROM canvas_players').fetchall():
                db.execute(
                    "UPDATE pixels SET actor_id=?,actor_kind='telegram',placed_at_ms=? "
                    "WHERE x=? AND y=? AND color=? AND revision=? AND actor_kind='unknown'",
                    (row['telegram_id'],row['placed_at_ms'],row['x'],row['y'],row['color'],row['revision']),
                )
                db.execute(
                    "UPDATE canvas_history SET actor_id=?,actor_kind='telegram' "
                    "WHERE revision=? AND x0=? AND y0=? AND x1=? AND y1=? "
                    "AND color=? AND source IN ('player','baseline') AND actor_kind='unknown'",
                    (row['telegram_id'],row['revision'],row['x'],row['y'],row['x']+1,row['y']+1,row['color']),
                )
            db.execute('INSERT INTO canvas_authorship_meta VALUES (1)')
        db.commit()


def actor_fields(user):
    def text(name, limit):
        value = user.get(name, '')
        return ''.join(c for c in value if c.isprintable())[:limit] if isinstance(value,str) else ''
    return (user['id'],(text('first_name',80)+' '+text('last_name',80)).strip(),
            text('username',64),'telegram')


def event_status(db, now_ms):
    row = db.execute('SELECT started_at_ms, ends_at_ms FROM canvas_event WHERE id=1').fetchone()
    if not row['ends_at_ms']:
        return None
    return {**dict(row), 'server_time_ms': now_ms,
            'active': row['started_at_ms'] <= now_ms < row['ends_at_ms'],
            'finished': now_ms >= row['ends_at_ms']}


def online_count(db, now_ms):
    return db.execute('SELECT COUNT(DISTINCT telegram_id) FROM miniapp_presence '
                      'WHERE active=1 AND last_seen_ms>?', (now_ms-PRESENCE_TTL_MS,)).fetchone()[0]


def cooldown_for_online(count):
    return 250 if count < 5 else 500 if count < 9 else COOLDOWN_MS


def app_cooldown(db, now_ms, user_id, reported_count):
    # A custom client cannot accelerate by hiding its presence while drawing.
    # The visible badge retains the actual foreground presence count; pacing
    # also uses recent accepted placements for the same 35-second window.
    count = db.execute(
        'SELECT COUNT(*) FROM (SELECT telegram_id FROM miniapp_presence '
        'WHERE active=1 AND last_seen_ms>? UNION SELECT telegram_id FROM canvas_players '
        'WHERE placed_at_ms>? UNION SELECT ? WHERE ?>0)',
        (now_ms-PRESENCE_TTL_MS,now_ms-PRESENCE_TTL_MS,user_id,user_id),
    ).fetchone()[0]
    return cooldown_for_online(max(reported_count,count))


def rollback_plan(db, target, through):
    """Replay recorded edits, excluding previous rollbacks and the target batch.

    Materialized rollback frames stay in the timeline but never resurrect an
    edit that a prior rollback removed. Memory is bounded by the finite board.
    """
    condition = (
        "WHERE h.actor_id=? AND h.actor_kind='telegram' AND h.source IN ('player','admin') "
        'AND h.id<=? AND NOT EXISTS (SELECT 1 FROM canvas_rollback_edits r WHERE r.history_id=h.id)'
    )
    count = db.execute('SELECT COUNT(*) FROM canvas_history h '+condition,(target,through)).fetchone()[0]
    events = db.execute('SELECT h.x0,h.y0,h.x1,h.y1 FROM canvas_history h '+condition,(target,through))
    cells = {(x,y) for row in events
             for y in range(max(CANVAS_BOUNDS['y0'],row['y0']),min(CANVAS_BOUNDS['y1'],row['y1']))
             for x in range(max(CANVAS_BOUNDS['x0'],row['x0']),min(CANVAS_BOUNDS['x1'],row['x1']))}
    if not cells:
        return count, []
    x0,x1=min(x for x,y in cells),max(x for x,y in cells)+1
    y0,y1=min(y for x,y in cells),max(y for x,y in cells)+1
    before, after = {}, {}
    rows = db.execute(
        "SELECT h.id,h.x0,h.y0,h.x1,h.y1,h.color,h.actor_id,h.actor_kind,h.source FROM canvas_history h WHERE h.source!='rollback' "
        'AND h.x0<? AND h.x1>? AND h.y0<? AND h.y1>? '
        'AND NOT EXISTS (SELECT 1 FROM canvas_rollback_edits r WHERE r.history_id=h.id) ORDER BY h.id',
        (x1,x0,y1,y0),
    )
    for row in rows:
        removed = (row['actor_id']==target and row['actor_kind']=='telegram'
                   and row['source'] in ('player','admin') and row['id']<=through)
        for y in range(max(y0,row['y0']),min(y1,row['y1'])):
            for x in range(max(x0,row['x0']),min(x1,row['x1'])):
                cell = (x,y)
                if cell not in cells:
                    continue
                before[cell] = removed
                if not removed:
                    after[cell] = row['color']
    restored = [(x,y,after.get((x,y),-1)) for x,y in sorted(cells, key=lambda c:(c[1],c[0]))
                if before.get((x,y))]
    return count, restored


def create_app(database_path=None):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 16_384
    path = database_path or os.environ.get(
        'KEPIK_CANVAS_DATABASE', '/var/lib/kkepik-pixels/canvas.sqlite3',
    )
    initialize(path)
    app.config['CANVAS_DATABASE'] = path
    register_chat(app, connect, event_status)
    register_replay(app, connect, event_status)

    def failure(message, status=400, **extra):
        return jsonify(success=False, error=message, **extra), status

    @app.before_request
    def authenticate():
        user = authenticated_telegram_user(request)
        request.canvas_actor = actor_fields(user) if user else (None,'','', 'unknown')
        request.canvas_user_id = user['id'] if user else 0
        request.canvas_admin = bool(user and user['id'] == CANVAS_ADMIN_TELEGRAM_ID)
        if request.path.startswith('/admin/'):
            # Never accept privileged launch credentials in URLs or JSON bodies.
            request.canvas_admin = request.canvas_admin and bool(
                request.headers.get('X-Telegram-Init-Data'),
            )
            origin = request.headers.get('Origin')
            if origin and origin != request.host_url.rstrip('/'):
                return failure('Нет доступа к управлению полотном.', 403)
            if not request.canvas_admin:
                if not valid_admin_token(request.headers.get('X-Canvas-Admin-Token', '')):
                    return failure('Нет доступа к управлению полотном.', 403)
                # A website capability identifies the shared web-admin account,
                # never an arbitrary Telegram ID supplied alongside the token.
                request.canvas_actor = (None,'Веб-администратор','','web_admin')
            request.canvas_admin = True
            return None
        if user is None:
            return failure('Откройте приложение из Telegram.', 401)
        request.canvas_user_id = user['id']

    @app.after_request
    def response_headers(response):
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.errorhandler(sqlite3.OperationalError)
    def busy(_error):
        response, status = failure('Доска занята. Попробуйте через секунду.', 503)
        response.headers['Retry-After'] = '1'
        return response, status

    @app.errorhandler(HTTPException)
    def http_error(error):
        return failure('Не удалось выполнить запрос.', error.code)

    @app.get('/admin/state')
    @app.get('/state')
    def state():
        try:
            x0, y0, x1, y1 = (
                int(request.args[name]) for name in ('x0', 'y0', 'x1', 'y1')
            )
            since = int(request.args.get('since', '0'))
            chat_after = int(request.args.get('chat_after', '-1'))
        except (KeyError, ValueError):
            return failure('Неверная область доски.')
        if (
            not CANVAS_BOUNDS['x0'] <= x0 < x1 <= CANVAS_BOUNDS['x1']
            or not CANVAS_BOUNDS['y0'] <= y0 < y1 <= CANVAS_BOUNDS['y1']
            or (x1 - x0) * (y1 - y0) > MAX_VIEW_AREA
            or since < 0
            or not -1 <= chat_after <= 9_007_199_254_740_991
        ):
            return failure('Неверная область доски.')

        with closing(connect(app.config['CANVAS_DATABASE'])) as db:
            # Snapshot and cursor belong to the same read transaction. Changes
            # accepted while the response is built remain visible next time.
            db.execute('BEGIN')
            observed_at_ms = time.time_ns() // 1_000_000
            revision = db.execute(
                'SELECT revision FROM canvas_meta WHERE id = 1',
            ).fetchone()['revision']
            full = since == 0 or since > revision
            rows = db.execute(
                'SELECT x, y, color, revision FROM pixels '
                'WHERE x >= ? AND x < ? AND y >= ? AND y < ? AND revision > ?',
                (x0, x1, y0, y1, 0 if full else since),
            ).fetchall()
            player = db.execute(
                'SELECT placed_at_ms,cooldown_ms,revision FROM canvas_players WHERE telegram_id = ?',
                (request.canvas_user_id,),
            ).fetchone()
            event = event_status(db, observed_at_ms)
            count = online_count(db, observed_at_ms)
            cooldown = app_cooldown(db,observed_at_ms,request.canvas_user_id,count)
            banned = db.execute('SELECT 1 FROM canvas_bans WHERE telegram_id=?',
                                (request.canvas_user_id,)).fetchone() is not None
            access_revision = db.execute('SELECT COALESCE(MAX(id),0) FROM canvas_moderation').fetchone()[0]
            chat = chat_summary(db, request.canvas_user_id, chat_after, observed_at_ms)
            db.commit()
        now_ms = time.time_ns() // 1_000_000
        remaining = max(0, player['placed_at_ms'] + player['cooldown_ms'] - now_ms) if player else 0
        return jsonify(
            success=True, full=full, revision=revision, palette=PALETTE,
            admin=request.canvas_admin, bounds=CANVAS_BOUNDS,
            cooldown_ms=cooldown, cooldown_at_ms=observed_at_ms,
            player_revision=player['revision'] if player else 0, remaining_ms=remaining,
            event=event, online_count=count, online_at_ms=observed_at_ms,
            banned=banned, access_at_ms=observed_at_ms, access_revision=access_revision,
            chat=chat,
            pixels=[[row['x'], row['y'], row['color'], row['revision']] for row in rows],
        )

    @app.post('/presence')
    def presence():
        # Presence covers every authenticated mini-app page, not the canvas
        # editor alone. The Telegram identity is never taken from the payload.
        if not request.headers.get('X-Telegram-Init-Data'):
            return failure('Откройте приложение из Telegram.', 401)
        origin = request.headers.get('Origin')
        if origin and origin != request.host_url.rstrip('/'):
            return failure('Нет доступа.', 403)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return failure('Неверная сессия.')
        session, sequence, active = (data.get(k) for k in ('session_id', 'sequence', 'active'))
        if (not isinstance(session, str) or not 8 <= len(session) <= 64
            or not all(c.isascii() and (c.isalnum() or c in '-_') for c in session)
            or type(sequence) is not int or not 1 <= sequence <= 9_007_199_254_740_991
            or type(active) is not bool):
            return failure('Неверная сессия.')
        with closing(connect(app.config['CANVAS_DATABASE'])) as db:
            db.execute('BEGIN IMMEDIATE')
            now_ms = time.time_ns() // 1_000_000
            db.execute('DELETE FROM miniapp_presence WHERE last_seen_ms<=?', (now_ms-PRESENCE_RETENTION_MS,))
            db.execute('INSERT INTO miniapp_presence VALUES (?,?,?,?,?) '
                       'ON CONFLICT (telegram_id,session_id) DO UPDATE SET '
                       'sequence=excluded.sequence,active=excluded.active,last_seen_ms=excluded.last_seen_ms '
                       'WHERE excluded.sequence>miniapp_presence.sequence',
                       (request.canvas_user_id,session,sequence,int(active),now_ms))
            # An account with many forged session IDs still counts once and
            # cannot grow the table indefinitely. Ordinary tabs are preserved.
            db.execute('DELETE FROM miniapp_presence WHERE telegram_id=? AND session_id NOT IN '
                       '(SELECT session_id FROM miniapp_presence WHERE telegram_id=? '
                       'ORDER BY active DESC,last_seen_ms DESC,sequence DESC LIMIT 16)',
                       (request.canvas_user_id,request.canvas_user_id))
            count = online_count(db, now_ms)
            db.commit()
        return jsonify(success=True, online_count=count, online_at_ms=now_ms)

    @app.post('/admin/event/start')
    def start_event():
        if not isinstance(request.get_json(silent=True), dict):
            return failure('Неверный запрос.')
        with closing(connect(app.config['CANVAS_DATABASE'])) as db:
            db.execute('BEGIN IMMEDIATE')
            now_ms = time.time_ns() // 1_000_000
            db.execute('UPDATE canvas_event SET started_at_ms=?,ends_at_ms=? '
                       'WHERE id=1 AND ends_at_ms=0', (now_ms,now_ms+EVENT_DURATION_MS))
            event = event_status(db, now_ms)
            db.commit()
        return jsonify(success=True,event=event)

    @app.get('/admin/bans')
    def bans():
        with closing(connect(app.config['CANVAS_DATABASE'])) as db:
            rows = db.execute('SELECT telegram_id,banned_at_ms FROM canvas_bans '
                              'ORDER BY banned_at_ms DESC,telegram_id').fetchall()
        return jsonify(success=True,bans=[dict(row) for row in rows])

    @app.post('/admin/ban')
    def ban():
        data = request.get_json(silent=True)
        if not isinstance(data,dict):
            return failure('Неверный запрос.')
        target, blocked = data.get('telegram_id'), data.get('banned')
        if (type(target) is not int or not 1 <= target <= 9_007_199_254_740_991
            or type(blocked) is not bool or target == CANVAS_ADMIN_TELEGRAM_ID):
            return failure('Неверный пользователь.')
        with closing(connect(app.config['CANVAS_DATABASE'])) as db:
            db.execute('BEGIN IMMEDIATE')
            now_ms=time.time_ns()//1_000_000
            if blocked:
                changed=db.execute('INSERT OR IGNORE INTO canvas_bans VALUES (?,?,?,?)',
                                   (target,now_ms,request.canvas_actor[0],request.canvas_actor[3])).rowcount
            else:
                changed=db.execute('DELETE FROM canvas_bans WHERE telegram_id=?',(target,)).rowcount
            if changed:
                db.execute('INSERT INTO canvas_moderation (target_id,action,at_ms,actor_id,actor_kind) '
                           'VALUES (?,?,?,?,?)',(target,'ban' if blocked else 'unban',now_ms,
                                                request.canvas_actor[0],request.canvas_actor[3]))
            db.commit()
        return jsonify(success=True,telegram_id=target,banned=blocked)

    @app.get('/admin/rollback')
    def preview_rollback():
        try:
            target = int(request.args['telegram_id'])
        except (KeyError,ValueError):
            return failure('Неверный пользователь.')
        if not 1 <= target <= 9_007_199_254_740_991:
            return failure('Неверный пользователь.')
        with closing(connect(app.config['CANVAS_DATABASE'])) as db:
            db.execute('BEGIN')
            through = db.execute('SELECT COALESCE(MAX(id),0) FROM canvas_history').fetchone()[0]
            count, cells = rollback_plan(db,target,through)
            db.commit()
        return jsonify(success=True,telegram_id=target,through_id=through,
                       undone_events=count,changed_cells=len(cells))

    @app.post('/admin/rollback')
    def rollback():
        data=request.get_json(silent=True)
        if not isinstance(data,dict):
            return failure('Неверный запрос.')
        target,through,request_id=(data.get(k) for k in ('telegram_id','through_id','request_id'))
        if (type(target) is not int or not 1 <= target <= 9_007_199_254_740_991
            or type(through) is not int or through < 0
            or not isinstance(request_id,str) or not 8 <= len(request_id) <= 64
            or not all(c.isascii() and (c.isalnum() or c in '-_') for c in request_id)):
            return failure('Неверный откат.')
        with closing(connect(app.config['CANVAS_DATABASE'])) as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT * FROM canvas_rollbacks WHERE request_id=?',(request_id,)).fetchone()
            if previous:
                db.rollback()
                if (previous['target_id']!=target or previous['through_id']!=through
                    or previous['actor_id']!=request.canvas_actor[0] or previous['actor_kind']!=request.canvas_actor[3]):
                    return failure('Этот запрос уже выполнен.',409)
                return jsonify(success=True,revision=previous['revision'],
                               changed_cells=previous['changed_cells'],undone_events=previous['undone_events'])
            head=db.execute('SELECT COALESCE(MAX(id),0) FROM canvas_history').fetchone()[0]
            if through > head:
                db.rollback()
                return failure('Проверьте откат заново.',409)
            count,cells=rollback_plan(db,target,through)
            now=time.time_ns()//1_000_000
            if cells:
                db.execute('UPDATE canvas_meta SET revision=revision+1 WHERE id=1')
            revision=db.execute('SELECT revision FROM canvas_meta WHERE id=1').fetchone()[0]
            operation=db.execute('INSERT INTO canvas_rollbacks '
                                 '(request_id,target_id,through_id,at_ms,actor_id,actor_kind,changed_cells,undone_events,revision) '
                                 'VALUES (?,?,?,?,?,?,?,?,?)',
                                 (request_id,target,through,now,request.canvas_actor[0],request.canvas_actor[3],len(cells),count,revision)).lastrowid
            db.execute('INSERT INTO canvas_rollback_edits (history_id,rollback_id) '
                       "SELECT h.id,? FROM canvas_history h WHERE h.actor_id=? AND h.actor_kind='telegram' "
                       "AND h.source IN ('player','admin') AND h.id<=? "
                       'AND NOT EXISTS (SELECT 1 FROM canvas_rollback_edits r WHERE r.history_id=h.id)',
                       (operation,target,through))
            db.executemany('INSERT INTO pixels (x,y,color,revision,actor_id,actor_name,actor_username,actor_kind,placed_at_ms) '
                           'VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT (x,y) DO UPDATE SET '
                           'color=excluded.color,revision=excluded.revision,actor_id=excluded.actor_id,'
                           'actor_name=excluded.actor_name,actor_username=excluded.actor_username,'
                           'actor_kind=excluded.actor_kind,placed_at_ms=excluded.placed_at_ms',
                           ((x,y,color,revision,*request.canvas_actor,now) for x,y,color in cells))
            db.executemany('INSERT INTO canvas_history '
                           '(revision,at_ms,x0,y0,x1,y1,color,source,actor_id,actor_name,actor_username,actor_kind) '
                           "VALUES (?,?,?,?,?,?,?,'rollback',?,?,?,?)",
                           ((revision,now,x,y,x+1,y+1,color,*request.canvas_actor) for x,y,color in cells))
            if count:
                db.execute('INSERT INTO canvas_moderation (target_id,action,at_ms,actor_id,actor_kind) '
                           "VALUES (?,'rollback',?,?,?)",(target,now,request.canvas_actor[0],request.canvas_actor[3]))
            db.commit()
        return jsonify(success=True,revision=revision,changed_cells=len(cells),undone_events=count)

    @app.post('/place')
    def place():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return failure('Неверный пиксель.')
        x, y, color = (data.get(name) for name in ('x', 'y', 'color'))
        request_id = data.get('request_id')
        if (
            any(type(value) is not int for value in (x, y, color))
            or not CANVAS_BOUNDS['x0'] <= x < CANVAS_BOUNDS['x1']
            or not CANVAS_BOUNDS['y0'] <= y < CANVAS_BOUNDS['y1']
            or not 0 <= color < len(PALETTE)
            or not isinstance(request_id, str)
            or not 8 <= len(request_id) <= 64
            or not all(character.isascii() and (character.isalnum() or character in '-_')
                       for character in request_id)
        ):
            return failure('Неверный пиксель.')

        with closing(connect(app.config['CANVAS_DATABASE'])) as db:
            # Acquire the write lock before checking the user's timestamp: two
            # tabs, concurrent requests and separate workers share one limit.
            db.execute('BEGIN IMMEDIATE')
            now_ms = time.time_ns() // 1_000_000
            if db.execute('SELECT 1 FROM canvas_bans WHERE telegram_id=?',
                          (request.canvas_user_id,)).fetchone():
                access_revision = db.execute('SELECT COALESCE(MAX(id),0) FROM canvas_moderation').fetchone()[0]
                db.rollback()
                return failure('Рисование на полотне заблокировано.',403,banned=True,
                               access_at_ms=now_ms,access_revision=access_revision)
            player = db.execute(
                'SELECT * FROM canvas_players WHERE telegram_id = ?',
                (request.canvas_user_id,),
            ).fetchone()
            cooldown = app_cooldown(db,now_ms,request.canvas_user_id,online_count(db,now_ms))
            if player and player['request_id'] == request_id:
                db.rollback()
                if (player['x'], player['y'], player['color']) != (x, y, color):
                    return failure('Этот запрос уже выполнен.', 409)
                return jsonify(
                    success=True, pixel=[x, y, color, player['revision']],
                    remaining_ms=max(0, player['placed_at_ms'] + player['cooldown_ms'] - now_ms),
                    cooldown_ms=cooldown,cooldown_at_ms=now_ms,player_revision=player['revision'],
                )
            # Check the persistent deadline inside the same write transaction
            # as the cooldown and placement, after acquiring the write lock.
            event = event_status(db, now_ms)
            if event and not event['active']:
                db.rollback()
                return failure('Ивент завершён. Полотно доступно для просмотра.'
                               if event['finished'] else 'Ивент ещё не начался.', 423, event=event)
            remaining = player['placed_at_ms'] + player['cooldown_ms'] - now_ms if player else 0
            if remaining > 0:
                db.rollback()
                response, status = failure(
                    'Подождите следующий пиксель.', 429, remaining_ms=remaining,
                    cooldown_ms=cooldown,cooldown_at_ms=now_ms,player_revision=player['revision'],
                )
                response.headers['Retry-After'] = str(math.ceil(remaining / 1000))
                return response, status

            db.execute('UPDATE canvas_meta SET revision = revision + 1 WHERE id = 1')
            revision = db.execute(
                'SELECT revision FROM canvas_meta WHERE id = 1',
            ).fetchone()['revision']
            db.execute(
                'INSERT INTO pixels (x,y,color,revision,actor_id,actor_name,actor_username,actor_kind,placed_at_ms) '
                'VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT (x,y) DO UPDATE SET '
                'color=excluded.color,revision=excluded.revision,actor_id=excluded.actor_id,'
                'actor_name=excluded.actor_name,actor_username=excluded.actor_username,'
                'actor_kind=excluded.actor_kind,placed_at_ms=excluded.placed_at_ms',
                (x,y,color,revision,*request.canvas_actor,now_ms),
            )
            db.execute(
                'INSERT INTO canvas_players (telegram_id,placed_at_ms,request_id,x,y,color,revision,cooldown_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?) '
                'ON CONFLICT (telegram_id) DO UPDATE SET '
                'placed_at_ms=excluded.placed_at_ms, request_id=excluded.request_id, '
                'x=excluded.x, y=excluded.y, color=excluded.color, revision=excluded.revision,cooldown_ms=excluded.cooldown_ms',
                (request.canvas_user_id, now_ms, request_id, x, y, color, revision,cooldown),
            )
            db.execute(
                'INSERT INTO canvas_history (revision,at_ms,x0,y0,x1,y1,color,source,actor_id,actor_name,actor_username,actor_kind) '
                "VALUES (?,?,?,?,?,?,?,'player',?,?,?,?)",
                (revision,now_ms,x,y,x+1,y+1,color,*request.canvas_actor),
            )
            db.commit()
        return jsonify(success=True, pixel=[x, y, color, revision], remaining_ms=cooldown,
                       cooldown_ms=cooldown,cooldown_at_ms=now_ms,player_revision=revision)

    @app.post('/admin/fill')
    def admin_fill():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return failure('Неверная область.')
        x0, y0, x1, y1, color = (data.get(k) for k in ('x0', 'y0', 'x1', 'y1', 'color'))
        if (
            any(type(value) is not int for value in (x0, y0, x1, y1, color))
            or not CANVAS_BOUNDS['x0'] <= x0 < x1 <= CANVAS_BOUNDS['x1']
            or not CANVAS_BOUNDS['y0'] <= y0 < y1 <= CANVAS_BOUNDS['y1']
            or (x1-x0)*(y1-y0) > MAX_VIEW_AREA
            or not -1 <= color < len(PALETTE)
        ):
            return failure('Выберите не больше 65 536 клеток.')
        with closing(connect(app.config['CANVAS_DATABASE'])) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE canvas_meta SET revision=revision+1 WHERE id=1')
            revision = db.execute('SELECT revision FROM canvas_meta WHERE id=1').fetchone()[0]
            now_ms=time.time_ns()//1_000_000
            if color == -1:
                # Keep revision tombstones so incremental clients see erasure.
                db.execute('UPDATE pixels SET color=-1,revision=?,actor_id=?,actor_name=?,actor_username=?,'
                           'actor_kind=?,placed_at_ms=? WHERE x>=? AND x<? AND y>=? AND y<?',
                           (revision,*request.canvas_actor,now_ms,x0,x1,y0,y1))
            else:
                db.executemany(
                    'INSERT INTO pixels (x,y,color,revision,actor_id,actor_name,actor_username,actor_kind,placed_at_ms) '
                    'VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT (x,y) DO UPDATE SET '
                    'color=excluded.color,revision=excluded.revision,actor_id=excluded.actor_id,'
                    'actor_name=excluded.actor_name,actor_username=excluded.actor_username,'
                    'actor_kind=excluded.actor_kind,placed_at_ms=excluded.placed_at_ms',
                    ((x,y,color,revision,*request.canvas_actor,now_ms) for y in range(y0,y1) for x in range(x0,x1)),
                )
            db.execute(
                'INSERT INTO canvas_history (revision,at_ms,x0,y0,x1,y1,color,source,actor_id,actor_name,actor_username,actor_kind) '
                "VALUES (?,?,?,?,?,?,?,'admin',?,?,?,?)",
                (revision,now_ms,x0,y0,x1,y1,color,*request.canvas_actor),
            )
            db.commit()
        return jsonify(success=True, revision=revision, area=[x0,y0,x1,y1], color=color)

    @app.get('/admin/pixel')
    def admin_pixel():
        try:
            x,y=(int(request.args[k]) for k in ('x','y'))
        except (KeyError,ValueError):
            return failure('Неверная клетка.')
        if not CANVAS_BOUNDS['x0'] <= x < CANVAS_BOUNDS['x1'] or not CANVAS_BOUNDS['y0'] <= y < CANVAS_BOUNDS['y1']:
            return failure('Клетка вне полотна.')
        with closing(connect(app.config['CANVAS_DATABASE'])) as db:
            db.execute('BEGIN')
            pixel=db.execute('SELECT * FROM pixels WHERE x=? AND y=?',(x,y)).fetchone()
            edits=db.execute('SELECT * FROM canvas_history WHERE x0<=? AND x1>? AND y0<=? AND y1>? '
                             'ORDER BY id DESC LIMIT 20',(x,x,y,y)).fetchall()
            author_banned=bool(pixel and pixel['actor_id'] and db.execute(
                'SELECT 1 FROM canvas_bans WHERE telegram_id=?',(pixel['actor_id'],)).fetchone())
            db.commit()
        return jsonify(success=True,pixel=dict(pixel) if pixel else None,
                       edits=[dict(row) for row in edits],author_banned=author_banned)

    @app.get('/admin/history')
    def admin_history():
        try:
            x0,y0,x1,y1 = (int(request.args[k]) for k in ('x0','y0','x1','y1'))
            after = int(request.args.get('after',0))
            limit = int(request.args.get('limit',1000))
            until = int(request.args['until']) if 'until' in request.args else None
        except (KeyError,ValueError):
            return failure('Неверная область истории.')
        if (not CANVAS_BOUNDS['x0'] <= x0 < x1 <= CANVAS_BOUNDS['x1']
            or not CANVAS_BOUNDS['y0'] <= y0 < y1 <= CANVAS_BOUNDS['y1']
            or (x1-x0)*(y1-y0) > MAX_VIEW_AREA or after < 0 or not 1 <= limit <= 2000
            or (until is not None and until < 0)):
            return failure('Неверная область истории.')
        with closing(connect(app.config['CANVAS_DATABASE'])) as db:
            db.execute('BEGIN')
            head = db.execute('SELECT COALESCE(MAX(id),0) FROM canvas_history').fetchone()[0]
            if until is not None:
                head = min(head, until)
            started = db.execute('SELECT started_at_ms FROM canvas_history_meta').fetchone()[0]
            rows = db.execute('SELECT * FROM canvas_history WHERE id>? AND id<=? '
                              'AND x0<? AND x1>? AND y0<? AND y1>? ORDER BY id LIMIT ?',
                              (after,head,x1,x0,y1,y0,limit+1)).fetchall()
            db.commit()
        more = len(rows)>limit
        rows = rows[:limit]
        return jsonify(success=True, palette=PALETTE, started_at_ms=started,
                       cursor=rows[-1]['id'] if more else head, more=more, head=head,
                       events=[dict(row) for row in rows])

    return app
