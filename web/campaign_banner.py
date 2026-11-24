"""Time-bounded promotions for the Mini App."""
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, jsonify, make_response, render_template, request

from telegram_auth import authenticated_telegram_user

CAMPAIGN_ID = 'changellenge-best-company-award-2026'
STARTS_AT = datetime.fromisoformat('2026-11-03T12:47:54+00:00')
ENDS_AT = STARTS_AT + timedelta(days=3)
DESTINATION = 'https://u.to/_5nhIg'
PREVIEW_OWNER = 1084693264
PREVIEW_ASSETS = Path('/var/lib/kkepik-campaign-previews')
PREVIEW_DESTINATION = ('https://be-champions.ru/services/'
                       '#rec1852248511:~:text=%D0%A1%D1%82%D1%83%D0%B4%D0%B5%D0%BD%D1%82%2012')
CHAMPION_STARTS_AT = datetime.fromisoformat('2026-11-20T11:48:36+00:00')
CHAMPION_ENDS_AT = CHAMPION_STARTS_AT + timedelta(days=3)
campaign_banner_bp = Blueprint('campaign_banner', __name__)


def campaign_state(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('An explicit timezone is required')
    return dict(id=CAMPAIGN_ID, active=STARTS_AT <= now < ENDS_AT,
                starts_at_ms=int(STARTS_AT.timestamp() * 1000),
                ends_at_ms=int(ENDS_AT.timestamp() * 1000),
                server_now_ms=int(now.timestamp() * 1000))


def banner_context():
    now = datetime.now(timezone.utc)
    state = campaign_state(now)
    return {
        'championship_banner': dict(**state, url=DESTINATION) if state['active'] else None,
        'champion_banner_live': CHAMPION_STARTS_AT <= now < CHAMPION_ENDS_AT,
    }


@campaign_banner_bp.get('/api/campaign/banner')
def public_campaign_state():
    response = jsonify(campaign_state())
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@campaign_banner_bp.get('/api/campaign/owner-preview')
def owner_preview():
    now = datetime.now(timezone.utc)
    init_data = request.headers.get('X-Telegram-Init-Data', '')
    user = authenticated_telegram_user(request) if 0 < len(init_data) <= 16384 else None
    if (not user or now >= CHAMPION_ENDS_AT
            or (now < CHAMPION_STARTS_AT and user['id'] != PREVIEW_OWNER)):
        response = make_response('', 404)
    else:
        try:
            photos = {
                slide: b64encode((PREVIEW_ASSETS / f'champion-{slide}.webp').read_bytes()).decode('ascii')
                for slide in ('gym', 'pool')
            }
        except OSError:
            response = make_response('', 503)
        else:
            response = make_response(render_template('champion_preview.html',
                photos=photos, destination=PREVIEW_DESTINATION))
            response.headers['X-Campaign-Remaining-Ms'] = str(
                int((CHAMPION_ENDS_AT - now).total_seconds() * 1000))
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Robots-Tag'] = 'noindex'
    response.vary.add('X-Telegram-Init-Data')
    return response
