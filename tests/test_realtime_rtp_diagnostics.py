from types import SimpleNamespace

import pytest

from vauxr.realtime.rtp_diagnostics import snapshot


class Stats(dict):
    pass


class Receiver:
    track = SimpleNamespace(kind="audio")
    _RTCRtpReceiver__codecs = {
        111: SimpleNamespace(mimeType="audio/opus", clockRate=48000, channels=2),
    }
    transport = SimpleNamespace(_get_stats=lambda: Stats({
        "transport": SimpleNamespace(type="transport", bytesReceived=1234, dtlsState="connected")
    }))

    async def getStats(self):
        return Stats({"in": SimpleNamespace(type="inbound-rtp", ssrc=42, kind="audio",
                                             packetsReceived=7, packetsLost=1, jitter=0.25)})

    def getSynchronizationSources(self):
        return [SimpleNamespace(source=42)]


@pytest.mark.asyncio
async def test_snapshot_reports_only_receiver_metadata():
    connection = SimpleNamespace(pc=SimpleNamespace(getReceivers=lambda: [Receiver()]))
    row = await snapshot(connection)
    assert row == {
        "receivers": 1,
        "sources": [42],
        "codecs": [{"pt": 111, "mime": "audio/opus", "clock": 48000, "channels": 2}],
        "stats": [{"type": "inbound-rtp", "ssrc": 42, "kind": "audio",
                   "packetsReceived": 7, "packetsLost": 1, "jitter": 0.25}],
        "transports": [{"type": "transport", "bytesReceived": 1234, "dtlsState": "connected"}],
    }
    assert "payload" not in repr(row).lower()
