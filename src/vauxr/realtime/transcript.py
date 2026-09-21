"""Bounded, ordered delivery of replaceable browser transcript snapshots."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable


class TranscriptRelay:
    LIMIT = 64
    TEXT_LIMIT = 6000  # Browser display already retains only this suffix.

    def __init__(
        self, send: Callable[[dict[str, object]], Awaitable[bool]], fail: Callable[[], None],
        *, timeout: float = 5.0,
    ) -> None:
        self._send = send
        self._fail = fail
        self._timeout = timeout
        self._pending: deque[dict[str, object]] = deque()
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self.started_at: float | None = None
        self.sent = 0
        self.coalesced = 0
        self.failures = 0
        self.max_send_ms = 0.0
        self.last_send_ms = 0.0
        self._window_max_send_ms = 0.0

    def enqueue(self, role: str, text: str, turn_id: str, final: bool) -> None:
        if self._closed:
            return
        message: dict[str, object] = {
            "type": "realtime.transcript", "role": role, "text": text[-self.TEXT_LIMIT:],
            "turn_id": turn_id, "final": final,
        }
        # Only replace the tail: never move a final or another speaker's update
        # past an earlier update. One in-flight send remains immutable.
        if self._pending and self._pending[-1]["turn_id"] == turn_id and not self._pending[-1]["final"]:
            self._pending[-1] = message
            self.coalesced += 1
        elif len(self._pending) < self.LIMIT:
            self._pending.append(message)
        else:
            # Persistent history uses a separate acknowledged path. Never silently
            # drop ordered browser finals and continue an apparently healthy session.
            self._failed()
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    def _failed(self) -> None:
        if not self._closed:
            self._closed = True
            self.failures += 1
            self._pending.clear()
            self._fail()

    async def _run(self) -> None:
        while self._pending:
            message = self._pending.popleft()
            self.started_at = time.monotonic()
            try:
                delivered = await asyncio.wait_for(self._send(message), timeout=self._timeout)
                if delivered is False:
                    self._failed()
                    return
                self.sent += 1
            except Exception:  # noqa: BLE001 - Fail closed without logging transport payloads.
                self._failed()
                return
            finally:
                self.last_send_ms = (time.monotonic() - self.started_at) * 1000
                self.max_send_ms = max(self.max_send_ms, self.last_send_ms)
                self._window_max_send_ms = max(self._window_max_send_ms, self.last_send_ms)
                self.started_at = None

    def metrics(self, *, reset_window: bool = False) -> dict[str, int | float]:
        window_max = self._window_max_send_ms
        if reset_window:
            self._window_max_send_ms = 0.0
        return {"pending": len(self._pending), "sent": self.sent, "coalesced": self.coalesced,
                "failures": self.failures, "max_send_ms": round(self.max_send_ms, 3),
                "last_send_ms": round(self.last_send_ms, 3), "window_max_send_ms": round(window_max, 3),
                "inflight_ms": round((time.monotonic() - self.started_at) * 1000, 3)
                if self.started_at is not None else 0}

    async def close(self, *, drain: bool = False) -> None:
        self._closed = True
        task = self._task
        try:
            if drain and task is not None:
                await asyncio.wait_for(asyncio.shield(task), timeout=.25)
        except TimeoutError:
            pass
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self._pending.clear()
