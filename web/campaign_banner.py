"""Time-bounded, public promotion for the Mini App; no user data or tracking."""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify

CAMPAIGN_ID = 'changellenge-cup-moscow-2026'
STARTS_AT = datetime.fromisoformat('2026-10-29T07:54:45+00:00')
ENDS_AT = STARTS_AT + timedelta(days=3)
DESTINATION = 'https://clck.ru/3WjwZy'
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
    state = campaign_state()
    return {'championship_banner': dict(**state, url=DESTINATION) if state['active'] else None}


@campaign_banner_bp.get('/api/campaign/banner')
def public_campaign_state():
    response = jsonify(campaign_state())
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
