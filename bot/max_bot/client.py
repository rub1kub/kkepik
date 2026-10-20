"""MAX connection lifecycle and Telegram bridge."""

from __future__ import annotations

import asyncio
import logging
import re
import time


logger = logging.getLogger(__name__)

_max_api = None
_dispatcher = None
_polling_task: asyncio.Task | None = None
_supervisor_task: asyncio.Task | None = None
_telegram_bot = None
_accept_after_ms = int(time.time() * 1000) - 30_000
_history_lookback_ms = 72 * 60 * 60 * 1000
_history_limit = 100
_history_timeout_seconds = 30
_client_close_timeout_seconds = 5


def set_telegram_bot(bot) -> None:
    global _telegram_bot
    _telegram_bot = bot


def get_telegram_bot():
    return _telegram_bot


async def _read_saved_token() -> str | None:
    from pyromax.utils import read_token

    # Keep compatibility with the token name used by pyromax 0.5.x.
    return await read_token("max_token")


async def _cancel_client_task(task: asyncio.Task | None) -> None:
    """Bound shutdown even when pyromax swallows the first cancellation."""
    if task is None:
        return

    for _ in range(2):
        if not task.done():
            task.cancel()
        # wait_for waits for cancellation to finish and can itself hang when
        # the library suppresses CancelledError. wait has a hard time limit.
        done, _ = await asyncio.wait({task}, timeout=_client_close_timeout_seconds)
        if done:
            if not task.cancelled():
                try:
                    task.result()
                except Exception:
                    logger.exception("MAX task failed while stopping: %s", task.get_name())
            return
        logger.warning("MAX task did not stop; cancelling again: %s", task.get_name())

    logger.error("MAX task exceeded shutdown timeout: %s", task.get_name())
    task.cancel()


async def _close_api(api) -> None:
    if api is None:
        return

    mapper = getattr(api, "mapper", None)
    manager = getattr(mapper, "_lifecycle_manager", None)
    lifecycle_task = getattr(manager, "_manage_lifecycle_task", None)
    await _cancel_client_task(lifecycle_task)

    if mapper is not None:
        close_task = asyncio.create_task(mapper.close(), name="max-connection-close")
        try:
            done, _ = await asyncio.wait(
                {close_task}, timeout=_client_close_timeout_seconds
            )
            if done:
                close_task.result()
            else:
                logger.warning("MAX connection close timed out")
                await _cancel_client_task(close_task)
        except asyncio.CancelledError:
            close_task.cancel()
            raise
        except Exception:
            logger.exception("MAX connection close failed")


async def start_max_client() -> None:
    """Connect to MAX and start listening only for new incoming updates."""
    global _max_api, _dispatcher, _polling_task

    from max_bot.config import MAX_ENABLED, MAX_WATCH_CHAT_IDS

    if not MAX_ENABLED:
        print("[MAX] Интеграция отключена (MAX_ENABLED = False)")
        return
    if not MAX_WATCH_CHAT_IDS:
        raise RuntimeError("MAX_WATCH_CHAT_IDS is empty; refusing to monitor all chats")
    if _is_client_healthy():
        return

    from pyromax import Dispatcher, MaxApi

    token = await _read_saved_token()
    if not token:
        raise RuntimeError("MAX token is missing in tokens.json")

    print(f"[MAX] Подключение; разрешённые чаты: {MAX_WATCH_CHAT_IDS}")
    api = await asyncio.wait_for(MaxApi(token=token), timeout=30)
    dispatcher = Dispatcher()

    from max_bot.handlers import register_handlers

    register_handlers(dispatcher, accept_after_ms=_accept_after_ms)
    _max_api = api
    _dispatcher = dispatcher
    _polling_task = asyncio.create_task(
        dispatcher.start_polling(max_api=api),
        name="max-polling",
    )
    print(f"[MAX] Авторизован: id={api.id}; чатов: {len(api.chats or [])}")
    print("[MAX] Polling запущен в режиме только чтения")


