"""Bounded durable interval accounting and cancelable, isolated HTTPS delivery."""
from __future__ import annotations

import asyncio
import json
import os
import random
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

import aiohttp

from .contract import LIMIT, context, epoch, metrics, serialize, timestamp

# Provisioned collector URL is deliberately absent until Actions publishes it.
# Only an explicitly configured HTTPS endpoint is eligible; never use a placeholder.
COLLECTOR_URL = ''


class Telemetry:
    def __init__(self, directory: Path, *, endpoint: str = COLLECTOR_URL,
                 wall=time.time, mono=time.monotonic, sender=None, device_count=lambda: 0):
        self.path = directory / 'telemetry.sqlite3'
        self.endpoint = endpoint
        self.wall, self.mono = wall, mono
        self.sender = sender or self._http_send
        self.device_count = device_count
        self.environment_disabled = os.environ.get('VAUXR_TELEMETRY_ENABLED', '').lower() == 'false'
        self.enabled = False
        self.worker = None
        self.lock = asyncio.Lock()
        self.decision_lock = asyncio.Lock()
        self.started = int(wall())
        self.last_mono = mono()
        self.uptime_pending = 0
        self.preview = None
        self.startup_queued = False
        self.closed = False
        self.inflight = None
        self.generation = 0

    def _transaction(self, change):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Pre-create privately before sqlite opens; directory/config values are never serialized.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with sqlite3.connect(self.path, timeout=0.1) as db:
            db.execute('PRAGMA secure_delete=ON')
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS queue '
                       '(id TEXT PRIMARY KEY, body BLOB NOT NULL, born INTEGER NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT value FROM state WHERE id=1').fetchone()
            s = json.loads(row[0]) if row else {'enabled': True, 'acknowledged': False, 'high': 0,
                                               'month_high': '', 'identity': None, 'active': None}
            result = change(db, s)
            db.execute('INSERT OR REPLACE INTO state VALUES (1,?)', (json.dumps(s),))
            return result

    async def _db(self, change):
        # Cancellation must join the thread before releasing the lifecycle lock;
        # otherwise disable could race an already-started durable write.
        task = asyncio.create_task(asyncio.to_thread(self._transaction, change))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    def _base(self, s, now, kind):
        return {'schema_version': 1, 'report_id': str(uuid4()), 'monthly_id': s['identity'],
                'month': timestamp(now)[:7], 'created_at': timestamp(now),
                'context': context(self.device_count()), 'kind': kind}

    def _insert(self, db, body):
        # Persist serialized bytes once so retries cannot acquire new context or counters.
        raw = serialize(body)
        born = epoch(body.get('interval_start', body.get('started_at')))
        db.execute('INSERT OR IGNORE INTO queue VALUES (?,?,?)', (body['report_id'], raw, born))
        while True:
            n, size = db.execute('SELECT COUNT(*), COALESCE(SUM(LENGTH(body)),0) FROM queue').fetchone()
            if n <= 256 and size <= 2_097_152:
                break
            db.execute('DELETE FROM queue WHERE rowid=(SELECT MIN(rowid) FROM queue)')

    def _seal(self, db, s, end, now):
        active = s['active']
        if active is None or end <= active['start']:
            return
        body = self._base(s, active['start'], 'interval')
        body.update(created_at=timestamp(now), interval_start=timestamp(active['start']),
                    interval_end=timestamp(end), metrics=active['metrics'])
        self._insert(db, body)
        s['active'] = None

    def _advance(self, db, s, now, uptime):
        # Wall-clock rollback suspends observations and sealing until the durable
        # high-water catches up. Monotonic time alone measures runtime, never UTC.
        if now < s['high'] or timestamp(now)[:7] < s['month_high']:
            return False
        month = timestamp(now)[:7]
        active = s['active']
        if active:
            # Spread monotonic uptime over elapsed wall time conservatively. Clock
            # jumps do not manufacture uptime, and each interval remains bounded.
            elapsed = max(0, now - s['high'])
            remaining = min(uptime, elapsed * 1000)
            cursor = s['high']
            while active and cursor < now:
                end = min(now, (active['start'] // 3600 + 1) * 3600)
                amount = min(remaining, max(0, end - cursor) * 1000)
                active['metrics']['uptime_ms'] += amount
                remaining -= amount
                cursor = end
                if end == (active['start'] // 3600 + 1) * 3600:
                    # Seal before rotating identity so the closed interval keeps its original month.
                    self._seal(db, s, end, now)
                    if timestamp(end)[:7] != s['month_high']:
                        s['identity'] = str(uuid4())
                        s['month_high'] = timestamp(end)[:7]
                    s['active'] = {'start': end, 'metrics': metrics()}
                    active = s['active']
            # A long outage must not loop through unbounded empty history.
        if month != s['month_high'] or not s['identity']:
            s['identity'] = str(uuid4())
            s['month_high'] = month
        if s['active'] is None:
            s['active'] = {'start': now, 'metrics': metrics()}
        s['high'] = now
        db.execute('DELETE FROM queue WHERE born < ?', (now - 604800,))
        return True

    def _clear(self, db, s):
        db.execute('DELETE FROM queue')
        s.update(identity=None, active=None)

    async def start(self):
        async with self.lock:
            def boot(db, s):
                if self.environment_disabled or not s['enabled'] or not s['acknowledged']:
                    self._clear(db, s)
                    return False
                # Resume durable counters; monotonic uptime resets per process,
                # so downtime is never counted or intervals overlapped.
                return True
            try:
                self.enabled = await self._db(boot)
            except (OSError, sqlite3.Error, ValueError):
                self.enabled = False
        if self.enabled:
            await self.tick()
            self._launch()

    def _launch(self):
        if self.enabled and not self.closed and self.worker is None:
            self.worker = asyncio.create_task(self._run())

    async def tick(self):
        async with self.lock:
            if not self.enabled or self.closed:
                return
            now = int(self.wall())
            mono = self.mono()
            self.uptime_pending += max(0, int((mono - self.last_mono) * 1000))
            uptime = self.uptime_pending
            self.last_mono = mono
            def update(db, s):
                if now - s['high'] > 604800 and s['active']:
                    # Bounded recovery after a large forward clock jump.
                    s['active'] = None  # Expired interval falls outside the retry horizon.
                previous_high = s['high']
                if not self._advance(db, s, now, uptime):
                    if timestamp(now)[:7] < s['month_high']:
                        s['identity'] = None
                        s['active'] = None
                    return False, uptime, None
                consumed = min(uptime, max(0, now - previous_high) * 1000)
                if (not self.startup_queued and timestamp(self.started)[:7] == s['month_high']
                        and 0 <= now - self.started <= 604800):
                    candidate = self.preview
                    if candidate and candidate['monthly_id'] == s['identity']:
                        body = candidate
                    else:
                        body = self._base(s, now, 'startup')
                        body.update(started_at=timestamp(self.started), startup_count=1)
                    self._insert(db, body)
                return True, consumed, db.execute('SELECT COUNT(*) FROM queue').fetchone()[0]
            try:
                startup_handled, consumed, queued = await self._db(update)
                self.uptime_pending -= consumed
                self.startup_queued = self.startup_queued or startup_handled
                return queued
            except (OSError, sqlite3.Error, ValueError):
                pass  # Outage/storage failures are isolated from service operations.

    async def observe(self, kind, outcome, duration_ms, provider, model, generation=None):
        if generation is not None and generation != self.generation:
            return
        await self.tick()
        async with self.lock:
            if not self.enabled or self.closed or (generation is not None and generation != self.generation):
                return
            now = int(self.wall())
            def add(db, s):
                if now < s['high'] or not s['active'] or not s['identity']:
                    return
                self._advance(db, s, now, 0)
                m = s['active']['metrics']
                # Admit outcome + bucket together, including when a single bucket
                # saturates. Never independently saturate paired observations.
                if sum(m['outcomes'].values()) >= LIMIT:
                    return
                bucket = ('lt_250' if duration_ms < 250 else '250_999' if duration_ms < 1000 else
                          '1000_4999' if duration_ms < 5000 else '5000_29999' if duration_ms < 30000 else
                          'ge_30000')
                m['outcomes'][outcome] += 1
                m['latency_ms'][bucket] += 1
                for group, name in [('sessions', kind), ('providers', provider), ('models', model)]:
                    m[group][name] = min(LIMIT, m[group][name] + 1)
                for feature in (['realtime'] if kind == 'realtime' else ['stt', 'llm', 'tts']):
                    m['features'][feature] = min(LIMIT, m['features'][feature] + 1)
            try:
                await self._db(add)
            except (OSError, sqlite3.Error, ValueError, KeyError):
                pass

    async def view(self):
        async with self.lock:
            def read(db, s):
                row = db.execute('SELECT body FROM queue ORDER BY rowid LIMIT 1').fetchone()
                if row and self.enabled:
                    payload = bytes(row[0]).decode()
                elif not s['acknowledged'] and not self.environment_disabled and not self.closed:
                    if self.preview is None:
                        identity = s['identity'] or str(uuid4())
                        body = self._base({**s, 'identity': identity}, max(self.started, int(self.wall())), 'startup')
                        body.update(started_at=timestamp(self.started), month=timestamp(self.started)[:7], startup_count=1)
                        self.preview = body
                    payload = serialize(self.preview).decode()
                else:
                    payload = None
                return {'enabled': self.enabled, 'preference_enabled': s['enabled'],
                        'acknowledged': s['acknowledged'], 'environment_disabled': self.environment_disabled,
                        'endpoint_ready': bool(self.endpoint), 'payload': payload,
                        'payload_status': 'queued' if row and self.enabled else 'proposed startup',
                        'queued': db.execute('SELECT COUNT(*) FROM queue').fetchone()[0]}
            return await self._db(read)

    async def decide(self, enabled: bool):
        async with self.decision_lock:
            return await self._decide(enabled)

    async def _decide(self, enabled: bool):
        # Flip the in-memory gate before awaiting cancellation or durable cleanup.
        self.enabled = False
        self.generation += 1
        await self._stop_worker()
        async with self.lock:
            def change(db, s):
                s.update(enabled=enabled, acknowledged=True)
                if not enabled or self.environment_disabled:
                    self._clear(db, s)
                    return False
                if self.preview and not s['identity'] and timestamp(self.started)[:7] >= s['month_high']:
                    s['identity'] = self.preview['monthly_id']
                    s['month_high'] = self.preview['month']
                return True
            self.enabled = await self._db(change) and not self.environment_disabled and not self.closed
            if not self.enabled:
                self.preview = None
        if self.enabled:
            await self.tick()
            self._launch()
        return await self.view()

    async def _http_send(self, raw):
        # No environment proxies, redirects, shared secrets, cookies, or response bodies.
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5), trust_env=False) as client,
            client.post(self.endpoint, data=raw, headers={'Content-Type': 'application/json'},
                        allow_redirects=False) as response,
        ):
            return response.status

    async def send_once(self):
        if not self.enabled or self.closed or not self.endpoint.startswith('https://'):
            return False
        async with self.lock:
            now = int(self.wall())
            def next_row(db, s):
                db.execute('DELETE FROM queue WHERE born < ?', (now - 604800,))
                if now < s['high'] or not s['identity']:
                    return None
                return db.execute('SELECT id,body FROM queue ORDER BY rowid LIMIT 1').fetchone()
            row = await self._db(next_row)
        if not row or not self.enabled:
            return False
        self.inflight = asyncio.create_task(self.sender(bytes(row[1])))
        try:
            async with asyncio.timeout(5):
                status = await self.inflight
        finally:
            self.inflight = None
        async with self.lock:
            if not self.enabled or self.closed:
                return False
            # Permanent client rejection discards only this report; timeout/throttle responses retain it.
            if 200 <= status < 300 or 400 <= status < 500 and status not in (408, 429):
                await self._db(lambda db, s: db.execute('DELETE FROM queue WHERE id=?', (row[0],)))
        return 200 <= status < 300

    async def _run(self):
        delay, next_send, first = 60, None, True
        while self.enabled and not self.closed:
            try:
                queued = await self.tick()
                if queued == 0:
                    next_send, delay = None, 60
                elif queued:
                    if next_send is None:
                        next_send = self.mono() + random.uniform(0, 5 if first else 300)
                        first = False
                    if self.mono() >= next_send:
                        success = await self.send_once()
                        delay = 60 if success else min(3600, delay * 2)
                        next_send = None if success else self.mono() + random.uniform(delay / 2, delay)
            except (OSError, sqlite3.Error, ValueError, aiohttp.ClientError, TimeoutError):
                next_send = self.mono() + random.uniform(delay / 2, delay)
                delay = min(3600, delay * 2)
            await asyncio.sleep(1)

    async def _stop_worker(self):
        if self.inflight:
            self.inflight.cancel()
        worker, self.worker = self.worker, None
        if worker:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    async def close(self):
        self.closed = True
        self.enabled = False
        self.generation += 1
        await self._stop_worker()
        # Counters are already durable. Do not manufacture an interval at shutdown.
