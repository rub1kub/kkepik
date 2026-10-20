"""Public, server-rendered college knowledge pages and bounded read-only proxy.

No Telegram session, bot database, or private profile is consulted here. All
requests go to a fixed loopback service and only documented public fields leave
this boundary. Relative dates are resolved in the college's timezone.
"""
from datetime import date, datetime, timedelta
import base64
import binascii
import json
import hashlib
import os
import re
import threading
import time
from urllib.parse import quote, urlencode
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, current_app, jsonify, render_template, request, send_file
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests

from config import API_URL, STATIC_VERSION
from public_knowledge_contract import build_openapi


knowledge_public_bp = Blueprint('knowledge_public', __name__)
PUBLIC_ORIGIN = 'https://kkepik.rub1kub.ru'
COLLEGE_TIMEZONE = ZoneInfo('Europe/Moscow')
UPSTREAM_TIMEOUT = (3.05, 20)
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_SOURCE_BYTES = 25 * 1024 * 1024
ENTITY_PATHS = {'group': 'groups', 'teacher': 'teachers', 'audience': 'audiences'}
ENTITY_LABELS = {'group': 'Группа', 'teacher': 'Преподаватель', 'audience': 'Аудитория'}
COLLEGE_FALLBACK = {'id': 'kkep', 'name': 'Краснодарский колледж электронного приборостроения',
                    'city': 'Краснодар', 'aliases': ['ККЭП']}
DAY_STATUSES = frozenset(('available', 'no_lessons', 'missing', 'invalid_source', 'entity_not_found'))
STATUS_MESSAGES = {
    'missing': 'На эту дату нет опубликованного расписания в нашем архиве. Это не означает, что пар не было.',
    'invalid_source': 'Расписание получено, но источник пока не удалось корректно обработать.',
    'entity_not_found': 'Эта группа, преподаватель или аудитория не найдены в источнике на указанную дату.',
    'no_lessons': 'В опубликованном расписании для этого дня занятия не указаны.',
}
_catalog_cache = {'payload': None, 'expires': 0.0}
_catalog_lock = threading.Lock()
limiter = Limiter(key_func=get_remote_address, default_limits=[],
                  storage_uri=os.getenv('KNOWLEDGE_RATE_LIMIT_STORAGE_URI', 'memory://'),
                  headers_enabled=True, retry_after='delta-seconds')


class PublicKnowledgeError(Exception):
    def __init__(self, status, message=None):
        self.status = status
        self.message = message or {
            400: 'Некорректные параметры запроса', 404: 'Данные не найдены',
            429: 'Слишком много запросов. Повторите позже',
            502: 'Некорректный ответ сервиса расписания',
            503: 'Сервис расписания временно недоступен',
            504: 'Сервис расписания не ответил вовремя',
        }.get(status, 'Сервис временно недоступен')


@knowledge_public_bp.record_once
def _initialize(state):
    # App callbacks run in reverse registration order. Run this after the
    # limiter has added its headers, not in the blueprint's earlier callback.
    state.app.after_request(_successful_headers)
    limiter.init_app(state.app)


def _successful_headers(response):
    if request.blueprint == knowledge_public_bp.name and 200 <= response.status_code < 400:
        # flask-limiter also emits Retry-After on 200 responses. Successful
        # reads should not advertise a retry delay; preserve the real 429.
        response.headers.pop('Retry-After', None)
    return response


@knowledge_public_bp.after_request
def _headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers.setdefault('Cache-Control', 'public, max-age=30, must-revalidate')
    if request.path.startswith('/api/v2/'):
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, HEAD, OPTIONS'
    if response.status_code >= 400:
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Robots-Tag'] = 'noindex'
    return response


@knowledge_public_bp.errorhandler(PublicKnowledgeError)
def _handle_error(exc):
    if request.path.startswith('/api/v2/'):
        codes = {400: 'invalid_request', 404: 'not_found', 429: 'rate_limited',
                 502: 'invalid_upstream_response', 503: 'service_unavailable', 504: 'upstream_timeout'}
        response = jsonify(error=exc.message, code=codes.get(exc.status, 'service_unavailable'))
    else:
        response = Response(render_template('public_schedule_error.html',
                            **_context(), message=exc.message, status=exc.status), mimetype='text/html')
    response.status_code = exc.status
    if exc.status == 429:
        response.headers['Retry-After'] = '1'
    return response


