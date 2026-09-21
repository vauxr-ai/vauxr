"""Agent-plugin WS endpoint (`/agent`).

Port of `src/agent-server.ts`. Each plugin connection authenticates with
a agent token, then exchanges `agent.transcript` / `agent.response.*`
messages with the active device pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from aiohttp import WSMsgType, web

import vauxr.auth.connections as auth_connections
import vauxr.agents.registry as agent_registry
from vauxr.auth.service import authenticate, current
from vauxr.auth.policy import Operation, Principal, allowed, audit_denial
from vauxr.config import get_config
from vauxr.devices.config import load_device_display_name

log = logging.getLogger("vauxr.agent_server")

_AUTH_TIMEOUT_S = 10.0


class DeviceResponseListener(TypedDict):
    on_delta: Callable[[str, str], None]
    on_end: Callable[[str], None]
    on_error: Callable[[str, str], None]


class _Connection:
    __slots__ = ("authenticated", "authority", "agent", "principal", "ws")

    def __init__(self, ws: web.WebSocketResponse) -> None:
        self.ws: web.WebSocketResponse = ws
        self.agent: agent_registry.Agent | None = None
        self.authenticated = False
        self.principal: Principal | None = None
        self.authority: auth_connections.Connection | None = None


async def _send_json(ws: web.WebSocketResponse, obj: dict[str, Any]) -> None:
    if ws.closed:
        return
    try:
        await ws.send_str(json.dumps(obj, separators=(",", ":")))
    except (ConnectionResetError, RuntimeError):
        pass


class AgentServer:
    def __init__(self) -> None:
        self._realtime_requests: dict[str, tuple[_Connection, str, asyncio.Future]] = {}
        self._connections: dict[str, _Connection] = {}
        self._response_listeners: dict[str, DeviceResponseListener] = {}
        self._response_agents: dict[str, str] = {}
        self._media_turns: dict[asyncio.Event, str] = {}
        self._media_authorities: dict[asyncio.Event, auth_connections.Connection] = {}

    async def realtime_request(
        self, agent_id: str, device_id: str, session: str, operation: str,
        payload: dict[str, Any] | None = None, *, timeout: float = 30,
    ) -> dict[str, Any]:
        active = agent_registry.get_active()
        conn = self._connections.get(agent_id)
        if (active is None or active.id != agent_id or conn is None or conn.ws.closed
                or not current(conn.principal)):
            raise RuntimeError("Selected Agent is not connected; connect and activate the OpenClaw integration")
        request_id = secrets.token_hex(16)
        future = asyncio.get_running_loop().create_future()
        self._realtime_requests[request_id] = (conn, device_id, future)
        try:
            frame = {"type": "agent.realtime.request", "requestId": request_id,
                     "deviceId": device_id, "session": session, "operation": operation,
                     "payload": payload or {}}
            # Match Standard: reread committed display metadata on every request,
            # never derive identity or titles from hello labels or caller payloads.
            name = load_device_display_name(get_config().data_dir, device_id)
            if name is not None:
                frame["deviceDisplayName"] = name
            await _send_json(conn.ws, frame)
            return await asyncio.wait_for(future, timeout)
        finally:
            # Cancelling the waiting media session never sends backend cancellation.
            self._realtime_requests.pop(request_id, None)

    # --- Pipeline-facing API ---

    def get_active_agent(self) -> agent_registry.Agent | None:
        return agent_registry.get_active()

    def is_active_connected(self) -> bool:
        active = agent_registry.get_active()
        if active is None:
            return False
        if active.type == "openclaw-direct":
            return True
        conn = self._connections.get(active.id)
        return conn is not None and not conn.ws.closed and current(conn.principal)

    def send_transcript(self, device_id: str, text: str) -> bool:
        active = agent_registry.get_active()
        if active is None:
            log.warning("No active agent — dropping transcript")
            return False
        if active.type == "openclaw-direct":
            return False
        conn = self._connections.get(active.id)
        if conn is None or conn.ws.closed or not current(conn.principal):
            log.warning("Active agent %s not connected — dropping transcript", active.name)
            return False

        # device_id comes from the authenticated device turn. Display metadata
        # must never replace it in sessions, response listeners or routing.
        frame = {
            "type": "agent.transcript",
            "deviceId": device_id,
            "sessionKey": f"vauxr:{device_id}",
            "text": text,
        }
        name = load_device_display_name(get_config().data_dir, device_id)
        if name is not None:
            frame["deviceDisplayName"] = name

        # Schedule the WS send on the event loop; the pipeline calls this
        # synchronously, so we return True optimistically once we've queued.
        asyncio.create_task(
            _send_json(
                conn.ws,
                frame,
            )
        )
        log.info("Sent transcript")
        return True

    def retain_media_turn(
        self, agent_id: str, abort: asyncio.Event,
        close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Keep exact turn authority until all queued media has completed."""
        if abort in self._media_turns:
            raise ValueError("media turn already retained")
        self._media_turns[abort] = agent_id
        conn = self._connections.get(agent_id)
        if conn is not None and conn.principal is not None:
            async def close_revoked() -> None:
                # A completed entry may already be in a teardown snapshot.
                if abort not in self._media_turns:
                    return
                abort.set()
                if close is not None:
                    await close()

            # This authority belongs to the media, not the socket. Socket close,
            # routing fallback and replacement turns must not release it.
            self._media_authorities[abort] = auth_connections.retain(conn.principal, close_revoked)

    def release_media_turn(self, abort: asyncio.Event) -> None:
        self._media_turns.pop(abort, None)
        auth_connections.release(self._media_authorities.pop(abort, None))

    def add_response_listener(self, device_id: str, listener: DeviceResponseListener) -> None:
        self._response_listeners[device_id] = listener
        active = agent_registry.get_active()
        self._response_agents[device_id] = active.id if active else ""

    def remove_response_listener(
        self, device_id: str, listener: DeviceResponseListener | None = None
    ) -> None:
        # When a listener is given, only remove it if it's still the active one.
        # This stops a finishing turn from tearing down a newer turn's listener
        # that overwrote it (overlapping turns / barge-in).
        if listener is not None and self._response_listeners.get(device_id) is not listener:
            return
        self._response_listeners.pop(device_id, None)
        self._response_agents.pop(device_id, None)

    def get_response_listener(self, device_id: str) -> DeviceResponseListener | None:
        return self._response_listeners.get(device_id)

    # --- WS handler ---

    async def handle_connection(self, ws: web.WebSocketResponse) -> None:
        log.info("new agent connection")
        conn = _Connection(ws)
        auth_done = asyncio.Event()

        async def auth_timeout() -> None:
            try:
                await asyncio.wait_for(auth_done.wait(), timeout=_AUTH_TIMEOUT_S)
            except TimeoutError:
                if not conn.authenticated:
                    await _send_json(
                        ws,
                        {"type": "error", "code": "AUTH_TIMEOUT", "message": "Authentication timeout"},
                    )
                    await ws.close()

        timeout_task = asyncio.create_task(auth_timeout())

        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    payload = json.loads(msg.data)
                except json.JSONDecodeError:
                    await _send_json(
                        ws,
                        {"type": "error", "code": "INVALID_MESSAGE", "message": "Invalid JSON"},
                    )
                    continue

                if not isinstance(payload, dict):
                    continue

                if not conn.authenticated:
                    if payload.get("type") == "agent.auth":
                        await self._handle_auth(conn, payload.get("token", ""))
                        auth_done.set()
                    else:
                        audit_denial(False)
                        await _send_json(
                            ws,
                            {
                                "type": "error",
                                "code": "UNAUTHORIZED",
                                "message": "Must authenticate first",
                            },
                        )
                    continue

                await self._handle_authenticated_message(conn, payload)
        finally:
            auth_connections.release(conn.authority)
            auth_done.set()
            timeout_task.cancel()
            if conn.authenticated and conn.agent is not None:
                log.info("agent disconnected: %s (%s)", conn.agent.name, conn.agent.id)
                # Only remove if this conn is still the current one (a
                # replacement might have superseded it during auth).
                if self._connections.get(conn.agent.id) is conn:
                    self._connections.pop(conn.agent.id, None)
            else:
                log.info("unauthenticated agent connection closed")

    async def _handle_auth(self, conn: _Connection, token: str) -> None:
        principal = authenticate(token)
        agent = agent_registry.get_by_id(principal.subject) if principal else None
        if not allowed(principal, Operation.AGENT_CONNECT) or agent is None:
            audit_denial(principal is not None)
            await _send_json(
                conn.ws,
                {"type": "error", "code": "UNAUTHORIZED" if principal is None else "FORBIDDEN",
                 "message": "Access denied"},
            )
            await conn.ws.close()
            return
        conn.principal = principal
        conn.authenticated = True
        conn.agent = agent

        teardown: list[auth_connections.Teardown] = []

        async def close_revoked() -> None:
            if not teardown:
                teardown.append(auth_connections.Teardown(conn.ws.close))
                if self._connections.get(agent.id) is conn:
                    self._connections.pop(agent.id, None)
                    dependents = {device_id: listener for device_id, listener in self._response_listeners.items()
                                  if self._response_agents.get(device_id) == agent.id}
                    for device_id, listener in dependents.items():
                        async def notify(device_id=device_id, listener=listener) -> None:
                            listener["on_error"](device_id, "integration_revoked")

                        teardown.append(auth_connections.Teardown(notify))
            await auth_connections.run_teardowns(teardown)

        conn.authority = auth_connections.retain(principal, close_revoked)

        existing = self._connections.get(agent.id)
        if existing is not None and existing is not conn:
            # Kick the previous connection — store ours afterward so the
            # kicked one's close-cleanup (see handle_connection) sees a
            # different conn and doesn't clear our slot.
            await existing.ws.close()

        # Replacement close yields: revoke/rotation may retire this principal meanwhile.
        if not current(principal) or conn.ws.closed:
            await conn.ws.close()
            return
        self._connections[agent.id] = conn
        await _send_json(
            conn.ws,
            {"type": "agent.ready", "agentId": agent.id, "name": agent.name},
        )
        log.info("agent authenticated: %s (%s)", agent.name, agent.id)

    async def _handle_authenticated_message(self, conn: _Connection, msg: dict[str, Any]) -> None:
        active = agent_registry.get_active()
        authenticated = current(conn.principal)
        if (not authenticated or not allowed(conn.principal, Operation.VOICE_RESPONSE)
                or conn.agent is None or conn.principal.subject != conn.agent.id
                or active is None or active.id != conn.agent.id
                or self._connections.get(conn.agent.id) is not conn
                or not isinstance(msg.get("type"), str)
                or msg.get("type") not in {
                    "agent.response.delta", "agent.response.end", "agent.response.error", "agent.realtime.result"
                }):
            audit_denial(authenticated)
            await _send_json(conn.ws, {"type": "error",
                                      "code": "FORBIDDEN" if authenticated else "UNAUTHORIZED",
                                      "message": "Access denied"})
            await conn.ws.close()
            return
        if msg.get("type") == "agent.realtime.result":
            pending = self._realtime_requests.get(msg.get("requestId", ""))
            if pending and pending[0] is conn and pending[1] == msg.get("deviceId"):
                future = pending[2]
                if not future.done():
                    if msg.get("error"):
                        future.set_exception(RuntimeError("Backend realtime operation failed; check backend action status"))
                    elif isinstance(msg.get("result"), dict):
                        future.set_result(msg["result"])
            return
        device_id = msg.get("deviceId")
        run_id = msg.get("runId")
        msg_type = msg.get("type")
        if not isinstance(device_id, str) or not isinstance(run_id, str):
            agent_name = conn.agent.name if conn.agent else "?"
            log.warning("%s: ignoring %s — missing deviceId or runId", agent_name, msg_type)
            return
        listener = self._response_listeners.get(device_id)
        if listener is None:
            agent_name = conn.agent.name if conn.agent else "?"
            log.warning("%s: no listener for %s (%s)", agent_name, device_id, msg_type)
            return

        if self._response_agents.get(device_id) != conn.agent.id:
            audit_denial(True)
            log.warning("Ignoring response from agent other than listener origin")
            return

        if msg_type == "agent.response.delta":
            text = msg.get("text")
            if isinstance(text, str):
                listener["on_delta"](run_id, text)
        elif msg_type == "agent.response.end":
            listener["on_end"](run_id)
        elif msg_type == "agent.response.error":
            listener["on_error"](run_id, str(msg.get("message", "Agent error")))
        else:
            await _send_json(
                conn.ws,
                {"type": "error", "code": "UNKNOWN_MESSAGE", "message": f"Unknown type: {msg_type}"},
            )
