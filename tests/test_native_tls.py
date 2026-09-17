"""Native listener, immutable TLS reload and isolated Certbot lifecycle tests."""

import asyncio
import ssl
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from aiohttp import ClientConnectionError, ClientSession, TCPConnector, web

import auth
import config
import native_tls
from native_tls import CertificateContext, TLSConfig, TLSService, load_tls_config
from server import make_app, run_server
from tests.test_optional_tls import tls_contexts


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in list(__import__('os').environ):
        if name.startswith(('HTTPS_', 'ACME_', 'OWNER_')):
            monkeypatch.delenv(name)
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('REALTIME_ENABLED', '0')
    config.reset_config()
    auth._store = None
    yield
    config.reset_config()
    auth._store = None


def manual(tmp_path):
    return TLSConfig(True, 8443, str(tmp_path / 'cert.pem'), str(tmp_path / 'key.pem'), '127.0.0.1')


def enable(monkeypatch):
    monkeypatch.setenv('HTTPS_ENABLED', '1')
    monkeypatch.setenv('OWNER_HTTPS_ORIGIN', 'https://voice.example:8443')


def automatic(tmp_path):
    cfg = replace(manual(tmp_path), acme=True, domain='voice.example', email='ops@example.com',
                  staging=True, state_dir=str(tmp_path / 'acme'))
    return cfg


@pytest.mark.parametrize('values', [
    {'HTTPS_CERT_FILE': 'one'}, {'HTTPS_PORT': '8443'}, {'ACME_ROUTE53_ENABLED': '1'},
    {'HTTPS_ENABLED': 'maybe'}, {'HTTPS_ENABLED': '1'},
    {'HTTPS_ENABLED': '1', 'OWNER_HTTPS_ORIGIN': 'https://voice.example', 'HTTPS_CERT_FILE': 'one'},
    {'HTTPS_ENABLED': '1', 'OWNER_HTTPS_ORIGIN': 'https://voice.example', 'HTTPS_PORT': '8080'},
    {'HTTPS_ENABLED': '1', 'OWNER_HTTPS_ORIGIN': 'https://voice.example', 'HTTPS_PORT': '65536'},
])
def test_invalid_configuration(monkeypatch, tmp_path, values):
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        load_tls_config(str(tmp_path))


def test_automated_configuration(monkeypatch, tmp_path):
    assert not load_tls_config(str(tmp_path)).enabled
    enable(monkeypatch)
    monkeypatch.setenv('ACME_ROUTE53_ENABLED', '1')
    monkeypatch.setenv('ACME_DOMAIN', 'voice.example')
    monkeypatch.setenv('ACME_EMAIL', 'ops@example.com')
    with pytest.raises(ValueError, match='ACME_ACCEPT_TOS'):
        load_tls_config(str(tmp_path))
    monkeypatch.setenv('ACME_ACCEPT_TOS', '1')
    production = load_tls_config(str(tmp_path))
    assert production.cert.endswith('/production/config/live/voice.example/fullchain.pem')
    monkeypatch.setenv('ACME_STAGING', '1')
    assert '/staging/' in load_tls_config(str(tmp_path)).cert
    monkeypatch.setenv('ACME_DOMAIN', '../bad')
    with pytest.raises(ValueError, match='ACME_DOMAIN'):
        load_tls_config(str(tmp_path))


@pytest.mark.parametrize('case', ['expired', 'hostname'])
def test_invalid_certificates(tmp_path, case):
    tls_contexts(tmp_path, case)
    with pytest.raises(ValueError):
        CertificateContext(manual(tmp_path)).reload()


