"""Authenticated, bounded client events and server-side action auditing."""
import logging
import re
import time
import uuid

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.exceptions import HTTPException
from telegram_auth import authenticated_telegram_user
from activity_store import ActivityStore, RateLimited, entity, schedule_date

activity_bp = Blueprint('activity', __name__)
CLIENT_ACTIONS = {'page.open', 'page.resume', 'schedule.select', 'schedule.view', 'schedule.empty',
                  'schedule.error', 'favorites.open', 'favorites.dialog', 'ui.action',
                  'schedule.download.click', 'schedule.download.cancel', 'schedule.download.launch'}
CLIENT_KEYS = {'id', 'action', 'entity_type', 'entity_id', 'date', 'client_at_ms', 'details'}
CLIENT_DETAILS = {'page', 'reason', 'is_favorite', 'transport', 'tab', 'control'}


def store():
    return ActivityStore(current_app.config.get('ACTIVITY_DB'))


@activity_bp.post('/api/activity/events')
def client_events():
    request.max_content_length = 16384
    response = None
    if request.headers.get('Origin') and request.headers['Origin'] != request.host_url.rstrip('/'):
        return jsonify(error='forbidden'), 403
    # The actor always comes from signed Telegram data, never from an event field.
    if not request.headers.get('X-Telegram-Init-Data') or len(request.headers['X-Telegram-Init-Data']) > 16384:
        return jsonify(error='unauthorized'), 401
    user = authenticated_telegram_user(request)
    if not user:
        return jsonify(error='unauthorized'), 401
    body = request.get_json(silent=True)
    try:
        if not isinstance(body, dict) or set(body) != {'events'} or not isinstance(body['events'], list) or not 1 <= len(body['events']) <= 20:
            raise ValueError
        events = []
        for value in body['events']:
            if not isinstance(value, dict) or set(value) - CLIENT_KEYS or value.get('action') not in CLIENT_ACTIONS:
                raise ValueError
            details = value.get('details') or {}
            if not isinstance(details, dict) or set(details) - CLIENT_DETAILS:
                raise ValueError
            choices = {'reason': {'entity_switch', 'render'},
                       'transport': {'cache', 'network', 'unknown'},
                       'tab': {'today', 'calendar', 'members', 'settings'}}
            for key, allowed in choices.items():
                if key in details and details[key] not in allowed:
                    raise ValueError
            if 'is_favorite' in details and type(details['is_favorite']) is not bool:
                raise ValueError
            if 'page' in details and not re.fullmatch(r'/(?:[a-z0-9_/-]{0,80})', details['page']):
                raise ValueError
            if 'control' in details and not re.fullmatch(r'[A-Za-z_]{1,50}', details['control']):
                raise ValueError
            events.append(dict(event_id=value.get('id'), action=value['action'], outcome='observed',
                entity_type=value.get('entity_type'), entity_id=value.get('entity_id'), date=value.get('date'),
                details=details, client_at_ms=value.get('client_at_ms')))
        accepted = store().append_batch(user['id'], 'miniapp', 'client', events, rate_limit=True)
        return jsonify(success=True, accepted=accepted)
    except RateLimited:
        response = jsonify(error='rate_limited'); response.status_code = 429
        response.headers['Retry-After'] = '60'
        return response
    except (ValueError, KeyError, TypeError):
        return jsonify(error='invalid_event'), 400
    except Exception as error:
        current_app.logger.warning('Activity journal unavailable (%s)', type(error).__name__)
        return jsonify(error='temporarily_unavailable'), 503


