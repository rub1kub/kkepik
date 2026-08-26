"""Read-only MAX handlers that forward fresh schedule files to Telegram."""

from __future__ import annotations

import os
import re
import tempfile
import traceback
from urllib.parse import unquote, urlparse

import aiohttp
from pyromax import MaxApi
from pyromax.models import FileAttachment, Message


ADMIN_ID = 1084693264
MAX_FILE_BYTES = 25 * 1024 * 1024
_seen_message_ids: dict[str, None] = {}


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
    _remember_message(message_key)

    try:
        print(
            f"[MAX] Новое сообщение в разрешённом чате {message.chat_id}; "
            f"вложений: {len(message.attaches or [])}"
        )
        for attachment in message.attaches or []:
            if isinstance(attachment, FileAttachment):
                await _process_file_attachment(attachment, max_api)

        if message.text:
            await _check_text_for_schedule_links(message.text)
    except Exception as exc:
        print(f"[MAX] Ошибка обработки сообщения: {exc}")
        traceback.print_exc()


def _remember_message(message_key: str) -> None:
    _seen_message_ids[message_key] = None
    if len(_seen_message_ids) > 5000:
        _seen_message_ids.pop(next(iter(_seen_message_ids)))


async def _process_file_attachment(attachment: FileAttachment, max_api: MaxApi) -> None:
    declared_size = int(getattr(attachment, "size", 0) or 0)
    if declared_size > MAX_FILE_BYTES:
        print(f"[MAX] Вложение слишком большое: {declared_size} байт")
        return

    file_name = _safe_file_name(
        getattr(attachment, "name", None)
        or getattr(attachment, "file_name", None)
        or ""
    )
    if file_name and not _is_schedule_file(file_name):
        return

    data, headers = await max_api.download_file(attachment)
    if not data:
        print("[MAX] Не удалось скачать файловое вложение")
        return
    if len(data) > MAX_FILE_BYTES:
        print(f"[MAX] Загруженный файл слишком большой: {len(data)} байт")
        return

    if not file_name:
        file_name = _filename_from_headers(headers or {})
    file_name = _safe_file_name(file_name)
    if not _is_schedule_file(file_name):
        return
    await _process_schedule_bytes(bytes(data), file_name)


async def _check_text_for_schedule_links(text: str) -> None:
    urls = re.findall(r"https?://\S+\.(?:pdf|xlsx)(?:\?\S*)?", text, re.IGNORECASE)
    for url in urls:
        file_name = _safe_file_name(unquote(urlparse(url).path.rsplit("/", 1)[-1]))
        if not _is_schedule_file(file_name):
            continue
        data = await _download_url(url)
        if data:
            await _process_schedule_bytes(data, file_name)


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


async def _process_schedule_bytes(data: bytes, file_name: str) -> None:
    from max_bot.client import get_telegram_bot

    if not _has_valid_signature(data, file_name):
        print(f"[MAX] Файл '{file_name}' имеет неверный формат")
        return

    bot = get_telegram_bot()
    if bot is None:
        print("[MAX] Telegram бот недоступен; файл не обработан")
        return

    try:
        await bot.send_message(
            ADMIN_ID,
            f"📩 Получен файл расписания из MAX:\n<b>{file_name}</b>",
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
        await _notify_admin(bot, f"Ошибка при обработке {file_name}: {exc}")
        return

    prefix = "Рассылка завершена" if success else "Файл отклонён"
    print(f"[MAX] {prefix}: {result}")
    await _notify_admin(bot, f"{prefix}: {result}")


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
