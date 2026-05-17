"""Routing-channel registry.

This module lands in Phase 12. Phase 6 only needs the
`validate_channel_token` async hook so the HTTP auth check (which is shared
between device tokens and channel tokens) can call into it without erroring.
"""

from __future__ import annotations

from typing import Any


async def validate_channel_token(_raw_token: str) -> Any | None:
    """Stub: real implementation lands in Phase 12."""
    return None
