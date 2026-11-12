"""Optional telemetry lifecycle and privacy-safe observation boundary."""
from __future__ import annotations

import asyncio

_current = None
_pending: set[asyncio.Task] = set()


def observe(kind: str, outcome: str, duration_ms: int, *, provider: str = 'custom',
            model: str = 'custom', endpoint: str = '') -> None:
    """Never accept content, identifiers, exceptions, or arbitrary event mappings."""
    from .contract import categories
    if _current is None or not _current.enabled or len(_pending) >= 128:
        return
    p, m = categories(provider, model, endpoint)
    task = asyncio.create_task(_current.observe(
        kind if kind in ('standard', 'realtime') else 'custom',
        outcome if outcome in ('success', 'failure', 'cancelled') else 'failure',
        max(0, int(duration_ms)), p, m, _current.generation))
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    task.add_done_callback(lambda t: None if t.cancelled() else t.exception())


def begin():
    """Fence a session to the consent generation in which it actually began."""
    import time
    if _current is None or not _current.enabled:
        return None
    return _current, _current.generation, time.monotonic()


def finish(marker, kind: str, outcome: str, **categories) -> None:
    import time
    if marker is None:
        return
    runtime, generation, began = marker
    if runtime is not _current or generation != runtime.generation or not runtime.enabled:
        return
    observe(kind, outcome, int((time.monotonic() - began) * 1000), **categories)
