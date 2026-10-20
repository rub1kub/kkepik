import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pyromax.mapping.envelope.v11.payloads.models import FileMappingModel
from pyromax.models import Message

from max_bot.client import _catch_up_history, _close_api, _supervisor_loop


def make_message(message_id: int, timestamp: int, *, file_name: str | None = None):
    attaches = []
    if file_name:
        attaches.append(
            FileMappingModel(
                type="FILE",
                token="file-token",
                file_id=message_id,
                size=10,
                name=file_name,
            )
        )
    return Message(
        message_id=message_id,
        chat_id=-100,
        time=timestamp,
        type="MESSAGE",
        text=None,
        cid=None,
        attaches=attaches,
    )


class MaxHistoryCatchUpTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_recent_unprocessed_schedule_files_are_recovered(self):
        now_ms = 10_000_000
        recent = make_message(
            3,
            now_ms - 1_000,
            file_name="Расписание на 03.09.2026_ГРУППЫ.pdf",
        )
        already_processed = make_message(
            2,
            now_ms - 2_000,
            file_name="Расписание на 02.09.2026_ГРУППЫ.pdf",
        )
        unrelated = make_message(4, now_ms - 500, file_name="document.pdf")
        api = AsyncMock()
        api.get_chat_history.return_value = [recent, unrelated, already_processed]

        async def handle(message, max_api, *, accept_after_ms):
            self.assertIs(max_api, api)
            self.assertLess(accept_after_ms, message.time)

        with (
            patch("max_bot.config.MAX_WATCH_CHAT_IDS", [-100]),
            patch(
                "message_registry.is_message_recorded",
                side_effect=lambda platform, direction, chat_id, message_id: message_id == 2,
            ),
            patch("max_bot.handlers._handle_message", new=AsyncMock(side_effect=handle)) as handler,
        ):
            recovered = await _catch_up_history(api, now_ms=now_ms)

        self.assertEqual(recovered, 1)
        handler.assert_awaited_once()
        self.assertEqual(handler.await_args.args[0].message_id, 3)

    async def test_close_api_does_not_swallow_outer_task_cancellation(self):
        first_cancel = asyncio.Event()
        release = asyncio.Event()

        async def lifecycle():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                first_cancel.set()
                await release.wait()

        lifecycle_task = asyncio.create_task(lifecycle())
        mapper = SimpleNamespace(
            _lifecycle_manager=SimpleNamespace(
                _manage_lifecycle_task=lifecycle_task,
            ),
            close=AsyncMock(),
        )
        close_task = asyncio.create_task(_close_api(SimpleNamespace(mapper=mapper)))
        await asyncio.wait_for(first_cancel.wait(), timeout=1)

        close_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await close_task

        release.set()

    async def test_close_api_retries_swallowed_lifecycle_cancellation(self):
        started = asyncio.Event()
        cancellations = []

        async def lifecycle():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellations.append(1)
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancellations.append(2)
                    raise

        lifecycle_task = asyncio.create_task(lifecycle())
        await started.wait()
        mapper = SimpleNamespace(
            _lifecycle_manager=SimpleNamespace(
                _manage_lifecycle_task=lifecycle_task,
            ),
            close=AsyncMock(),
        )
        try:
            with patch("max_bot.client._client_close_timeout_seconds", 0.01):
                await asyncio.wait_for(_close_api(SimpleNamespace(mapper=mapper)), 1)
            self.assertEqual(cancellations, [1, 2])
            self.assertTrue(lifecycle_task.cancelled())
            mapper.close.assert_awaited_once()
        finally:
            lifecycle_task.cancel()
            await asyncio.gather(lifecycle_task, return_exceptions=True)

    async def test_supervisor_reconnects_after_history_timeout(self):
        import max_bot.client as client

        started = asyncio.Event()
        reconnected = asyncio.Event()
        connections = []

        async def lifecycle():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # pyromax can suppress cancellation during transport cleanup.
                await asyncio.Event().wait()

        lifecycle_task = asyncio.create_task(lifecycle())
        await started.wait()
        mapper = SimpleNamespace(
            _lifecycle_manager=SimpleNamespace(
                _manage_lifecycle_task=lifecycle_task,
            ),
            close=AsyncMock(),
        )
        api = SimpleNamespace(mapper=mapper)

        async def connect():
            connections.append(1)
            client._max_api = api
            reconnected.set()

        with (
            patch("max_bot.client._max_api", api),
            patch("max_bot.client._polling_task", None),
            patch("max_bot.client._dispatcher", None),
            patch("max_bot.client._client_close_timeout_seconds", 0.01),
            patch("max_bot.client._is_client_healthy", side_effect=lambda: client._max_api is not None),
            patch("max_bot.client.start_max_client", new=AsyncMock(side_effect=connect)),
            patch("max_bot.client._catch_up_history", new=AsyncMock(side_effect=TimeoutError)) as history,
        ):
            supervisor_task = asyncio.create_task(_supervisor_loop(retry_delay=0.01))
            try:
                await asyncio.wait_for(reconnected.wait(), 1)
                self.assertEqual(len(connections), 1)
                history.assert_awaited()
                self.assertTrue(lifecycle_task.cancelled())
            finally:
                supervisor_task.cancel()
                lifecycle_task.cancel()
                await asyncio.gather(supervisor_task, lifecycle_task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
