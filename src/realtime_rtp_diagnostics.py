"""Metadata-only WebRTC receiver diagnostics; never records RTP payload bytes."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

log = logging.getLogger("vauxr.rtp_meta")


class ReceiverProbe:
    """Constant-space counters on one receiver; never retains media bytes."""

    def __init__(self, receiver: Any):
        self.receiver = receiver
        self.counts: dict[str, int] = {}
        self.ranges: dict[str, tuple[int, int]] = {}
        self.last_sequence = self.last_timestamp = None
        self.restores: list[tuple[Any, str, Any, Any]] = []

    def count(self, name: str, amount: int = 1) -> None:
        self.counts[name] = self.counts.get(name, 0) + amount

    def sample(self, name: str, value: int) -> None:
        low, high = self.ranges.get(name, (value, value))
        self.ranges[name] = (min(low, value), max(high, value))

    def wrap(self, obj: Any, name: str, replacement: Any) -> None:
        original = getattr(obj, name)
        self.restores.append((obj, name, original, replacement))
        setattr(obj, name, replacement)

    def restore(self) -> None:
        for obj, name, original, wrapper in reversed(self.restores):
            if getattr(obj, name) is wrapper:
                setattr(obj, name, original)
        self.restores.clear()
        if getattr(self.receiver, "_vauxr_probe", None) is self:
            delattr(self.receiver, "_vauxr_probe")

    def drain(self) -> dict[str, Any]:
        row: dict[str, Any] = dict(self.counts)
        for name, (low, high) in self.ranges.items():
            row[name + "_min"] = low
            row[name + "_max"] = high
        self.counts.clear()
        self.ranges.clear()
        thread = getattr(self.receiver, "_RTCRtpReceiver__decoder_thread", None)
        row["decoder_alive"] = bool(thread and thread.is_alive())
        queue = getattr(self.receiver, "_RTCRtpReceiver__decoder_queue", None)
        row["decoder_queue"] = queue.qsize() if queue is not None else None
        row["track_queue"] = self.receiver.track._queue.qsize()
        return row


def install(connection: Any) -> list[ReceiverProbe]:
    probes: list[ReceiverProbe] = []
    for receiver in connection.pc.getReceivers():
        if getattr(getattr(receiver, "track", None), "kind", None) != "audio":
            continue
        if getattr(receiver, "_vauxr_probe", None) is not None:
            continue
        jitter = getattr(receiver, "_RTCRtpReceiver__jitter_buffer", None)
        if jitter is None:
            continue
        probe = ReceiverProbe(receiver)
        original_add = jitter.add
        def add(packet: Any, *, _original=original_add, _probe=probe):
            # Observations must not change packet admission or exception behavior.
            try:
                _probe.count("packets")
                _probe.count("markers", int(bool(packet.marker)))
                _probe.sample("pt", int(packet.payload_type))
                _probe.sample("payload_bytes", len(packet.payload))
                if _probe.last_timestamp is not None:
                    _probe.sample("timestamp_delta", (packet.timestamp - _probe.last_timestamp) % (1 << 32))
                    _probe.sample("sequence_delta", (packet.sequence_number - _probe.last_sequence) % (1 << 16))
                _probe.last_timestamp = packet.timestamp
                _probe.last_sequence = packet.sequence_number
            except Exception:
                pass
            result = _original(packet)
            try:
                if result[1] is not None:
                    _probe.count("assembled_frames")
                    _probe.sample("assembled_bytes", len(result[1].data))
            except Exception:
                pass
            return result
        probe.wrap(jitter, "add", add)
        queue = receiver.track._queue
        original_put = queue.put
        async def put(frame: Any, *, _original=original_put, _probe=probe):
            await _original(frame)
            try:
                if frame is not None:
                    _probe.count("decoded_frames")
                    _probe.sample("decoded_samples", int(frame.samples))
            except Exception:
                pass
        probe.wrap(queue, "put", put)
        original_recv = receiver.track.recv
        async def recv(*, _original=original_recv, _probe=probe):
            frame = await _original()
            try:
                _probe.count("delivered_frames")
            except Exception:
                pass
            return frame
        probe.wrap(receiver.track, "recv", recv)
        receiver._vauxr_probe = probe
        probes.append(probe)
    return probes


def _row(stat: Any) -> dict[str, Any]:
    return {name: getattr(stat, name) for name in (
        "type", "ssrc", "kind", "packetsReceived", "packetsLost", "jitter",
        "bytesReceived", "bytesSent", "dtlsState", "state",
    ) if hasattr(stat, name)}


async def snapshot(connection: Any) -> dict[str, Any]:
    receivers = [r for r in connection.pc.getReceivers()
                 if getattr(getattr(r, "track", None), "kind", None) == "audio"]
    rows: list[dict[str, Any]] = []
    codecs: list[dict[str, Any]] = []
    sources: list[int] = []
    for receiver in receivers:
        stats = await receiver.getStats()
        rows.extend(_row(value) for value in stats.values())
        codec_map = getattr(receiver, "_RTCRtpReceiver__codecs", {})
        codecs.extend({"pt": int(pt), "mime": str(codec.mimeType),
                       "clock": int(codec.clockRate), "channels": int(codec.channels or 0)}
                      for pt, codec in codec_map.items())
        sources.extend(int(source.source) for source in receiver.getSynchronizationSources())
    transports = [_row(value) for receiver in receivers
                  for value in receiver.transport._get_stats().values()]
    return {"receivers": len(receivers), "sources": sorted(set(sources)),
            "codecs": codecs, "stats": rows, "transports": transports}


async def monitor(connection: Any, device_id: str, probes: list[ReceiverProbe] | None = None) -> None:
    seq = 0
    probes = probes if probes is not None else install(connection)
    try:
        while True:
            await asyncio.sleep(1)
            row = await snapshot(connection)
            row["jitter_input"] = [probe.drain() for probe in probes]
            row["seq"] = seq
            seq += 1
            # Device id intentionally excluded: diagnostics are correlated by the
            # surrounding session lifecycle, not by persistent identifiers.
            log.info("rtp_meta %s", json.dumps(row, separators=(",", ":"), sort_keys=True))
    finally:
        for probe in probes:
            probe.restore()
