"""Async FIFO of text segments with a single serial synthesizer worker.

Port of `src/segment-queue.ts`. Used by the pipeline to keep TTS playback
strictly ordered while segments are produced upstream by the
:mod:`vauxr.idle_segmenter`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger("vauxr.segment_queue")


class SegmentQueue:
    def __init__(
        self,
        *,
        synthesize: Callable[[str], Awaitable[None]],
        abort_event: asyncio.Event,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        self._synthesize = synthesize
        self._abort = abort_event
        self._on_error = on_error
        self._items: list[str] = []
        self._closed = False
        self._wakeup: asyncio.Event = asyncio.Event()
        self._worker = asyncio.create_task(self._run())

    def push(self, segment: str) -> None:
        if self._closed or not segment:
            return
        self._items.append(segment)
        self._wakeup.set()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._wakeup.set()

    async def done(self) -> None:
        await self._worker

    async def _run(self) -> None:
        while True:
            if self._abort.is_set():
                return
            if not self._items:
                if self._closed:
                    return
                self._wakeup.clear()
                # Wait for either a new item, close(), or abort.
                abort_wait = asyncio.create_task(self._abort.wait())
                wake_wait = asyncio.create_task(self._wakeup.wait())
                try:
                    done, pending = await asyncio.wait(
                        {abort_wait, wake_wait}, return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    for t in (abort_wait, wake_wait):
                        if not t.done():
                            t.cancel()
                if self._abort.is_set():
                    return
                continue

            segment = self._items.pop(0)
            pending = len(self._items)
            log.info(
                "synthesizing (%d chars, %d queued): %r",
                len(segment),
                pending,
                segment[:80] + ("…" if len(segment) > 80 else ""),
            )
            try:
                await self._synthesize(segment)
                log.info("done (%d chars)", len(segment))
            except Exception as err:  # noqa: BLE001
                if self._on_error is not None:
                    self._on_error(err)
                else:
                    log.error("synth error: %s", err)
