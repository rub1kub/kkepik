# max_bot/handlers.py
"""
Обработчики событий pyromax: мониторинг чатов MAX на файлы расписания,
скачивание и рассылка через Telegram.
Режим: ТОЛЬКО ЧТЕНИЕ. Бот ничего не отправляет в MAX.
"""

import os
import re
import tempfile
import traceback
import aiohttp

from pyromax.api.MaxApi import MaxApi
from pyromax.types.Message import Message
from pyromax.types.File import File

ADMIN_ID = 1084693264


# ── Monkey-patch: сохраняем сырые данные вложений до обработки Pydantic ──
# pyromax конвертирует raw dict → File(...), при этом поле fileName теряется,
# потому что оно не объявлено в Pydantic-модели File.
# Также: валидатор attaches_to_model падает если у attach нет ключа '_type'.

_orig_from_update = Message.from_update.__func__


@classmethod
def _from_update_with_raw(cls, update):
    # 1. Сохраняем raw данные ДО валидации Pydantic
    raw_attaches = []
    raw_message = {}
    try:
        raw_message = update.payload.get('message', {}) if update.payload else {}
        raw_attaches = list(raw_message.get('attaches', []))
    except Exception:
        pass

    # 2. Логируем полный raw payload для диагностики
    try:
        payload = update.payload or {}
        msg_data = payload.get('message', {})
        print(f"[MAX-DEBUG] Raw payload keys: {list(payload.keys())}")
        print(f"[MAX-DEBUG] Raw message keys: {list(msg_data.keys())}")
        if raw_attaches:
            for i, ra in enumerate(raw_attaches):
                if isinstance(ra, dict):
                    print(f"[MAX-DEBUG] raw_attach[{i}]: keys={list(ra.keys())}, "
                          f"_type={ra.get('_type', 'N/A')}, type={ra.get('type', 'N/A')}, "
                          f"fileName={ra.get('fileName', 'N/A')}")
                else:
                    print(f"[MAX-DEBUG] raw_attach[{i}]: {type(ra).__name__} = {repr(ra)[:200]}")
        else:
            print(f"[MAX-DEBUG] raw_attaches пусто, text={msg_data.get('text', '')[:80]}")

        # Проверяем link/body — MAX иногда хранит файлы там
        for extra_key in ('link', 'body', 'forward', 'payload'):
            if extra_key in msg_data and msg_data[extra_key]:
                print(f"[MAX-DEBUG] message['{extra_key}'] = {_safe_repr(msg_data[extra_key]) if isinstance(msg_data[extra_key], dict) else repr(msg_data[extra_key])[:200]}")
    except Exception as e:
        print(f"[MAX-DEBUG] Ошибка логирования: {e}")

    # 3. Фиксим attaches: добавляем _type если отсутствует (иначе валидатор pyromax падает)
    try:
        if update.payload and 'message' in update.payload:
            attaches = update.payload['message'].get('attaches', [])
            for i, a in enumerate(attaches):
                if isinstance(a, dict) and '_type' not in a:
                    inferred_type = a.get('type', 'UNKNOWN').upper()
                    print(f"[MAX-DEBUG] Attach[{i}] без _type, ставим '{inferred_type}'")
                    attaches[i] = dict(a)
                    attaches[i]['_type'] = inferred_type
    except Exception as e:
        print(f"[MAX-DEBUG] Ошибка фикса attaches: {e}")

    # 4. Вызываем оригинальный from_update
    try:
        msg = _orig_from_update(cls, update)
    except Exception as e:
        print(f"[MAX] from_update FAILED: {e}")
        traceback.print_exc()
        # Fallback: создаём Message с пустыми attaches
        try:
            payload = update.payload or {}
            message_data = dict(payload.get('message', {}))
            message_data['attaches'] = []  # убираем проблемные attaches
            msg = cls(
                **update.model_dump(),
                **payload,
                **message_data,
                max_api=update.max_api
            )
        except Exception as e2:
            print(f"[MAX] Fallback Message creation FAILED: {e2}")
            traceback.print_exc()
            raise

    # 5. Прикрепляем raw данные
    object.__setattr__(msg, '_raw_attaches', raw_attaches)
    object.__setattr__(msg, '_raw_message', raw_message)
    return msg


Message.from_update = _from_update_with_raw


def _extract_file_ids(attach, raw_data: dict = None) -> tuple[int, str]:
    """Извлекает (fileId, token) из вложения для запроса URL через API."""
    file_id = 0
    token = ''

    if raw_data and isinstance(raw_data, dict):
        file_id = raw_data.get('fileId', 0) or 0
        token = raw_data.get('token', '') or ''

    if isinstance(attach, File):
        if not file_id:
            file_id = getattr(attach, 'file_id', 0) or 0
        if not token:
            token = getattr(attach, 'file_token', '') or ''

    elif isinstance(attach, dict):
        if not file_id:
            file_id = attach.get('fileId', 0) or 0
        if not token:
            token = attach.get('token', '') or ''

    return int(file_id) if file_id else 0, str(token)


