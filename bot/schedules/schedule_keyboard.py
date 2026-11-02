"""Shared inline keyboard for schedule messages."""

from aiogram import types
from urllib.parse import urlencode


WEBAPP_URL = "https://kkepik.rub1kub.ru/"


def create_schedule_keyboard(
    schedule_date: str,
    schedule_type: str = "groups",
    *, user_id: int | None = None, entity_type: str | None = None, entity_id: str | None = None,
) -> types.InlineKeyboardMarkup:
    params = {'source': 'bot'}
    if type(user_id) is int and 0 < user_id < 2**52:
        params['user_id'] = user_id
    if entity_type in ('group', 'teacher', 'audience') and entity_id:
        params.update(entity_type=entity_type, entity_id=entity_id)
    url = f"{WEBAPP_URL}api/schedule/download/{schedule_type}/{schedule_date}?" + urlencode(params)
    open_button = types.InlineKeyboardButton(
        text="Открыть",
        style="primary",
        web_app=types.WebAppInfo(url=WEBAPP_URL),
    )
    download_button = types.InlineKeyboardButton(
        text="Скачать PDF",
        url=url,
    )
    return types.InlineKeyboardMarkup(
        inline_keyboard=[[open_button, download_button]],
    )
