import hashlib
import hmac
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

from fastapi import HTTPException, Response
from pydantic import ValidationError

import config
import global_schedules as gs
import message_settings_api as api
from schedule_preferences import (SchedulePreferences, get_preferences, get_all_preferences,
                                  init_preferences_table, preset_preferences, save_preferences, wants_broadcast)
from schedules.message_constructor import render_schedule_message, personal_subject_aliases
from schedules.personal_delivery import send_personal_schedule, split_message
from schedules.schedule_dates import current_college_date
from schedules.schedule_formatter import telegram_text_length
from tracked_groups import migrate_legacy_groups


def signed_data(uid=42, auth_date=None, token="test-only-token"):
    values = {"user": json.dumps({"id": uid, "first_name": "Test"}),
              "auth_date": str(int(time.time()) if auth_date is None else auth_date)}
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, "\n".join(f"{key}={value}" for key, value in sorted(values.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


LINES = ["▪️1 пара – Нет", "▪️2 пара – Математика – <b>Иванова И.И.</b> – 48",
         "▪️3 пара – История – Петров А.В. – 35"]


class RendererTest(unittest.TestCase):
    def render(self, p=None, lines=None, date="06.10.2026"):
        return render_schedule_message(lines or LINES, "150-Д9-1ИНС", date, preferences=p)

    def test_all_presets_are_valid_and_differ(self):
        messages = [self.render(preset_preferences(name)) for name in ("full", "compact", "time_focus", "minimal")]
        self.assertEqual(len(set(messages)), 4)
        self.assertIn("8:45–10:05", messages[0])
        self.assertNotIn("Иванова", messages[1])
        self.assertIn("Начало занятий: <b>10:25</b> · до <b>13:25</b>", messages[2])
        self.assertNotIn("10:25", messages[3])
        self.assertNotIn("Иванова", messages[3])
        self.assertNotIn("1.", messages[3])

    def test_times_are_not_month_or_course_dependent(self):
        for group in ("150-Д9-1ИНС", "103-Д9-3ИНС"):
            self.assertIn("10:25–11:45", render_schedule_message(LINES, group, "06.10.2026"))

    def test_saturday_times_and_empty_pairs(self):
        result = self.render(SchedulePreferences(show_free_pairs=False), date="10.10.2026")
        self.assertIn("10:10–11:25", result)
        self.assertNotIn("1 пара", result)

    def test_hidden_fields_and_custom_order(self):
        p = SchedulePreferences(show_times=False, show_teachers=False, show_rooms=False,
            lesson_rows=[["subject", "pair"]], blocks=["footer", "lessons", "header", "group", "bounds"], footer_text="Доброе утро")
        result = self.render(p)
        self.assertTrue(result.startswith("Доброе утро"))
        self.assertIn("Математика</b> · <b>▪️2 пара", result)
        for excluded in ("10:25", "Иванова", "48"):
            self.assertNotIn(excluded, result)

    def test_templates_and_source_data_are_escaped(self):
        p = SchedulePreferences(header_template="<script>{group}</script> {date}",
            footer_text="<b>мой текст</b> & ещё", lesson_template="{subject}\n{time} | {room}")
        result = self.render(p, ["▪️2 пара – <img src=x onerror=alert(1)> – Иванова И.И. – 48"])
        self.assertNotIn("<script>", result)
        self.assertNotIn("<img", result)
        self.assertIn("&lt;b&gt;мой текст&lt;/b&gt; &amp; ещё", result)

    def test_teacher_schedule_is_rendered_with_groups_and_rooms(self):
        result = render_schedule_message(["▪️2 пара – 150-Д9-1ИНС – Математика – ауд. 48"],
            "Иванова И.И.", "06.10.2026", teacher=True)
        self.assertIn("Математика", result)
        self.assertIn("Группа 150-Д9-1ИНС", result)
        self.assertIn("аудитория 48", result)
        self.assertIn("Преподаватель", result)

    def test_day_bounds_ignore_free_periods_and_input_order(self):
        p = preset_preferences("time_focus")
        result = self.render(p, [LINES[2], LINES[0], LINES[1]])
        self.assertIn("Начало занятий: <b>10:25</b> · до <b>13:25</b>", result)

    def test_alias_scope_and_source_not_modified(self):
        self.assertIsNone(personal_subject_aliases(config.ROLE_TEACHER, "103-Д9-3ИНС"))
        self.assertIsNone(personal_subject_aliases(config.ROLE_STUDENT, "104-Д9-3ИНС"))
        aliases = personal_subject_aliases(config.ROLE_STUDENT, "103-Д9-3ИНС")
        self.assertIn("Торнадо", aliases["Тестирование информационных систем"])
        before = list(LINES)
        self.render(preset_preferences("compact"))
        self.assertEqual(LINES, before)

    def test_empty_day_is_explicit(self):
        result = self.render(SchedulePreferences(show_free_pairs=False), ["▪️1 пара – Нет"])
        self.assertIn("Занятий нет.", result)

    def test_legacy_teacher_layout_and_combined_heading_are_not_lost(self):
        result = render_schedule_message(["Совмещенные пары:", "▪️2 пара – Математика – 48"],
            "Иванова И.И.", "06.10.2026", teacher=True)
        self.assertIn("Совмещенные пары:", result)
        self.assertIn("Математика", result)
        self.assertIn("аудитория 48", result)

    def test_notification_policy_distinguishes_new_and_updated_schedule(self):
        self.assertTrue(wants_broadcast(SchedulePreferences(), is_update=True))
        for update in (False, True):
            self.assertFalse(wants_broadcast(SchedulePreferences(notifications_enabled=False), is_update=update))
        p = SchedulePreferences(notify_updates=False)
        self.assertTrue(wants_broadcast(p, is_update=False))
        self.assertFalse(wants_broadcast(p, is_update=True))

    def test_invalid_preferences_rejected(self):
        for change in ({"user_id": 99}, {"show_times": "false"}, {"delivery_mode": "evil"},
            {"header_template": "{user.password}"}, {"header_template": "{date!r}"},
            {"header_template": "{date:1000000}"}, {"header_template": "{"},
            {"lesson_template": "{pair}"}, {"lesson_rows": [["subject", "subject"]]},
            {"lesson_rows": [["time"]]}, {"lesson_rows": [[]]},
            {"blocks": ["header"] * 5}, {"footer_text": "x" * 241}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                SchedulePreferences(**change)


class PersistenceAndApiTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = str(Path(self.directory.name) / "fixture.sqlite")
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, role TEXT, name_or_group TEXT, is_class_teacher INTEGER, class_group TEXT)")
            conn.executemany("INSERT INTO users VALUES(?,?,?,?,?)", [
                (42, config.ROLE_STUDENT, "150-Д9-1ИНС", 0, None),
                (43, config.ROLE_STUDENT, "151-Д9-1ИНС", 0, None),
                (44, config.ROLE_TEACHER, "Иванова И.И.", 0, None)])
            migrate_legacy_groups(conn)
            init_preferences_table(conn)
        self.addCleanup(patch.stopall)
        patch.object(config, "get_db_path", return_value=self.db).start()
        patch.object(config, "get_bot_token", return_value="test-only-token").start()
        patch.object(gs, "last_groups_df", object()).start()
        patch.object(gs, "last_groups_date", current_college_date().strftime("%d.%m.%Y")).start()
        patch.object(gs, "last_groups_crop_cache", {"150-Д9-1ИНС": b"png-fixture"}).start()
        patch.object(gs, "last_teachers_df", None).start()
        patch.object(api.group_schedule, "get_schedule_for_group", return_value=list(LINES)).start()

    def test_roundtrip_is_personal_and_defaults_have_time(self):
        self.assertTrue(get_preferences(42).show_times)
        preferences = preset_preferences("minimal")
        preferences.footer_text = "Мой текст"
        save_preferences(42, preferences)
        self.assertEqual(get_preferences(42), preferences)
        self.assertEqual(get_preferences(43), SchedulePreferences())
        self.assertEqual(get_all_preferences(), {42: preferences})

    def test_save_revalidates_and_requires_registered_user(self):
        with self.assertRaises(ValueError):
            save_preferences(999, SchedulePreferences())
        p = SchedulePreferences()
        p.delivery_mode = "not-a-mode"
        with self.assertRaises(ValidationError):
            save_preferences(42, p)

    def test_invalid_stored_data_falls_back_safely(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO schedule_preferences(user_id,preferences) VALUES(42,?)", ("{bad-json",))
        self.assertEqual(get_preferences(42), SchedulePreferences())

    def test_signature_controls_identity(self):
        response = Response()
        self.assertEqual(api.authenticated_id(response, signed_data()), 42)
        self.assertIn("no-store", response.headers["cache-control"])

    def test_forged_expired_duplicate_and_invalid_id_auth_rejected(self):
        for data in ("", signed_data(token="wrong"), signed_data(auth_date=int(time.time()) - 86401),
            signed_data(auth_date=int(time.time()) + 100), signed_data() + "&user=%7B%22id%22%3A43%7D",
            signed_data(uid=True), signed_data(uid="42"), signed_data(uid=-42), "x=" + "a" * 20000):
            with self.subTest(data=data[:30]), self.assertRaises(HTTPException) as raised:
                api.authenticated_id(Response(), data)
            self.assertEqual(raised.exception.status_code, 401)

    def test_preview_never_saves_and_refuses_foreign_entities(self):
        before = get_all_preferences()
        result = api.preview_payload(42, preset_preferences("minimal"))
        self.assertFalse(result["example"])
        self.assertEqual(result["entity"], "group:150-Д9-1ИНС")
        self.assertNotIn("10:25", result["html"])
        self.assertEqual(get_all_preferences(), before)
        for action in (lambda: api.preview_payload(42, SchedulePreferences(), "group:151-Д9-1ИНС"),
                       lambda: api.preview_image("group:151-Д9-1ИНС", 42)):
            with self.assertRaises(HTTPException) as raised:
                action()
            self.assertEqual(raised.exception.status_code, 403)

    def test_save_route_and_read_return_four_presets(self):
        preferences = preset_preferences("compact")
        self.assertTrue(api.write_settings(preferences, 42)["saved"])
        result = api.read_settings(42)
        self.assertEqual(len(result["presets"]), 4)
        self.assertEqual(result["preferences"], preferences.model_dump())
        self.assertEqual(get_preferences(43), SchedulePreferences())

    def test_missing_schedule_is_labelled_as_example(self):
        with patch.object(api.group_schedule, "get_schedule_for_group", return_value=None):
            result = api.preview_payload(42, SchedulePreferences())
        self.assertTrue(result["example"])
        self.assertFalse(result["has_image"])

    def test_empty_schedule_is_not_an_example(self):
        with patch.object(api.group_schedule, "get_schedule_for_group", return_value=[]):
            result = api.preview_payload(42, SchedulePreferences(show_free_pairs=False))
        self.assertFalse(result["example"])
        self.assertIn("Занятий нет.", result["html"])

    def test_image_response_is_private_and_requires_current_date(self):
        response = api.preview_image("group:150-Д9-1ИНС", 42)
        self.assertEqual(response.body, b"png-fixture")
        self.assertIn("no-store", response.headers["cache-control"])
        with patch.object(gs, "last_groups_date", "01.01.2020"), self.assertRaises(HTTPException):
            api.preview_image("group:150-Д9-1ИНС", 42)

    def test_unregistered_user_is_not_found(self):
        with self.assertRaises(HTTPException) as raised:
            api.read_settings(999)
        self.assertEqual(raised.exception.status_code, 404)


class DeliveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_text_photo_and_photo_text_modes(self):
        for mode in ("text", "photo", "photo_text"):
            with self.subTest(mode=mode):
                bot = AsyncMock()
                await send_personal_schedule(bot, 42, "<b>Расписание</b>", b"image", "keyboard",
                    SchedulePreferences(delivery_mode=mode, silent=True))
                call = bot.send_message if mode == "text" else bot.send_photo
                call.assert_awaited_once()
                self.assertEqual(call.await_args.kwargs["reply_markup"], "keyboard")
                self.assertTrue(call.await_args.kwargs["disable_notification"])
                if mode == "photo":
                    self.assertIsNone(call.await_args.kwargs["caption"])

    async def test_no_image_falls_back_to_text_and_updates_remain_visible(self):
        bot = AsyncMock()
        await send_personal_schedule(bot, 42, "Текст", None, None, SchedulePreferences(delivery_mode="photo"))
        bot.send_message.assert_awaited_once()
        await send_personal_schedule(bot, 42, "‼️ РАСПИСАНИЕ ИЗМЕНИЛОСЬ\nТекст", b"image", None,
            SchedulePreferences(delivery_mode="photo"))
        self.assertIn("ИЗМЕНИЛОСЬ", bot.send_photo.await_args.kwargs["caption"])

    async def test_long_text_is_not_truncated_and_keyboard_is_only_on_last_chunk(self):
        bot = AsyncMock()
        text = "<b>" + "очень длинно 😀 &amp; " * 500 + "</b>"
        await send_personal_schedule(bot, 42, text, b"image", "keyboard", SchedulePreferences())
        bot.send_photo.assert_awaited_once()
        messages = [call.kwargs for call in bot.send_message.await_args_list]
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(telegram_text_length(message["text"]) <= 4000 for message in messages))
        self.assertTrue(all(message["reply_markup"] is None for message in messages[:-1]))
        self.assertEqual(messages[-1]["reply_markup"], "keyboard")
        plain = lambda value: unescape(re.sub(r"<[^>]*>", "", value))
        self.assertEqual("".join(plain(message["text"]) for message in messages), plain(text))

    async def test_preview_matches_real_broadcast_for_every_preset(self):
        from handlers.schedule_broadcaster import _send_tracked_group_schedule
        fixture = PersistenceAndApiTest()
        fixture.setUp()
        try:
            for name in ("full", "compact", "time_focus", "minimal"):
                p = preset_preferences(name)
                preview = api.preview_payload(42, p)
                bot = AsyncMock()
                await _send_tracked_group_schedule(bot, 42, gs.last_groups_df, "150-Д9-1ИНС",
                    gs.last_groups_date, gs.last_groups_crop_cache, is_update=False, tracked_count=1,
                    always_show_group=False, recipient_role=config.ROLE_STUDENT, preferences=p)
                text = bot.send_message.await_args.kwargs["text"] if name == "minimal" else bot.send_photo.await_args.kwargs["caption"]
                self.assertEqual(text, preview["html"])
        finally:
            fixture.doCleanups()


class MessageSplitTest(unittest.TestCase):
    def test_utf16_length_and_balanced_html_across_chunks(self):
        self.assertEqual(telegram_text_length("<b>😀 &amp;</b>"), 4)
        value = '<b><i>' + '😀&lt;' * 100 + '</i></b>'
        chunks = split_message(value, limit=17)
        class Checker(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack = []
            def handle_starttag(self, tag, attrs):
                self.stack.append(tag)
            def handle_endtag(self, tag):
                if not self.stack or self.stack.pop() != tag:
                    raise AssertionError("Unbalanced formatting")
        for chunk in chunks:
            self.assertLessEqual(telegram_text_length(chunk), 17)
            parser = Checker()
            parser.feed(chunk)
            self.assertFalse(parser.stack)


if __name__ == "__main__":
    unittest.main()
