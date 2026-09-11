"""Real unanswered WebRTC offers must leave repeated cleanup awaitable."""

import asyncio
from collections.abc import Iterator

import pytest

pytest.importorskip("pipecat")

from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection
from loguru import logger
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection

import config
import realtime_session
from realtime_session import RealtimeManager, RealtimeSession
from realtime_teardown import protect_handshake_teardown


@pytest.fixture
def warnings() -> Iterator[list[str]]:
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    try:
        yield messages
    finally:
        logger.remove(sink)


async def wait_for_watchdog(connection: SmallWebRTCConnection) -> asyncio.Task:
    async with asyncio.timeout(2):
        while connection._connecting_timeout_task is None:
            await asyncio.sleep(0)
    return connection._connecting_timeout_task


@pytest.mark.parametrize("esp32_mode", [False, True])
async def test_manager_repeated_failed_handshake_cleanup(
    monkeypatch: pytest.MonkeyPatch, warnings: list[str], esp32_mode: bool,
) -> None:
    monkeypatch.setenv("DEVICE_TOKEN", "teardown-test")
    monkeypatch.setenv("REALTIME_ESP32", str(int(esp32_mode)))
    monkeypatch.setenv("REALTIME_HOST", "127.0.0.1")
    config.reset_config()
    manager = RealtimeManager()
    monkeypatch.setattr(realtime_session, "_manager", manager)
    connections: list[SmallWebRTCConnection] = []

    async def start_without_voice_services(
        session: RealtimeSession, connection: SmallWebRTCConnection,
    ) -> None:
        # Keep real manager/handler/peer teardown, omit external voice services.
        session._connection = connection
        connection.connection_timeout_secs = 0.05
        connections.append(connection)
        await connection.connect()

    monkeypatch.setattr(RealtimeSession, "start", start_without_voice_services)
    handler = manager._request_handler()
    handler.update_ice_servers([])  # No STUN or other external services.
    try:
        for _ in range(3):
            remote = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            remote.addTrack(AudioStreamTrack())
            try:
                await remote.setLocalDescription(await remote.createOffer())
                manager.begin_preroll("teardown-test")
                answer = await asyncio.wait_for(manager.handle_offer("teardown-test", {
                    "sdp": remote.localDescription.sdp, "type": "offer",
                }), 2)
                assert answer is not None
                connection = connections[-1]
                watchdog = await wait_for_watchdog(connection)
                # Deliberately never deliver the answer to the remote peer.
                # Shield ensures the assertion's timeout cannot poison close().
                await asyncio.wait_for(asyncio.shield(watchdog), 2)
                assert not watchdog.cancelled()
                assert connection.pc.connectionState == "closed"
                assert connection.pc._RTCPeerConnection__isClosed.done()
                assert connection._connecting_timeout_task is None
                assert not connection._track_map
                assert not connection._outgoing_messages_queue
                assert connection._data_channel_timeout_task is None
                for _ in range(3):
                    await asyncio.wait_for(asyncio.gather(
                        connection.disconnect(), connection.disconnect(),
                        manager.stop("teardown-test"), handler.close(),
                    ), 2)
                assert not manager._sessions
                assert not handler._pcs_map
            finally:
                await asyncio.wait_for(remote.close(), 2)
        assert sum("Timeout establishing the connection" in message for message in warnings) == 3
    finally:
        config.reset_config()
        for connection in connections:
            # Bounded even when this test is run against the broken baseline.
            try:
                await asyncio.wait_for(connection.disconnect(), 0.2)
            except TimeoutError:
                pass


async def test_external_disconnect_still_cancels_pending_watchdog() -> None:
    connection = SmallWebRTCConnection(ice_servers=[])
    protect_handshake_teardown(connection)
    try:
        # Exercise both the original peer and Pipecat's peer reset lifecycle.
        for _ in range(2):
            connection._monitoring_connecting_state()
            watchdog = connection._connecting_timeout_task
            await asyncio.wait_for(connection.disconnect(), 2)
            await asyncio.gather(watchdog, return_exceptions=True)
            assert watchdog.cancelled()
            assert connection._connecting_timeout_task is None
            await asyncio.wait_for(connection.disconnect(), 2)
            connection._initialize()
    finally:
        await asyncio.wait_for(connection.disconnect(), 2)
