from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from realtime_transcript import TranscriptRelay


async def test_slow_send_preserves_order_and_only_coalesces_adjacent_snapshots() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    sent: list[tuple[object, object, object]] = []

    async def send(message: dict[str, object]) -> bool:
        entered.set()
        await release.wait()
        sent.append((message["turn_id"], message["text"], message["final"]))
        return True

    failed = Mock()
    relay = TranscriptRelay(send, failed)
    relay.enqueue("user", "first", "u1", False)
    await entered.wait()
    for text in ("a", "ab", "abc"):
        relay.enqueue("assistant", text, "a1", False)
    relay.enqueue("user", "user final", "u1", True)
    relay.enqueue("assistant", "assistant final", "a1", True)
    assert relay.metrics()["pending"] == 3
    assert relay.metrics()["coalesced"] == 2
    release.set()
    await relay.close(drain=True)
    assert sent == [("u1", "first", False), ("a1", "abc", False),
                    ("u1", "user final", True), ("a1", "assistant final", True)]
    failed.assert_not_called()
    assert relay._task.done()
    assert relay.metrics(reset_window=True)["window_max_send_ms"] > 0
    assert relay.metrics()["window_max_send_ms"] == 0
    assert relay.metrics()["max_send_ms"] > 0


async def test_queue_and_text_are_bounded_and_overflow_fails_once() -> None:
    send, failed = AsyncMock(return_value=True), Mock()
    relay = TranscriptRelay(send, failed)
    for i in range(relay.LIMIT):
        relay.enqueue("assistant", "x" * 7000, str(i), True)
    assert relay.metrics()["pending"] == relay.LIMIT
    assert all(len(message["text"]) == relay.TEXT_LIMIT for message in relay._pending)
    relay.enqueue("user", "overflow", "last", True)
    relay.enqueue("user", "ignored", "last", True)
    failed.assert_called_once()
    assert relay.metrics()["pending"] == 0
    await relay.close()
    send.assert_not_called()


@pytest.mark.parametrize("failure", ["timeout", "exception", "undelivered"])
async def test_send_failures_clear_pending_and_terminate(failure: str) -> None:
    failed = Mock()
    cancelled = asyncio.Event()

    async def send(_message: dict[str, object]) -> bool:
        if failure == "exception":
            raise RuntimeError("synthetic")
        if failure == "undelivered":
            return False
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return True

    relay = TranscriptRelay(send, failed, timeout=.01)
    relay.enqueue("user", "synthetic", "u", True)
    relay.enqueue("assistant", "synthetic", "a", True)
    await asyncio.wait_for(relay._task, 1)
    failed.assert_called_once()
    assert relay.metrics()["pending"] == 0
    assert relay.metrics()["failures"] == 1
    assert cancelled.is_set() is (failure == "timeout")
    await relay.close()


async def test_close_cancels_inflight_send_and_rejects_late_enqueue() -> None:
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def send(_message: dict[str, object]) -> bool:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return True

    failed = Mock()
    relay = TranscriptRelay(send, failed)
    relay.enqueue("user", "synthetic", "u", False)
    await entered.wait()
    await asyncio.wait_for(relay.close(), .1)
    relay.enqueue("assistant", "late", "a", True)
    assert cancelled.is_set()
    assert relay._task.done()
    assert relay.metrics()["pending"] == 0
    failed.assert_not_called()
