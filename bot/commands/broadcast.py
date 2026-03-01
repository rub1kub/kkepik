from aiogram import types
from aiogram.client.bot import Bot
import config
import sqlite3
import re

async def cmd_broadcast(message: types.Message, bot: Bot):
    """
    Команда для рассылки сообщения всем пользователям.
    Использование: /broadcast <текст сообщения>
    Если есть фото, оно будет отправлено вместе с текстом.
    Поддерживается отправка нескольких фотографий.
    """
    # Проверяем, является ли пользователь администратором
    if message.from_user.id not in config.ADMINS:
        await message.reply("🚫 У Вас нет прав для использования этой команды.")
        return

    # Получаем текст сообщения
    if message.photo:
        # Если есть фото, берем текст из caption
        text = message.caption.replace("/broadcast", "").strip() if message.caption else ""
    elif message.document:
        # Если есть документ, берем текст из caption
        text = message.caption.replace("/broadcast", "").strip() if message.caption else ""
    else:
        # Если фото и документа нет, берем текст из message.text
        text = message.text.replace("/broadcast", "").strip() if message.text else ""
    
    # Проверяем, есть ли текст сообщения, фото или документ
    if not text and not message.photo and not message.document:
        await message.reply("⚠️ Укажите текст сообщения, прикрепите фото или файл с подписью.")
        return

    # Извлекаем группы из текста (например, 109 гр., 117 гр.)
    group_pattern = re.compile(r"(\d{3}) ?гр")
    groups = set(group_pattern.findall(text))

    # Получаем список всех пользователей
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()
    if groups:
        # Если группы найдены, выбираем только их
        # Создаем условие для каждой группы
        conditions = []
        params = []
        for group in groups:
            conditions.append("name_or_group LIKE ?")
            params.append(f"{group}%")
        
        # Объединяем условия с OR
        query = f"SELECT user_id FROM users WHERE {' OR '.join(conditions)}"
        cur.execute(query, params)
    else:
        # Если групп нет, выбираем всех
        cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    conn.close()

    if not users:
        await message.reply("❌ Нет зарегистрированных пользователей.")
        return

    # Отправляем сообщение каждому пользователю
    success_count = 0
    failed_count = 0

    for (user_id,) in users:
        try:
            if message.photo:
                # Берем только самое большое фото (обычно это оригинал)
                photo = max(message.photo, key=lambda x: x.width * x.height)
                # Отправляем фото с текстом
                await bot.send_photo(
                    chat_id=user_id,
                    photo=photo.file_id,
                    caption=text
                )
            elif message.document:
                # Отправляем документ с подписью
                await bot.send_document(
                    chat_id=user_id,
                    document=message.document.file_id,
                    caption=text
                )
            else:
                # Если фото и документа нет, отправляем только текст
                await bot.send_message(
                    chat_id=user_id,
                    text=text
                )
            success_count += 1
        except Exception as e:
            print(f"Ошибка отправки пользователю {user_id}: {e}")
            failed_count += 1

    # Отправляем отчет администратору
    report = f"✅ Рассылка завершена:\n"
    report += f"✓ Успешно отправлено: {success_count}\n"
    report += f"✗ Ошибок отправки: {failed_count}"
    
    await message.reply(report) 
    
async def cmd_ermolov(message: types.Message, bot: Bot):
    """
    Команда для рассылки сообщения всем пользователям.
    Использование: /ermolov <текст сообщения>
    Если есть фото, оно будет отправлено вместе с текстом.
    Поддерживается отправка нескольких фотографий.
    """
    # Проверяем, является ли пользователь администратором
    if message.from_user.id not in config.ADMINS:
        await message.reply("🚫 У Вас нет прав для использования этой команды.")
        return

    # Получаем текст сообщения
    if message.photo:
        # Если есть фото, берем текст из caption
        text = message.caption.replace("/ermolov", "").strip() if message.caption else ""
    else:
        # Если фото нет, берем текст из message.text
        text = message.text.replace("/ermolov", "").strip() if message.text else ""
    
    # Проверяем, есть ли текст сообщения или фото
    if not text and not message.photo:
        await message.reply("⚠️ Укажите текст сообщения или прикрепите фото с подписью.")
        return

    user_id = 244266114

    if message.photo:
    # Берем только самое большое фото (обычно это оригинал)
        photo = max(message.photo, key=lambda x: x.width * x.height)
              
        # Отправляем фото с текстом
        await bot.send_photo(
                chat_id=user_id,
                photo=photo.file_id,
                caption=text
        )
    else:
        # Если фото нет, отправляем только текст
        await bot.send_message(
            chat_id=user_id,
            text=text
        )
    
    report = f"✅ Рассылка завершена:\n"
    await message.reply(report) 