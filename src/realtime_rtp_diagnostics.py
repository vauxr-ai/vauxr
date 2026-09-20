"""Metadata-only WebRTC receiver diagnostics; never records RTP payload bytes."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

log = logging.getLogger("vauxr.rtp_meta")


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


async def monitor(connection: Any, device_id: str) -> None:
    seq = 0
    try:
        while True:
            await asyncio.sleep(1)
            row = await snapshot(connection)
            row["seq"] = seq
            seq += 1
            # Device id intentionally excluded: diagnostics are correlated by the
            # surrounding session lifecycle, not by persistent identifiers.
            log.info("rtp_meta %s", json.dumps(row, separators=(",", ":"), sort_keys=True))
    except asyncio.CancelledError:
        raise
