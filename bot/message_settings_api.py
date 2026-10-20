"""Authenticated personal preferences and side-effect-free live previews."""
import hashlib
import hmac
import json
import sqlite3
import time
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

import config
import global_schedules as gs
from schedule_preferences import SchedulePreferences, PRESET_INFO, get_preferences, preset_preferences, save_preferences
from schedules import group_schedule, teacher_schedule
from schedules.message_constructor import render_schedule_message, personal_subject_aliases
from schedules.schedule_formatter import fit_photo_caption, telegram_text_length
from schedules.schedule_dates import current_college_date, is_current_schedule
from tracked_groups import get_tracked_groups

router = APIRouter(prefix="/message-settings", tags=["personal schedule"])


def authenticated_id(response: Response, x_telegram_init_data: str = Header(default="")) -> int:
    response.headers["Cache-Control"] = "private, no-store"
    try:
        if not x_telegram_init_data or len(x_telegram_init_data) > 16384:
            raise ValueError
        pairs = parse_qsl(x_telegram_init_data, keep_blank_values=True, strict_parsing=True)
        if len(pairs) != len(dict(pairs)):
            raise ValueError
        params = dict(pairs)
        signature = params.pop("hash", "")
        age = time.time() - int(params.get("auth_date", "0"))
        token = config.get_bot_token()
        if not token or not -60 <= age <= 86400:
            raise ValueError
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, "\n".join(f"{k}={v}" for k, v in sorted(params.items())).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError
        uid = json.loads(params["user"])["id"]
        if type(uid) is not int or uid <= 0:
            raise ValueError
        return uid
    except (ValueError, TypeError, KeyError):
        raise HTTPException(401, "Откройте конструктор из Telegram") from None


def profile_and_choices(uid):
    with sqlite3.connect(config.get_db_path()) as conn:
        row = conn.execute("SELECT role,name_or_group FROM users WHERE user_id=?", (uid,)).fetchone()
    if not row:
        raise HTTPException(404, "Сначала зарегистрируйтесь в боте через /start")
    role, name = row
    groups = get_tracked_groups(config.get_db_path(), uid)
    if not groups and role == config.ROLE_STUDENT and name:
        groups = [name.upper()]
    choices = [{"key": "group:" + name, "label": name, "teacher": False} for name in groups]
    if role == config.ROLE_TEACHER:
        choices.insert(0, {"key": "teacher:" + name, "label": name, "teacher": True})
    return role, choices


def preview_payload(uid, preferences, entity=None):
    role, choices = profile_and_choices(uid)
    selected = next((choice for choice in choices if choice["key"] == entity), None) if entity else (choices[0] if choices else None)
    if entity and selected is None:
        raise HTTPException(403, "Можно выбрать только своё расписание")
    teacher = bool(selected and selected["teacher"])
    name = selected["label"] if selected else "Пример группы"
    df = gs.last_groups_df
    date = gs.last_groups_date
    lines = None
    if teacher:
        if gs.last_teachers_df is not None and is_current_schedule(gs.last_teachers_date):
            lines = teacher_schedule.get_schedule_for_teacher(gs.last_teachers_df, name)
            date = gs.last_teachers_date
        if not lines and df is not None and is_current_schedule(gs.last_groups_date):
            lines = teacher_schedule.get_schedule_for_teacher(df, name)
            date = gs.last_groups_date
    elif selected and df is not None and is_current_schedule(date):
        lines = group_schedule.get_schedule_for_group(df, name)
    example = lines is None
    if example:
        date = current_college_date().strftime("%d.%m.%Y")
        lines = ["▪️1 пара – Нет", "▪️2 пара – Математика – Иванова И.И. – 48", "▪️3 пара – История – Петров А.В. – 35"]
        if teacher:
            lines = ["▪️2 пара – 150-Д9-1ИНС – Математика – ауд. 48", "▪️3 пара – 151-Д9-1ИНС – История – ауд. 35"]
    elif not lines:
        lines = [f"▪️{pair} пара – Нет" for pair in range(1, 5)]
    image_bytes = gs.last_groups_crop_cache.get(name.upper()) if not example and not teacher else None
    has_image = bool(image_bytes)
    image_key = hashlib.sha256(image_bytes).hexdigest()[:16] if image_bytes else None
    aliases = personal_subject_aliases(role, name)
    text = render_schedule_message(lines, name, date, preferences=preferences,
        show_group=len([choice for choice in choices if not choice["teacher"]]) > 1 or teacher or not has_image,
        teacher=teacher, subject_aliases=aliases)
    # Use the same date-scoped footer as the actual broadcast for every recipient role.
    from schedules.schedule_notices import append_broadcast_schedule_notice, get_schedule_settings_notice
    text = append_broadcast_schedule_notice(text, role if not teacher else config.ROLE_TEACHER, date)
    caption = fit_photo_caption(text)
    return {"html": text, "caption_html": caption, "date": date, "entity": selected["key"] if selected else None,
            "example": example, "has_image": has_image, "image_key": image_key,
            "photo_notice": get_schedule_settings_notice(date), "length": telegram_text_length(text),
            "separate_text": has_image and preferences.delivery_mode == "photo_text" and telegram_text_length(caption) > 1024,
            "choices": choices}


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preferences: SchedulePreferences
    entity: str | None = Field(default=None, max_length=200)


@router.get("")
def read_settings(uid: int = Depends(authenticated_id)):
    preferences = get_preferences(uid)
    return {"preferences": preferences.model_dump(),
            "presets": [{"id": key, "name": name, "description": description, "preferences": preset_preferences(key).model_dump()} for key, name, description in PRESET_INFO],
            "preview": preview_payload(uid, preferences)}


@router.put("")
def write_settings(preferences: SchedulePreferences, uid: int = Depends(authenticated_id)):
    profile_and_choices(uid)
    save_preferences(uid, preferences)
    return {"preferences": preferences.model_dump(), "saved": True}


@router.post("/preview")
def preview(request: PreviewRequest, uid: int = Depends(authenticated_id)):
    return preview_payload(uid, request.preferences, request.entity)


@router.get("/image")
def preview_image(entity: str = "", uid: int = Depends(authenticated_id)):
    _, choices = profile_and_choices(uid)
    selected = next((choice for choice in choices if choice["key"] == entity and not choice["teacher"]), None)
    if not selected:
        raise HTTPException(403, "Можно выбрать только свою группу")
    image = gs.last_groups_crop_cache.get(selected["label"].upper())
    if not image or not is_current_schedule(gs.last_groups_date):
        raise HTTPException(404, "Изображение расписания недоступно")
    return Response(image, media_type="image/png", headers={"Cache-Control": "private, no-store"})
