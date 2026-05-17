"""Channel-plugin WS endpoint (`/channel`).

Phase 11 puts the in-process API the pipeline needs in place:
get_active_channel, send_transcript, add/remove_response_listener. Phase 12
adds the WS handler that talks to the `vauxr-openclaw` plugin.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from . import channel_registry

log = logging.getLogger("vauxr.channel_server")


class DeviceResponseListener(TypedDict):
    on_delta: Any  # Callable[[str, str], None]
    on_end: Any  # Callable[[str], None]
    on_error: Any  # Callable[[str, str], None]


class ChannelServer:
    """In-process broker between the channel plugin WS and the pipeline."""

    def __init__(self) -> None:
        # Active plugin connections keyed by channel id. Populated in Phase 12.
        self._connections: dict[str, Any] = {}
        self._response_listeners: dict[str, DeviceResponseListener] = {}

    def get_active_channel(self):
        return channel_registry.get_active()

    def is_active_connected(self) -> bool:
        active = channel_registry.get_active()
        if active is None:
            return False
        if getattr(active, "type", None) == "openclaw-direct":
            return True
        conn = self._connections.get(getattr(active, "id", ""))
        return conn is not None and not getattr(conn["ws"], "closed", True)

    def send_transcript(self, device_id: str, text: str) -> bool:
        """Forward the transcript to the active channel WS.

        Phase 12 lands the real WS send. For now: if no active channel /
        no live plugin connection, return False so the pipeline emits
        NO_CHANNEL — matching the Node behavior.
        """
        active = channel_registry.get_active()
        if active is None:
            log.warning("No active channel — dropping transcript")
            return False
        if getattr(active, "type", None) == "openclaw-direct":
            # The pipeline shouldn't have called us in direct mode, but match
            # the Node behavior: do nothing and return False.
            return False

        conn = self._connections.get(getattr(active, "id", ""))
        if conn is None or getattr(conn["ws"], "closed", True):
            log.warning("Active channel %s not connected — dropping transcript", getattr(active, "name", ""))
            return False

        # Phase 12 will wire the actual send.
        return self._send_to_plugin(conn, device_id, text)

    def _send_to_plugin(self, conn: dict[str, Any], device_id: str, text: str) -> bool:
        """Phase 12 hook — overridden / extended when the WS handler lands."""
        return False

    def add_response_listener(self, device_id: str, listener: DeviceResponseListener) -> None:
        self._response_listeners[device_id] = listener

    def remove_response_listener(self, device_id: str) -> None:
        self._response_listeners.pop(device_id, None)

    def get_response_listener(self, device_id: str) -> DeviceResponseListener | None:
        return self._response_listeners.get(device_id)
