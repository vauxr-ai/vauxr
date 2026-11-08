"""Content-free, minute-granularity device usage counters retained for 30 days."""
from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from vauxr.config import get_config

WINDOWS = {"1h": (3600, 60), "24h": (86400, 3600), "7d": (604800, 21600), "30d": (2592000, 86400)}
METRICS = [
    {"key": "stt_transcriptions", "label": "STT transcriptions", "definition": "Successful nonempty standard speech transcriptions (WebSocket and WebRTC)."},
    {"key": "tts_syntheses", "label": "TTS syntheses", "definition": "Completed nonempty standard speech synthesis segments, including announcements; not playback confirmations or whole replies."},
    {"key": "realtime_user_turns", "label": "Realtime user turns", "definition": "Nonempty user transcript turns finalized by the realtime provider quiet-gap boundary; not standard STT calls."},
    {"key": "realtime_assistant_turns", "label": "Realtime assistant turns", "definition": "Nonempty assistant transcript turns finalized by the realtime provider quiet-gap boundary; not TTS calls or playback confirmations."},
]
KEYS = tuple(m["key"] for m in METRICS)
RETENTION = 30 * 86400


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")


class MetricsStore:
    def __init__(self, directory: Path, *, now: float | None = None):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "device-metrics.sqlite3"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS counters (device TEXT, minute INTEGER, metric TEXT, count INTEGER NOT NULL, PRIMARY KEY(device,minute,metric))")
            db.execute("CREATE INDEX IF NOT EXISTS counters_retention ON counters(minute)")
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value REAL)")
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('observed_since', ?)", (time.time() if now is None else now,))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=1)
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, device_id: str, metric: str, *, now: float | None = None):
        if metric not in KEYS:
            raise ValueError("unknown metric")
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute("DELETE FROM counters WHERE minute < ?", (int((now - RETENTION) // 60) * 60,))
            db.execute("INSERT INTO counters VALUES (?, ?, ?, 1) ON CONFLICT(device,minute,metric) DO UPDATE SET count=count+1", (device_id, int(now // 60) * 60, metric))

    def view(self, device_id: str, window: str, *, now: float | None = None):
        duration, size = WINDOWS[window]
        now = time.time() if now is None else now
        # End at the current bucket, with exactly duration/size buckets.
        start = int(now // size) * size - duration + size
        buckets = [{"start": iso(t), **dict.fromkeys(KEYS, 0)} for t in range(start, start + duration, size)]
        totals = dict.fromkeys(KEYS, 0)
        with self.connect() as db:
            db.execute("DELETE FROM counters WHERE minute < ?", (int((now - RETENTION) // 60) * 60,))
            observed = db.execute("SELECT value FROM metadata WHERE key='observed_since'").fetchone()[0]
            rows = db.execute("SELECT minute,metric,count FROM counters WHERE device=? AND minute>=? AND minute<=?", (device_id, start, now)).fetchall()
        for minute, metric, count in rows:
            if metric in totals:
                buckets[(minute - start) // size][metric] += count
                totals[metric] += count
        return {"device_id": device_id, "window": window, "bucket_seconds": size, "observed_since": iso(observed), "retention_days": 30, "metrics": METRICS, "totals": totals, "buckets": buckets}


_store = None
_store_dir = None


def get_store() -> MetricsStore:
    global _store, _store_dir
    directory = Path(get_config().data_dir)
    if _store is None or directory != _store_dir:
        _store = MetricsStore(directory)
        _store_dir = directory
    return _store


def record(device_id: str | None, metric: str):
    if device_id is None:
        return
    try:
        get_store().record(device_id, metric)
    except (OSError, sqlite3.Error):
        # Telemetry must never interrupt voice delivery.
        logging.getLogger(__name__).exception("Device usage counter could not be persisted")
