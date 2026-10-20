"""Authenticated canvas chat; additive tables in the isolated canvas database."""
from __future__ import annotations
from contextlib import closing
import math
import time

from flask import jsonify, request
from pixel_admin_auth import CANVAS_ADMIN_TELEGRAM_ID
from pixel_chat_filter import clean_message, blocked_reason, safe_name

CHAT_COOLDOWN_MS = 5000
CHAT_WINDOW_MS = 60_000
CHAT_WINDOW_LIMIT = 6


def initialize_chat(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS canvas_chat_meta (
            id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL
        );
        INSERT OR IGNORE INTO canvas_chat_meta VALUES(1,0);
        CREATE TABLE IF NOT EXISTS canvas_chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER NOT NULL, actor_name TEXT NOT NULL,
            text TEXT NOT NULL, at_ms INTEGER NOT NULL,
            request_id TEXT NOT NULL, hidden INTEGER NOT NULL DEFAULT 0,
            UNIQUE(actor_id,request_id)
        );
        CREATE INDEX IF NOT EXISTS chat_visible ON canvas_chat_messages(hidden,id);
        CREATE INDEX IF NOT EXISTS chat_by_actor ON canvas_chat_messages(actor_id,at_ms);
        CREATE TABLE IF NOT EXISTS canvas_chat_senders (
            telegram_id INTEGER PRIMARY KEY, attempted_at_ms INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS canvas_chat_bans (
            telegram_id INTEGER PRIMARY KEY, banned_at_ms INTEGER NOT NULL,
            actor_id INTEGER, actor_kind TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS canvas_chat_moderation (
            id INTEGER PRIMARY KEY, target_id INTEGER, message_id INTEGER,
            action TEXT NOT NULL, at_ms INTEGER NOT NULL,
            actor_id INTEGER, actor_kind TEXT NOT NULL
        );
    ''')


def remaining_time(db, user_id, now):
    row=db.execute('SELECT attempted_at_ms FROM canvas_chat_senders WHERE telegram_id=?',(user_id,)).fetchone()
    remaining=max(0,row['attempted_at_ms']+CHAT_COOLDOWN_MS-now) if row else 0
    recent=db.execute('SELECT at_ms FROM canvas_chat_messages WHERE actor_id=? AND at_ms>? '
                      'ORDER BY at_ms DESC LIMIT ?', (user_id,now-CHAT_WINDOW_MS,CHAT_WINDOW_LIMIT)).fetchall()
    if len(recent)>=CHAT_WINDOW_LIMIT:
        remaining=max(remaining,recent[-1]['at_ms']+CHAT_WINDOW_MS-now)
    return remaining


def chat_summary(db,user_id,after=-1,now=None):
    now=now if now is not None else time.time_ns()//1_000_000
    head=db.execute('SELECT COALESCE(MAX(id),0) FROM canvas_chat_messages').fetchone()[0]
    revision=db.execute('SELECT revision FROM canvas_chat_meta WHERE id=1').fetchone()[0]
    unread=db.execute('SELECT COUNT(*) FROM (SELECT id FROM canvas_chat_messages WHERE hidden=0 AND id>? AND actor_id!=? LIMIT 100)',
                      (after,user_id)).fetchone()[0] if after>=0 else 0
    blocked=db.execute('SELECT 1 FROM canvas_chat_bans WHERE telegram_id=?',(user_id,)).fetchone() is not None
    return {'head':head,'revision':revision,'unread':min(100,unread),'banned':blocked,
            'remaining_ms':remaining_time(db,user_id,now),'observed_at_ms':now}


def message_json(row,viewer,admin):
    data={'id':row['id'],'name':row['actor_name'],'text':row['text'],'at_ms':row['at_ms'],
          'own':row['actor_id']==viewer,'admin':row['actor_id']==CANVAS_ADMIN_TELEGRAM_ID}
    if admin:data['actor_id']=row['actor_id']
    return data


def register_chat(app,connect,event_status):
    def failure(text,code=400,**extra):
        return jsonify(success=False,error=text,**extra),code

    @app.get('/chat/messages')
    @app.get('/admin/chat/messages')
    def messages():
        try:
            after=int(request.args.get('after','-1'))
        except ValueError:return failure('Неверный запрос.')
        if not -1<=after<=9_007_199_254_740_991:return failure('Неверный запрос.')
        with closing(connect(app.config['CANVAS_DATABASE'])) as db:
            db.execute('BEGIN')
            now=time.time_ns()//1_000_000
            summary=chat_summary(db,request.canvas_user_id,after,now)
            rows=db.execute('SELECT * FROM canvas_chat_messages WHERE hidden=0 ORDER BY id DESC LIMIT 50').fetchall()
            event=event_status(db,now)
            db.commit()
        admin=bool(request.canvas_admin and (request.path.startswith('/admin/') or request.headers.get('X-Telegram-Init-Data')))
        return jsonify(success=True,chat=summary,event=event,admin=admin,
                       messages=[message_json(row,request.canvas_user_id,admin) for row in reversed(rows)])

    @app.post('/chat/messages')
    def send_message():
        # Launch credentials for writes must come from the verified header,
        # never an identity claimed in JSON or a copied URL.
        if not request.headers.get('X-Telegram-Init-Data'):
            return failure('Откройте чат из Telegram.',401)
        origin=request.headers.get('Origin')
        if origin and origin!=request.host_url.rstrip('/'):
            return failure('Нет доступа к чату.',403)
        data=request.get_json(silent=True)
        if not isinstance(data,dict):return failure('Неверное сообщение.')
        text=clean_message(data.get('text'))
        rid=data.get('request_id')
        if text is None:return failure('Введите сообщение до 200 символов.')
        if not isinstance(rid,str) or not 8<=len(rid)<=64 or not all(c.isascii() and (c.isalnum() or c in '-_') for c in rid):
            return failure('Неверный запрос.')
        reason=blocked_reason(text)
        with closing(connect(app.config['CANVAS_DATABASE'])) as db:
            db.execute('BEGIN IMMEDIATE')
            now=time.time_ns()//1_000_000
            summary=chat_summary(db,request.canvas_user_id,now=now)
            if summary['banned']:
                return failure('Возможность писать в чат заблокирована.',403,chat=summary)
            previous=db.execute('SELECT * FROM canvas_chat_messages WHERE actor_id=? AND request_id=?',
                                (request.canvas_user_id,rid)).fetchone()
            if previous:
                if previous['text']!=text:return failure('Этот запрос уже выполнен.',409,chat=summary)
                return jsonify(success=True,message=None if previous['hidden'] else message_json(previous,request.canvas_user_id,request.canvas_admin),chat=summary)
            event=event_status(db,now)
            if event and not event['active']:
                return failure('Ивент завершён. Чат доступен для чтения.',423,event=event,chat=summary)
            remaining=summary['remaining_ms']
            if remaining:
                response,code=failure('Подождите перед следующим сообщением.',429,chat=summary)
                response.headers['Retry-After']=str(math.ceil(remaining/1000))
                return response,code
            db.execute('INSERT INTO canvas_chat_senders VALUES(?,?) ON CONFLICT(telegram_id) '
                       'DO UPDATE SET attempted_at_ms=excluded.attempted_at_ms',(request.canvas_user_id,now))
            if reason:
                db.commit()
                summary['remaining_ms']=CHAT_COOLDOWN_MS
                return failure('Сообщение не отправлено: ссылки в чате отключены.' if reason=='links' else
                               'Сообщение не отправлено: уберите мат, оскорбления или запрещённую символику.',
                               400,chat=summary)
            cursor=db.execute('INSERT INTO canvas_chat_messages(actor_id,actor_name,text,at_ms,request_id) VALUES(?,?,?,?,?)',
                              (request.canvas_user_id,safe_name(request.canvas_actor[1]),text,now,rid))
            db.execute('UPDATE canvas_chat_meta SET revision=revision+1 WHERE id=1')
            row=db.execute('SELECT * FROM canvas_chat_messages WHERE id=?',(cursor.lastrowid,)).fetchone()
            summary=chat_summary(db,request.canvas_user_id,now=now)
            db.commit()
        return jsonify(success=True,message=message_json(row,request.canvas_user_id,request.canvas_admin),chat=summary)

    @app.post('/admin/chat/hide')
    def hide_message():
        data=request.get_json(silent=True)
        target=data.get('message_id') if isinstance(data,dict) else None
        if type(target) is not int or not 1<=target<=9_007_199_254_740_991:return failure('Неверное сообщение.')
        with closing(connect(app.config['CANVAS_DATABASE'])) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM canvas_chat_messages WHERE id=?',(target,)).fetchone()
            if row is None:return failure('Сообщение не найдено.',404)
            if not row['hidden']:
                db.execute('UPDATE canvas_chat_messages SET hidden=1 WHERE id=?',(target,))
                db.execute('UPDATE canvas_chat_meta SET revision=revision+1 WHERE id=1')
                db.execute('INSERT INTO canvas_chat_moderation(target_id,message_id,action,at_ms,actor_id,actor_kind) VALUES(?,?,\'hide\',?,?,?)',
                           (row['actor_id'],target,time.time_ns()//1_000_000,request.canvas_actor[0],request.canvas_actor[3]))
            db.commit()
        return jsonify(success=True,message_id=target)

    @app.get('/admin/chat/bans')
    def chat_bans():
        with closing(connect(app.config['CANVAS_DATABASE'])) as db:
            rows=db.execute('SELECT telegram_id,banned_at_ms FROM canvas_chat_bans ORDER BY banned_at_ms DESC').fetchall()
        return jsonify(success=True,bans=[dict(row) for row in rows])

    @app.post('/admin/chat/ban')
    def ban_user():
        data=request.get_json(silent=True)
        target=data.get('telegram_id') if isinstance(data,dict) else None
        banned=data.get('banned') if isinstance(data,dict) else None
        if type(target) is not int or not 1<=target<=9_007_199_254_740_991 or type(banned) is not bool or target==CANVAS_ADMIN_TELEGRAM_ID:
            return failure('Неверный пользователь.')
        with closing(connect(app.config['CANVAS_DATABASE'])) as db:
            db.execute('BEGIN IMMEDIATE')
            now=time.time_ns()//1_000_000
            changed=(db.execute('INSERT OR IGNORE INTO canvas_chat_bans VALUES(?,?,?,?)',
                                (target,now,request.canvas_actor[0],request.canvas_actor[3])).rowcount if banned else
                     db.execute('DELETE FROM canvas_chat_bans WHERE telegram_id=?',(target,)).rowcount)
            if changed:
                db.execute('UPDATE canvas_chat_meta SET revision=revision+1 WHERE id=1')
                db.execute('INSERT INTO canvas_chat_moderation(target_id,action,at_ms,actor_id,actor_kind) VALUES(?,?,?,?,?)',
                           (target,'ban' if banned else 'unban',now,request.canvas_actor[0],request.canvas_actor[3]))
            db.commit()
        return jsonify(success=True,telegram_id=target,banned=banned)
