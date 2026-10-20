"""Read-only MAX handlers that forward fresh schedule files to Telegram."""

from __future__ import annotations

import os
import re
import tempfile
import traceback
import html
import time
from urllib.parse import unquote, urlparse

import aiohttp
from pyromax import MaxApi
from pyromax.models import FileAttachment, Message

from message_registry import is_message_recorded, record_message


ADMIN_ID = 1084693264
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_RETRY_DELAY_MS = 5 * 60 * 1000
_seen_message_ids: dict[str, None] = {}
_retry_after_ms: dict[str, int] = {}


def register_handlers(dispatcher, *, accept_after_ms: int) -> None:
    """Register incoming-message handling without any MAX write actions."""

    @dispatcher.message()
    async def on_message(message: Message, max_api: MaxApi):
        await _handle_message(message, max_api, accept_after_ms=accept_after_ms)


async def _handle_message(
    message: Message,
    max_api: MaxApi,
    *,
    accept_after_ms: int,
) -> None:
    from max_bot.config import MAX_WATCH_CHAT_IDS

    if message.chat_id not in MAX_WATCH_CHAT_IDS:
        return
    if message.time and message.time < accept_after_ms:
        print(f"[MAX] Старое событие {message.message_id} пропущено")
        return

    message_key = f"{message.chat_id}:{message.message_id}"
    if message_key in _seen_message_ids:
        print(f"[MAX] Повтор события {message.message_id} пропущен")
        return
    retry_after = _retry_after_ms.get(message_key, 0)
    if retry_after > int(time.monotonic() * 1000):
        return
    try:
        if is_message_recorded(
            "max",
            "incoming",
            message.chat_id,
            message.message_id,
        ):
            print(f"[MAX] Уже обработанное событие {message.message_id} пропущено")
            return
    except Exception as exc:
        print(f"[MAX] Не удалось проверить постоянный дедупликатор: {exc}")
    _remember_message(message_key)
    _hydrate_file_attachments(message)

    try:
        print(
            f"[MAX] Новое сообщение в разрешённом чате {message.chat_id}; "
            f"вложений: {len(message.attaches or [])}"
        )
        found_schedule = False
        processing_succeeded = True
        for attachment in message.attaches or []:
            if isinstance(attachment, FileAttachment):
                result = await _process_file_attachment(
                    attachment,
                    max_api,
                    source_chat_id=message.chat_id,
                    source_message_id=message.message_id,
                )
                if result is not None:
                    found_schedule = True
                    processing_succeeded = processing_succeeded and result

        if message.text:
            result = await _check_text_for_schedule_links(
                message.text,
                source_chat_id=message.chat_id,
                source_message_id=message.message_id,
            )
            if result is not None:
                found_schedule = True
                processing_succeeded = processing_succeeded and result

        if found_schedule and not processing_succeeded:
            _schedule_retry(message_key)
            return

        if not found_schedule:
            record_message(
                platform="max",
                direction="incoming",
                chat_id=message.chat_id,
                message_id=message.message_id,
                message_kind="max_event",
            )
        _retry_after_ms.pop(message_key, None)
    except Exception as exc:
        _schedule_retry(message_key)
        print(f"[MAX] Ошибка обработки сообщения: {exc}")
        traceback.print_exc()
        await _notify_processing_error(
            error=exc,
            source_chat_id=message.chat_id,
            source_message_id=message.message_id,
        )


def _remember_message(message_key: str) -> None:
    _seen_message_ids[message_key] = None
    if len(_seen_message_ids) > 5000:
        _seen_message_ids.pop(next(iter(_seen_message_ids)))


def _schedule_retry(message_key: str) -> None:
    _seen_message_ids.pop(message_key, None)
    _retry_after_ms[message_key] = int(time.monotonic() * 1000) + MAX_RETRY_DELAY_MS
    if len(_retry_after_ms) > 5000:
        _retry_after_ms.pop(next(iter(_retry_after_ms)))


def _hydrate_file_attachments(message: Message) -> None:
    """Restore fields omitted by pyromax for attachments loaded from history."""
    for attachment in message.attaches or []:
        if not isinstance(attachment, FileAttachment):
            continue
        attachment.uploaded = True
        attachment.chat_id = message.chat_id
        attachment.message_id = message.message_id


