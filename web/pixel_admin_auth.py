"""Short-lived canvas capabilities issued only after the website admin login."""

import os
from pathlib import Path

from itsdangerous import BadSignature, URLSafeTimedSerializer


CANVAS_ADMIN_TELEGRAM_ID = 1084693264
TOKEN_MAX_AGE = 900


def serializer():
    # This separate random key is outside the web root, readable by WSGI only.
    path = os.environ.get('KEPIK_CANVAS_ADMIN_KEY', '/var/lib/kkepik-pixels/admin.key')
    key = Path(path).read_text(encoding='ascii').strip()
    if len(key) < 64:
        raise ValueError('Canvas admin key is not configured')
    return URLSafeTimedSerializer(key, salt='kepik-canvas-admin-v1')


def issue_admin_token():
    return serializer().dumps({'scope': 'canvas:admin'})


def valid_admin_token(token):
    if not isinstance(token, str) or not token or len(token) > 1024:
        return False
    try:
        return serializer().loads(token, max_age=TOKEN_MAX_AGE) == {'scope': 'canvas:admin'}
    except (BadSignature, OSError, ValueError):
        return False
