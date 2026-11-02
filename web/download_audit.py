"""File download statistics from Telegram headers or a user_id URL label."""
import re
import uuid
from datetime import datetime

from flask import current_app, g, request
from telegram_auth import authenticated_telegram_user
from activity_store import ActivityStore, entity

FILE_ROUTE = re.compile(r'^/api/schedule/download/(groups|teachers)/(\d{2}\.\d{2}\.\d{4})$')
SOURCE_ROUTE = re.compile(r'^/api/v2/sources/([a-f0-9]{64})$')


def store():
    return ActivityStore(current_app.config.get('ACTIVITY_DB'))


def record(activity, logger, scope, phase, *, outcome='ok', **details):
    try:
        activity.append(scope['actor_id'], scope['channel'], 'server', 'schedule.download.' + phase,
            outcome=outcome, event_id=uuid.uuid4().hex,
            entity_type=scope.get('entity_type'), entity_id=scope.get('entity_id'),
            date=scope.get('schedule_date'), details={**scope['details'], **details})
    except Exception as error:
        logger.warning('Download journal write skipped (%s)', type(error).__name__)


class Transfer:
    """Count consumed bytes; report exhaustion, cancellation and stream failure."""
    def __init__(self, body, finish):
        self.body, self.iterator, self.finish = body, iter(body), finish
        self.sent, self.done = 0, False

    def __iter__(self):
        return self

    def end(self, phase, **details):
        if not self.done:
            self.done = True
            self.finish(phase, bytes_sent=self.sent, **details)

    def __next__(self):
        try:
            chunk = next(self.iterator)
        except StopIteration:
            self.end('complete')
            raise
        except Exception as error:
            self.end('error', error_type=type(error).__name__)
            raise
        self.sent += len(chunk)
        return chunk

    def close(self):
        try:
            close = getattr(self.body, 'close', None)
            if close:
                close()
        finally:
            self.end('interrupted', reason='connection_closed')


def init_download_audit(app):
    @app.before_request
    def download_started():
        file = FILE_ROUTE.fullmatch(request.path)
        source = SOURCE_ROUTE.fullmatch(request.path)
        if request.method not in ('GET', 'HEAD') or not (file or source):
            return
        user = authenticated_telegram_user(request) if request.headers.get('X-Telegram-Init-Data') else None
        label = request.args.get('user_id', '')
        uid = int(label) if re.fullmatch(r'[0-9]{1,16}', label) else 0
        uid = uid if 0 < uid < 2**52 else 0
        actor_id = user['id'] if user else uid
        source_channel = {'bot': 'telegram', 'miniapp': 'miniapp'}.get(request.args.get('source'), 'web')
        scope = dict(actor_id=actor_id, channel='miniapp' if user else source_channel if uid else 'web',
            details=dict(download_id=uuid.uuid4().hex, delivery='http', method=request.method,
                         attribution='signed_header' if user else 'url_id' if uid else 'anonymous'))
        try:
            kind, value = entity(request.args.get('entity_type'), request.args.get('entity_id'))
            scope.update(entity_type=kind, entity_id=value)
        except ValueError:
            pass
        g.download_scope = scope
        if file:
            scope['details']['schedule_type'] = file[1]
            try:
                scope['schedule_date'] = datetime.strptime(file[2], '%d.%m.%Y').date().isoformat()
            except ValueError:
                return  # Existing route validation supplies the error response.
        else:
            scope['details']['source_id'] = source[1]

    @app.after_request
    def download_finished(response):
        scope = getattr(g, 'download_scope', None)
        if scope is None:
            return response
        activity, logger = store(), current_app.logger
        scope['details'].update(http_status=response.status_code, endpoint=request.url_rule.rule if request.url_rule else 'download')
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Download-ID'] = scope['details']['download_id']
        if request.method == 'HEAD':
            record(activity, logger, scope, 'probe', outcome='observed')
            return response
        mime = response.mimetype
        file_type = {'application/pdf': 'pdf', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
                     'application/vnd.ms-excel': 'xls'}.get(mime)
        if response.status_code == 304:
            record(activity, logger, scope, 'not_modified', outcome='observed')
            return response
        record(activity, logger, scope, 'request', outcome='observed')
        if response.status_code not in (200, 206) or not file_type:
            record(activity, logger, scope, 'error', outcome='error' if response.status_code >= 500 else 'rejected')
            return response
        expected = response.content_length
        scope['details'].update(file_type=file_type, bytes_expected=expected, partial=response.status_code == 206)
        record(activity, logger, scope, 'started', outcome='observed')

        def finish(phase, **details):
            if phase == 'complete' and expected is not None and details['bytes_sent'] != expected:
                phase, details['reason'] = 'error', 'length_mismatch'
            record(activity, logger, scope, phase, outcome='ok' if phase == 'complete' else 'error', **details)

        response.response = Transfer(response.response, finish)
        response.direct_passthrough = False
        return response
