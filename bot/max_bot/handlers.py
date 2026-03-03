# max_bot/handlers.py
"""
Обработчики событий pyromax: мониторинг чатов MAX на файлы расписания,
скачивание и рассылка через Telegram.
Режим: ТОЛЬКО ЧТЕНИЕ. Бот ничего не отправляет в MAX.
"""

import os
import re
import tempfile
import aiohttp

from pyromax.api.MaxApi import MaxApi
from pyromax.types.Message import Message
from pyromax.types.File import File

ADMIN_ID = 1084693264


# ── Monkey-patch: сохраняем сырые данные вложений до обработки Pydantic ──
# pyromax конвертирует raw dict → File(...), при этом поле fileName теряется,
# потому что оно не объявлено в Pydantic-модели File.
# Сохраняем raw attaches в атрибут _raw_attaches для использования в обработчике.

_orig_from_update = Message.from_update.__func__

@classmethod
def _from_update_with_raw(cls, update):
    msg = _orig_from_update(cls, update)
    try:
        raw_msg = update.payload.get('message', {})
        object.__setattr__(msg, '_raw_attaches', raw_msg.get('attaches', []))
    except Exception:
        object.__setattr__(msg, '_raw_attaches', [])
    return msg

Message.from_update = _from_update_with_raw


def _extract_file_info(attach, raw_data: dict = None) -> tuple[str, str]:
    """
    Извлекает (filename, url) из вложения, используя все доступные источники.
    Возвращает (file_name, file_url).
    """
    file_name = ''
    file_url = ''

    # Источник 1: raw_data dict (из monkey-patch — самый надёжный)
    if raw_data and isinstance(raw_data, dict):
        for key in ('fileName', 'filename', 'file_name', 'name', 'title'):
            if not file_name:
                val = raw_data.get(key)
                if val and isinstance(val, str):
                    file_name = val
        if not file_url:
            file_url = raw_data.get('url', '') or ''

    # Источник 2: типизированный File из pyromax
    if isinstance(attach, File):
        if not file_url:
            file_url = attach.url or ''
        if not file_name:
            file_name = getattr(attach, '_filename', '') or ''

    # Источник 3: raw dict (когда _type не распознан pyromax)
    elif isinstance(attach, dict):
        if not file_name:
            for key in ('fileName', 'filename', 'file_name', 'name', 'title'):
                val = attach.get(key)
                if val and isinstance(val, str):
                    file_name = val
                    break
        if not file_url:
            for key in ('url', 'fileUrl', 'file_url', 'downloadUrl'):
                val = attach.get(key)
                if val and isinstance(val, str):
                    file_url = val
                    break

    # Источник 4: произвольный объект с атрибутами (Photo, Video, etc.)
    elif hasattr(attach, '__dict__'):
        for attr in ('fileName', 'filename', 'file_name', 'name', 'title'):
            if not file_name:
                val = getattr(attach, attr, None)
                if val and isinstance(val, str):
                    file_name = val
        if not file_url:
            file_url = getattr(attach, 'url', '') or ''

    # Источник 5: извлечение из URL
    if not file_name and file_url:
        try:
            from urllib.parse import urlparse, unquote
            path_part = unquote(urlparse(file_url).path.split('/')[-1])
            if '.' in path_part:
                file_name = path_part
        except Exception:
            pass

    return file_name, file_url


def register_handlers(dispatcher):
    """Регистрирует обработчики событий pyromax."""

    @dispatcher.message()
    async def on_message(message: Message, max_api: MaxApi):
        """Обрабатывает входящие сообщения. Ищет файлы расписания."""
        try:
            chat_id = message.chat_id
            text = message.text or ''
            sender_id = message.sender

            print(f"[MAX] Сообщение в чате {chat_id} от user={sender_id}: {text[:100]}")

            # Проверяем, из отслеживаемого ли чата
            from max_bot.config import MAX_WATCH_CHAT_IDS
            if MAX_WATCH_CHAT_IDS and chat_id not in MAX_WATCH_CHAT_IDS:
                return

            # Обрабатываем вложения
            attaches = message.attaches or []
            raw_attaches = getattr(message, '_raw_attaches', [])

            if attaches:
                print(f"[MAX] Вложения ({len(attaches)}), raw_attaches={len(raw_attaches)}")

            for i, attach in enumerate(attaches):
                raw = raw_attaches[i] if i < len(raw_attaches) else {}
                await _process_attachment(attach, chat_id, raw_data=raw)

            # Также проверяем ссылки на файлы в тексте
            if text:
                await _check_text_for_schedule_links(text, chat_id)

        except Exception as e:
            import traceback
            print(f"[MAX] Ошибка обработки сообщения: {e}")
            traceback.print_exc()


