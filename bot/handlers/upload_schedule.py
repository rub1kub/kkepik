# handlers/upload_schedule.py
"""
Обработка загруженного файла расписания через Telegram (доступ только админам).
Делегирует обработку и рассылку в schedule_broadcaster.
"""

import os
import tempfile
from aiogram import types
from aiogram.client.bot import Bot
import config
from handlers.schedule_broadcaster import process_and_broadcast


async def handle_document(message: types.Message, bot: Bot):
    """
    Обработка загруженного XLSX/PDF файла расписания.
    Доступ только админам из config.ADMINS.
    """
    user_id = message.from_user.id
    if user_id not in config.ADMINS:
        await message.reply("🚫 У Вас нет прав для загрузки.")
        return

    doc: types.Document = message.document
    file_name = doc.file_name or "file.xlsx"

    if not (file_name.lower().endswith(".xlsx") or file_name.lower().endswith(".pdf")):
        await message.reply("⚠️ Требуется файл .xlsx или .pdf.")
        return

    try:
        file_info = await bot.get_file(doc.file_id)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_file_path = os.path.join(temp_dir, file_name)
            await bot.download_file(file_info.file_path, temp_file_path)

            await message.reply("⚙️ Принято. Обработка...")

            # Рассылка через общий broadcaster
            async def log_fn(text):
                await message.reply(text)

            success, result_msg = await process_and_broadcast(
                file_path=temp_file_path,
                file_name=file_name,
                bot=bot,
                log_fn=log_fn,
            )

            if success:
                await message.reply(f"✅ {result_msg}")
            else:
                await message.reply(f"❌ {result_msg}")

    except Exception as e:
        await message.reply(f"❌ Ошибка при обработке файла: {str(e)}")
