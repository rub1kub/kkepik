"""Authenticated Mini App access to the same duty queue used by /duty."""
from functools import lru_cache
from pathlib import Path
from typing import Literal
import json
import re
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from typing import Annotated

import config
from duties.store import Store, DutyError, settings
from duties.directory import Directory
from duties.views import day_payload, forecast
from message_settings_api import authenticated_id

router = APIRouter(prefix='/duties', tags=['private duties'])


@lru_cache(maxsize=1)
def get_store():
    return Store(Path(config.DATA_DIR) / ('duties-test.db' if config.TEST_MODE else 'duties.db'))


@lru_cache(maxsize=4)
def people_directory(store):
    return Directory(store, config.get_db_path(), config.get_bot_token)


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Options(Body):
    time: str = '08:00'
    cutoff: str = '08:30'
    report: str = '16:00'
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5], min_length=1, max_length=7)
    owner_daily: StrictBool = True
    audience: Literal['assigned', 'off'] = 'assigned'
    requests: StrictBool = True
    owner_events: StrictBool = True
    mode: Literal['reserve', 'pair'] = 'reserve'
    place: str = Field(default='', max_length=120)
    tasks: str = Field(default='', max_length=500)
    wait_minutes: int = Field(default=10, ge=2, le=30)


ChoiceRef = Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{32}$')]


class PeopleRequest(Body):
    space: int | None = Field(default=None, gt=0)


class Roster(Body):
    pairs: list[tuple[ChoiceRef, ChoiceRef]] = Field(default_factory=list, max_length=30)
    reserves: list[ChoiceRef] = Field(default_factory=list, max_length=60)
    options: Options = Field(default_factory=Options)
    preview_token: str | None = Field(default=None, pattern=r'^\d{10}\.[0-9a-f]{64}$')


class Action(Body):
    action: Literal['swap', 'rename', 'exempt', 'active', 'mute', 'leave', 'pause', 'resume', 'archive',
                    'invite', 'manager', 'transfer', 'approve', 'reject', 'decline', 'reconsider',
                    'offer_yes', 'offer_no', 'done', 'finish', 'correct', 'cancel_day', 'manual_pair', 'manual_reserve']
    member: int | None = Field(default=None, gt=0)
    other: int | None = Field(default=None, gt=0)
    day: int | None = Field(default=None, gt=0)
    offer: int | None = Field(default=None, gt=0)
    claim: int | None = Field(default=None, gt=0)
    value: str | bool | None = None
    outcome: Literal['done', 'missed', 'excused'] = 'done'


def failure(exc):
    raise HTTPException(400, str(exc)) from None


def authorized(store, sid, uid, *, manage=False):
    try:
        with store.connect() as db:
            return dict(store.permission(db, sid, uid, manage=manage))
    except DutyError:
        raise HTTPException(403, 'У вас нет доступа к этим дежурствам') from None


def group_for(uid):
    with sqlite3.connect(f'file:{config.get_db_path()}?mode=ro', uri=True) as db:
        row = db.execute('SELECT role,name_or_group,class_group FROM users WHERE user_id=?', (uid,)).fetchone()
    if not row:
        raise HTTPException(404, 'Сначала выберите свою группу в боте через /start')
    group = row[1] if row[0] == config.ROLE_STUDENT else row[2]
    if not group:
        raise HTTPException(400, 'Сначала выберите свою группу в боте')
    return group


def web_name(value):
    return re.sub(r'\s*\(ID \d+\)$|^ID \d+$', '', value).strip() or 'Без имени'


def web_day(data):
    for person in data.get('people', []):
        person['name'] = web_name(person['name'])
    for offer in data.get('offers', []):
        offer['host'], offer['guest'] = web_name(offer['host']), web_name(offer['guest'])
    return data


def roster_plan(body, uid, store, *, sid=None):
    return people_directory(store).resolve(uid, body.pairs, body.reserves, sid)


@router.get('')
def lists(uid: int = Depends(authenticated_id), store: Store = Depends(get_store)):
    spaces = store.lists(uid)
    return dict(group=group_for(uid), spaces=[dict(id=s['id'], group=s['group_name'], role=s['role'], status=s['status']) for s in spaces])


@router.post('/people')
async def people(body: PeopleRequest, uid: int = Depends(authenticated_id), store: Store = Depends(get_store)):
    return await people_directory(store).choices(uid, body.space)


@router.post('/preview')
def preview(body: Roster, uid: int = Depends(authenticated_id), store: Store = Depends(get_store)):
    try:
        group, pairs, reserves = roster_plan(body, uid, store)
        options = settings(body.options.model_dump())
        return dict(group=group, pairs=[[dict(name=p['name']) for p in pair] for pair in pairs],
                    reserves=[dict(name=p['name']) for p in reserves],
                    preview_token=people_directory(store).signature(uid, group, pairs, reserves, options))
    except DutyError as exc:
        failure(exc)


@router.post('')
def create(body: Roster, uid: int = Depends(authenticated_id), store: Store = Depends(get_store)):
    try:
        group, pairs, reserves = roster_plan(body, uid, store)
        options = settings(body.options.model_dump())
        people_directory(store).verify_preview(body.preview_token, uid, group, pairs, reserves, options)
        sid = store.create(uid, group, pairs, reserves, options, auto_link=True)
        return dict(id=sid)
    except DutyError as exc:
        failure(exc)


