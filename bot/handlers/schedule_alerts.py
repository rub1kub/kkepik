"""Direct owner notifications for failures before a schedule broadcast."""

from __future__ import annotations

import html
import os
from typing import Any


OWNER_ID = int(os.getenv("SCHEDULE_ALERT_USER_ID", "1084693264"))


def _safe(value: Any, limit: int = 700) -> str:
    text = str(value or "неизвестно").strip()[:limit]
    return html.escape(text)


async def notify_schedule_error(
    bot,
    *,
    error: Any,
    source: str,
    file_name: str | None = None,
    source_chat_id: int | str | None = None,
    source_message_id: int | str | None = None,
) -> bool:
    """Notify only the owner; notification failure never masks the root error."""
    details = [
        "🚨 <b>Ошибка обработки расписания</b>",
        f"Источник: <b>{_safe(source, 80)}</b>",
    ]
    if file_name:
        details.append(f"Файл: <code>{_safe(file_name, 250)}</code>")
    if source_chat_id is not None:
        details.append(f"Chat ID: <code>{_safe(source_chat_id, 80)}</code>")
    if source_message_id is not None:
        details.append(f"Message ID: <code>{_safe(source_message_id, 100)}</code>")
    details.extend(
        [
            f"Причина: {_safe(error)}",
            "Рассылка этого файла не запускалась.",
        ]
    )
    try:
        await bot.send_message(OWNER_ID, "\n".join(details), parse_mode="HTML")
        return True
    except Exception:
        return False

