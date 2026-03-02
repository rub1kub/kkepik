# main.py
import asyncio
import logging
import sqlite3
import os
import re
import multiprocessing
import uvicorn

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineQuery
from aiogram.types.input_file import FSInputFile
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext

import config
from commands.find import cmd_find
from commands.start import cmd_start, process_role_callback, process_name, process_class_teacher_callback, process_class_group
from commands.reset import cmd_reset
from commands.broadcast import cmd_broadcast
from commands.broadcast_teachers import cmd_broadcast_teachers
from commands.app import cmd_app
from handlers.inline_mode import inline_schedule
from handlers.upload_schedule import handle_document

from global_schedules import (
    last_groups_df, last_groups_date,
    last_teachers_df, last_teachers_date
)

logging.basicConfig(level=logging.INFO)
# Подавляем шумный DEBUG от сторонних библиотек
logging.getLogger('pdfminer').setLevel(logging.WARNING)
logging.getLogger('websockets').setLevel(logging.WARNING)
logging.getLogger('MaxClient').setLevel(logging.INFO)
logging.getLogger('MaxApi').setLevel(logging.INFO)

def run_api():
    """Запускает FastAPI сервер"""
    uvicorn.run("api:app", host="0.0.0.0", port=config.get_api_port())

async def main():
    # Запускаем API в отдельном процессе
    api_process = multiprocessing.Process(target=run_api)
    api_process.start()
    
    # Используем функцию get_bot_token() вместо прямого обращения к BOT_TOKEN
    bot = Bot(
        token=config.get_bot_token(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Удаляем webhook перед запуском бота
    await bot.delete_webhook(drop_pending_updates=True)
    
    dp = Dispatcher(storage=MemoryStorage())

    # Регистрируем команды
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_find, Command(commands=["find"]))
    dp.message.register(cmd_reset, Command(commands=["reset"]))
    dp.message.register(cmd_broadcast, Command(commands=["broadcast"]))
    dp.message.register(cmd_broadcast_teachers, Command(commands=["broadcast_teachers"]))
    dp.message.register(cmd_app, Command(commands=["app"]))

    # Регистрируем callback для выбора роли
    dp.callback_query.register(process_role_callback, lambda c: c.data in ["role_student", "role_teacher"])
    
    # Регистрируем callback для вопроса о классном руководстве
    dp.callback_query.register(process_class_teacher_callback, lambda c: c.data in ["is_class_teacher_yes", "is_class_teacher_no"])
    # Регистрируем обработчик ввода номера группы для классного руководителя
    dp.message.register(process_class_group, cmd_start.__globals__["RegistrationStates"].waiting_group)

    # Обработчик ввода номера группы или ФИО
    dp.message.register(process_name, cmd_start.__globals__["RegistrationStates"].waiting_name)

    # Обработчик загрузки файла расписания
    dp.message.register(handle_document, F.document)

    # Inline‑режим для поиска расписания
    dp.inline_query.register(inline_schedule)

    async def on_startup():
        bot_info = await bot.get_me()
        print(f"[OK] Bot started as @{bot_info.username} {'(TEST MODE)' if config.TEST_MODE else ''}")
        print(f"[OK] API running at http://0.0.0.0:{config.get_api_port()}")

        # Запускаем MAX клиент (передаём ссылку на Telegram бот)
        try:
            from max_bot.client import start_max_client, set_telegram_bot
            set_telegram_bot(bot)
            await start_max_client()
        except Exception as e:
            print(f"[MAX] Не удалось запустить: {e}")

        # Загружаем последние файлы расписания в кэш
        try:
            import global_schedules as gs
            gs.reload_cache()
        except Exception as e:
            print(f"[startup] Ошибка загрузки кэша: {e}")

    async def on_shutdown():
        print("Бот остановлен")
        try:
            from max_bot.client import stop_max_client
            await stop_max_client()
        except Exception:
            pass
        api_process.terminate()
        api_process.join()

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())