async def _get_file_url_from_api(max_api: MaxApi, file_id: int) -> str:
    """Получает URL скачивания файла через MAX API (опкод GET_FILE = 88)."""
    try:
        from pyromax.types.OpcodeEnum import Opcode
        print(f"[MAX] Запрашиваем URL для fileId={file_id}...")
        response, seq = await max_api.max_client.send_and_receive(
            opcode=Opcode.GET_FILE.value,
            payload={'fileId': file_id}
        )
        if response:
            for item in (response if isinstance(response, list) else [response]):
                if isinstance(item, dict):
                    payload = item.get('payload', {})
                    # URL может быть на разных уровнях
                    url = payload.get('url', '')
                    if url:
                        print(f"[MAX] Получен URL: {url[:80]}")
                        return url
                    # Проверяем вложенные структуры
                    for key in ('info', 'file', 'data'):
                        nested = payload.get(key)
                        if isinstance(nested, dict):
                            url = nested.get('url', '')
                            if url:
                                print(f"[MAX] Получен URL из {key}: {url[:80]}")
                                return url
                        elif isinstance(nested, list):
                            for n in nested:
                                if isinstance(n, dict):
                                    url = n.get('url', '')
                                    if url:
                                        print(f"[MAX] Получен URL из {key}[]: {url[:80]}")
                                        return url
                    # Логируем весь payload если URL не найден
                    print(f"[MAX] GET_FILE payload (URL не найден): {_safe_repr(payload)}")
        else:
            print(f"[MAX] GET_FILE пустой ответ")
    except Exception as e:
        print(f"[MAX] Ошибка GET_FILE: {e}")
        traceback.print_exc()
    return ''


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

        # Проверяем вложенные объекты (payload, file, etc.)
        for nested_key in ('payload', 'file', 'data'):
            nested = raw_data.get(nested_key)
            if isinstance(nested, dict):
                if not file_name:
                    for key in ('fileName', 'filename', 'file_name', 'name', 'title'):
                        val = nested.get(key)
                        if val and isinstance(val, str):
                            file_name = val
                            break
                if not file_url:
                    for key in ('url', 'fileUrl', 'file_url', 'downloadUrl'):
                        val = nested.get(key)
                        if val and isinstance(val, str):
                            file_url = val
                            break

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
                print(f"[MAX] Чат {chat_id} не в списке отслеживаемых: {MAX_WATCH_CHAT_IDS}")
                return

            # Обрабатываем вложения
            attaches = message.attaches or []
            raw_attaches = getattr(message, '_raw_attaches', [])
            raw_message = getattr(message, '_raw_message', {})

            print(f"[MAX] attaches={len(attaches)}, raw_attaches={len(raw_attaches)}")

            # Обрабатываем типизированные вложения
            for i, attach in enumerate(attaches):
                raw = raw_attaches[i] if i < len(raw_attaches) else {}
                await _process_attachment(attach, chat_id, raw_data=raw, max_api=max_api)

            # Если typed attaches пусто, но raw_attaches есть — обрабатываем raw напрямую
            if not attaches and raw_attaches:
                print(f"[MAX] Typed attaches пусто, обрабатываем {len(raw_attaches)} raw attaches")
                for raw in raw_attaches:
                    await _process_attachment(raw, chat_id, raw_data=raw, max_api=max_api)

            # Проверяем link/body/forward в raw_message — MAX иногда хранит файлы там
            for extra_key in ('link', 'body', 'forward'):
                extra = raw_message.get(extra_key)
                if isinstance(extra, dict):
                    await _process_attachment(extra, chat_id, raw_data=extra, max_api=max_api)
                elif isinstance(extra, list):
                    for item in extra:
                        if isinstance(item, dict):
                            await _process_attachment(item, chat_id, raw_data=item, max_api=max_api)

            # Также проверяем ссылки на файлы в тексте
            if text:
                await _check_text_for_schedule_links(text, chat_id)

        except Exception as e:
            print(f"[MAX] Ошибка обработки сообщения: {e}")
            traceback.print_exc()


async def _process_attachment(attach, chat_id: int, raw_data: dict = None, max_api: MaxApi = None):
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

        url_short = file_url[:80] if file_url else ''
        print(f"[MAX] Extracted: name={file_name!r}, url={url_short!r}")

        if not file_name:
            print(f"[MAX] Вложение без имени файла, пропускаем")
            return

        if not _is_schedule_file(file_name):
            print(f"[MAX] Файл '{file_name}' не похож на расписание, пропускаем")
            return

        print(f"[MAX] Обнаружен файл расписания: {file_name} (чат: {chat_id})")

        # Если URL нет — получаем через MAX API по fileId
        if not file_url and max_api:
            file_id, token = _extract_file_ids(attach, raw_data)
            if file_id:
                file_url = await _get_file_url_from_api(max_api, file_id)

        if not file_url:
            print(f"[MAX] Нет URL для скачивания файла '{file_name}'")
            return

        await _download_and_broadcast(file_url, file_name)

    except Exception as e:
        print(f"[MAX] Ошибка обработки вложения: {e}")
        traceback.print_exc()


def _safe_repr(d: dict, max_len: int = 500) -> str:
    """Безопасный repr для диагностики (обрезает длинные значения)."""
    parts = []
    for k, v in d.items():
        sv = repr(v)
        if len(sv) > 120:
            sv = sv[:117] + '...'
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
        print(f"[MAX] Ошибка при скачивании/рассылке: {e}")
        traceback.print_exc()
        await _notify_admin(bot, f"Ошибка при обработке {file_name}: {e}")


async def _notify_admin(bot, text: str):
    """Отправляет уведомление админу."""
    try:
        await bot.send_message(ADMIN_ID, text)
    except Exception:
        pass
