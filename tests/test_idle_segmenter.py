"""Phase 10: IdleSegmenter timing behavior."""

from __future__ import annotations

import asyncio

import pytest

from idle_segmenter import IdleSegmenter


@pytest.mark.asyncio
async def test_no_flush_while_deltas_within_idle_window() -> None:
    segments: list[str] = []
    ends: list[str | None] = []
    seg = IdleSegmenter(idle_pause_ms=400, on_segment=segments.append, on_end=ends.append)

    seg.push("the ")
    await asyncio.sleep(0.1)
    seg.push("quick ")
    await asyncio.sleep(0.1)
    seg.push("brown ")
    await asyncio.sleep(0.1)
    seg.push("fox")

    assert segments == []
    assert ends == []
    seg.abort()


@pytest.mark.asyncio
async def test_flushes_after_idle_gap() -> None:
    segments: list[str] = []
    seg = IdleSegmenter(idle_pause_ms=100, on_segment=segments.append, on_end=lambda _: None)

    seg.push("hello world")
    await asyncio.sleep(0.05)
    assert segments == []
    await asyncio.sleep(0.10)
    assert segments == ["hello world"]
    seg.abort()


@pytest.mark.asyncio
async def test_multiple_flushes_within_one_run() -> None:
    segments: list[str] = []
    seg = IdleSegmenter(idle_pause_ms=80, on_segment=segments.append, on_end=lambda _: None)

    seg.push("first chunk")
    await asyncio.sleep(0.15)
    assert segments == ["first chunk"]

    seg.push("second chunk")
    await asyncio.sleep(0.15)
    assert segments == ["first chunk", "second chunk"]

    seg.push("third chunk")
    await asyncio.sleep(0.15)
    assert segments == ["first chunk", "second chunk", "third chunk"]
    seg.abort()


@pytest.mark.asyncio
async def test_end_flushes_leftover() -> None:
    segments: list[str] = []
    ends: list[str | None] = []
    seg = IdleSegmenter(idle_pause_ms=400, on_segment=segments.append, on_end=ends.append)

    seg.push("partial buffer")
    await asyncio.sleep(0.05)
    seg.end()

    assert segments == []
    assert ends == ["partial buffer"]


@pytest.mark.asyncio
async def test_end_with_empty_buffer_emits_none() -> None:
    segments: list[str] = []
    ends: list[str | None] = []
    seg = IdleSegmenter(idle_pause_ms=80, on_segment=segments.append, on_end=ends.append)

    seg.push("flushed")
    await asyncio.sleep(0.15)
    assert segments == ["flushed"]

    seg.end()
    assert ends == [None]


@pytest.mark.asyncio
async def test_abort_drops_buffer() -> None:
    segments: list[str] = []
    ends: list[str | None] = []
    seg = IdleSegmenter(idle_pause_ms=80, on_segment=segments.append, on_end=ends.append)

    seg.push("doomed buffer")
    await asyncio.sleep(0.02)
    seg.abort()
    await asyncio.sleep(0.20)

    assert segments == []
    assert ends == []


@pytest.mark.asyncio
async def test_push_after_end_is_ignored() -> None:
    segments: list[str] = []
    seg = IdleSegmenter(idle_pause_ms=80, on_segment=segments.append, on_end=lambda _: None)

    seg.end()
    seg.push("late")
    await asyncio.sleep(0.20)
    assert segments == []


@pytest.mark.asyncio
async def test_delta_after_flush_re_arms_timer() -> None:
    segments: list[str] = []
    seg = IdleSegmenter(idle_pause_ms=80, on_segment=segments.append, on_end=lambda _: None)

    seg.push("first")
    await asyncio.sleep(0.15)
    assert segments == ["first"]

    seg.push("second")
    await asyncio.sleep(0.05)
    assert segments == ["first"]
    await asyncio.sleep(0.10)
    assert segments == ["first", "second"]
    seg.abort()
