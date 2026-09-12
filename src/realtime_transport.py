"""Per-connection policy for ESP32 media with WebSocket application control.

Pipecat 1.9.0 has no public switch for its data-channel watchdog. Keep this
small instance-local adaptation here; never patch the shared connection class.
Recheck it (and SmallWebRTCTrack.recv) when upgrading the pinned dependency.
"""

from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection


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
