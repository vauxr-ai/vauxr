"""Token validation — mirrors `src/auth.ts`."""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from config import get_config


@dataclass(frozen=True)
class AuthResult:
    ok: bool
    reason: str | None = None


def validate_token(token: str) -> AuthResult:
    """Constant-time comparison against the configured device token."""
    expected = get_config().device.token
    a = token.encode("utf-8")
    b = expected.encode("utf-8")
    # The Node version short-circuits on length mismatch with the same
    # "Invalid token" reason, so the API contract is identical either way.
    if len(a) != len(b):
        return AuthResult(ok=False, reason="Invalid token")
    if not hmac.compare_digest(a, b):
        return AuthResult(ok=False, reason="Invalid token")
    return AuthResult(ok=True)


async def validate_channel_http_token(token: str) -> AuthResult:
    """Channel token check — delegates to the channel registry.

    Imported lazily to avoid a circular import (channel_registry pulls in
    config, which doesn't depend on auth, but channel_registry is a Phase 12
    module that will live alongside this one).
    """
    import channel_registry  # noqa: PLC0415  (lazy import)

    channel = await channel_registry.validate_channel_token(token)
    if channel is None:
        return AuthResult(ok=False, reason="Invalid token")
    return AuthResult(ok=True)