async def stop_max_client() -> None:
    global _max_api, _dispatcher, _polling_task

    if _polling_task and not _polling_task.done():
        _polling_task.cancel()

    # Stop the reconnect lifecycle before waiting for polling to unwind.
    await _close_api(_max_api)

    await _cancel_client_task(_polling_task)

    _max_api = None
    _dispatcher = None
    _polling_task = None
    print("[MAX] Клиент остановлен")


def _is_client_healthy() -> bool:
    if not _max_api or not _polling_task or _polling_task.done():
        return False

    connected = getattr(getattr(_max_api, "mapper", None), "_mapper_connected", None)
    return bool(connected and connected.is_set())


def _may_contain_schedule(message) -> bool:
    from pyromax.models import FileAttachment

    from max_bot.handlers import _is_schedule_file

    for attachment in message.attaches or []:
        if not isinstance(attachment, FileAttachment):
            continue
        file_name = (
            getattr(attachment, "name", None)
            or getattr(attachment, "file_name", None)
            or ""
        )
        if not file_name or _is_schedule_file(file_name):
            return True

    return bool(
        message.text
        and re.search(r"https?://\S+\.(?:pdf|xlsx)(?:\?\S*)?", message.text, re.I)
    )


async def _catch_up_history(api, *, now_ms: int | None = None) -> int:
    """Process recent schedule messages missed by the live websocket."""
    from max_bot.config import MAX_WATCH_CHAT_IDS
    from max_bot.handlers import _handle_message
    from message_registry import is_message_recorded

    current_ms = now_ms or int(time.time() * 1000)
    accept_after_ms = current_ms - _history_lookback_ms
    recovered = 0

    for chat_id in MAX_WATCH_CHAT_IDS:
        messages = await asyncio.wait_for(
            api.get_chat_history(
                chat_id,
                backward=_history_limit,
                from_time=current_ms,
                get_messages=True,
            ),
            timeout=_history_timeout_seconds,
        )
        recent_messages = sorted(
            (
                message
                for message in messages or []
                if (not message.time or message.time >= accept_after_ms)
                and _may_contain_schedule(message)
            ),
            key=lambda message: (message.time or 0, str(message.message_id)),
        )
        for message in recent_messages:
            if is_message_recorded(
                "max",
                "incoming",
                message.chat_id,
                message.message_id,
            ):
                continue
            await _handle_message(
                message,
                api,
                accept_after_ms=accept_after_ms,
            )
            recovered += 1

    if recovered:
        print(f"[MAX] История: проверено пропущенных сообщений: {recovered}")
    return recovered


async def _supervisor_loop(retry_delay: int) -> None:
    while True:
        try:
            if not _is_client_healthy():
                print("[MAX] Supervisor: клиент неактивен, запускаю заново...")
                await stop_max_client()
                await start_max_client()
            if _max_api is not None:
                await _catch_up_history(_max_api)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("MAX supervisor check failed: %s", exc)
            await stop_max_client()
        await asyncio.sleep(retry_delay)


async def start_max_supervisor(retry_delay: int = 30) -> None:
    global _supervisor_task

    from max_bot.config import MAX_ENABLED

    if not MAX_ENABLED:
        print("[MAX] Интеграция отключена (MAX_ENABLED = False)")
        return
    if _supervisor_task and not _supervisor_task.done():
        return

    _supervisor_task = asyncio.create_task(
        _supervisor_loop(retry_delay),
        name="max-supervisor",
    )


async def stop_max_supervisor() -> None:
    global _supervisor_task

    if _supervisor_task and not _supervisor_task.done():
        _supervisor_task.cancel()
        try:
            await _supervisor_task
        except asyncio.CancelledError:
            pass
    _supervisor_task = None
    await stop_max_client()


def get_max_api():
    return _max_api
