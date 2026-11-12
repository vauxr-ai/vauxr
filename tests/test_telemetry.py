"""Real durable queue and production wire-path privacy/lifecycle regressions."""
import asyncio
import itertools
import json
import sqlite3
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiohttp
import jsonschema
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from tests.auth_helpers import owner_headers
from vauxr import telemetry
from vauxr.config import reset_config
from vauxr.telemetry.contract import SCHEMA, serialize
from vauxr.telemetry.http import KEY
from vauxr.telemetry.runtime import Telemetry
from vauxr.web.server import make_http_app


class Clock:
    def __init__(self, text='2026-11-30T23:59:50Z'):
        self.wall = datetime.fromisoformat(text).timestamp()
        self.mono = 100.0

    def move(self, seconds, monotonic=None):
        self.wall += seconds
        self.mono += max(0, seconds) if monotonic is None else monotonic


def runtime(tmp_path, clock, **kwargs):
    result = Telemetry(tmp_path, wall=lambda: clock.wall, mono=lambda: clock.mono, **kwargs)
    # Deterministic tests drive the production tick/send methods themselves.
    result._launch = lambda: None
    return result


async def rows(t):
    return await t._db(lambda db, s: [bytes(r[0]) for r in db.execute('SELECT body FROM queue ORDER BY rowid')])


async def state(t):
    return await t._db(lambda db, s: json.loads(json.dumps(s)))


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.delenv('VAUXR_TELEMETRY_ENABLED', raising=False)
    telemetry._current = None
    yield
    telemetry._current = None


async def test_notice_preview_and_environment_prevent_startup(tmp_path, monkeypatch):
    clock = Clock()
    sent = []
    async def sender(raw):
        sent.append(raw)
        return 200
    t = runtime(tmp_path, clock, endpoint='https://collector.invalid', sender=sender)
    await t.start()
    await t.tick()
    await t.observe('standard', 'success', 10, 'custom', 'custom')
    assert not await t.send_once()
    assert await rows(t) == [] and not sent
    proposed = (await t.view())['payload']
    assert (await state(t))['identity'] is None
    await t.decide(True)
    assert await rows(t) == [proposed.encode()]
    await t.tick()
    assert len(await rows(t)) == 1
    await t.send_once()
    assert sent == [proposed.encode()]
    await t.close()
    monkeypatch.setenv('VAUXR_TELEMETRY_ENABLED', 'false')
    off = runtime(tmp_path, clock, endpoint='https://collector.invalid', sender=sender)
    await off.start()
    await off.decide(True)
    assert not off.enabled and not await off.send_once()
    assert not await rows(off) and (await state(off))['identity'] is None
    await off.close()


async def test_restart_hour_midnight_month_accounting_and_identity(tmp_path):
    c = Clock()
    t = runtime(tmp_path, c)
    await t.start()
    await t.decide(True)
    old_identity = (await state(t))['identity']
    await t.observe('standard', 'success', 250, 'custom', 'custom')
    c.move(5)
    await t.tick()
    await t.close()
    c.move(2, monotonic=0)  # Downtime must not contribute uptime.
    t = runtime(tmp_path, c)
    await t.start()
    c.move(3)
    await t.observe('realtime', 'cancelled', 5000, 'custom', 'custom')
    c.move(3600)
    await t.tick()
    payloads = [json.loads(r) for r in await rows(t)]
    starts = [p for p in payloads if p['kind'] == 'startup']
    intervals = [p for p in payloads if p['kind'] == 'interval']
    assert len(starts) == 2  # Processes, never installations.
    assert intervals[0]['interval_start'] == '2026-11-30T23:59:50Z'
    assert intervals[0]['interval_end'] == '2026-12-01T00:00:00Z'
    assert intervals[0]['monthly_id'] == old_identity
    assert intervals[0]['metrics']['outcomes']['success'] == 1
    assert intervals[0]['metrics']['uptime_ms'] == 8000
    assert intervals[1]['metrics']['outcomes']['cancelled'] == 1
    assert intervals[1]['monthly_id'] != old_identity
    assert intervals[1]['interval_end'] == '2026-12-01T01:00:00Z'
    for p in payloads:
        jsonschema.Draft202012Validator(SCHEMA, format_checker=jsonschema.FormatChecker()).validate(p)
        serialize(p)
    await t.close()


