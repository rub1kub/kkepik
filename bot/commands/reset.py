# commands/reset.py
import sqlite3
import config
from aiogram import types
from aiogram.fsm.context import FSMContext

# Обработчик команды /reset
async def cmd_reset(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("⚠️ Все Ваши данные сброшены.\n\nПерезапустите бота: /start")