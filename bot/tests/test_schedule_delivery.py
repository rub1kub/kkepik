import unittest
from unittest.mock import AsyncMock, patch

from handlers.schedule_broadcaster import (
    _notify_users_about_missing_groups,
    _send_tracked_group_schedule,
    find_missing_tracked_groups,
    find_missing_tracked_groups_by_user,
)


class StubScheduleFrame:
    pass


class ScheduleDeliveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = AsyncMock()
        self.lines = [
            "▪️1 пара – Основы алгоритмизации – <b>Иванов И.И.</b> – 101"
        ]

    async def send(self, *, tracked_count, always_show_group):
        from handlers import schedule_broadcaster

        original = schedule_broadcaster.group_schedule.get_schedule_for_group
        schedule_broadcaster.group_schedule.get_schedule_for_group = lambda df, group: self.lines
        try:
            await _send_tracked_group_schedule(
                self.bot,
                1,
                StubScheduleFrame(),
                "103-Д9-2ИНС",
                "10.06.2026",
                {"103-Д9-2ИНС": b"image"},
                is_update=False,
                tracked_count=tracked_count,
                always_show_group=always_show_group,
            )
        finally:
            schedule_broadcaster.group_schedule.get_schedule_for_group = original

        return self.bot.send_photo.await_args.kwargs["caption"]

    async def test_single_student_group_hides_redundant_group_line(self):
        caption = await self.send(tracked_count=1, always_show_group=False)

        self.assertNotIn("Группа <b>", caption)

    async def test_two_student_groups_show_group_line(self):
        caption = await self.send(tracked_count=2, always_show_group=False)

        self.assertIn("Группа <b>103-Д9-2ИНС</b>", caption)

    async def test_teacher_group_always_shows_group_line(self):
        caption = await self.send(tracked_count=1, always_show_group=True)

        self.assertIn("Группа <b>103-Д9-2ИНС</b>", caption)

    async def test_missing_group_is_not_sent_to_user(self):
        from handlers import schedule_broadcaster

        original = schedule_broadcaster.group_schedule.get_schedule_for_group
        schedule_broadcaster.group_schedule.get_schedule_for_group = lambda df, group: None
        try:
            sent = await _send_tracked_group_schedule(
                self.bot,
                1,
                StubScheduleFrame(),
                "999-Д9-2ИНС",
                "01.09.2026",
                {},
                is_update=False,
                tracked_count=1,
                always_show_group=False,
            )
        finally:
            schedule_broadcaster.group_schedule.get_schedule_for_group = original

        self.assertFalse(sent)
        self.bot.send_message.assert_not_awaited()
        self.bot.send_photo.assert_not_awaited()

    async def test_subject_alias_is_limited_to_target_students_in_all_delivery_modes(self):
        from schedules.schedule_formatter import build_group_schedule_message

        subject = "Тестирование информационных систем"
        lines = [f"▪️4 пара – {subject} – Зиманина Т.Н. – 63",
                 f"▪️5 пара – Практика: {subject} – Иванов И.И. – 64"]
        original_lines = list(lines)
        for group, role, alias_expected in [
            ("103-Д9-3ИНС", "Я студент", True),
            ("103-Д9-3ИНС", "Я преподаватель", False),
            ("103-Д9-3ИНС", None, False),
            ("103-Д9-2ИНС", "Я студент", False),
            ("104-Д9-3ИНС", "Я студент", False),
        ]:
            for photo in (True, False):
                for update in (True, False):
                    with self.subTest(group=group, role=role, photo=photo, update=update):
                        bot = AsyncMock()
                        with patch('handlers.schedule_broadcaster.group_schedule.get_schedule_for_group',
                                   return_value=lines):
                            await _send_tracked_group_schedule(
                                bot, 42, StubScheduleFrame(), group, "10.09.2026",
                                {group: b"image"} if photo else {}, is_update=update,
                                tracked_count=2, always_show_group=role == "Я преподаватель",
                                recipient_role=role,
                            )
                        call = bot.send_photo.await_args if photo else bot.send_message.await_args
                        text = call.kwargs["caption" if photo else "text"]
                        expected = build_group_schedule_message(lines, group, "10.09.2026",
                            subject_aliases={subject: "Торнадо 🌪🌪🌪"} if alias_expected else None)
                        if update:
                            expected = "‼️ РАСПИСАНИЕ ИЗМЕНИЛОСЬ\n\n" + expected
                        self.assertEqual(text, expected)
                        self.assertEqual(lines, original_lines)

    def test_missing_groups_are_counted_for_owner_summary(self):
        missing = find_missing_tracked_groups(
            {1: ["103-Д3-3ИНС"], 2: ["OLD-Д9", "103-Д3-3ИНС"], 3: ["OLD-Д9"]},
            {"103-Д3-3ИНС"},
        )

        self.assertEqual(missing, {"OLD-Д9": 2})

    def test_missing_groups_are_grouped_per_user(self):
        missing = find_missing_tracked_groups_by_user(
            {
                1: ["103-Д9-3ИНС", "OLD-Д9"],
                2: ["OLD-Д9", "OTHER-Д9"],
                3: ["103-Д9-3ИНС"],
            },
            {"103-Д9-3ИНС"},
        )

        self.assertEqual(
            missing,
            {1: ["OLD-Д9"], 2: ["OLD-Д9", "OTHER-Д9"]},
        )

    async def test_user_is_told_how_to_fix_a_missing_group(self):
        notified = await _notify_users_about_missing_groups(
            self.bot,
            {42: ["OLD-Д9"]},
            "04.09.2026",
        )

        self.assertEqual(notified, 1)
        self.bot.send_message.assert_awaited_once()
        args = self.bot.send_message.await_args.args
        self.assertEqual(args[0], 42)
        self.assertIn("OLD-Д9", args[1])
        self.assertIn("/groups", args[1])


if __name__ == "__main__":
    unittest.main()
