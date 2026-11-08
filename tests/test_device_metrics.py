import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vauxr.devices.metrics import MetricsStore, WINDOWS


def test_buckets_persistence_isolation_and_retention(tmp_path):
    now = 1800000000
    store = MetricsStore(tmp_path, now=now)
    store.record('one', 'stt_transcriptions', now=now)
    store.record('one', 'stt_transcriptions', now=now + 20)
    store.record('two', 'tts_syntheses', now=now)
    store = MetricsStore(tmp_path, now=now + 100)
    for window, (duration, size) in WINDOWS.items():
        view = store.view('one', window, now=now + 30)
        assert len(view['buckets']) == duration // size
        assert view['totals']['stt_transcriptions'] == 2
        assert view['totals']['tts_syntheses'] == 0
        assert sum(b['stt_transcriptions'] for b in view['buckets']) == 2
        assert view['observed_since'].startswith('2027-01-15')
    store.view('one', '30d', now=now + 31 * 86400)
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM counters').fetchone()[0] == 0


async def test_metrics_owner_only_and_validation(tmp_path, monkeypatch):
    from tests.auth_helpers import owner_headers, seed
    from vauxr.auth.policy import Role
    from vauxr.web.server import make_http_app
    from vauxr.devices import registry
    from vauxr import config

    monkeypatch.setenv('OWNER_HTTPS_ORIGIN', 'https://owner.example')
    monkeypatch.setenv('OWNER_TRUSTED_PROXIES', '127.0.0.1/32')
    config.reset_config()
    monkeypatch.setattr(registry, 'get', lambda id: SimpleNamespace(id=id) if id == 'one' else None)
    seed('device-secret', Role.DEVICE, 'one')
    seed('integration-secret', Role.INTEGRATION, 'agent')
    async with TestClient(TestServer(make_http_app())) as client:
        path = '/api/devices/one/metrics'
        assert (await client.get(path)).status == 401
        assert (await client.get(path + '/1h')).status == 401
        for token in ['device-secret', 'integration-secret']:
            assert (await client.get(path, headers={'Authorization': f'Bearer {token}'})).status == 403
            assert (await client.get(path + '/1h', headers={'Authorization': f'Bearer {token}'})).status == 403
        headers = owner_headers(client)
        assert (await client.get('/api/devices/absent/metrics', headers=headers)).status == 404
        assert (await client.get(path + '?window=forever', headers=headers)).status == 400
        assert (await client.get(path + '/forever', headers=headers)).status == 400
        assert (await client.get('/api/devices/absent/metrics/1h', headers=headers)).status == 404
        response = await client.get(path + '/1h?window=30d', headers=headers)
        assert response.status == 200
        data = await response.json()
        assert data['window'] == '1h'
        assert len(data['buckets']) == 60
        response = await client.get(path, headers=headers)
        assert response.status == 200
        assert (await response.json())['totals']['tts_syntheses'] == 0


@pytest.mark.parametrize('failure', [False, True])
async def test_success_only_provider_counters(tmp_path, failure):
    from vauxr.speech.wyoming_protocol import WyomingEvent, encode_event
    from vauxr.speech.wyoming_stt import transcribe
    from vauxr.speech.wyoming_tts import synthesize
    from vauxr.speech.store import resolve
    from vauxr.devices.metrics import get_store

    async def stt_peer(reader, writer):
        await reader.read(8192)
        writer.write(encode_event(WyomingEvent('error' if failure else 'transcript', {'text': 'private speech'})))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def tts_peer(reader, writer):
        await reader.read(8192)
        writer.write(encode_event(WyomingEvent('audio-start', {'rate': 24000})))
        writer.write(encode_event(WyomingEvent('audio-chunk', {}, b'\0\0')))
        writer.write(encode_event(WyomingEvent('error' if failure else 'audio-stop')))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    selection = resolve()
    async with await asyncio.start_server(stt_peer, '127.0.0.1', 0) as stt, await asyncio.start_server(tts_peer, '127.0.0.1', 0) as tts:
        selection = replace(selection, stt=replace(selection.stt, host='127.0.0.1', port=stt.sockets[0].getsockname()[1]), tts=replace(selection.tts, host='127.0.0.1', port=tts.sockets[0].getsockname()[1]))
        async def run():
            await transcribe([b'\0\0'], backend=selection.stt, device_id='one')
        async def run_tts():
            return [c async for c in synthesize('private speech', selection=selection, device_id='one')]
        for action in [run, run_tts]:
            if failure:
                with pytest.raises(Exception):
                    await action()
            else:
                await action()
    totals = get_store().view('one', '1h')['totals']
    assert totals['stt_transcriptions'] == (0 if failure else 1)
    assert totals['tts_syntheses'] == (0 if failure else 1)
    assert b'private speech' not in get_store().path.read_bytes()


async def test_cancelled_tts_not_counted(tmp_path):
    from vauxr.speech.wyoming_protocol import WyomingEvent, encode_event
    from vauxr.speech.wyoming_tts import synthesize
    from vauxr.speech.store import resolve
    from vauxr.devices.metrics import get_store

    async def peer(reader, writer):
        await reader.read(8192)
        for event in [WyomingEvent('audio-start', {'rate': 24000}),
                      WyomingEvent('audio-chunk', {}, b'\0\0'), WyomingEvent('audio-stop')]:
            writer.write(encode_event(event))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async with await asyncio.start_server(peer, '127.0.0.1', 0) as server:
        selection = resolve()
        selection = replace(selection, tts=replace(selection.tts, host='127.0.0.1', port=server.sockets[0].getsockname()[1]))
        abort = asyncio.Event()
        async for chunk in synthesize('cancelled', selection=selection, device_id='one', abort_event=abort):
            assert chunk
            abort.set()
    assert get_store().view('one', '1h')['totals']['tts_syntheses'] == 0
