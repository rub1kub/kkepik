"""MAX connection lifecycle and Telegram bridge."""

from __future__ import annotations

import asyncio
import logging
import time


logger = logging.getLogger(__name__)

_max_api = None
_dispatcher = None
_polling_task: asyncio.Task | None = None
_supervisor_task: asyncio.Task | None = None
_telegram_bot = None
_accept_after_ms = int(time.time() * 1000) - 30_000


def set_telegram_bot(bot) -> None:
    global _telegram_bot
    _telegram_bot = bot


def get_telegram_bot():
    return _telegram_bot


async def _read_saved_token() -> str | None:
    from pyromax.utils import read_token

    # Keep compatibility with the token name used by pyromax 0.5.x.
    return await read_token("max_token")


async def _close_api(api) -> None:
    if api is None:
        return

    mapper = getattr(api, "mapper", None)
    manager = getattr(mapper, "_lifecycle_manager", None)
    lifecycle_task = getattr(manager, "_manage_lifecycle_task", None)
    if lifecycle_task and not lifecycle_task.done():
        lifecycle_task.cancel()
        try:
            await lifecycle_task
        except asyncio.CancelledError:
            pass

    if mapper is not None:
        try:
            await mapper.close()
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

    if _polling_task and not _polling_task.done():
        try:
            await asyncio.wait_for(_polling_task, timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    _max_api = None
    _dispatcher = None
    _polling_task = None
    print("[MAX] Клиент остановлен")


def _is_client_healthy() -> bool:
    if not _max_api or not _polling_task or _polling_task.done():
        return False

    connected = getattr(getattr(_max_api, "mapper", None), "_mapper_connected", None)
    return bool(connected and connected.is_set())


async def _supervisor_loop(retry_delay: int) -> None:
    while True:
        try:
            if not _is_client_healthy():
                print("[MAX] Supervisor: клиент неактивен, запускаю заново...")
                await stop_max_client()
                await start_max_client()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("MAX supervisor restart failed: %s", exc)
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
