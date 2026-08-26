import unittest
from unittest.mock import AsyncMock

from handlers.schedule_broadcaster import _send_tracked_group_schedule


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


if __name__ == "__main__":
    unittest.main()
