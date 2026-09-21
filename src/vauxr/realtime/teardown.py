"""Instance-local workaround for Pipecat's connecting-timeout close race.

Verified with Pipecat 1.9.0 / aiortc 1.15.0; recheck these private hooks on
dependency upgrades.
"""

import asyncio

from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection


def protect_handshake_teardown(connection: SmallWebRTCConnection) -> None:
    """Let a connecting watchdog finish the peer close it initiated."""
    close = connection._close

    async def close_without_watchdog_cancellation() -> None:
        if connection._connecting_timeout_task is asyncio.current_task():
            # aiortc emits 'closed' before close() finishes. Pipecat's state
            # callback cancels this watchdog, stranding aiortc's close future.
            # Retire the watchdog before that callback can run. The current
            # task still owns and awaits cleanup; no detached task is needed.
            connection._connecting_timeout_task = None
        await close()

    connection._close = close_without_watchdog_cancellation