def api_action(path, method, payload):
    if path.startswith('/api/schedule/download'):
        return None  # Download audit records identity and the body transfer separately.
    if path == '/api/bootstrap': return 'app.bootstrap'
    if re.fullmatch(r'/api/schedule/(me|group|teacher|audience)', path): return 'schedule.fetch'
    if path.startswith('/api/favorites'):
        return {'/api/favorites': 'favorite.list', '/api/favorites/add': 'favorite.add',
                '/api/favorites/remove': 'favorite.remove'}.get(path)
    if path.startswith('/api/message-settings'):
        return 'settings.preview' if path.endswith(('/preview', '/image')) else 'settings.read' if method == 'GET' else 'settings.save'
    if path.startswith('/api/duties'):
        return 'duty.read' if method == 'GET' else 'duty.' + ('preview' if path.endswith('/preview') else 'action')
    if path == '/api/schedule/reactions/toggle': return 'reaction.toggle'
    if path.startswith('/api/attendance/') and method != 'GET': return 'attendance.change'
    if path.startswith('/api/subject-hours') and method != 'GET': return 'subject_hours.change'
    if path == '/api/subject-hours': return 'subject_hours.read'
    if path == '/api/review': return 'review.submit'
    if '/new_game' in path or path.endswith(('/create', '/join')): return 'game.start'
    if path.startswith('/api/submit/') or path.endswith('/submit_score'): return 'game.submit'
    if path.endswith(('/exit', '/surrender')): return 'game.exit'
    if path == '/api/congratulate': return 'counter.add'
    if path.startswith('/api/') and method in ('POST', 'PUT', 'DELETE') and not path.startswith(('/api/v2/', '/api/vpn/')):
        return 'api.mutation'
    return None


def init_activity(app):
    app.register_blueprint(activity_bp)

    @app.before_request
    def activity_started():
        g.activity_started = time.monotonic()

    @app.after_request
    def activity_completed(response):
        if request.path.startswith('/api/activity/'):
            response.headers['Cache-Control'] = 'private, no-store'
            return response
        if not request.path.startswith('/api/') or not request.url_rule:
            return response
        try:
            payload = request.get_json(silent=True) if request.is_json and (request.content_length or 0) <= 65536 else {}
            payload = payload if isinstance(payload, dict) else {}
            action = api_action(request.path, request.method, payload)
            if not action:
                return response
            user = authenticated_telegram_user(request)
            if not user:
                return response
            body = response.get_json(silent=True) if response.is_json else {}
            body = body if isinstance(body, dict) else {}
            outcome = 'error' if response.status_code >= 500 else 'rejected' if response.status_code >= 400 or body.get('success') is False else 'ok'
            details = dict(endpoint=request.url_rule.rule, method=request.method,
                http_status=response.status_code, duration_ms=int((time.monotonic() - getattr(g, 'activity_started', time.monotonic())) * 1000))
            for key in ('space', 'day', 'member'):
                if type(payload.get(key)) is int: details[key] = payload[key]
            suffix = request.view_args.get('suffix', '')
            if request.path.startswith('/api/duties') and re.match(r'^\d+(?:/|$)', suffix):
                details['space'] = int(suffix.split('/')[0])
            if isinstance(payload.get('action'), str) and re.fullmatch('[a-z_]{1,40}', payload['action']):
                details['action'] = payload['action']
            kind = payload.get('entity_type') if payload.get('entity_type') in ('group', 'teacher', 'audience') else None
            value = payload.get('entity_id') if kind else None
            for key in ('group', 'teacher', 'audience'):
                if not kind and isinstance(payload.get(key) or request.args.get(key), str):
                    kind, value = key, payload.get(key) or request.args.get(key)
                if not kind and isinstance(body.get(key), str): kind, value = key, body[key]
            try: kind, value = entity(kind, value)
            except ValueError: kind, value = None, None
            try: date = schedule_date(payload.get('date') or request.args.get('date'))
            except ValueError: date = None
            store().append(user['id'], 'miniapp', 'server', action, outcome=outcome,
                event_id=uuid.uuid4().hex, entity_type=kind, entity_id=value, date=date, details=details)
        except Exception as error:
            # Logging failure must not change an action's existing result.
            current_app.logger.warning('Activity journal write skipped (%s)', type(error).__name__)
        return response
