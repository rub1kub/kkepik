from aiogram import types
from aiogram.filters import Command

async def cmd_app(message: types.Message):
    """Отправляет сообщение с кнопкой для открытия мини-приложения с играми"""
    webapp_url = "https://kkepik.ru/"
    
    # Создаем инлайн кнопку для открытия веб-приложения
    webapp_button = types.InlineKeyboardButton(
        text="📱 Открыть приложение",
        web_app=types.WebAppInfo(url=webapp_url)
    )
    
    # Создаем клавиатуру с кнопкой
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[webapp_button]])
    
    # Отправляем сообщение с кнопкой
    await message.answer(
        "📱 KKEPIK APP!\n\n"
        "Нажмите на кнопку ниже, чтобы продолжить!",
        reply_markup=keyboard
    ) 