async def _process_file_attachment(
    attachment: FileAttachment,
    max_api: MaxApi,
    *,
    source_chat_id: int | str,
    source_message_id: int | str,
) -> bool | None:
    file_name = _safe_file_name(
        getattr(attachment, "name", None)
        or getattr(attachment, "file_name", None)
        or ""
    )
    declared_size = int(getattr(attachment, "size", 0) or 0)
    if declared_size > MAX_FILE_BYTES:
        print(f"[MAX] Вложение слишком большое: {declared_size} байт")
        await _notify_processing_error(
            error=f"Файл слишком большой: {declared_size} байт",
            file_name=file_name or None,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
        )
        _record_schedule_source(
            source_chat_id,
            source_message_id,
            file_name,
            message_kind="schedule_rejected",
        )
        return True

    if file_name and not _is_schedule_file(file_name):
        return None

    data, headers = await max_api.download_file(attachment)
    if not data:
        print("[MAX] Не удалось скачать файловое вложение")
        await _notify_processing_error(
            error="MAX не вернул содержимое файлового вложения",
            file_name=file_name or None,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
        )
        return False
    if len(data) > MAX_FILE_BYTES:
        print(f"[MAX] Загруженный файл слишком большой: {len(data)} байт")
        await _notify_processing_error(
            error=f"Загруженный файл слишком большой: {len(data)} байт",
            file_name=file_name or None,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
        )
        _record_schedule_source(
            source_chat_id,
            source_message_id,
            file_name,
            message_kind="schedule_rejected",
        )
        return True

    if not file_name:
        file_name = _filename_from_headers(headers or {})
    file_name = _safe_file_name(file_name)
    if not _is_schedule_file(file_name):
        return None
    return await _process_schedule_bytes(
        bytes(data),
        file_name,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
    )


async def _check_text_for_schedule_links(
    text: str,
    *,
    source_chat_id: int | str,
    source_message_id: int | str,
) -> bool | None:
    urls = re.findall(r"https?://\S+\.(?:pdf|xlsx)(?:\?\S*)?", text, re.IGNORECASE)
    found_schedule = False
    processing_succeeded = True
    for url in urls:
        file_name = _safe_file_name(unquote(urlparse(url).path.rsplit("/", 1)[-1]))
        if not _is_schedule_file(file_name):
            continue
        found_schedule = True
        data = await _download_url(url)
        if data:
            current_succeeded = await _process_schedule_bytes(
                data,
                file_name,
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
            )
            processing_succeeded = current_succeeded and processing_succeeded
        else:
            processing_succeeded = False
            await _notify_processing_error(
                error="Не удалось скачать файл расписания по ссылке",
                file_name=file_name,
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
            )
    return processing_succeeded if found_schedule else None


async def _download_url(url: str) -> bytes | None:
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, allow_redirects=True) as response:
            if response.status != 200:
                print(f"[MAX] Ошибка скачивания ссылки: HTTP {response.status}")
                return None
            declared_size = int(response.headers.get("Content-Length", 0) or 0)
            if declared_size > MAX_FILE_BYTES:
                print(f"[MAX] Файл по ссылке слишком большой: {declared_size} байт")
                return None

            data = bytearray()
            async for chunk in response.content.iter_chunked(128 * 1024):
                data.extend(chunk)
                if len(data) > MAX_FILE_BYTES:
                    print("[MAX] Файл по ссылке превысил допустимый размер")
                    return None
            return bytes(data)


