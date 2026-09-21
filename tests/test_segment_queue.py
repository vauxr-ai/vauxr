"""Phase 10: SegmentQueue serial-synthesizer behavior."""

from __future__ import annotations

import asyncio

import pytest

from vauxr.segment_queue import SegmentQueue


@pytest.mark.asyncio
async def test_processes_in_order() -> None:
    received: list[str] = []
    abort = asyncio.Event()

    async def synth(s: str) -> None:
        received.append(s)
        await asyncio.sleep(0.01)

    q = SegmentQueue(synthesize=synth, abort_event=abort)
    q.push("one")
    q.push("two")
    q.push("three")
    q.close()
    await q.done()
    assert received == ["one", "two", "three"]


@pytest.mark.asyncio
async def test_empty_segments_dropped() -> None:
    received: list[str] = []
    abort = asyncio.Event()

    async def synth(s: str) -> None:
        received.append(s)

    q = SegmentQueue(synthesize=synth, abort_event=abort)
    q.push("")
    q.push("real")
    q.close()
    await q.done()
    assert received == ["real"]


@pytest.mark.asyncio
async def test_abort_stops_worker() -> None:
    received: list[str] = []
    abort = asyncio.Event()

    async def synth(s: str) -> None:
        received.append(s)
        await asyncio.sleep(0.05)

    q = SegmentQueue(synthesize=synth, abort_event=abort)
    q.push("a")
    q.push("b")
    q.push("c")

    # Let one segment start, then abort.
    await asyncio.sleep(0.02)
    abort.set()
    q.close()
    await q.done()

    # At least the first item should have been picked up; not all three.
    assert received[:1] == ["a"]
    assert len(received) < 3


@pytest.mark.asyncio
async def test_on_error_called() -> None:
    errors: list[BaseException] = []
    abort = asyncio.Event()

    async def synth(s: str) -> None:
        if s == "boom":
            raise RuntimeError("nope")

    q = SegmentQueue(synthesize=synth, abort_event=abort, on_error=errors.append)
    q.push("ok")
    q.push("boom")
    q.push("after")
    q.close()
    await q.done()
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)


@pytest.mark.asyncio
async def test_push_after_close_ignored() -> None:
    received: list[str] = []
    abort = asyncio.Event()

    async def synth(s: str) -> None:
        received.append(s)

    q = SegmentQueue(synthesize=synth, abort_event=abort)
    q.close()
    q.push("ignored")
    await q.done()
    assert received == []
