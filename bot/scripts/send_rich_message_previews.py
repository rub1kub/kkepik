#!/usr/bin/env python3
"""Send owner-only previews of the regular media schedule layout."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram import Bot
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)

from config import get_bot_token
from schedules.pdf_crop import crop_group_screenshots


OWNER_CHAT_ID = 1084693264
WEBAPP_URL = "https://kkepik.rub1kub.ru/"
MOOD = '<tg-emoji emoji-id="5319066444883828666">😎</tg-emoji>'


@dataclass(frozen=True)
class Preview:
    caption: str
    crop: bytes
    keyboard: InlineKeyboardMarkup


def load_crop(pdf_name: str, group: str) -> bytes:
    path = Path("/root/kkepik_bot/data") / pdf_name
    crops = crop_group_screenshots(str(path))
    crop = crops.get(group.upper())
    if not crop:
        raise RuntimeError(f"No PDF crop for {group} in {path}")
    return crop


def keyboard(date: str, *, vertical: bool = False) -> InlineKeyboardMarkup:
    open_button = InlineKeyboardButton(
        text="Открыть",
        style="primary",
        web_app=WebAppInfo(url=WEBAPP_URL),
    )
    pdf_button = InlineKeyboardButton(
        text="Скачать PDF",
        url=f"{WEBAPP_URL}api/schedule/download/groups/{date}",
    )
    rows = [[open_button], [pdf_button]] if vertical else [[open_button, pdf_button]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def standard_caption(*, emphasize: bool = False) -> str:
    teacher_1 = "<b>Каркавин Д. О.</b>" if emphasize else "Каркавин Д. О."
    teacher_2 = "<b>Ломанов А. Е.</b>" if emphasize else "Ломанов А. Е."
    room_84 = "<b>84</b>" if emphasize else "84"
    room_121 = "<b>121</b>" if emphasize else "121"
    return (
        f"{MOOD} Расписание на <b>10.06.2026</b>\n\n"
        "Группа <b>103-Д9-2ИНС</b>:\n\n"
        f"▪️1 пара – Основы алгоритмизации и программирования – {teacher_1} – {room_84}\n"
        f"▪️2 пара – Архитектура аппаратных средств – {teacher_2} – {room_121}\n"
        f"▪️3 пара – Основы алгоритмизации и программирования – {teacher_1} – {room_84}"
    )


def compact_caption() -> str:
    return (
        f"{MOOD} Расписание на <b>10.06.2026</b>\n\n"
        "Группа <b>103-Д9-2ИНС</b>:\n\n"
        "▪️<b>1 пара</b> – Основы алгоритмизации и программирования\n"
        "   <b>Каркавин Д. О. · аудитория 84</b>\n\n"
        "▪️<b>2 пара</b> – Архитектура аппаратных средств\n"
        "   <b>Ломанов А. Е. · аудитория 121</b>\n\n"
        "▪️<b>3 пара</b> – Основы алгоритмизации и программирования\n"
        "   <b>Каркавин Д. О. · аудитория 84</b>"
    )


def saturday_caption() -> str:
    return (
        f"{MOOD} Расписание на <b>06.06.2026</b>\n\n"
        "Группа <b>103-Д9-2ИНС</b>:\n\n"
        "▪️1 пара · <i>8:45–10:00</i> – Иностранный язык в профессиональной деятельности "
        "– <b>Степанова О. В. / Борисова Е. В.</b> – <b>61, 108</b>\n"
        "▪️2 пара · <i>10:10–11:25</i> – Информационные технологии "
        "– <b>Сидоренко О. М.</b> – <b>68</b>"
    )


def first_month_caption() -> str:
    return (
        f"{MOOD} Расписание на <b>02.09.2026</b>\n\n"
        "Группа <b>122-Д9-1КСК</b>:\n\n"
        "▪️1 пара · <i>8:45–10:05</i> – Нет\n"
        "▪️2 пара · <i>10:25–11:45</i> – <b>Информатика</b> – Шушунов В. В. – <b>93</b>\n"
        "▪️3 пара · <i>12:05–13:25</i> – <b>История</b> – Чернобай Л. Ю. – <b>38</b>\n"
        "▪️4 пара · <i>13:35–14:55</i> – <b>История</b> – Чернобай Л. Ю. – <b>38</b>"
    )


def build_previews() -> list[Preview]:
    weekday_crop = load_crop("Расписание на 10.06.2026_ГРУППЫ.pdf", "103-Д9-2ИНС")
    saturday_crop = load_crop("Расписание на 06.06.2026_ГРУППЫ.pdf", "103-Д9-2ИНС")
    first_year_crop = load_crop("Расписание на 30.05.2026_ГРУППЫ.pdf", "122-Д9-1КСК")

    return [
        Preview(standard_caption(), weekday_crop, keyboard("10.06.2026", vertical=True)),
        Preview(standard_caption(emphasize=True), weekday_crop, keyboard("10.06.2026")),
        Preview(compact_caption(), weekday_crop, keyboard("10.06.2026")),
        Preview(saturday_caption(), saturday_crop, keyboard("06.06.2026")),
        Preview(first_month_caption(), first_year_crop, keyboard("30.05.2026")),
    ]


async def send_previews(chat_id: int) -> None:
    if chat_id != OWNER_CHAT_ID:
        raise SystemExit(f"Refusing to send: only {OWNER_CHAT_ID} is allowed")

    bot = Bot(get_bot_token())
    try:
        chat = await bot.get_chat(chat_id)
        if chat.id != OWNER_CHAT_ID or chat.type != "private":
            raise SystemExit("Refusing to send: target is not the owner's private chat")

        for number, preview in enumerate(build_previews(), 1):
            message = await bot.send_photo(
                chat_id=OWNER_CHAT_ID,
                photo=BufferedInputFile(preview.crop, filename="schedule.png"),
                caption=preview.caption,
                parse_mode="HTML",
                reply_markup=preview.keyboard,
                disable_notification=True,
                protect_content=True,
            )
            print(f"sent preview={number} message_id={message.message_id} chat_id={message.chat.id}")
            await asyncio.sleep(0.35)
    finally:
        await bot.session.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true", help="send previews instead of validating only")
    parser.add_argument("--chat-id", type=int, default=OWNER_CHAT_ID)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    previews = build_previews()
    for preview in previews:
        if len(preview.caption) > 1024:
            raise RuntimeError("Photo caption exceeds Telegram's 1024-character limit")
    print(f"validated {len(previews)} media previews; allowed_chat_id={OWNER_CHAT_ID}")
    if args.send:
        asyncio.run(send_previews(args.chat_id))


if __name__ == "__main__":
    main()
