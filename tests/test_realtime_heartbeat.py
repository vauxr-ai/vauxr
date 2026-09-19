"""Pinned transport contract for the browser's data-channel heartbeat.

Drive Pipecat's real ping handler and writer with a deterministic clock. Consume
RawAudioTrack frames (including underrun silence), not just its enqueue Future.
The browser hook tests independently exercise the actual outgoing ping schedule.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from importlib.metadata import version
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pipecat")
from pyee.asyncio import AsyncIOEventEmitter
from pipecat.frames.frames import OutputAudioRawFrame
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc import connection as connection_module
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import (
    RawAudioTrack, SmallWebRTCClient, SmallWebRTCOutputTransport,
)


@pytest.fixture
async def transport(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[SimpleNamespace]:
    assert version("pipecat-ai") == "1.9.0"
    clock = SimpleNamespace(now=1000.0)
    # Replace only connection.py's clock, never asyncio or RawAudioTrack pacing.
    monkeypatch.setattr(connection_module, "time", SimpleNamespace(time=lambda: clock.now))
    connection = SmallWebRTCConnection(ice_servers=[])
    peer = connection._pc
    channel = AsyncIOEventEmitter()
    channel.readyState = "connecting"
    peer.emit("datachannel", channel)
    ping = channel.listeners("message")[0]
    connection._pc = SimpleNamespace(connectionState="connected")
    connection._connect_invoked = True
    closed = asyncio.Event()
    callbacks = SimpleNamespace(on_client_disconnected=AsyncMock(side_effect=lambda _: closed.set()))
    client = SmallWebRTCClient(connection, callbacks)
    track = RawAudioTrack(24000)
    track._start -= 60  # Consume deterministically without real-time pacing.
    client._audio_output_track = track
    output = SmallWebRTCOutputTransport(client, TransportParams(audio_out_enabled=True))
    try:
        yield SimpleNamespace(clock=clock, connection=connection, ping=ping, client=client,
                              track=track, output=output, callbacks=callbacks, closed=closed)
    finally:
        track.stop()
        connection._pc = peer
        await peer.close()


async def consume_write(transport: SimpleNamespace) -> tuple[bool, int]:
    # One actual 40ms output write, consumed as four 10ms track frames.
    writing = asyncio.create_task(transport.output.write_audio_frame(
        OutputAudioRawFrame(b"\x00\x40" * 960, 24000, 1)))
    try:
        await asyncio.sleep(0)
        nonzero = 0
        for _ in range(4):
            frame = await transport.track.recv()
            nonzero += int((frame.to_ndarray() != 0).sum())
        return await asyncio.wait_for(writing, 1), nonzero
    finally:
        if not writing.done():
            writing.cancel()
        await asyncio.gather(writing, return_exceptions=True)


@pytest.mark.parametrize("interval_ms,immediate", [(5000, False), (1000, True)])
async def test_periodic_ping_gate_consumes_speech_or_two_seconds_of_silence(
    transport: SimpleNamespace, interval_ms: int, immediate: bool,
) -> None:
    rejected: list[int] = []
    consumed = 0
    # Old browser: first ping at 5s, then rejection at [8,10), [13,15).
    # Repaired browser: ping on open and every second, no rejected audio.
    for ms in range(0, 15000, 40):
        transport.clock.now = 1000 + ms / 1000
        if ms % interval_ms == 0 and (ms > 0 or immediate):
            await transport.ping("ping")
        accepted, nonzero = await consume_write(transport)
        assert nonzero == (960 if accepted else 0)
        consumed += nonzero
        if not accepted:
            rejected.append(ms)
    if interval_ms == 5000:
        assert rejected == [*range(8000, 10000, 40), *range(13000, 15000, 40)]
        assert consumed == 11 * 24000
    else:
        assert rejected == []
        assert consumed == 15 * 24000
    assert not transport.track._chunk_queue


async def test_missing_pings_still_reject_at_three_seconds_and_resume_only_on_ping(
    transport: SimpleNamespace,
) -> None:
    await transport.ping("ping")
    transport.clock.now += 2.999
    assert await consume_write(transport) == (True, 960)
    transport.clock.now += .001
    assert await consume_write(transport) == (False, 0)
    transport.clock.now += 10
    assert await consume_write(transport) == (False, 0)
    await transport.ping("ping")
    assert await consume_write(transport) == (True, 960)


@pytest.mark.parametrize("reason", ["not_started", "closing", "no_track", "closed", "failed"])
async def test_transport_lifecycle_still_rejects_writes(transport: SimpleNamespace, reason: str) -> None:
    await transport.ping("ping")
    if reason == "not_started":
        transport.connection._connect_invoked = False
    elif reason == "closing":
        transport.client._closing = True
    elif reason == "no_track":
        transport.client._audio_output_track = None
    else:
        transport.connection._pc.connectionState = reason
        transport.connection._close = AsyncMock()
        # Invoke the installed state handler and its registered client callbacks.
        await transport.connection._handle_new_connection_state()
        if reason == "failed":
            transport.connection._close.assert_awaited_once()
            # The mocked peer close must deliver the close event as aiortc does.
            await transport.connection._call_event_handler("closed")
        await asyncio.wait_for(transport.closed.wait(), 1)
        transport.callbacks.on_client_disconnected.assert_awaited_once()
    assert await consume_write(transport) == (False, 0)
    assert not transport.track._chunk_queue


async def test_write_cancellation_remains_cancellation(transport: SimpleNamespace) -> None:
    await transport.ping("ping")
    writing = asyncio.create_task(transport.output.write_audio_frame(
        OutputAudioRawFrame(b"\x00\x40" * 960, 24000, 1)))
    await asyncio.sleep(0)
    assert not writing.done()  # Real client awaits consumption.
    writing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await writing
    # Pinned behavior: already queued PCM may still be consumed after cancellation.
    for _ in range(4):
        assert (await transport.track.recv()).to_ndarray().any()
    assert not transport.track._chunk_queue
