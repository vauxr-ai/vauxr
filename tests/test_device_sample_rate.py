import pytest
from aiohttp.test_utils import TestClient, TestServer
from tests.auth_helpers import owner_headers
from vauxr import config
from vauxr.devices import registry
from vauxr.devices.config import load_device_configs
from vauxr.web.server import make_http_app


async def test_sample_rate_validation_persistence_and_reset(monkeypatch, tmp_path):
    monkeypatch.setenv('OWNER_HTTPS_ORIGIN', 'https://owner.example')
    monkeypatch.setenv('OWNER_TRUSTED_PROXIES', '127.0.0.1/32')
    config.reset_config()
    registry.reset()
    registry.register('speaker', None)
    try:
        async with TestClient(TestServer(make_http_app())) as client:
            headers = owner_headers(client)
            for value in [True, 16000.0, '16000', 0, -1, 12345]:
                response = await client.patch('/api/devices/speaker', headers=headers, json={'output_sample_rate': value})
                assert response.status == 400
            for value in [16000, 22050, 24000, 44100]:
                response = await client.patch('/api/devices/speaker', headers=headers, json={'output_sample_rate': value})
                assert response.status == 200
                assert (await response.json())['config']['output_sample_rate'] == value
                assert load_device_configs(str(tmp_path), strict=True)['speaker']['output_sample_rate'] == value
            response = await client.patch('/api/devices/speaker', headers=headers, json={'output_sample_rate': None})
            assert response.status == 200
            assert 'output_sample_rate' not in (await response.json())['config']
            assert 'output_sample_rate' not in load_device_configs(str(tmp_path), strict=True)['speaker']
    finally:
        registry.reset()
