"""Respect personal delivery format and Telegram message limits."""
from aiogram.types import BufferedInputFile
from html import escape, unescape
from html.parser import HTMLParser
from schedules.schedule_formatter import fit_photo_caption, telegram_text_length


def split_message(text, limit=4000):
    """Split even one long line; reopen formatting without breaking entities/emoji."""
    if limit < 2:
        raise ValueError("Message limit must accommodate an emoji")
    if telegram_text_length(text) <= limit:
        return [text]

    class Chunker(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.parts, self.chunks, self.tags, self.length = [], [], [], 0

        def flush(self):
            if not self.length:
                return
            self.chunks.append("".join(self.parts) + "".join(f"</{tag}>" for tag, _ in reversed(self.tags)))
            self.parts = [markup for _, markup in self.tags]
            self.length = 0

        def visible(self, markup, value):
            size = len(value.encode("utf-16-le")) // 2
            if self.length + size > limit:
                self.flush()
            self.parts.append(markup)
            self.length += size

        def handle_data(self, data):
            for char in data:
                self.visible(escape(char, quote=False), char)

        def handle_entityref(self, name):
            self.visible(f"&{name};", unescape(f"&{name};"))

        def handle_charref(self, name):
            self.visible(f"&#{name};", unescape(f"&#{name};"))

        def handle_starttag(self, tag, attrs):
            markup = self.get_starttag_text()
            self.parts.append(markup)
            self.tags.append((tag, markup))

        def handle_endtag(self, tag):
            self.parts.append(f"</{tag}>")
            if self.tags and self.tags[-1][0] == tag:
                self.tags.pop()

    parser = Chunker()
    parser.feed(text)
    parser.close()
    parser.flush()
    return parser.chunks


async def send_personal_schedule(bot, user_id, text, image, keyboard, preferences, *, preview=False, notice=None):
    options = {"disable_notification": preferences.silent}
    if image and preferences.delivery_mode != "text":
        photo = BufferedInputFile(image, filename="schedule.png")
        if preferences.delivery_mode == "photo":
            caption = "\n\n".join(value for value in (
                "‼️ РАСПИСАНИЕ ИЗМЕНИЛОСЬ" if text.startswith("‼️ РАСПИСАНИЕ ИЗМЕНИЛОСЬ") else None,
                notice,
            ) if value) or None
            await bot.send_photo(user_id, photo=photo, caption=caption, parse_mode="HTML", reply_markup=keyboard, **options)
            return
        caption = fit_photo_caption(text)
        if telegram_text_length(caption) <= 1024:
            await bot.send_photo(user_id, photo=photo, caption=caption, parse_mode="HTML", reply_markup=keyboard, **options)
            return
        await bot.send_photo(user_id, photo=photo, reply_markup=keyboard if preview else None, **options)
    chunks = split_message(text)
    for index, chunk in enumerate(chunks):
        await bot.send_message(user_id, text=chunk, parse_mode="HTML",
                               reply_markup=keyboard if preview or index == len(chunks) - 1 else None, **options)
