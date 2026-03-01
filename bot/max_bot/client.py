# max_bot/client.py
"""
Pyromax клиент: подключение, Dispatcher и lifecycle.
Хранит ссылку на Telegram бот для рассылки.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

_max_api = None
_dispatcher = None
_task = None
_telegram_bot = None


def set_telegram_bot(bot):
    """Сохраняет ссылку на Telegram бот для использования в обработчиках MAX."""
    global _telegram_bot
    _telegram_bot = bot


def get_telegram_bot():
    """Возвращает экземпляр Telegram бота."""
    return _telegram_bot


async def start_max_client():
    """Инициализирует и запускает pyromax клиент с QR/token авторизацией."""
    global _max_api, _dispatcher, _task

    from max_bot.config import MAX_ENABLED

    if not MAX_ENABLED:
        print("[MAX] Интеграция отключена (MAX_ENABLED = False)")
        return

    try:
        from pyromax.api.MaxApi import MaxApi
        from pyromax.api.observer.Dispatcher import Dispatcher
    except ImportError:
        print("[MAX] Библиотека pyromax не установлена. pip install pyromax")
        return

    try:
        print("[MAX] Подключение к MAX messenger...")
        _max_api = await MaxApi()
        print(f"[MAX] Авторизован как: {_max_api.first_name} (id={_max_api.id})")
        print(f"[MAX] Чатов: {len(_max_api.chats)}")

        # Создаём Dispatcher и регистрируем обработчики
        _dispatcher = Dispatcher()
        from max_bot.handlers import register_handlers
        register_handlers(_dispatcher)

        # Запускаем polling в фоне с авто-реконнектом
        _task = asyncio.create_task(_run_polling())
        print("[MAX] Polling запущен")
    except Exception as e:
        print(f"[MAX] Ошибка при запуске: {e}")
        import traceback
        traceback.print_exc()


async def _run_polling():
    """Polling с авто-реконнектом."""
    try:
        await _max_api.reload_if_connection_broke(_dispatcher)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[MAX] Polling остановлен с ошибкой: {e}")
        import traceback
        traceback.print_exc()


async def stop_max_client():
    """Останавливает pyromax клиент."""
    global _max_api, _dispatcher, _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    if _max_api:
        try:
            await _max_api.detach()
        except Exception:
            pass
    _max_api = None
    _dispatcher = None
    _task = None
    print("[MAX] Клиент остановлен")


def get_max_api():
    """Возвращает текущий экземпляр MaxApi или None."""
    return _max_api
