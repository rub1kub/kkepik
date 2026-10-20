"""Persistent identifiers for incoming sources and outgoing Telegram messages."""

from __future__ import annotations

import contextvars
import hashlib
import logging
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from aiogram import Bot, types


_DATE_RE = re.compile(r"\b(\d{1,2}\.\d{1,2}\.\d{4})\b")
_TRACKING_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "message_tracking_context",
    default={},
)
_INITIALIZED_PATHS: set[str] = set()
_INIT_LOCK = threading.Lock()


def get_registry_path() -> str:
    configured = os.getenv("MESSAGE_REGISTRY_PATH", "").strip()
    if configured:
        return configured

    import config

    return os.path.join(config.DATA_DIR, "message_registry.db")


def init_registry(db_path: str | os.PathLike[str] | None = None) -> str:
    path = str(db_path or get_registry_path())
    if path in _INITIALIZED_PATHS:
        return path

    with _INIT_LOCK:
        if path in _INITIALIZED_PATHS:
            return path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path, timeout=10) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS message_registry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    message_kind TEXT NOT NULL DEFAULT 'message',
                    telegram_method TEXT,
                    schedule_date TEXT,
                    schedule_type TEXT,
                    entity TEXT,
                    file_name TEXT,
                    content_preview TEXT,
                    content_hash TEXT,
                    media_group_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(platform, direction, chat_id, message_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_message_registry_schedule "
                "ON message_registry(schedule_date, schedule_type, entity)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_message_registry_kind "
                "ON message_registry(message_kind, created_at)"
            )
        _INITIALIZED_PATHS.add(path)
    return path


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def record_message(
    *,
    platform: str,
    direction: str,
    chat_id: int | str,
    message_id: int | str,
    message_kind: str = "message",
    telegram_method: str | None = None,
    schedule_date: str | None = None,
    schedule_type: str | None = None,
    entity: str | None = None,
    file_name: str | None = None,
    content: str | None = None,
    media_group_id: str | None = None,
    db_path: str | os.PathLike[str] | None = None,
) -> None:
    """Insert or enrich one message without ever storing bot credentials."""
    path = init_registry(db_path)
    raw_content = content or ""
    preview = raw_content[:1000] or None
    content_hash = (
        hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
        if raw_content
        else None
    )
    values = (
        platform,
        direction,
        str(chat_id),
        str(message_id),
        message_kind,
        _clean_optional(telegram_method),
        _clean_optional(schedule_date),
        _clean_optional(schedule_type),
        _clean_optional(entity),
        _clean_optional(file_name),
        preview,
        content_hash,
        _clean_optional(media_group_id),
    )
    with sqlite3.connect(path, timeout=10) as conn:
        conn.execute(
            """
            INSERT INTO message_registry (
                platform, direction, chat_id, message_id, message_kind,
                telegram_method, schedule_date, schedule_type, entity,
                file_name, content_preview, content_hash, media_group_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, direction, chat_id, message_id) DO UPDATE SET
                message_kind = excluded.message_kind,
                telegram_method = COALESCE(excluded.telegram_method, telegram_method),
                schedule_date = COALESCE(excluded.schedule_date, schedule_date),
                schedule_type = COALESCE(excluded.schedule_type, schedule_type),
                entity = COALESCE(excluded.entity, entity),
                file_name = COALESCE(excluded.file_name, file_name),
                content_preview = COALESCE(excluded.content_preview, content_preview),
                content_hash = COALESCE(excluded.content_hash, content_hash),
                media_group_id = COALESCE(excluded.media_group_id, media_group_id),
                updated_at = CURRENT_TIMESTAMP
            """,
            values,
        )


def is_message_recorded(
    platform: str,
    direction: str,
    chat_id: int | str,
    message_id: int | str,
    *,
    db_path: str | os.PathLike[str] | None = None,
) -> bool:
    path = init_registry(db_path)
    with sqlite3.connect(path, timeout=10) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM message_registry
            WHERE platform = ? AND direction = ? AND chat_id = ? AND message_id = ?
            """,
            (platform, direction, str(chat_id), str(message_id)),
        ).fetchone()
    return row is not None


def list_messages(
    *,
    schedule_date: str | None = None,
    message_kind: str | None = None,
    chat_id: int | str | None = None,
    db_path: str | os.PathLike[str] | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    path = init_registry(db_path)
    where = []
    params: list[Any] = []
    if schedule_date:
        where.append("schedule_date = ?")
        params.append(schedule_date)
    if message_kind:
        where.append("message_kind = ?")
        params.append(message_kind)
    if chat_id is not None:
        where.append("chat_id = ?")
        params.append(str(chat_id))
    query = "SELECT * FROM message_registry"
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 10000)))

    with sqlite3.connect(path, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@contextmanager
def message_tracking_context(**metadata: Any) -> Iterator[None]:
    current = dict(_TRACKING_CONTEXT.get())
    current.update({key: value for key, value in metadata.items() if value is not None})
    token = _TRACKING_CONTEXT.set(current)
    try:
        yield
    finally:
        _TRACKING_CONTEXT.reset(token)


def _record_telegram_item(
    method: Any,
    result: Any,
    *,
    db_path: str | os.PathLike[str] | None = None,
) -> None:
    context = _TRACKING_CONTEXT.get()
    message_id = getattr(result, "message_id", None)
    if message_id is None:
        return

    result_chat = getattr(result, "chat", None)
    chat_id = getattr(result_chat, "id", None) or getattr(method, "chat_id", None)
    if chat_id is None:
        return

    content = getattr(result, "text", None) or getattr(result, "caption", None)
    if not content:
        content = getattr(method, "text", None) or getattr(method, "caption", None)
    schedule_date = context.get("schedule_date")
    if not schedule_date and content:
        match = _DATE_RE.search(content)
        schedule_date = match.group(1) if match else None

    record_message(
        platform="telegram",
        direction="outgoing",
        chat_id=chat_id,
        message_id=message_id,
        message_kind=context.get("message_kind", "message"),
        telegram_method=method.__class__.__name__,
        schedule_date=schedule_date,
        schedule_type=context.get("schedule_type"),
        entity=context.get("entity"),
        file_name=context.get("file_name"),
        content=content,
        media_group_id=getattr(result, "media_group_id", None),
        db_path=db_path,
    )


def record_outgoing_result(
    method: Any,
    result: Any,
    *,
    db_path: str | os.PathLike[str] | None = None,
) -> None:
    if isinstance(result, (list, tuple)):
        for item in result:
            _record_telegram_item(method, item, db_path=db_path)
        return
    _record_telegram_item(method, result, db_path=db_path)


class TrackedBot(Bot):
    """Aiogram bot that records successful sends without changing handlers."""

    async def __call__(self, method, request_timeout: int | None = None):
        result = await super().__call__(method, request_timeout=request_timeout)
        try:
            record_outgoing_result(method, result)
        except Exception:
            logging.exception("[message_registry] Не удалось сохранить message_id")
        return result

