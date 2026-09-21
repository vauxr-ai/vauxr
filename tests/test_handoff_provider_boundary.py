"""Real RTP -> Pipecat resampler -> Live provider wire, no paid services."""
import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from pipecat.services.openai.live.llm import OpenAILiveLLMService
from websockets.asyncio.server import serve

import vauxr.agents.registry as agent_registry
import vauxr.devices.registry as device_registry
import vauxr.realtime.session as realtime_session
import vauxr.server as server


@pytest.mark.asyncio
@pytest.mark.parametrize('receipt_first', [True, False])
async def test_one_correlated_receipt_survives_provider_readiness_order(monkeypatch, receipt_first):
    monkeypatch.setenv('OPENAI_API_KEY', 'local-test-only')
    monkeypatch.setenv('REALTIME_AUDIO_DIAGNOSTICS', '1')
    monkeypatch.setattr(agent_registry, 'get_active', lambda: SimpleNamespace(id='selected', type='openclaw'))
    monkeypatch.setattr(server, '_authorize_message', AsyncMock(return_value=True))
    manager = realtime_session.RealtimeManager()
    monkeypatch.setattr(realtime_session, '_manager', manager)
    manager.configure(SimpleNamespace(realtime_request=AsyncMock(return_value={'instructions': '', 'messages': []})))
    ws = SimpleNamespace(closed=False, send_str=AsyncMock())
    device_registry.register('race-test', ws=ws)
    ctx = server.ConnectionCtx(device_id='race-test', realtime=True)
    ctx.turn_id = 7
    ctx.handoff_ready = True
    manager.prepare_handoff('race-test')
    handler = manager._request_handler()
    handler.update_ice_servers([])
    remote = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    remote.addTrack(AudioStreamTrack())
    start_requested, release_provider, append_received = (asyncio.Event() for _ in range(3))
    appended = []

    async def provider(socket):
        async for raw in socket:
            event = json.loads(raw)
            if event['type'] == 'session.start':
                start_requested.set()
                await release_provider.wait()
                await socket.send(json.dumps({'type': 'session.started', 'session': {'id': 'local'}}))
            elif event['type'] == 'session.input_audio.append':
                appended.append(len(base64.b64decode(event['audio'])))
                append_received.set()

    connect = OpenAILiveLLMService._connect
    async with serve(provider, '127.0.0.1', 0) as peer:
        async def local_connect(service):
            service.base_url = f'ws://127.0.0.1:{peer.sockets[0].getsockname()[1]}'
            await connect(service)
        monkeypatch.setattr(OpenAILiveLLMService, '_connect', local_connect)
        session = None
        try:
            await remote.setLocalDescription(await remote.createOffer())
            answer = await manager.handle_offer('race-test', {'type': 'offer', 'sdp': remote.localDescription.sdp})
            session = manager._sessions['race-test']
            await remote.setRemoteDescription(RTCSessionDescription(sdp=answer['sdp'], type=answer['type']))
            await asyncio.wait_for(start_requested.wait(), 5)
            # Wrong/missing IDs cannot unlock the real application input gate.
            for turn in (None, 6, True):
                await server.handle_text(server.AppState(), ws, ctx, json.dumps({'type': 'realtime.media_ready', 'turn_id': turn}))
                assert session._handoff_pending
            if not receipt_first:
                release_provider.set()
                async with asyncio.timeout(3):
                    while not session._live_service._session_started:
                        await asyncio.sleep(.01)
            # Exactly ONE valid receipt; no fabricated device retry after ready.
            await server.handle_text(server.AppState(), ws, ctx, json.dumps({'type': 'realtime.media_ready', 'turn_id': 7}))
            if receipt_first:
                await asyncio.sleep(.1)
                assert not append_received.is_set()
                release_provider.set()
            await asyncio.wait_for(append_received.wait(), 3)
            assert ctx.realtime_media and not session._handoff_pending
            assert any(size > 0 for size in appended)
            stats = await session._connection.pc.getReceivers()[0].getStats()
            assert any(getattr(s, 'packetsReceived', 0) > 0 for s in stats.values() if s.type == 'inbound-rtp')
            print('real RTP -> decoded -> resampled -> provider append bytes:', appended[:4])
        finally:
            release_provider.set()
            await asyncio.wait_for(manager.stop('race-test'), 5)
            if session and session._runner:
                await session._runner.cancel()
                await asyncio.wait_for(asyncio.shield(session._runner_task), 5)
            await handler.close()
            await remote.close()
            device_registry.unregister('race-test')