async def _process_attachment(attach, chat_id: int, raw_data: dict = None):
    """Обрабатывает вложение — типизированный File, raw dict или другой тип."""
    try:
        attach_type = type(attach).__name__
        # Полная диагностика raw_data
        if raw_data and isinstance(raw_data, dict):
            print(f"[MAX] Attach type={attach_type}, raw keys={list(raw_data.keys())}, "
                  f"raw_data={_safe_repr(raw_data)}")
        else:
            print(f"[MAX] Attach type={attach_type}, raw_data={raw_data!r}")

        file_name, file_url = _extract_file_info(attach, raw_data)

        print(f"[MAX] Extracted: name={file_name!r}, url={file_url[:80]!r if file_url else ''!r}")

        if not file_name:
            print(f"[MAX] Вложение без имени файла, пропускаем")
            return

        if not _is_schedule_file(file_name):
            print(f"[MAX] Файл '{file_name}' не похож на расписание, пропускаем")
            return

        print(f"[MAX] Обнаружен файл расписания: {file_name} (чат: {chat_id})")

        if not file_url:
            print(f"[MAX] Нет URL для скачивания файла '{file_name}'")
            return

        await _download_and_broadcast(file_url, file_name)

    except Exception as e:
        import traceback
        print(f"[MAX] Ошибка обработки вложения: {e}")
        traceback.print_exc()


def _safe_repr(d: dict, max_len: int = 300) -> str:
    """Безопасный repr для диагностики (обрезает длинные значения)."""
    parts = []
    for k, v in d.items():
        sv = repr(v)
        if len(sv) > 80:
            sv = sv[:77] + '...'
        parts.append(f"{k}={sv}")
    result = '{' + ', '.join(parts) + '}'
    return result[:max_len]


async def _check_text_for_schedule_links(text: str, chat_id: int):
    """Проверяет текст на наличие ссылок на файлы расписания."""
    url_pattern = r'https?://\S+\.(?:pdf|xlsx)\b'
    urls = re.findall(url_pattern, text, re.IGNORECASE)

    for url in urls:
        file_name = url.split('/')[-1].split('?')[0]
        if _is_schedule_file(file_name):
            print(f"[MAX] Обнаружена ссылка на расписание: {file_name}")
            await _download_and_broadcast(url, file_name)


def _is_schedule_file(file_name: str) -> bool:
    """Проверяет, похоже ли имя файла на файл расписания."""
    if not file_name:
        return False
    ext = os.path.splitext(file_name)[1].lower()
    if ext not in ('.pdf', '.xlsx'):
        return False
    upper = file_name.upper()
    keywords = ('ГРУПП', 'ПРЕПОДАВАТЕЛИ', 'РАСПИСАНИЕ')
    return any(kw in upper for kw in keywords)


async def _download_and_broadcast(file_url: str, file_name: str):
    """Скачивает файл и запускает рассылку через Telegram."""
    from max_bot.client import get_telegram_bot

    bot = get_telegram_bot()
    if not bot:
        print("[MAX] Telegram бот не доступен, рассылка невозможна")
        return

    # Уведомляем админа о получении расписания из MAX
    try:
        await bot.send_message(
            ADMIN_ID,
            f"📩 Получен файл расписания из MAX:\n<b>{file_name}</b>\n\nНачинаю обработку и рассылку...",
            parse_mode="HTML",
        )
    except Exception as e:
        print(f"[MAX] Не удалось уведомить админа: {e}")

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = os.path.join(temp_dir, file_name)

            # Скачиваем файл
            print(f"[MAX] Скачиваем: {file_url}")
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as resp:
                    if resp.status != 200:
                        print(f"[MAX] Ошибка скачивания: HTTP {resp.status}")
                        await _notify_admin(bot, f"Ошибка скачивания {file_name}: HTTP {resp.status}")
                        return
                    data = await resp.read()

            with open(temp_path, 'wb') as f:
                f.write(data)

            print(f"[MAX] Скачано: {file_name} ({len(data)} байт)")

            # Обрабатываем и рассылаем
            from handlers.schedule_broadcaster import process_and_broadcast
            success, msg = await process_and_broadcast(
                file_path=temp_path,
                file_name=file_name,
                bot=bot,
            )

            if success:
                print(f"[MAX] Рассылка завершена: {msg}")
                await _notify_admin(bot, f"Рассылка завершена: {msg}")
            else:
                print(f"[MAX] Ошибка рассылки: {msg}")
                await _notify_admin(bot, f"Ошибка рассылки {file_name}: {msg}")

    except Exception as e:
        import traceback
        print(f"[MAX] Ошибка при скачивании/рассылке: {e}")
        traceback.print_exc()
        await _notify_admin(bot, f"Ошибка при обработке {file_name}: {e}")


async def _notify_admin(bot, text: str):
    """Отправляет уведомление админу."""
    try:
        await bot.send_message(ADMIN_ID, text)
    except Exception:
        pass
