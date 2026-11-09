"""Real owner boundaries and durable default fallback on requested removal."""

import pytest

from tests import test_agent_rename
from tests.auth_helpers import TRANSPORT_HEADERS, owner_headers
from vauxr.agents import registry
from vauxr.speech.store import get_store

client = test_agent_rename.client


async def test_default_and_builtin_are_guarded_then_assigned_device_falls_back(client):
    registry.ensure_support()
    headers = owner_headers(client)
    assert (await client.delete('/api/agents/route', headers=headers)).status == 409
    assert registry.get_by_id('route') is not None
    store = get_store()
    store.update({'agent_id': 'route'}, device_id='speaker')
    assert registry.activate('vauxr-support')
    response = await client.delete('/api/agents/route', headers=headers)
    assert response.status == 200
    assert registry.get_by_id('route') is None
    assert 'speaker' not in store.assignments
    assert store.agent_id('speaker') == 'vauxr-support'
    from vauxr.speech.store import SpeechStore
    persisted = SpeechStore(store.path.parent, tuple(store.backends.values()))
    assert persisted.agent_id('speaker') == 'vauxr-support'
    assert 'route' not in persisted.agents


@pytest.mark.parametrize('headers,status', [({},401),
    ({**TRANSPORT_HEADERS,'Authorization':'Bearer integration-secret'},403),
    ({**TRANSPORT_HEADERS,'Authorization':'Bearer device-secret'},403)])
async def test_remove_is_owner_only(client, headers, status):
    assert (await client.delete('/api/agents/route',headers=headers)).status == status
    assert registry.get_by_id('route') is not None


async def test_remove_requires_csrf(client):
    headers={key:value for key,value in owner_headers(client).items() if key!='X-CSRF-Token'}
    assert (await client.delete('/api/agents/route',headers=headers)).status == 403
    assert registry.get_by_id('route') is not None


async def test_support_removal_survives_restart_and_other_builtin_is_protected(client):
    registry.ensure_support()
    headers = owner_headers(client)
    registry.register(registry.Agent("protected", "Protected", "openclaw", False, "2026-11-08", True))
    assert (await client.delete("/api/agents/protected", headers=headers)).status == 409
    assert (await client.delete("/api/agents/vauxr-support", headers=headers)).status == 200
    registry._reset_for_tests()
    registry.ensure_support()
    assert registry.get_by_id("vauxr-support") is None
