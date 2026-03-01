from aiogram import types
from aiogram.client.bot import Bot
import config
import sqlite3

async def cmd_broadcast_teachers(message: types.Message, bot: Bot):
    """
    Команда для рассылки сообщения только преподавателям.
    Использование: /broadcast_teachers <текст сообщения>
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
        text = message.caption.replace("/broadcast_teachers", "").strip() if message.caption else ""
    else:
        # Если фото нет, берем текст из message.text
        text = message.text.replace("/broadcast_teachers", "").strip() if message.text else ""
    
    # Проверяем, есть ли текст сообщения или фото
    if not text and not message.photo:
        await message.reply("⚠️ Укажите текст сообщения или прикрепите фото с подписью.")
        return

    # Получаем список всех преподавателей
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE role = 'Я преподаватель'")
    teachers = cur.fetchall()
    conn.close()

    if not teachers:
        await message.reply("❌ Нет зарегистрированных преподавателей.")
        return

    # Отправляем сообщение каждому преподавателю
    success_count = 0
    failed_count = 0

    for (user_id,) in teachers:
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
            else:
                # Если фото нет, отправляем только текст
                await bot.send_message(
                    chat_id=user_id,
                    text=text
                )
            success_count += 1
        except Exception as e:
            print(f"Ошибка отправки преподавателю {user_id}: {e}")
            failed_count += 1

    # Отправляем отчет администратору
    report = f"✅ Рассылка преподавателям завершена:\n"
    report += f"✓ Успешно отправлено: {success_count}\n"
    report += f"✗ Ошибок отправки: {failed_count}"
    
    await message.reply(report) 