@router.get('/{sid}')
def detail(sid: int, uid: int = Depends(authenticated_id), store: Store = Depends(get_store)):
    access = authorized(store, sid, uid)
    data = store.view(sid, uid)
    admin = access['role'] != 'member'
    members = []
    with store.connect() as db:
        roles = {r['uid']: r['role'] for r in db.execute('SELECT uid,role FROM access WHERE space=?', (sid,))}
        # Include inactive entries for managers so they can restore them.
        if admin:
            inactive = [dict(r) for r in db.execute('SELECT m.*,NULL rank, '
                "(SELECT count(*) FROM assignments a WHERE a.member=m.id AND a.state='done') completed "
                'FROM members m WHERE m.space=? AND m.active=0', (sid,))]
        else:
            inactive = []
        old_days = [r['id'] for r in db.execute('SELECT id FROM days WHERE space=? ORDER BY date DESC LIMIT 31', (sid,))]
        old_days = list(dict.fromkeys(old_days + [d['id'] for d in data['days']]))
    for m in data['members'] + inactive:
        value = dict(id=m['id'], name=web_name(m['name']), pair=m['pair'], rank=m['rank'], mine=m['uid'] == uid,
                     linked=bool(m['uid']), completed=m['completed'], exempt_until=m['exempt_until'], active=bool(m['active']))
        if admin:
            value.update(role=roles.get(m['uid']))
        members.append(value)
    return dict(id=sid, group=data['space']['group_name'], status=data['space']['status'],
                role=access['role'], muted=bool(access['muted']), first_date=data['space']['first_date'],
                options=settings(json.loads(data['space']['settings'])), members=members,
                days=[web_day(day_payload(store, did, uid)) for did in old_days],
                forecast=[web_day(d) for d in forecast(store, sid, uid, Path(config.DATA_DIR) / 'knowledge.db')],
                claims=[dict(id=c['id'], name=c['profile_name'] or c['name'], member=c['member']) for c in data['claims']],
                events=[dict(action=e['action'], created=e['created']) for e in store.history(sid, uid)] if admin else [])


@router.put('/{sid}/settings')
def save_settings(sid: int, body: Options, uid: int = Depends(authenticated_id), store: Store = Depends(get_store)):
    authorized(store, sid, uid, manage=True)
    try:
        store.update_settings(sid, uid, body.model_dump())
        return dict(ok=True)
    except DutyError as exc:
        failure(exc)


@router.post('/{sid}/roster')
def append(sid: int, body: Roster, uid: int = Depends(authenticated_id), store: Store = Depends(get_store)):
    authorized(store, sid, uid, manage=True)
    try:
        _, pairs, reserves = roster_plan(body, uid, store, sid=sid)
        store.append_roster(sid, uid, pairs, reserves, auto_link=True)
        return dict(ok=True)
    except DutyError as exc:
        failure(exc)


@router.post('/{sid}/actions')
def action(sid: int, body: Action, uid: int = Depends(authenticated_id), store: Store = Depends(get_store)):
    authorized(store, sid, uid)
    a = body.action
    try:
        if a == 'active' and type(body.value) is not bool:
            raise DutyError('Укажите, участвует ли человек в дежурствах')
        if a == 'rename' and not isinstance(body.value, str):
            raise DutyError('Укажите имя участника')
        if a == 'exempt' and body.value is not None and not isinstance(body.value, str):
            raise DutyError('Укажите дату освобождения')
        # Verify every reference belongs to this list before any mutation.
        with store.connect() as db:
            for table, key in (('members', body.member), ('members', body.other), ('days', body.day), ('claims', body.claim)):
                if key is not None and not db.execute(f'SELECT 1 FROM {table} WHERE id=? AND space=?', (key, sid)).fetchone():
                    raise DutyError('Запись не найдена в этом списке')
            if body.offer is not None and not db.execute('SELECT 1 FROM offers o JOIN days d ON d.id=o.day WHERE o.id=? AND d.space=?', (body.offer, sid)).fetchone():
                raise DutyError('Предложение не найдено в этом списке')
        if a == 'swap':
            store.swap_partners(body.member, body.other, uid)
        elif a in ('rename', 'exempt', 'active'):
            store.edit_member(body.member, uid, {'rename': 'name', 'exempt': 'exempt_until', 'active': 'active'}[a], body.value)
        elif a in ('mute', 'leave', 'pause', 'resume', 'archive'):
            store.manage(sid, uid, a)
        elif a == 'invite':
            return dict(url='https://t.me/kkepik_bot?start=duty_' + store.invite(sid, uid, body.member))
        elif a in ('manager', 'transfer'):
            with store.connect() as db:
                target = store._member(db, body.member)
            store.manage(sid, uid, a, target['uid'])
        elif a in ('approve', 'reject'):
            store.resolve_claim(body.claim, uid, body.member if a == 'approve' else None)
        elif a == 'decline':
            store.decline(body.day, uid)
        elif a == 'reconsider':
            store.reconsider(body.day, uid)
        elif a in ('offer_yes', 'offer_no'):
            store.reply_offer(body.offer, uid, a == 'offer_yes')
        elif a == 'done':
            store.report_done(body.day, uid)
        elif a == 'finish':
            store.finish(body.day, uid, body.member, body.outcome)
        elif a == 'correct':
            store.correct_result(body.day, body.member, uid, body.outcome)
        elif a == 'cancel_day':
            store.cancel_day(body.day, uid)
        elif a == 'manual_pair':
            with store.connect() as db:
                target = store._member(db, body.member)
            store.manual_pair(body.day, uid, target['pair'])
        elif a == 'manual_reserve':
            store.manual_reserve(body.day, uid, body.member, body.other)
        return dict(ok=True)
    except (DutyError, TypeError, AttributeError) as exc:
        failure(exc if isinstance(exc, DutyError) else DutyError('Выберите участника или дежурство для этого действия'))