async def test_actual_https_wss_reload_and_plaintext_boundary(tmp_path, monkeypatch, unused_tcp_port):
    _, client_ssl = tls_contexts(tmp_path, 'valid')
    cfg = manual(tmp_path)
    context = CertificateContext(cfg)
    assert context.reload()
    first = context.active
    assert first.minimum_version == ssl.TLSVersion.TLSv1_2
    assert first.options & ssl.OP_NO_COMPRESSION
    assert first.num_tickets == 0
    assert not context.reload()
    monkeypatch.setenv('OWNER_HTTPS_ORIGIN', f'https://127.0.0.1:{unused_tcp_port}')
    runner = web.AppRunner(make_app())
    await runner.setup()
    secure = web.TCPSite(runner, '127.0.0.1', unused_tcp_port, ssl_context=context.listener)
    plain = web.TCPSite(runner, '127.0.0.1', 0)
    await secure.start()
    await plain.start()
    base = f'https://127.0.0.1:{unused_tcp_port}'
    try:
        async with ClientSession(connector=TCPConnector(force_close=True)) as client:  # noqa: SIM117
            async with client.ws_connect(base + '/ws', ssl=client_ssl, autoping=False) as ws:
                response = await client.get(base + '/api/auth/status', ssl=client_ssl)
                assert response.status == 200
                # Even a spoofed HTTPS authority/forwarded scheme on the compatibility port is denied.
                plain_port = plain._server.sockets[0].getsockname()[1]
                response = await client.get(f'http://127.0.0.1:{plain_port}/api/auth/status',
                                            headers={'Host': f'127.0.0.1:{unused_tcp_port}',
                                                     'X-Forwarded-Proto': 'https'})
                assert response.status == 403
                async with client.ws_connect(f'http://127.0.0.1:{plain_port}/ws') as device:
                    await device.send_json({'type': 'hello', 'device_id': 'test', 'token': 'invalid'})
                    assert (await device.receive_json())['code'] == 'UNAUTHORIZED'
                # Renew to an independently generated certificate and verify it on a new connection.
                _, renewed_client = tls_contexts(tmp_path, 'valid')
                assert context.reload() and context.active is not first
                response = await client.get(base + '/api/auth/status', ssl=renewed_client)
                assert response.status == 200
                await ws.ping(b'after-renewal')
                assert (await ws.receive(timeout=2)).data == b'after-renewal'
                valid = context.active
                Path(cfg.key).write_text('broken')
                with pytest.raises((ValueError, ssl.SSLError)):
                    context.reload()
                assert context.active is valid
                response = await client.get(base + '/api/auth/status', ssl=renewed_client)
                assert response.status == 200
                await ws.ping(b'after-failure')
                assert (await ws.receive(timeout=2)).data == b'after-failure'
                context.expires = datetime.now(UTC) - timedelta(seconds=1)
                with pytest.raises(ClientConnectionError):
                    await client.get(base + '/api/auth/status', ssl=renewed_client)
    finally:
        await runner.cleanup()


async def test_issuance_must_finish_before_ready_and_lock_is_exclusive(tmp_path, monkeypatch):
    cfg = automatic(tmp_path)
    service = TLSService(cfg)
    calls = []

    async def issue(config):
        calls.append(config)
        assert service.context.active is None
        tls_contexts(tmp_path, 'valid')

    monkeypatch.setattr(native_tls, 'run_certbot', issue)
    try:
        await service.prepare()
        assert len(calls) == 1
        duplicate = TLSService(cfg)
        with pytest.raises(RuntimeError, match='already owns'):
            await duplicate.prepare()
        await duplicate.close()
    finally:
        await service.close()
    replacement = TLSService(cfg)
    await replacement.prepare()
    await replacement.close()