async def _process_schedule_bytes(
    data: bytes,
    file_name: str,
    *,
    source_chat_id: int | str,
    source_message_id: int | str,
) -> bool:
    from max_bot.client import get_telegram_bot

    if not _has_valid_signature(data, file_name):
        print(f"[MAX] Файл '{file_name}' имеет неверный формат")
        await _notify_processing_error(
            error="Содержимое файла не соответствует его расширению",
            file_name=file_name,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
        )
        _record_schedule_source(
            source_chat_id,
            source_message_id,
            file_name,
            message_kind="schedule_rejected",
        )
        return True

    bot = get_telegram_bot()
    if bot is None:
        print("[MAX] Telegram бот недоступен; файл не обработан")
        return False

    try:
        await bot.send_message(
            ADMIN_ID,
            f"📩 Получен файл расписания из MAX:\n<b>{html.escape(file_name)}</b>",
            parse_mode="HTML",
        )
    except Exception as exc:
        print(f"[MAX] Не удалось уведомить администратора: {exc}")

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = os.path.join(temp_dir, file_name)
            with open(temp_path, "wb") as file:
                file.write(data)

            from handlers.schedule_broadcaster import process_and_broadcast

            success, result = await process_and_broadcast(
                file_path=temp_path,
                file_name=file_name,
                bot=bot,
                broadcast=True,
            )
    except Exception as exc:
        print(f"[MAX] Ошибка обработки файла: {exc}")
        traceback.print_exc()
        await _notify_processing_error(
            error=exc,
            file_name=file_name,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            bot=bot,
        )
        return False

    if not success:
        print(f"[MAX] Файл отклонён: {result}")
        await _notify_processing_error(
            error=result,
            file_name=file_name,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            bot=bot,
        )
        _record_schedule_source(
            source_chat_id,
            source_message_id,
            file_name,
            message_kind="schedule_rejected",
        )
        return True

    _record_schedule_source(source_chat_id, source_message_id, file_name)
    print(f"[MAX] Рассылка завершена: {result}")
    await _notify_admin(bot, f"Рассылка завершена: {result}")
    return True


def _record_schedule_source(
    source_chat_id: int | str,
    source_message_id: int | str,
    file_name: str,
    *,
    message_kind: str = "schedule_source",
) -> None:
    try:
        date_match = re.search(r"(\d{1,2}\.\d{1,2}\.\d{4})", file_name)
        upper_name = file_name.upper()
        record_message(
            platform="max",
            direction="incoming",
            chat_id=source_chat_id,
            message_id=source_message_id,
            message_kind=message_kind,
            schedule_date=date_match.group(1) if date_match else None,
            schedule_type=(
                "groups"
                if "ГРУПП" in upper_name
                else "teachers" if "ПРЕПОДАВАТЕЛИ" in upper_name else None
            ),
            file_name=file_name,
        )
    except Exception as exc:
        print(f"[MAX] Не удалось записать message_id источника: {exc}")


async def _notify_processing_error(
    *,
    error,
    source_chat_id: int | str,
    source_message_id: int | str,
    file_name: str | None = None,
    bot=None,
) -> None:
    if bot is None:
        from max_bot.client import get_telegram_bot

        bot = get_telegram_bot()
    if bot is None:
        print("[MAX] Telegram бот недоступен; уведомление об ошибке не отправлено")
        return

    from handlers.schedule_alerts import notify_schedule_error

    await notify_schedule_error(
        bot,
        error=error,
        source="MAX",
        file_name=file_name,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
    )


async def _notify_admin(bot, text: str) -> None:
    try:
        await bot.send_message(ADMIN_ID, text)
    except Exception:
        pass


def _safe_file_name(file_name: str) -> str:
    return file_name.replace("\\", "/").rsplit("/", 1)[-1].replace("\x00", "").strip()


def _filename_from_headers(headers: dict[str, str]) -> str:
    content_disposition = next(
        (value for key, value in headers.items() if key.lower() == "content-disposition"),
        "",
    )
    match = re.search(
        r"filename\*?=['\"]?(?:UTF-8'')?([^;\"']+)",
        content_disposition,
        re.IGNORECASE,
    )
    return _safe_file_name(unquote(match.group(1))) if match else ""


def _is_schedule_file(file_name: str) -> bool:
    extension = os.path.splitext(file_name)[1].lower()
    if extension not in (".pdf", ".xlsx"):
        return False
    upper_name = file_name.upper()
    return any(
        keyword in upper_name
        for keyword in ("ГРУПП", "ПРЕПОДАВАТЕЛИ", "РАСПИСАНИЕ")
    )


def _has_valid_signature(data: bytes, file_name: str) -> bool:
    extension = os.path.splitext(file_name)[1].lower()
    if extension == ".pdf":
        return data.startswith(b"%PDF-")
    if extension == ".xlsx":
        return data.startswith(b"PK\x03\x04")
    return False