@knowledge_public_bp.errorhandler(429)
def _rate_error(exc):
    return _handle_error(PublicKnowledgeError(429))


@knowledge_public_bp.errorhandler(Exception)
def _internal_error(exc):
    # Exceptions may contain loopback file paths or request content. Log only
    # their type and never echo their text to a public caller.
    current_app.logger.error('public_knowledge internal error type=%s', type(exc).__name__)
    return _handle_error(PublicKnowledgeError(503))


def _today():
    return datetime.now(COLLEGE_TIMEZONE).date()


def _iso(value, relative=False):
    if relative and value in ('today', 'tomorrow', 'yesterday'):
        return (_today() + timedelta(days={'today': 0, 'tomorrow': 1, 'yesterday': -1}[value])).isoformat()
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise PublicKnowledgeError(400, 'Укажите дату ГГГГ-ММ-ДД')
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise PublicKnowledgeError(400, 'Указана несуществующая дата') from None


def _entity(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 200
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise PublicKnowledgeError(400, 'Укажите название длиной от 1 до 200 символов')
    return value.strip()


def _params(allowed):
    if set(request.args) - set(allowed) or any(len(request.args.getlist(key)) != 1 for key in request.args):
        raise PublicKnowledgeError(400, 'Неизвестные или повторяющиеся параметры запроса')
    return request.args.to_dict()


def _range(params, max_days=31, required=False):
    if 'date' in params:
        if 'start' in params or 'end' in params:
            raise PublicKnowledgeError(400, 'Выберите одну дату или диапазон')
        params['date'] = _iso(params['date'], relative=True)
    elif 'start' in params or 'end' in params:
        if not params.get('start') or not params.get('end'):
            raise PublicKnowledgeError(400, 'Для диапазона нужны start и end')
        params['start'], params['end'] = _iso(params['start']), _iso(params['end'])
        count = (date.fromisoformat(params['end']) - date.fromisoformat(params['start'])).days + 1
        if not 1 <= count <= max_days:
            raise PublicKnowledgeError(400, f'Диапазон должен содержать от 1 до {max_days} дней')
    elif required:
        params['date'] = _today().isoformat()
    return params


def _with_entity(params, optional=False):
    if optional and 'entity_type' not in params and 'entity' not in params:
        return params
    if params.get('entity_type') not in ENTITY_PATHS:
        raise PublicKnowledgeError(400, 'entity_type: group, teacher или audience')
    params['entity'] = _entity(params.get('entity'))
    return params


def _open_upstream(path, params=None):
    # path is selected by code, never taken from a user-controlled URL.
    try:
        response = requests.get(f'{API_URL}/knowledge/{path}', params=params or {},
                                timeout=UPSTREAM_TIMEOUT, allow_redirects=False, stream=True)
    except requests.Timeout:
        raise PublicKnowledgeError(504) from None
    except requests.RequestException:
        raise PublicKnowledgeError(503) from None
    if response.status_code != 200:
        status = response.status_code
        response.close()
        raise PublicKnowledgeError(400 if status == 422 else 404 if status == 404 else 503)
    return response


def _read_bounded(response, limit):
    data = bytearray()
    try:
        size = response.headers.get('Content-Length', '')
        if size.isdigit() and int(size) > limit:
            raise PublicKnowledgeError(502)
        for chunk in response.iter_content(chunk_size=65536):
            data.extend(chunk)
            if len(data) > limit:
                raise PublicKnowledgeError(502)
        return bytes(data)
    except requests.Timeout:
        raise PublicKnowledgeError(504) from None
    except requests.RequestException:
        raise PublicKnowledgeError(503) from None
    finally:
        response.close()


def _fetch(path, params=None):
    response = _open_upstream(path, params)
    if response.headers.get('Content-Type', '').split(';', 1)[0].lower() != 'application/json':
        response.close()
        raise PublicKnowledgeError(502)
    try:
        payload = json.loads(_read_bounded(response, MAX_JSON_BYTES))
    except (UnicodeError, ValueError):
        raise PublicKnowledgeError(502) from None
    if not isinstance(payload, dict):
        raise PublicKnowledgeError(502)
    return _public_payload(payload, path)


def _text(value, maximum=1000, nullable=False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise PublicKnowledgeError(502)
    return value


def _strings(value, maximum=10000):
    if not isinstance(value, list) or len(value) > maximum:
        raise PublicKnowledgeError(502)
    return [_text(item, 200) for item in value]


def _college(value):
    if not isinstance(value, dict):
        raise PublicKnowledgeError(502)
    if value.get('id') != 'kkep':
        raise PublicKnowledgeError(502)
    return {key: _text(value.get(key), 200) for key in ('id', 'name', 'city')} | {
        'aliases': _strings(value.get('aliases', []), 50)}


def _source(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise PublicKnowledgeError(502)
    source_id = _text(value.get('id'), 64)
    if not re.fullmatch(r'[0-9a-f]{64}', source_id):
        raise PublicKnowledgeError(502)
    revision = value.get('revision')
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise PublicKnowledgeError(502)
    result = {'id': source_id, 'revision': revision,
            'received_at': _text(value.get('received_at'), 100, nullable=True),
            'published_at': _text(value.get('published_at'), 100, nullable=True),
            'url': f'{PUBLIC_ORIGIN}/api/v2/sources/{source_id}'}
    for key in ('parsed_at', 'academic_year'):
        if key in value:
            result[key] = _text(value[key], 100, nullable=True)
    if 'archived_import' in value:
        if not isinstance(value['archived_import'], bool):
            raise PublicKnowledgeError(502)
        result['archived_import'] = value['archived_import']
    for key, allowed in {'received_at_semantics': ('local_file_mtime', 'ingested_at'),
                         'revision_order': ('archive_import_order', 'observation_order')}.items():
        if key in value:
            if value[key] not in allowed:
                raise PublicKnowledgeError(502)
            result[key] = value[key]
    return result


def _lessons(value):
    if not isinstance(value, list) or len(value) > 1000:
        raise PublicKnowledgeError(502)
    lessons = []
    for lesson in value:
        if not isinstance(lesson, dict):
            raise PublicKnowledgeError(502)
        pair = lesson.get('pair_number')
        if not isinstance(pair, int) or isinstance(pair, bool) or not 0 <= pair <= 20:
            raise PublicKnowledgeError(502)
        result = {'pair_number': pair, 'subject': _text(lesson.get('subject'), 2000)}
        for key in ('group', 'teacher', 'audience', 'start_time', 'end_time', 'association_status'):
            if key in lesson:
                result[key] = _text(lesson[key], 500, nullable=True)
        if 'audiences' in lesson:
            result['audiences'] = _strings(lesson['audiences'], 100)
        if 'lesson_status' in lesson:
            if lesson['lesson_status'] != 'scheduled':
                raise PublicKnowledgeError(502)
            result['lesson_status'] = 'scheduled'
        subgroup = lesson.get('subgroup')
        if subgroup is not None and (not isinstance(subgroup, (str, int)) or isinstance(subgroup, bool)):
            raise PublicKnowledgeError(502)
        result['subgroup'] = subgroup
        lessons.append(result)
    return lessons


def _day(value):
    if not isinstance(value, dict) or value.get('status') not in DAY_STATUSES:
        raise PublicKnowledgeError(502)
    try:
        iso_date = _iso(value.get('date'))
    except PublicKnowledgeError:
        raise PublicKnowledgeError(502) from None
    result = {'date': iso_date, 'status': value['status']}
    if 'lessons' in value:
        result['lessons'] = _lessons(value['lessons'])
    if 'source' in value:
        result['source'] = _source(value['source'])
    for key in ('warning', 'note'):
        if key in value:
            result[key] = _text(value[key], 2000)
    return result


def _public_payload(payload, kind):
    """Explicit field boundary: no upstream paths, auth fields, or student data."""
    if kind == 'resolve':
        if payload.get('entity_type') not in ENTITY_PATHS or not isinstance(payload.get('ambiguous'), bool):
            raise PublicKnowledgeError(502)
        result = {'entity_type': payload['entity_type'], 'query': _text(payload.get('query'), 200),
                  'matches': _strings(payload.get('matches')), 'ambiguous': payload['ambiguous']}
        if 'truncated' in payload:
            if not isinstance(payload['truncated'], bool):
                raise PublicKnowledgeError(502)
            result['truncated'] = payload['truncated']
        return result
    if kind == 'changes':
        revisions, changes = payload.get('revisions'), payload.get('changes')
        if not isinstance(revisions, list) or not isinstance(changes, list) or len(revisions) > 1000:
            raise PublicKnowledgeError(502)
        clean_revisions = []
        for entry in revisions:
            if not isinstance(entry, dict) or not isinstance(entry.get('revision'), int):
                raise PublicKnowledgeError(502)
            clean_entry = {'revision': entry['revision'], 'source': _source(entry.get('source')),
                           'lessons': _lessons(entry.get('lessons', []))}
            if 'status' in entry:
                if entry['status'] not in ('parsed', 'failed'):
                    raise PublicKnowledgeError(502)
                clean_entry['status'] = entry['status']
            clean_revisions.append(clean_entry)
        clean_changes = []
        for entry in changes:
            if not isinstance(entry, dict):
                raise PublicKnowledgeError(502)
            clean_changes.append({'from_revision': entry.get('from_revision'), 'to_revision': entry.get('to_revision'),
                                  'added': _lessons(entry.get('added', [])), 'removed': _lessons(entry.get('removed', []))})
        result = {'date': _text(payload.get('date'), 10), 'entity_type': _text(payload.get('entity_type'), 20),
                'entity': _text(payload.get('entity'), 200), 'revisions': clean_revisions, 'changes': clean_changes,
                'history_complete': payload.get('history_complete') is True,
                'note': _text(payload.get('note', ''), 2000)}
        if 'college' in payload:
            result['college'] = _college(payload['college'])
        if 'timezone' in payload:
            result['timezone'] = _text(payload['timezone'], 100)
        if 'order_basis' in payload:
            if payload['order_basis'] != 'observation_order_not_publication':
                raise PublicKnowledgeError(502)
            result['order_basis'] = payload['order_basis']
        return result
    result = {}
    for key in ('college', 'timezone', 'updated_at', 'entity_type', 'entity', 'range'):
        if key not in payload:
            continue
        if key == 'college':
            result[key] = _college(payload[key])
        elif key == 'range':
            if not isinstance(payload[key], dict):
                raise PublicKnowledgeError(502)
            result[key] = {field: _text(payload[key].get(field), 10, nullable=True) for field in ('start', 'end')}
        else:
            result[key] = _text(payload[key], 200, nullable=key == 'updated_at' or kind == 'coverage')
    if kind == 'catalog':
        if not all(key in result for key in ('college', 'timezone')):
            raise PublicKnowledgeError(502)
        for key in ('groups', 'teachers', 'audiences', 'dates'):
            result[key] = _strings(payload.get(key))
        if 'available_dates' in payload:
            result['available_dates'] = _strings(payload['available_dates'])
        for value in result['dates']:
            try:
                _iso(value)
            except PublicKnowledgeError:
                raise PublicKnowledgeError(502) from None
        if 'group_dates' in payload:
            mapping = payload['group_dates']
            if not isinstance(mapping, dict) or len(mapping) > 10000:
                raise PublicKnowledgeError(502)
            result['group_dates'] = {}
            for group, values in mapping.items():
                _text(group, 200)
                days = _strings(values)
                if any(day not in result['dates'] for day in days):
                    raise PublicKnowledgeError(502)
                result['group_dates'][group] = sorted(set(days))
        return result
    if kind not in ('schedule', 'coverage') or not isinstance(payload.get('days'), list):
        raise PublicKnowledgeError(502)
    if len(payload['days']) > (31 if kind == 'schedule' else 10000):
        raise PublicKnowledgeError(502)
    if not all(key in result for key in ('college', 'timezone')) or result['timezone'] != 'Europe/Moscow':
        raise PublicKnowledgeError(502)
    if kind == 'schedule' and (result.get('entity_type') not in ENTITY_PATHS
                               or not result.get('entity') or not result.get('range')):
        raise PublicKnowledgeError(502)
    result['days'] = [_day(day) for day in payload['days']]
    for key in ('dates', 'available_dates', 'missing_dates'):
        if key in payload:
            result[key] = _strings(payload[key])
    summary = payload.get('summary')
    if isinstance(summary, dict):
        result['summary'] = {key: value for key, value in summary.items()
                             if key in DAY_STATUSES | {'total', 'total_days', 'available_days', 'missing_days'}
                             and isinstance(value, int) and not isinstance(value, bool) and value >= 0}
        if isinstance(summary.get('complete'), bool):
            result['summary']['complete'] = summary['complete']
    # coverage is deliberately derived from day statuses rather than copying
    # an arbitrary nested upstream object into the public response.
    if kind == 'schedule':
        result['coverage'] = {'total_days': len(result['days']),
                              'complete': not any(day['status'] in ('missing', 'invalid_source', 'entity_not_found') for day in result['days']),
                              **{status: sum(day['status'] == status for day in result['days']) for status in DAY_STATUSES}}
    for key in ('note', 'lesson_status'):
        if key in payload:
            result[key] = _text(payload[key], 2000)
    for key in ('range_is_calendar', 'history_complete'):
        if key in payload:
            if not isinstance(payload[key], bool):
                raise PublicKnowledgeError(502)
            result[key] = payload[key]
    return result


def _catalog():
    now = time.monotonic()
    with _catalog_lock:
        if _catalog_cache['payload'] is not None and _catalog_cache['expires'] > now:
            return _catalog_cache['payload']
        payload = _fetch('catalog')
        _catalog_cache.update(payload=payload, expires=now + 30)
        return payload


def _entity_segment(entity):
    """Avoid encoded path separators: Apache intentionally rejects %2F.

    Ordinary group/teacher URLs stay readable. Only names containing path
    separators or our reserved '~' prefix get a reversible base64url segment.
    """
    if '/' in entity or '\\' in entity or entity.startswith('~'):
        return '~' + base64.urlsafe_b64encode(entity.encode('utf-8')).decode('ascii').rstrip('=')
    return quote(entity, safe='')


def _decode_entity_segment(segment):
    if not segment.startswith('~'):
        return _entity(segment)
    if not re.fullmatch(r'~[A-Za-z0-9_-]{1,1067}', segment):
        raise PublicKnowledgeError(400, 'Некорректный адрес названия')
    encoded = segment[1:]
    try:
        raw = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
        value = _entity(raw.decode('utf-8'))
    except (ValueError, UnicodeError, binascii.Error):
        raise PublicKnowledgeError(400, 'Некорректный адрес названия') from None
    if _entity_segment(value) != segment:
        raise PublicKnowledgeError(400, 'Некорректный адрес названия')
    return value


def _entity_url(kind, entity, suffix=''):
    return f'/colleges/kkep/{ENTITY_PATHS[kind]}/{_entity_segment(entity)}{suffix}'


def _context(data=None, **extra):
    return {'college': (data or {}).get('college', COLLEGE_FALLBACK),
            'canonical': PUBLIC_ORIGIN + request.path, 'public_origin': PUBLIC_ORIGIN,
            'STATIC_VERSION': STATIC_VERSION, 'today': _today().isoformat(),
            'entity_url': _entity_url, 'entity_labels': ENTITY_LABELS,
            'status_messages': STATUS_MESSAGES, **extra}


def _html(template, data=None, **extra):
    return Response(render_template(template, **_context(data, **extra)), mimetype='text/html')


@knowledge_public_bp.get('/colleges/kkep')
@limiter.limit('60/minute')
def college_page():
    catalog = _catalog()
    return _html('public_schedule_college.html', catalog, catalog=catalog)


@knowledge_public_bp.get('/colleges/kkep/<kind>')
@limiter.limit('60/minute')
def entity_index(kind):
    if kind not in ENTITY_PATHS.values():
        raise PublicKnowledgeError(404)
    catalog = _catalog()
    entity_type = next(key for key, value in ENTITY_PATHS.items() if value == kind)
    return _html('public_schedule_index.html', catalog, entity_type=entity_type,
                 entities=catalog[kind], catalog=catalog)


def _known_entity(catalog, kind, entity):
    entity = _decode_entity_segment(entity)
    candidates = [value for value in catalog[ENTITY_PATHS[kind]] if value.casefold() == entity.casefold()]
    if len(candidates) == 1:
        return candidates[0]
    # Historical entities may no longer be in today's directory; resolve them
    # against archived data rather than silently replacing them with a guess.
    resolved = _fetch('resolve', {'entity_type': kind, 'query': entity})
    exact = [value for value in resolved['matches'] if value.casefold() == entity.casefold()]
    if len(exact) != 1:
        raise PublicKnowledgeError(404, 'Название не найдено. Выберите его в справочнике.')
    return exact[0]


def _schedule_page(kind, entity, chosen_date=None, week_start=None):
    if kind not in ENTITY_PATHS.values():
        raise PublicKnowledgeError(404)
    entity_type = next(key for key, value in ENTITY_PATHS.items() if value == kind)
    catalog = _catalog()
    entity = _known_entity(catalog, entity_type, entity)
    explicit_date = chosen_date is not None
    chosen_date = _iso(chosen_date) if explicit_date else _today().isoformat()
    params = {'entity_type': entity_type, 'entity': entity}
    try:
        if week_start:
            start = date.fromisoformat(_iso(week_start))
            params.update(start=start.isoformat(), end=(start + timedelta(days=6)).isoformat())
            view = 'week'
        else:
            params['date'] = chosen_date
            start = date.fromisoformat(chosen_date)
            view = 'day'
        previous_date = (start - timedelta(days=7 if view == 'week' else 1)).isoformat()
        next_date = (start + timedelta(days=7 if view == 'week' else 1)).isoformat()
        monday = (start - timedelta(days=start.weekday())).isoformat()
    except (OverflowError, ValueError):
        raise PublicKnowledgeError(400, 'Дата находится за пределами поддерживаемой навигации') from None
    payload = _fetch('schedule', params)
    expected = params.get('date') or params['start']
    expected_end = params.get('date') or params['end']
    if (payload.get('entity_type') != entity_type or payload.get('entity') != entity
            or payload.get('range') != {'start': expected, 'end': expected_end}
            or not payload['days'] or any(not expected <= day['date'] <= expected_end for day in payload['days'])):
        raise PublicKnowledgeError(502)
    archive_dates = sorted(catalog['dates'], reverse=True)
    response = _html('public_schedule_day.html', catalog, entity_type=entity_type, entity=entity,
                     schedule=payload, view=view, chosen_date=start.isoformat(),
                     previous_date=previous_date, next_date=next_date,
                     week_start=monday,
                     archive_dates=archive_dates[:12],
                     canonical=PUBLIC_ORIGIN + _entity_url(entity_type, entity,
                         '/week/' + start.isoformat() if view == 'week' else '/' + chosen_date if explicit_date else ''))
    if all(day['status'] in ('missing', 'entity_not_found', 'invalid_source') for day in payload['days']):
        response.headers['X-Robots-Tag'] = 'noindex, follow'
    return response


@knowledge_public_bp.get('/colleges/kkep/<kind>/<entity>')
@limiter.limit('60/minute')
def current_schedule(kind, entity):
    _params(())
    return _schedule_page(kind, entity)


@knowledge_public_bp.get('/colleges/kkep/<kind>/<entity>/<chosen_date>')
@limiter.limit('60/minute')
def dated_schedule(kind, entity, chosen_date):
    _params(())
    return _schedule_page(kind, entity, chosen_date=chosen_date)


@knowledge_public_bp.get('/colleges/kkep/<kind>/<entity>/week/<week_start>')
@limiter.limit('60/minute')
def weekly_schedule(kind, entity, week_start):
    _params(())
    return _schedule_page(kind, entity, week_start=week_start)


@knowledge_public_bp.get('/colleges/kkep/<kind>/<entity>/archive')
@limiter.limit('60/minute')
def archive_page(kind, entity):
    if kind not in ENTITY_PATHS.values():
        raise PublicKnowledgeError(404)
    entity_type = next(key for key, value in ENTITY_PATHS.items() if value == kind)
    catalog = _catalog()
    entity = _known_entity(catalog, entity_type, entity)
    coverage = _fetch('coverage', {'entity_type': entity_type, 'entity': entity})
    return _html('public_schedule_archive.html', catalog, entity_type=entity_type, entity=entity,
                 coverage=coverage, archive_days=sorted(coverage['days'], key=lambda day: day['date'], reverse=True),
                 canonical=PUBLIC_ORIGIN + _entity_url(entity_type, entity, '/archive'))


@knowledge_public_bp.get('/api/v2/catalog')
@limiter.limit('10/second')
def api_catalog():
    _params(())
    return jsonify(_catalog())


@knowledge_public_bp.get('/api/v2/schedule')
@limiter.limit('10/second')
def api_schedule():
    params = _range(_with_entity(_params(('entity_type', 'entity', 'date', 'start', 'end'))), required=True)
    payload = _fetch('schedule', params)
    start = params.get('date') or params['start']
    end = params.get('date') or params['end']
    expected_dates = [(date.fromisoformat(start) + timedelta(days=offset)).isoformat()
                      for offset in range((date.fromisoformat(end) - date.fromisoformat(start)).days + 1)]
    if (payload.get('entity_type') != params['entity_type']
            or payload.get('entity', '').casefold() != params['entity'].casefold()
            or payload.get('range') != {'start': start, 'end': end}
            or [day['date'] for day in payload['days']] != expected_dates):
        raise PublicKnowledgeError(502)
    return jsonify(payload)


@knowledge_public_bp.get('/api/v2/resolve')
@limiter.limit('10/second')
def api_resolve():
    params = _params(('entity_type', 'query'))
    if params.get('entity_type') not in ENTITY_PATHS:
        raise PublicKnowledgeError(400, 'entity_type: group, teacher или audience')
    params['query'] = _entity(params.get('query'))
    return jsonify(_fetch('resolve', params))


@knowledge_public_bp.get('/api/v2/coverage')
@limiter.limit('10/second')
def api_coverage():
    params = _range(_with_entity(_params(('entity_type', 'entity', 'start', 'end')), optional=True), max_days=366)
    return jsonify(_fetch('coverage', params))


@knowledge_public_bp.get('/api/v2/changes')
@limiter.limit('10/second')
def api_changes():
    params = _with_entity(_params(('entity_type', 'entity', 'date')))
    params['date'] = _iso(params.get('date'), relative=True)
    return jsonify(_fetch('changes', params))


@knowledge_public_bp.get('/api/v2/sources/<source_id>')
@limiter.limit('20/minute')
def api_source(source_id):
    _params(())
    if not re.fullmatch(r'[0-9a-f]{64}', source_id):
        raise PublicKnowledgeError(404)
    upstream = _open_upstream(f'sources/{source_id}')
    mime = upstream.headers.get('Content-Type', '').split(';', 1)[0].lower()
    extension = {'application/pdf': 'pdf',
                 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
                 'application/vnd.ms-excel': 'xls'}.get(mime)
    if not extension:
        upstream.close()
        raise PublicKnowledgeError(502)
    content = _read_bounded(upstream, MAX_SOURCE_BYTES)
    if (extension == 'pdf' and not content.startswith(b'%PDF-')) or (extension == 'xlsx' and not content.startswith(b'PK')):
        raise PublicKnowledgeError(502)
    if hashlib.sha256(content).hexdigest() != source_id:
        raise PublicKnowledgeError(502)
    response = Response(content, mimetype=mime)
    response.headers['Content-Disposition'] = f'inline; filename="schedule-{source_id[:12]}.{extension}"'
    response.headers['Cache-Control'] = 'public, max-age=86400, immutable'
    response.headers['X-Robots-Tag'] = 'noindex'
    return response


@knowledge_public_bp.get('/api/v2/openapi.json')
def api_openapi():
    return jsonify(build_openapi())


def _agent_examples():
    """Self-contained example URLs shared by HTML and the text index."""
    group = {'entity_type': 'group', 'entity': '103-Д9-3ИНС'}
    example_date = '2026-10-09'
    examples = {
        'resolve': '/api/v2/resolve?' + urlencode({'entity_type': 'group', 'query': '103'}, quote_via=quote),
        'tomorrow': '/api/v2/schedule?' + urlencode({**group, 'date': 'tomorrow'}, quote_via=quote),
        'day': '/api/v2/schedule?' + urlencode({**group, 'date': example_date}, quote_via=quote),
        'range': '/api/v2/schedule?' + urlencode({**group, 'start': '2026-10-05', 'end': example_date}, quote_via=quote),
        'teacher': '/api/v2/schedule?' + urlencode({
            'entity_type': 'teacher', 'entity': 'Шульга Н.Н.', 'date': example_date}, quote_via=quote),
        'coverage': '/api/v2/coverage?' + urlencode({**group, 'start': '2026-10-05', 'end': example_date}, quote_via=quote),
        'changes': '/api/v2/changes?' + urlencode({**group, 'date': example_date}, quote_via=quote),
    }
    return {name: PUBLIC_ORIGIN + path for name, path in examples.items()}


@knowledge_public_bp.get('/agents')
def agents_page():
    return _html('public_schedule_agents.html', examples=_agent_examples(),
                 example_day_url=PUBLIC_ORIGIN + _entity_url('group', '103-Д9-3ИНС', '/2026-10-09'))


@knowledge_public_bp.get('/llms.txt')
def llms_file():
    examples = _agent_examples()
    text = f'''# ККЭПик: публичное расписание ККЭП

Краснодарский колледж электронного приборостроения, Краснодар.
Timezone: Europe/Moscow. Public, read-only; no account or API key required.

- [College and directories]({PUBLIC_ORIGIN}/colleges/kkep)
- [Agent documentation]({PUBLIC_ORIGIN}/agents)
- [OpenAPI 3.1]({PUBLIC_ORIGIN}/api/v2/openapi.json)
- [Catalog]({PUBLIC_ORIGIN}/api/v2/catalog)
- Remote MCP: {PUBLIC_ORIGIN}/mcp

## Runnable examples

- [Resolve group 103; ask if ambiguous]({examples['resolve']})
- [Group 103-Д9-3ИНС, 2026-10-09, HTML]({PUBLIC_ORIGIN + _entity_url('group', '103-Д9-3ИНС', '/2026-10-09')})
- [Same day, JSON]({examples['day']})
- [Tomorrow, Europe/Moscow, JSON]({examples['tomorrow']})
- [Group schedule, 2026-10-05 through 2026-10-09]({examples['range']})
- [Teacher Шульга Н.Н., 2026-10-09]({examples['teacher']})
- [Archive coverage for the group and date range]({examples['coverage']})
- [Saved revisions for the group and date]({examples['changes']})

Resolve the exact group first; do not guess when matches are ambiguous.
GET /api/v2/schedule requires
entity_type (group|teacher|audience), entity, and ISO date or start/end.
At most 31 calendar days per schedule request. today/tomorrow/yesterday
are resolved in Europe/Moscow, not the agent's local timezone.
Responses include source URLs and statuses per day. missing means no
available data, not no lessons. Published schedule is not confirmation
that a lesson actually took place. Historical revision coverage is limited.
No homework, personal profiles, grades, attendance, or student records
are exposed. These links do not guarantee automatic discovery by an AI.
'''
    return Response(text, mimetype='text/plain')


@knowledge_public_bp.get('/indexnow-key.txt')
def indexnow_key_file():
    # Ownership verification only; never serves an arbitrary path or settings.
    response = send_file(os.path.join(os.path.dirname(__file__), 'indexnow-key.txt'),
                         mimetype='text/plain', conditional=True, max_age=86400)
    response.headers['X-Robots-Tag'] = 'noindex'
    return response


@knowledge_public_bp.get('/robots.txt')
def robots_file():
    # This governs crawling, not authorization. GPTBot training policy is not
    # changed here; operators can configure it separately from search access.
    exclusions = ('/settings', '/admin/', '/api/me', '/api/schedule/me', '/api/user/',
                  '/api/game/', '/api/pixel/', '/api/message-settings', '/api/v2/sources/')
    sections = []
    for agent in ('*', 'OAI-SearchBot'):
        sections.append(f'User-agent: {agent}\nAllow: /colleges/\nAllow: /agents\nAllow: /api/v2/\n' +
                        '\n'.join(f'Disallow: {path}' for path in exclusions))
    return Response('\n\n'.join(sections) + f'\n\nSitemap: {PUBLIC_ORIGIN}/sitemap.xml\n', mimetype='text/plain')


@knowledge_public_bp.get('/sitemap.xml')
@limiter.limit('30/minute')
def sitemap_file():
    catalog = _catalog()
    paths = ['/', '/colleges/kkep', '/agents', '/colleges/kkep/groups',
             '/colleges/kkep/teachers', '/colleges/kkep/audiences']
    # Bounded sitemap. Date URLs are created only from published catalog days,
    # never from an arbitrary future calendar. Exclude teachers/audiences from
    # the date cross-product; their archive remains linked in their overview.
    known_dates = set(catalog['dates'])
    for kind, collection in ENTITY_PATHS.items():
        for entity in catalog[collection]:
            paths.extend((_entity_url(kind, entity), _entity_url(kind, entity, '/archive')))
            if kind == 'group':
                proven_dates = sorted(set(catalog.get('group_dates', {}).get(entity, ())) & known_dates, reverse=True)[:60]
                paths.extend(_entity_url(kind, entity, f'/{day}') for day in proven_dates)
            if len(paths) >= 45000:
                break
        if len(paths) >= 45000:
            break
    urls = ''.join(f'<url><loc>{escape(PUBLIC_ORIGIN + path)}</loc></url>' for path in paths[:45000])
    return Response('<?xml version="1.0" encoding="UTF-8"?>'
                    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + urls + '</urlset>',
                    mimetype='application/xml')
