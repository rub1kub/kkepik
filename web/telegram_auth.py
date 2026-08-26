"""Telegram Mini App init-data validation shared by API blueprints."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qsl


ENV_FILE = "/etc/kkepik/kkepik.ru.env"
DEFAULT_MAX_AGE_SECONDS = 86400
MAX_FUTURE_SKEW_SECONDS = 60


@lru_cache(maxsize=1)
def get_bot_token() -> str:
    token = os.getenv("BOT_TOKEN", "").strip()
    if token:
        return token
    try:
        with open(ENV_FILE, encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() == "BOT_TOKEN":
                    return value.strip()
    except OSError:
        pass
    return ""


def validate_init_data(
    init_data: str,
    *,
    bot_token: str | None = None,
    now: int | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> dict[str, str] | None:
    if not isinstance(init_data, str) or not init_data:
        return None

    try:
        params = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
        received_hash = params.pop("hash", "")
        auth_date = int(params.get("auth_date", "0"))
    except (TypeError, ValueError):
        return None

    token = (bot_token if bot_token is not None else get_bot_token()).strip()
    if not token or not received_hash or not auth_date:
        return None

    current_time = int(time.time()) if now is None else int(now)
    age = current_time - auth_date
    if age > max_age_seconds or age < -MAX_FUTURE_SKEW_SECONDS:
        return None

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        return None
    return params


def get_request_init_data(flask_request: Any) -> str:
    init_data = flask_request.headers.get("X-Telegram-Init-Data", "")
    if init_data:
        return init_data
    init_data = flask_request.args.get("tgWebAppData", "")
    if init_data:
        return init_data
    data = flask_request.get_json(silent=True)
    if isinstance(data, dict):
        return str(data.get("tgWebAppData") or data.get("initData") or "")
    return ""


def authenticated_telegram_user(flask_request: Any) -> dict[str, Any] | None:
    params = validate_init_data(get_request_init_data(flask_request))
    if not params:
        return None
    try:
        user = json.loads(params.get("user", "{}"))
        user_id = int(user.get("id", 0))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if user_id <= 0:
        return None
    user["id"] = user_id
    return user
