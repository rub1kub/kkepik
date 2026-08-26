"""Shared inline keyboard for schedule messages."""

from aiogram import types


WEBAPP_URL = "https://kkepik.rub1kub.ru/"


def create_schedule_keyboard(
    schedule_date: str,
    schedule_type: str = "groups",
) -> types.InlineKeyboardMarkup:
    open_button = types.InlineKeyboardButton(
        text="Открыть",
        style="primary",
        web_app=types.WebAppInfo(url=WEBAPP_URL),
    )
    download_button = types.InlineKeyboardButton(
        text="Скачать PDF",
        url=f"{WEBAPP_URL}api/schedule/download/{schedule_type}/{schedule_date}",
    )
    return types.InlineKeyboardMarkup(
        inline_keyboard=[[open_button, download_button]],
    )