async def test_renewal_failure_retains_context_and_worker_stops(tmp_path, monkeypatch, caplog):
    tls_contexts(tmp_path, 'valid')
    service = TLSService(automatic(tmp_path), reload_interval=0.01, renewal_interval=3600)
    entered = asyncio.Event()
    calls = []

    async def fail(_config):
        calls.append(1)
        entered.set()
        raise RuntimeError('SECRET MUST NOT BE LOGGED')

    monkeypatch.setattr(native_tls, 'run_certbot', fail)
    await service.prepare()
    active = service.context.active
    service.start()
    task = service.task
    service.start()
    assert service.task is task
    await asyncio.wait_for(entered.wait(), 2)
    await service.close()
    assert task.done() and service.task is None and service.lock is None
    assert service.context.active is active and len(calls) == 1
    assert 'SECRET' not in caplog.text and 'renewal failed' in caplog.text


async def test_subprocess_arguments_timeout_and_cancellation(tmp_path, monkeypatch):
    original = asyncio.create_subprocess_exec
    children = []
    commands = []

    async def spawn(*args, **kwargs):
        commands.append((args, kwargs))
        child = await original(sys.executable, '-c', 'import time; time.sleep(60)', **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    with pytest.raises(TimeoutError):
        await native_tls.run_certbot(automatic(tmp_path), timeout=0.02)
    assert children[0].returncode is not None
    args, kwargs = commands[0]
    assert '--dns-route53' in args and '--keep-until-expiring' in args
    assert 'https://acme-staging-v02.api.letsencrypt.org/directory' in args
    assert 'shell' not in kwargs and kwargs['stderr'] == asyncio.subprocess.DEVNULL
    task = asyncio.create_task(native_tls.run_certbot(automatic(tmp_path)))
    while len(children) < 2:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert children[1].returncode is not None


async def test_initial_failure_never_opens_listeners(tmp_path, monkeypatch):
    enable(monkeypatch)
    monkeypatch.setenv('ACME_ROUTE53_ENABLED', '1')
    monkeypatch.setenv('ACME_DOMAIN', 'voice.example')
    monkeypatch.setenv('ACME_EMAIL', 'ops@example.com')
    monkeypatch.setenv('ACME_ACCEPT_TOS', '1')
    opened = []

    async def fail(_config):
        raise RuntimeError('issuance failed')

    async def start(_site):
        opened.append(1)

    monkeypatch.setattr(native_tls, 'run_certbot', fail)
    monkeypatch.setattr(web.TCPSite, 'start', start)
    with pytest.raises(RuntimeError, match='issuance failed'):
        await run_server(make_app())
    assert not opened


async def test_real_server_three_listeners_and_cleanup(tmp_path, monkeypatch, unused_tcp_port_factory):
    ports = [unused_tcp_port_factory() for _ in range(3)]
    _, client_ssl = tls_contexts(tmp_path, 'valid')
    monkeypatch.setenv('HTTPS_ENABLED', '1')
    monkeypatch.setenv('HTTPS_PORT', str(ports[2]))
    monkeypatch.setenv('HTTPS_CERT_FILE', str(tmp_path / 'cert.pem'))
    monkeypatch.setenv('HTTPS_KEY_FILE', str(tmp_path / 'key.pem'))
    monkeypatch.setenv('OWNER_HTTPS_ORIGIN', f'https://127.0.0.1:{ports[2]}')
    monkeypatch.setenv('HTTP_PORT', str(ports[0]))
    monkeypatch.setenv('WS_PORT', str(ports[1]))
    started = asyncio.Event()
    original_start = native_tls.TLSService.start
    services = []

    def start(service):
        original_start(service)
        services.append(service)
        started.set()

    monkeypatch.setattr(native_tls.TLSService, 'start', start)
    task = asyncio.create_task(run_server(make_app()))
    try:
        await asyncio.wait_for(started.wait(), 3)
        async with ClientSession() as client:
            for port in ports[:2]:
                response = await client.get(f'http://127.0.0.1:{port}/api/auth/status')
                assert response.status == 403
                async with client.ws_connect(f'http://127.0.0.1:{port}/ws') as ws:
                    await ws.send_json({'type': 'hello', 'device_id': 'test', 'token': 'invalid'})
                    assert (await ws.receive_json())['code'] == 'UNAUTHORIZED'
            base = f'https://127.0.0.1:{ports[2]}'
            assert (await client.get(base + '/api/auth/status', ssl=client_ssl)).status == 200
            async with client.ws_connect(base + '/ws', ssl=client_ssl) as ws:
                await ws.send_json({'type': 'hello', 'device_id': 'test', 'token': 'invalid'})
                assert (await ws.receive_json())['code'] == 'UNAUTHORIZED'
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert services[0].task is None
    for port in ports:
        with pytest.raises(OSError):
            await asyncio.open_connection('127.0.0.1', port)


async def test_certbot_nonzero_and_worker_cancellation(tmp_path, monkeypatch, caplog):
    original = asyncio.create_subprocess_exec

    async def failed_process(*_args, **kwargs):
        return await original(sys.executable, '-c', 'raise SystemExit(1)', **kwargs)

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', failed_process)
    with pytest.raises(RuntimeError, match='Certbot failed'):
        await native_tls.run_certbot(automatic(tmp_path))
    entered = asyncio.Event()
    stopped = asyncio.Event()

    async def waiting(_config):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    tls_contexts(tmp_path, 'valid')
    monkeypatch.setattr(native_tls, 'run_certbot', waiting)
    service = TLSService(automatic(tmp_path))
    await service.prepare()
    service.start()
    await asyncio.wait_for(entered.wait(), 2)
    await asyncio.wait_for(service.close(), 2)
    assert stopped.is_set() and service.lock is None


async def test_cancel_startup_releases_automation_lock(tmp_path, monkeypatch):
    cfg = automatic(tmp_path)
    entered = asyncio.Event()

    async def waiting(_config):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(native_tls, 'run_certbot', waiting)
    monkeypatch.setattr(config, '_config', replace(config.load_config(), tls=cfg))
    task = asyncio.create_task(run_server(make_app()))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # A fresh service can acquire the same lock after cancellation.
    tls_contexts(tmp_path, 'valid')
    replacement = TLSService(cfg)
    await replacement.prepare()
    await replacement.close()


async def test_successful_issuance_with_invalid_output_fails_closed(tmp_path, monkeypatch):
    async def invalid(_config):
        tls_contexts(tmp_path, 'expired')

    monkeypatch.setattr(native_tls, 'run_certbot', invalid)
    service = TLSService(automatic(tmp_path))
    try:
        with pytest.raises(ValueError, match='validity'):
            await service.prepare()
        assert service.context.active is None and service.task is None
        with pytest.raises(RuntimeError, match='not ready'):
            service.start()
    finally:
        await service.close()


async def test_worker_rejects_invalid_renewal_then_loads_valid_one(tmp_path, monkeypatch, caplog):
    tls_contexts(tmp_path, 'valid')
    service = TLSService(automatic(tmp_path), reload_interval=0.01)
    called = asyncio.Event()

    async def invalid(_config):
        Path(service.config.cert).write_text('not a certificate')
        called.set()

    monkeypatch.setattr(native_tls, 'run_certbot', invalid)
    await service.prepare()
    first = service.context.active
    service.start()
    try:
        await asyncio.wait_for(called.wait(), 2)
        assert service.context.active is first
        assert 'reload rejected' in caplog.text
        tls_contexts(tmp_path, 'valid')
        async with asyncio.timeout(2):
            while service.context.active is first:
                await asyncio.sleep(0.01)
    finally:
        await service.close()


def test_mismatched_key_retains_context(tmp_path):
    tls_contexts(tmp_path, 'valid')
    service = CertificateContext(manual(tmp_path))
    service.reload()
    first = service.active
    original_cert = (tmp_path / 'cert.pem').read_bytes()
    tls_contexts(tmp_path, 'valid')
    (tmp_path / 'cert.pem').write_bytes(original_cert)
    with pytest.raises(ssl.SSLError):
        service.reload()
    assert service.active is first
