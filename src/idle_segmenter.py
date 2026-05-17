"""Idle-pause text segmenter.

Port of `src/idle-segmenter.ts`. Buffers incoming delta text and flushes it
as a segment whenever the stream goes idle for `idle_pause_ms`. Pure timer
logic — no I/O.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

log = logging.getLogger("vauxr.segmenter")


class IdleSegmenter:
    """Flush a text buffer when no deltas have arrived for `idle_pause_ms`.

    Designed to be driven from an asyncio context — the timer runs on the
    currently active event loop.
    """

    def __init__(
        self,
        *,
        idle_pause_ms: int,
        on_segment: Callable[[str], None],
        on_end: Callable[[str | None], None],
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._idle_pause = idle_pause_ms / 1000.0
        self._on_segment = on_segment
        self._on_end = on_end
        self._buffer = ""
        self._ended = False
        self._timer: asyncio.TimerHandle | None = None
        self._loop = loop

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = asyncio.get_event_loop()
        return self._loop

    def push(self, text: str) -> None:
        if self._ended or not text:
            return
        self._buffer += text
        self._arm_timer()

    def end(self) -> None:
        if self._ended:
            return
        self._ended = True
        self._clear_timer()
        if self._buffer:
            final = self._buffer
            self._buffer = ""
            log.info(
                "end flush (%d chars): %r",
                len(final),
                final[:80] + ("…" if len(final) > 80 else ""),
            )
            self._on_end(final)
        else:
            log.info("end (no remaining buffer)")
            self._on_end(None)

    def abort(self) -> None:
        if self._ended:
            return
        self._ended = True
        self._clear_timer()
        self._buffer = ""

    def _arm_timer(self) -> None:
        self._clear_timer()
        self._timer = self._get_loop().call_later(self._idle_pause, self._flush)

    def _clear_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _flush(self) -> None:
        self._timer = None
        if self._ended or not self._buffer:
            return
        segment = self._buffer
        self._buffer = ""
        log.info(
            "idle flush (%d chars): %r",
            len(segment),
            segment[:80] + ("…" if len(segment) > 80 else ""),
        )
        self._on_segment(segment)
