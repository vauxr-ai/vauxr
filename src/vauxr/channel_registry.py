"""Routing-channel registry.

Phase 12 fleshes this out (persistence, bcrypt token validation, CRUD,
activate/rotate). Phase 6 needed `validate_channel_token`; Phase 11 also
needs `get_active`. Both are placeholder shims callable from the test
suite via the `_set_active` test helper.
"""

from __future__ import annotations

from typing import Any


_active: Any | None = None


async def validate_channel_token(_raw_token: str) -> Any | None:
    """Stub: real implementation lands in Phase 12."""
    return None


def get_active() -> Any | None:
    return _active


def _set_active_for_tests(channel: Any | None) -> None:
    global _active
    _active = channel