async def test_rollback_and_saturation_preserve_accounting(tmp_path):
    c = Clock('2026-12-01T01:00:00Z')
    t = runtime(tmp_path, c)
    await t.start()
    await t.decide(True)
    await t.observe('standard', 'success', 20, 'custom', 'custom')
    c.move(-30, monotonic=30)
    await t.observe('standard', 'failure', 30, 'custom', 'custom')
    assert (await state(t))['active']['metrics']['outcomes']['failure'] == 0
    c.move(40)
    await t.tick()
    def saturate(db, s):
        s['active']['metrics']['outcomes']['success'] = 1000000
        s['active']['metrics']['latency_ms']['lt_250'] = 1000000
    await t._db(saturate)
    await t.observe('standard', 'cancelled', 5000, 'custom', 'custom')
    m = (await state(t))['active']['metrics']
    assert sum(m['outcomes'].values()) == sum(m['latency_ms'].values()) == 1000000
    old = (await state(t))['identity']
    c.move(-86400, monotonic=20)
    await t.tick()
    assert (await state(t))['identity'] is None
    await t.close()
    c.move(86401)
    t = runtime(tmp_path, c)
    await t.start()
    assert (await state(t))['identity'] not in (old, None)
    c.move(3600)
    await t.tick()
    intervals = [json.loads(r) for r in await rows(t) if json.loads(r)['kind'] == 'interval']
    for a, b in itertools.pairwise(intervals):
        assert a['interval_end'] <= b['interval_start']
    assert all(0 <= p['metrics']['uptime_ms'] <= 3600000 for p in intervals)
    await t.close()


async def test_retry_immutable_queue_bound_expiry_and_atomic_rollback(tmp_path):
    c = Clock('2026-11-11T00:00:00Z')
    seen = []
    async def outage(raw):
        seen.append(raw)
        return 503
    t = runtime(tmp_path, c, endpoint='https://collector.invalid', sender=outage)
    await t.start()
    await t.decide(True)
    assert not await t.send_once()
    c.move(10)
    assert not await t.send_once()
    assert seen[0] == seen[1]
    before = await state(t), await rows(t)
    original = t._insert
    def fail_after_insert(db, body):
        original(db, body)
        raise sqlite3.OperationalError('synthetic failure')
    t._insert = fail_after_insert
    c.move(3600)
    await t.tick()
    assert (await state(t), await rows(t)) == before
    t._insert = original
    await t.tick()
    await t._db(lambda db, s: [t._insert(db, {**json.loads(seen[0]), 'report_id': str(__import__('uuid').uuid4())})
                             for _ in range(300)])
    assert len(await rows(t)) == 256
    c.move(604801)
    await t.tick()
    assert len(await rows(t)) <= 1
    assert all(c.wall - datetime.fromisoformat(json.loads(p).get('started_at',
               json.loads(p).get('interval_start'))).timestamp() <= 604800 for p in await rows(t))
    assert t.path.stat().st_mode & 0o777 == 0o600
    await t.close()


async def test_disable_cancels_inflight_and_fences_scheduled_observations(tmp_path):
    c = Clock()
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def slow(raw):
        entered.set()
        try:
            await asyncio.sleep(100)
        finally:
            cancelled.set()
    t = runtime(tmp_path, c, endpoint='https://collector.invalid', sender=slow)
    await t.start()
    await t.decide(True)
    old_generation = t.generation
    send = asyncio.create_task(t.send_once())
    await entered.wait()
    await t.decide(False)
    with pytest.raises(asyncio.CancelledError):
        await send
    assert cancelled.is_set()
    assert not await rows(t) and (await state(t))['identity'] is None
    await t.decide(True)
    await t.observe('standard', 'success', 100, 'custom', 'custom', old_generation)
    assert sum((await state(t))['active']['metrics']['outcomes'].values()) == 0
    await t.close()


def certificate(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.now(UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
            .not_valid_after(now+timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), critical=False)
            .sign(key, hashes.SHA256()))
    cp, kp = tmp_path/'cert.pem', tmp_path/'key.pem'
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cp, kp)
    return server, ssl.create_default_context(cafile=str(cp))


