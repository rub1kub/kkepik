"""Validated, persistent personal schedule templates shared by bot and API."""
from __future__ import annotations

import json
import logging
import sqlite3
from string import Formatter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LessonField = Literal["pair", "time", "subject", "teacher", "room"]
Block = Literal["header", "group", "bounds", "lessons", "footer"]
TEMPLATE_FIELDS = {"emoji", "date", "group", "start", "end", "pair", "time", "subject", "teacher", "room"}


def validate_template(value: str, *, lesson: bool = False) -> str:
    try:
        fields = set()
        for _, name, spec, conversion in Formatter().parse(value):
            if name is None:
                continue
            if name not in TEMPLATE_FIELDS or spec or conversion:
                raise ValueError("Используйте только доступные поля без форматирования")
            fields.add(name)
    except ValueError as exc:
        raise ValueError("Проверьте поля в фигурных скобках: " + str(exc)) from None
    allowed = TEMPLATE_FIELDS if lesson else {"emoji", "date", "group", "start", "end"}
    if fields - allowed:
        raise ValueError("В заголовке доступны: emoji, date, group, start, end")
    if lesson and "subject" not in fields:
        raise ValueError("В шаблоне пары должно быть поле {subject}")
    return value


class SchedulePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    preset: Literal["full", "compact", "time_focus", "minimal", "custom"] = "full"
    show_times: bool = True
    show_teachers: bool = True
    show_rooms: bool = True
    show_free_pairs: bool = True
    show_emoji: bool = True
    show_group: Literal["auto", "always", "never"] = "auto"
    show_bounds: bool = False
    delivery_mode: Literal["photo_text", "text", "photo"] = "photo_text"
    emphasis: Literal["auto", "subject", "time", "none"] = "auto"
    spacing: Literal["spacious", "compact", "line"] = "spacious"
    room_label: Literal["full", "short", "none"] = "full"
    pair_prefix: Literal["bullet", "number", "none"] = "bullet"
    header_template: str = Field(default="{emoji} Расписание на {date}", min_length=1, max_length=200)
    lesson_template: str | None = Field(default=None, max_length=800)
    footer_text: str = Field(default="", max_length=240)
    lesson_rows: list[list[LessonField]] = Field(default_factory=lambda: [["pair", "time", "subject"], ["teacher", "room"]], min_length=1, max_length=3)
    blocks: list[Block] = Field(default_factory=lambda: ["header", "group", "bounds", "lessons", "footer"], min_length=5, max_length=5)
    notifications_enabled: bool = True
    notify_updates: bool = True
    silent: bool = False

    @field_validator("header_template")
    @classmethod
    def header_is_valid(cls, value):
        return validate_template(value)

    @field_validator("lesson_template")
    @classmethod
    def lesson_is_valid(cls, value):
        return validate_template(value, lesson=True) if value is not None else None

    @field_validator("lesson_rows")
    @classmethod
    def fields_are_unique(cls, rows):
        fields = [field for row in rows for field in row]
        if not all(rows) or len(fields) != len(set(fields)) or "subject" not in fields:
            raise ValueError("Поля не должны повторяться; добавьте предмет хотя бы в одну строку")
        return rows

    @field_validator("blocks")
    @classmethod
    def blocks_are_unique(cls, blocks):
        if len(set(blocks)) != 5:
            raise ValueError("Каждый блок должен встречаться ровно один раз")
        return blocks


PRESET_INFO = [
    ("full", "Полное", "Время, предмет, преподаватель и аудитория"),
    ("compact", "Компактное", "Все важное в одной строке, без преподавателей"),
    ("time_focus", "Когда идти", "Начало занятий и время каждой пары на первом месте"),
    ("minimal", "Минимум", "Только дата, предметы и аудитории"),
]


def preset_preferences(name: str) -> SchedulePreferences:
    changes = {
        "full": {},
        "compact": dict(show_teachers=False, show_free_pairs=False, spacing="compact", room_label="short", emphasis="none", lesson_rows=[["pair", "time", "subject", "room"]]),
        "time_focus": dict(show_bounds=True, show_free_pairs=False, emphasis="time", lesson_rows=[["time", "subject"], ["pair", "teacher", "room"]]),
        "minimal": dict(show_times=False, show_teachers=False, show_emoji=False, show_free_pairs=False, delivery_mode="text", spacing="compact", room_label="none", emphasis="none", pair_prefix="number", header_template="{date}", lesson_rows=[["pair", "subject", "room"]]),
    }
    if name not in changes:
        raise ValueError("Неизвестный шаблон")
    return SchedulePreferences(preset=name, **changes[name])


def wants_broadcast(preferences: SchedulePreferences, *, is_update: bool) -> bool:
    return preferences.notifications_enabled and (not is_update or preferences.notify_updates)


def init_preferences_table(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS schedule_preferences (
        user_id INTEGER PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
        preferences TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")


def _db_path(db_path):
    if db_path is not None:
        return db_path
    import config
    return config.get_db_path()


def _decode(raw: str) -> SchedulePreferences:
    try:
        return SchedulePreferences.model_validate(json.loads(raw))
    except (ValueError, TypeError):
        logging.warning("Invalid stored schedule preferences; using full template")
        return SchedulePreferences()


def get_preferences(user_id: int, db_path=None) -> SchedulePreferences:
    with sqlite3.connect(_db_path(db_path), timeout=10) as conn:
        row = conn.execute("SELECT preferences FROM schedule_preferences WHERE user_id=?", (user_id,)).fetchone()
    return _decode(row[0]) if row else SchedulePreferences()


def get_all_preferences(db_path=None) -> dict[int, SchedulePreferences]:
    with sqlite3.connect(_db_path(db_path), timeout=10) as conn:
        rows = conn.execute("SELECT user_id,preferences FROM schedule_preferences").fetchall()
    return {uid: _decode(raw) for uid, raw in rows}


def save_preferences(user_id: int, preferences: SchedulePreferences, db_path=None) -> None:
    preferences = SchedulePreferences.model_validate(preferences.model_dump())
    with sqlite3.connect(_db_path(db_path), timeout=10) as conn:
        if not conn.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone():
            raise ValueError("Сначала зарегистрируйтесь через /start")
        conn.execute("""INSERT INTO schedule_preferences(user_id,preferences) VALUES(?,?)
            ON CONFLICT(user_id) DO UPDATE SET preferences=excluded.preferences,updated_at=CURRENT_TIMESTAMP""",
            (user_id, preferences.model_dump_json()))
