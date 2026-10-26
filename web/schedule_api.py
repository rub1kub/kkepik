"""Public read-only schedule API; the bot service remains loopback-only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import os
import re
import uuid

from flask import Blueprint, Response, current_app, g, jsonify, request, send_file, render_template
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests
from werkzeug.exceptions import HTTPException

from config import API_URL, STATIC_VERSION
from public_api_contract import build_openapi
from telegram_auth import authenticated_telegram_user

schedule_bp = Blueprint('schedule', __name__)
limiter = Limiter(
    key_func=get_remote_address, default_limits=[],
    storage_uri=os.getenv('SCHEDULE_RATE_LIMIT_STORAGE_URI', 'memory://'),
    headers_enabled=True, retry_after='delta-seconds',
)
UPSTREAM_TIMEOUT = (3.05, 30)
_CATALOG_KINDS = ('groups', 'teachers', 'audiences')
_ERRORS = {
    400: ('invalid_request', 'Некорректный запрос'),
    401: ('unauthorized', 'Требуется авторизация Telegram'),
    403: ('forbidden', 'Доступ запрещён'),
    404: ('not_found', 'Данные не найдены'),
    405: ('method_not_allowed', 'Метод не поддерживается'),
    413: ('payload_too_large', 'Слишком большой запрос'),
    415: ('unsupported_media_type', 'Используйте Content-Type: application/json'),
    422: ('invalid_request', 'Некорректные параметры запроса'),
    429: ('rate_limited', 'Слишком много запросов. Повторите позже'),
    500: ('internal_error', 'Внутренняя ошибка сервера'),
    502: ('invalid_upstream_response', 'Некорректный ответ сервиса расписания'),
    503: ('service_unavailable', 'Сервис расписания временно недоступен'),
    504: ('upstream_timeout', 'Сервис расписания не ответил вовремя'),
}


class ApiError(Exception):
    def __init__(self, status, message=None):
        self.status = status
        self.message = message or _ERRORS[status][1]


def _request_id():
    if not getattr(g, 'schedule_request_id', None):
        g.schedule_request_id = uuid.uuid4().hex
    return g.schedule_request_id


def error_response(status, message=None):
    code, default_message = _ERRORS.get(status, _ERRORS[500])
    message = message or default_message
    response = jsonify(error=message, detail=message, code=code, request_id=_request_id())
    response.status_code = status
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Request-ID'] = _request_id()
    if status == 429:
        response.headers['Retry-After'] = '1'
    return response


@schedule_bp.record_once
def _init_api(state):
    limiter.init_app(state.app)


@schedule_bp.before_request
def _prepare_request():
    _request_id()
    request.max_content_length = 65536 if request.path.startswith('/api/duties') else 16384
    if len(request.headers.get('X-Telegram-Init-Data', '')) > 16384:
        raise ApiError(413)


@schedule_bp.after_request
def _response_headers(response):
    response.headers['X-Request-ID'] = _request_id()
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers.setdefault('Cache-Control', 'private, no-store')
    if response.status_code >= 400:
        # Never log query strings, bodies or Telegram authentication headers.
        current_app.logger.warning('schedule_api request_id=%s method=%s path=%s status=%s',
                                   _request_id(), request.method, request.path, response.status_code)
    return response


@schedule_bp.errorhandler(ApiError)
def _api_error(exc):
    return error_response(exc.status, exc.message)


@schedule_bp.errorhandler(HTTPException)
def _http_error(exc):
    response = error_response(exc.code)
    if exc.code == 405 and exc.valid_methods:
        response.headers['Allow'] = ', '.join(exc.valid_methods)
    return response


@schedule_bp.errorhandler(Exception)
def _unexpected_error(exc):
    current_app.logger.error('schedule_api internal error request_id=%s type=%s',
                             _request_id(), type(exc).__name__)
    return error_response(500)


def _get_authenticated_user_id():
    telegram_user = authenticated_telegram_user(request)
    if not telegram_user:
        raise ApiError(401)
    return telegram_user['id']


def _validate_date(value):
    if value is None:
        return
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{2}\.[0-9]{2}\.[0-9]{4}', value):
        raise ApiError(400, 'Дата должна быть в формате ДД.ММ.ГГГГ')
    try:
        datetime.strptime(value, '%d.%m.%Y')
    except ValueError:
        raise ApiError(400, 'Указана несуществующая дата') from None


def _schedule_parameters(entity=None):
    if request.method in ('GET', 'HEAD'):
        if any(len(request.args.getlist(key)) != 1 for key in request.args):
            raise ApiError(400, 'Параметры не должны повторяться')
        payload = request.args.to_dict()
    else:
        if not request.is_json:
            raise ApiError(415)
        payload = request.get_json()
    if not isinstance(payload, dict):
        raise ApiError(400, 'Тело запроса должно быть JSON-объектом')
    allowed = {'date', 'tgWebAppData', 'initData'} | ({entity} if entity else set())
    if set(payload) - allowed:
        raise ApiError(400, 'Запрос содержит неизвестные параметры')
    result = {}
    if entity:
        value = payload.get(entity)
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ApiError(400, f'Параметр {entity}: требуется строка длиной от 1 до 200 символов')
        if any(ord(char) < 32 for char in value):
            raise ApiError(400, f'Параметр {entity} содержит недопустимые символы')
        result[entity] = value.strip().upper() if entity == 'group' else value.strip()
    _validate_date(payload.get('date'))
    if payload.get('date') is not None:
        result['date'] = payload['date']
    return result


def _upstream(method, path, **kwargs):
    try:
        return getattr(requests, method)(f'{API_URL}{path}', timeout=UPSTREAM_TIMEOUT,
                                         allow_redirects=False, **kwargs)
    except requests.Timeout:
        raise ApiError(504) from None
    except requests.RequestException:
        raise ApiError(503) from None


def _json_from_upstream(response):
    try:
        payload = response.json()
    except ValueError:
        raise ApiError(502) from None
    if not isinstance(payload, dict):
        raise ApiError(502)
    return payload


def _public_user_payload(payload):
    # New internal fields must not become public by accident.
    result = {key: payload[key] for key in (
        'user_id', 'role', 'name_or_group', 'is_class_teacher', 'class_group'
    ) if key in payload}
    role = result.get('role')
    if role in {'Я преподаватель', 'teacher'}:
        result['role'] = 'teacher'
    elif role in {'Я студент', 'student'}:
        result['role'] = 'student'
    return result


def _checked_payload(response):
    if response.status_code == 200:
        return _json_from_upstream(response)
    # Do not relay internal exceptions or the server's file inventory.
    if response.status_code in (400, 401, 403, 404, 422, 429):
        raise ApiError(response.status_code)
    raise ApiError(503 if response.status_code >= 500 else 502)


def _fetch_catalog_values(kind):
    response = _upstream('get', f'/{kind}')
    try:
        if response.status_code == 404:
            return []
        values = _checked_payload(response).get(kind)
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ApiError(502)
        return sorted({value.strip() for value in values if value.strip()})
    finally:
        response.close()


def _catalog_json_response(payload):
    response = jsonify(payload)
    response.headers['Cache-Control'] = 'private, max-age=60, stale-while-revalidate=300'
    return response


@schedule_bp.route('/api/schedule/catalog')
@limiter.limit('10 per second')
def get_schedule_catalog():
    with ThreadPoolExecutor(max_workers=3) as executor:
        values = executor.map(_fetch_catalog_values, _CATALOG_KINDS)
        payload = dict(zip(_CATALOG_KINDS, values))
    return _catalog_json_response(payload)


@schedule_bp.route('/api/groups')
@limiter.limit('10 per second')
def get_groups_catalog():
    return _catalog_json_response({'groups': _fetch_catalog_values('groups')})


@schedule_bp.route('/api/teachers')
@limiter.limit('10 per second')
def get_teachers_catalog():
    return _catalog_json_response({'teachers': _fetch_catalog_values('teachers')})


@schedule_bp.route('/api/audiences')
@limiter.limit('10 per second')
def get_audiences_catalog():
    return _catalog_json_response({'audiences': _fetch_catalog_values('audiences')})


@schedule_bp.route('/api/me')
@limiter.limit('10 per second')
def get_me():
    user_id = _get_authenticated_user_id()
    response = _upstream('get', f'/user/{user_id}')
    try:
        # Compatibility with older bot processes that wrapped their own 404.
        if response.status_code == 500:
            detail = _json_from_upstream(response).get('detail', '')
            if isinstance(detail, str) and detail.startswith('404:'):
                raise ApiError(404, 'Пользователь не найден')
        return jsonify(_public_user_payload(_checked_payload(response)))
    finally:
        response.close()


def _schedule_response(path, parameters):
    response = _upstream('post', path, json=parameters)
    try:
        payload = _checked_payload(response)
        lines = payload.get('schedule')
        if not isinstance(lines, list) or any(not isinstance(line, str) for line in lines):
            raise ApiError(502)
        if not isinstance(payload.get('date'), str):
            raise ApiError(502)
        if parameters.get('date') and payload['date'] != parameters['date']:
            raise ApiError(502)
        return jsonify({key: payload[key] for key in ('group', 'teacher', 'audience', 'date', 'schedule')
                        if key in payload})
    finally:
        response.close()


@schedule_bp.route('/api/schedule/me', methods=['GET', 'POST'])
@limiter.limit('10 per second')
def get_my_schedule():
    user_id = _get_authenticated_user_id()
    return _schedule_response(f'/getUserSchedule/{user_id}', _schedule_parameters())


@schedule_bp.route('/api/schedule/group', methods=['GET', 'POST'])
@limiter.limit('10 per second')
def get_group_schedule():
    return _schedule_response('/schedule/group', _schedule_parameters('group'))


@schedule_bp.route('/api/schedule/teacher', methods=['GET', 'POST'])
@limiter.limit('10 per second')
def get_teacher_schedule():
    return _schedule_response('/schedule/teacher', _schedule_parameters('teacher'))


@schedule_bp.route('/api/schedule/audience', methods=['GET', 'POST'])
@limiter.limit('10 per second')
def get_audience_schedule():
    return _schedule_response('/schedule/audience', _schedule_parameters('audience'))


@schedule_bp.route('/api/schedule/download/<schedule_type>/<date>')
@limiter.limit('10 per second')
def download_schedule(schedule_type, date):
    if schedule_type not in ('groups', 'teachers'):
        raise ApiError(400, 'Тип расписания должен быть groups или teachers')
    _validate_date(date)
    upstream = _upstream('get', f'/schedule/download/{schedule_type}/{date}', stream=True)
    try:
        if upstream.status_code != 200:
            _checked_payload(upstream)
        content_type = upstream.headers.get('Content-Type', '').split(';')[0].strip().lower()
        extensions = {
            'application/pdf': '.pdf',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
        }
        if content_type not in extensions:
            raise ApiError(502)
        upstream.raw.decode_content = True
        response = send_file(upstream.raw, mimetype=content_type, as_attachment=True,
                             download_name=f'schedule_{schedule_type}_{date}{extensions[content_type]}',
                             conditional=False)
        # The raw file wrapper and close callback both release the upstream socket.
        response.call_on_close(upstream.close)
        response.direct_passthrough = False
        response.headers['Cache-Control'] = 'private, no-store'
        return response
    except Exception:
        upstream.close()
        raise


@schedule_bp.route('/api/openapi.json')
def openapi_spec():
    response = jsonify(build_openapi())
    response.headers['Cache-Control'] = 'no-cache'
    return response


@schedule_bp.route('/settings')
def message_constructor_page():
    return render_template('message_settings.html', STATIC_VERSION=STATIC_VERSION)


@schedule_bp.route('/duties')
def duties_page():
    return render_template('duties.html', STATIC_VERSION=STATIC_VERSION)


@schedule_bp.route('/api/duties', defaults={'suffix': ''}, methods=['GET', 'POST'])
@schedule_bp.route('/api/duties/<path:suffix>', methods=['GET', 'POST', 'PUT'])
@limiter.limit('120 per minute', key_func=lambda: str(_get_authenticated_user_id()))
def duties_api(suffix):
    _get_authenticated_user_id()
    allowed = (suffix == '' and request.method in ('GET', 'POST') or
               suffix in ('preview', 'people') and request.method == 'POST' or
               re.fullmatch(r'[1-9]\d*', suffix) and request.method == 'GET' or
               re.fullmatch(r'[1-9]\d*/settings', suffix) and request.method == 'PUT' or
               re.fullmatch(r'[1-9]\d*/(?:actions|roster)', suffix) and request.method == 'POST')
    if not allowed:
        raise ApiError(404)
    kwargs = {'headers': {'X-Telegram-Init-Data': request.headers.get('X-Telegram-Init-Data', '')}}
    if request.method != 'GET':
        if not request.is_json:
            raise ApiError(415)
        payload = request.get_json()
        if not isinstance(payload, dict):
            raise ApiError(400)
        kwargs['json'] = payload
    upstream = _upstream(request.method.lower(), '/duties' + ('/' + suffix if suffix else ''), **kwargs)
    try:
        payload = _json_from_upstream(upstream)
        if upstream.status_code != 200:
            message = payload.get('detail') if upstream.status_code in (400, 403, 404) else None
            raise ApiError(upstream.status_code if upstream.status_code in _ERRORS else 502,
                           message if isinstance(message, str) else None)
        return jsonify(payload)
    finally:
        upstream.close()


@schedule_bp.route('/api/message-settings', methods=['GET', 'PUT'])
@limiter.limit('10 per second')
def message_settings():
    _get_authenticated_user_id()
    kwargs = {'headers': {'X-Telegram-Init-Data': request.headers.get('X-Telegram-Init-Data', '')}}
    if request.method == 'PUT':
        if not request.is_json:
            raise ApiError(415)
        payload = request.get_json()
        if not isinstance(payload, dict):
            raise ApiError(400)
        kwargs['json'] = payload
    upstream = _upstream(request.method.lower(), '/message-settings', **kwargs)
    try:
        return jsonify(_checked_payload(upstream))
    finally:
        upstream.close()


@schedule_bp.route('/api/message-settings/preview', methods=['POST'])
@limiter.limit('10 per second')
def message_settings_preview():
    _get_authenticated_user_id()
    if not request.is_json:
        raise ApiError(415)
    payload = request.get_json()
    if not isinstance(payload, dict) or set(payload) - {'preferences', 'entity'}:
        raise ApiError(400)
    upstream = _upstream('post', '/message-settings/preview', json=payload,
                         headers={'X-Telegram-Init-Data': request.headers.get('X-Telegram-Init-Data', '')})
    try:
        return jsonify(_checked_payload(upstream))
    finally:
        upstream.close()


@schedule_bp.route('/api/message-settings/image')
@limiter.limit('10 per second')
def message_settings_image():
    _get_authenticated_user_id()
    entity = request.args.get('entity', '')
    if not entity or len(entity) > 200:
        raise ApiError(400)
    upstream = _upstream('get', '/message-settings/image', params={'entity': entity},
                         headers={'X-Telegram-Init-Data': request.headers.get('X-Telegram-Init-Data', '')})
    try:
        if upstream.status_code != 200:
            _checked_payload(upstream)
        if upstream.headers.get('Content-Type', '').split(';')[0] != 'image/png':
            raise ApiError(502)
        return Response(upstream.content, mimetype='image/png')
    finally:
        upstream.close()