async def test_production_https_emission_forbidden_sentinels_and_exact_fixtures(tmp_path, monkeypatch, caplog):
    captured = []
    async def receive(request):
        captured.append(await request.read())
        assert request.headers['Content-Type'] == 'application/json'
        return web.Response(status=202)
    app = web.Application()
    app.router.add_post('/v1/reports', receive)
    server_ssl, client_ssl = certificate(tmp_path)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0, ssl_context=server_ssl)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    original = aiohttp.ClientSession
    def trusted_test_session(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['timeout'].total == 5
        return original(connector=aiohttp.TCPConnector(ssl=client_ssl), **kwargs)
    monkeypatch.setattr(aiohttp, 'ClientSession', trusted_test_session)
    sentinel = 'FORBIDDEN_CONTENT_AUDIO_ERROR_SECRET_HOST_USER_DEVICE_URL'
    monkeypatch.setenv('OPENAI_API_KEY', sentinel)
    monkeypatch.setenv('HOSTNAME', sentinel)
    c = Clock('2026-11-11T00:00:00Z')
    t = runtime(tmp_path/'state', c, endpoint=f'https://localhost:{port}/v1/reports')
    telemetry._current = t
    try:
        await t.start()
        preview = (await t.view())['payload'].encode()
        assert not await t.send_once() and captured == []
        await t.decide(True)
        assert await t.send_once()
        assert captured[0] == preview
        telemetry.observe('standard', 'success', 1250, provider='openai', model=sentinel,
                          endpoint='https://' + sentinel)
        await asyncio.gather(*list(telemetry._pending))
        c.move(3600)
        await t.tick()
        assert await t.send_once()
        body = json.loads(captured[1])
        assert body['metrics']['providers']['custom'] == body['metrics']['models']['custom'] == 1
        assert body['metrics']['latency_ms']['1000_4999'] == 1
        for raw in captured:
            assert sentinel.encode() not in raw
            jsonschema.validate(json.loads(raw), SCHEMA)
            assert serialize(json.loads(raw)) == raw
        assert sentinel not in caplog.text
        # Actual production-emitted bytes, fixed synthetic clock but random IDs.
        artifact = Path('/tmp/vauxr-telemetry-emitted')
        artifact.mkdir(exist_ok=True)
        for label, raw in zip(('startup-v1.json', 'interval-v1.json'), captured):
            (artifact/label).write_bytes(raw)
    finally:
        await t.close()
        await runner.cleanup()


@pytest.mark.parametrize('mutation', [lambda p: {**p, 'extra': 'FORBIDDEN'},
    lambda p: {**p, 'schema_version': True}, lambda p: {**p, 'startup_count': 1.0},
    lambda p: {**p, 'context': {**p['context'], 'release': 'FORBIDDEN'}},
    lambda p: {**p, 'report_id': 'not-random'}, lambda p: {**p, 'month': '2026-13'}])
def test_strict_production_serializer(mutation):
    p = json.loads(Path('tests/fixtures/telemetry/startup-v1.json').read_text())
    with pytest.raises(ValueError):
        serialize(mutation(p))


async def test_authenticated_notice_toggle_preview_csrf_and_unknown_input(tmp_path, monkeypatch):
    reset_config()
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('OWNER_HTTPS_ORIGIN', 'https://owner.example')
    monkeypatch.setenv('OWNER_TRUSTED_PROXIES', '127.0.0.1/32')
    monkeypatch.delenv('OWNER_HTTP_ORIGIN', raising=False)
    client = TestClient(TestServer(make_http_app()))
    await client.start_server()
    try:
        assert (await client.get('/api/settings/telemetry')).status == 401
        headers = owner_headers(client)
        response = await client.get('/api/settings/telemetry', headers=headers)
        view = await response.json()
        assert response.status == 200 and not view['acknowledged'] and not view['enabled']
        assert response.headers['Cache-Control'] == 'no-store'
        no_csrf = {k:v for k,v in headers.items() if k != 'X-CSRF-Token'}
        assert (await client.put('/api/settings/telemetry', headers=no_csrf,
                json={'enabled': True, 'acknowledged': True})).status == 403
        for body in ({'enabled': 'true', 'acknowledged': True},
                     {'enabled': True, 'acknowledged': False},
                     {'enabled': True, 'acknowledged': True, 'extra': 1}):
            assert (await client.put('/api/settings/telemetry', headers=headers, json=body)).status == 400
        response = await client.put('/api/settings/telemetry', headers=headers,
                                    json={'enabled': True, 'acknowledged': True})
        assert response.status == 200
        assert (await response.json())['payload'] == view['payload']
        response = await client.put('/api/settings/telemetry', headers=headers,
                                    json={'enabled': False, 'acknowledged': True})
        assert response.status == 200 and (await response.json())['queued'] == 0
        assert (await state(client.app[KEY]))['identity'] is None
    finally:
        await client.close()
        reset_config()


async def test_real_voice_session_hook_emits_no_content_or_identifiers(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from vauxr import server
    c = Clock('2026-11-11T00:00:00Z')
    t = runtime(tmp_path, c)
    await t.start()
    await t.decide(True)
    telemetry._current = t
    completed = asyncio.Event()
    sentinel = 'FORBIDDEN_VOICE_AUDIO_TRANSCRIPT_DEVICE'
    async def voice(device_id, chunks, output, *args, **kwargs):
        assert device_id == sentinel and chunks == [sentinel.encode()]
        output.turn_completed()
        completed.set()
    monkeypatch.setattr(server, 'run_voice_turn', voice)
    monkeypatch.setattr(server.registry, 'get', lambda _: None)
    monkeypatch.setattr(server.registry, 'set_state', lambda *args: None)
    ctx = server.ConnectionCtx(state=server.ConnectionState.LISTENING,
                               device_id=sentinel, audio_chunks=[sentinel.encode()])
    await server._voice_end(SimpleNamespace(openclaw_client=None, agent_server=None),
                            SimpleNamespace(), ctx)
    await completed.wait()
    await asyncio.sleep(0)
    await asyncio.gather(*list(telemetry._pending))
    c.move(3600)
    await t.tick()
    reports = await rows(t)
    assert all(sentinel.encode() not in r for r in reports)
    interval = next(json.loads(r) for r in reports if json.loads(r)['kind'] == 'interval')
    assert interval['metrics']['sessions']['standard'] == 1
    assert interval['metrics']['outcomes']['success'] == 1
    await t.close()


async def test_worker_jitter_backoff_and_outage_isolation(tmp_path, monkeypatch):
    from vauxr.telemetry import runtime as module
    c = Clock('2026-11-11T00:00:00Z')
    attempts, uniforms = [], []
    async def unavailable(raw):
        attempts.append(c.mono)
        if len(attempts) == 3:
            t.enabled = False
        raise aiohttp.ClientConnectionError('synthetic outage')
    t = runtime(tmp_path, c, endpoint='https://collector.invalid', sender=unavailable)
    await t.start()
    await t.decide(True)
    original_sleep = asyncio.sleep
    async def advance(seconds):
        c.move(seconds)
        await original_sleep(0)
    def jitter(low, high):
        uniforms.append((low, high))
        return low
    monkeypatch.setattr(module.asyncio, 'sleep', advance)
    monkeypatch.setattr(module.random, 'uniform', jitter)
    await t._run()
    assert len(attempts) == 3 and attempts[1] - attempts[0] >= 30
    assert attempts[2] - attempts[1] >= 60
    assert uniforms[0] == (0, 5)
    assert all(high <= 3600 for low, high in uniforms)
    assert len(await rows(t)) == 1  # Outage neither loses nor mutates startup.
    await t.close()


def test_release_allowlist_cannot_be_claimed_by_runtime_env(monkeypatch):
    from vauxr.telemetry.contract import POLICY, categories, context
    monkeypatch.setenv('VAUXR_RELEASE_TAG', POLICY['released_versions'][0])
    monkeypatch.setenv('VAUXR_VERSION', POLICY['released_versions'][0])
    assert context()['release'] == 'custom'
    assert categories('openai', 'gpt-live-1', 'https://api.openai.com') == ('openai', 'gpt-live-1')
    assert categories('openai', 'gpt-live-1', 'https://api.openai.com.evil') == ('custom', 'custom')


async def test_session_consent_generation_fence(tmp_path):
    c = Clock()
    t = runtime(tmp_path, c)
    telemetry._current = t
    await t.start()
    before_notice = telemetry.begin()
    assert before_notice is None
    await t.decide(True)
    telemetry.finish(before_notice, 'standard', 'success')
    marker = telemetry.begin()
    await t.decide(False)
    await t.decide(True)
    telemetry.finish(marker, 'standard', 'success')
    assert not telemetry._pending
    await t.close()
