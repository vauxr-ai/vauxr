"""Per-connection policy for ESP32 media with WebSocket application control.

Pipecat 1.9.0 has no public switch for its data-channel watchdog. Keep this
small instance-local adaptation here; never patch the shared connection class.
Recheck it (and SmallWebRTCTrack.recv) when upgrading the pinned dependency.
"""

import asyncio

from pipecat.frames.frames import OutputAudioRawFrame
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCOutputTransport


def use_websocket_control(connection: SmallWebRTCConnection) -> None:
    """Opt this ESP32 peer out of unused data-channel messages and watchdogs.

    Media, ICE/DTLS failure handling and connection teardown stay with Pipecat.
    Replacing the start hook also covers renegotiation/restart on this instance.
    """
    connection._cancel_data_channel_timeout()
    connection._outgoing_messages_queue.clear()

    def no_data_channel_timeout() -> None:
        pass

    def discard_app_message(message: object) -> None:
        # Transcript, speech and audio lifecycle controls are relayed over WS.
        pass

    connection._start_data_channel_timeout = no_data_channel_timeout
    connection.send_app_message = discard_app_message


class AudioConsumption:
    """Track actual RawAudioTrack consumption for one Pipecat 1.9 output.

    add_audio_bytes is synchronous: its returned Future is the consumption
    acknowledgement, and the stock client already awaits it. Shield that Future
    from pipeline interruption and retain it across writes so a later end marker
    cannot mistake cancellation for consumption. Failed writes fail closed until
    peer teardown. These private client hooks must be rechecked on upgrades.
    """

    def __init__(self, output: SmallWebRTCOutputTransport) -> None:
        self._client = output._client
        self._pending: set[asyncio.Future[bool]] = set()
        self._failed = False
        output.write_audio_frame = self.write_audio_frame

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        track = self._client._audio_output_track
        if not self._client._can_send() or track is None:
            self._failed = True
            return False
        if not frame.audio:
            return True  # RawAudioTrack creates an unresolvable Future for empty bytes.
        try:
            consumed = track.add_audio_bytes(frame.audio)
        except Exception:
            self._failed = True
            raise
        self._pending.add(consumed)
        consumed.add_done_callback(self._consumed)
        return await asyncio.shield(consumed)

    def _consumed(self, future: asyncio.Future[bool]) -> None:
        if not future.cancelled() and future.exception() is None and future.result() is True:
            self._pending.discard(future)
        else:
            self._failed = True

    async def drained(self) -> bool:
        # Snapshot only writes preceding this ordered output marker. Retain
        # cancelled/interrupted writes until recv resolves them or close succeeds.
        for future in tuple(self._pending):
            if future.cancelled():
                return False
            if await asyncio.shield(future) is not True:
                return False
        return not self._failed
