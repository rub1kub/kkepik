"""Quick personal settings with entry into the Mini App constructor."""
from html import escape
from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from schedule_preferences import get_preferences, save_preferences, preset_preferences, PRESET_INFO
from schedules.schedule_keyboard import WEBAPP_URL
from tracked_groups import get_user_role
import config


def settings_panel(uid):
    p = get_preferences(uid)
    name = next((name for key, name, _ in PRESET_INFO if key == p.preset), "Мой шаблон")
    text = ("⚙️ <b>Настройки расписания</b>\n\n"
            f"Шаблон: <b>{escape(name)}</b>\n"
            f"Время пар: <b>{'показывать' if p.show_times else 'скрывать'}</b>\n\n"
            "В конструкторе можно переставить поля, изменить заголовок и выбрать формат доставки. "
            "Предпросмотр покажет ваше расписание до сохранения.")
    buttons = [[types.InlineKeyboardButton(text="🎨 Конструктор и предпросмотр", web_app=types.WebAppInfo(url=WEBAPP_URL + "settings"))]]
    buttons += [[types.InlineKeyboardButton(text=("✓ " if p.preset == key else "") + name, callback_data="settings:preset:" + key)
                 for key, name, _ in PRESET_INFO[index:index+2]] for index in (0, 2)]
    buttons.append([types.InlineKeyboardButton(text="🕒 " + ("Скрыть время" if p.show_times else "Показать время"), callback_data="settings:times")])
    buttons.append([types.InlineKeyboardButton(text="👁 Предпросмотр", callback_data="settings:preview")])
    return text, types.InlineKeyboardMarkup(inline_keyboard=buttons)


async def cmd_settings(message: types.Message, state=None):
    if message.chat.type != "private":
        await message.answer("Настройки доступны в личном чате с ботом: /settings")
        return
    if get_user_role(config.get_db_path(), message.from_user.id) is None:
        await message.answer("Сначала зарегистрируйтесь: /start")
        return
    if state is not None:
        await state.clear()
    text, keyboard = settings_panel(message.from_user.id)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


async def process_settings_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not callback.message or callback.message.chat.type != "private" or get_user_role(config.get_db_path(), uid) is None:
        await callback.answer("Откройте /settings в личном чате после регистрации", show_alert=True)
        return
    data = callback.data or ""
    if data == "settings:hide-preview":
        # Only messages generated as previews get this callback, never ordinary schedules.
        if callback.message.chat.id != uid or not getattr(callback.message, "reply_markup", None) or not any(
            button.callback_data == data
            for row in callback.message.reply_markup.inline_keyboard for button in row
        ):
            await callback.answer("Этот предпросмотр нельзя скрыть", show_alert=True)
            return
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            await callback.answer("Не удалось скрыть сообщение", show_alert=True)
            return
        await callback.answer("Предпросмотр скрыт")
        return
    p = get_preferences(uid)
    if data == "settings:preview":
        from message_settings_api import preview_payload
        from schedules.personal_delivery import send_personal_schedule
        import global_schedules as gs
        await callback.answer()
        preview = preview_payload(uid, p)
        image = gs.last_groups_crop_cache.get((preview["entity"] or "").removeprefix("group:").upper()) if preview["has_image"] else None
        prefix = "Пример оформления: актуальное расписание пока недоступно.\n\n" if preview["example"] else ""
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="Скрыть", style="danger", callback_data="settings:hide-preview")
        ]])
        await send_personal_schedule(callback.bot, callback.message.chat.id, prefix + preview["html"], image,
                                     keyboard, p, preview=True, notice=preview.get("photo_notice"))
        return
    if data == "settings:times":
        p.show_times = not p.show_times
        if p.show_times and not p.lesson_template and "time" not in sum(p.lesson_rows, []):
            row = next(row for row in p.lesson_rows if "subject" in row)
            row.insert(row.index("subject"), "time")
        p.preset = "custom"
    elif data.startswith("settings:preset:"):
        try:
            selected = preset_preferences(data.rsplit(":", 1)[1])
        except ValueError:
            await callback.answer("Шаблон не найден", show_alert=True)
            return
        selected.notifications_enabled = p.notifications_enabled
        selected.notify_updates = p.notify_updates
        selected.silent = p.silent
        p = selected
    else:
        await callback.answer()
        return
    save_preferences(uid, p)
    text, keyboard = settings_panel(uid)
    if text != callback.message.html_text or keyboard != callback.message.reply_markup:
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                raise
    await callback.answer("Настройки сохранены")
