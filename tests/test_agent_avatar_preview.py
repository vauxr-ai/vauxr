"""Real owner boundary, durable raster storage and production Wyoming preview."""

import asyncio
import io
import json
import struct
import wave
from types import SimpleNamespace

import pytest
from PIL import Image

from tests import test_agent_rename
from tests.auth_helpers import TRANSPORT_HEADERS, owner_headers, seed
from vauxr.agents import registry as agents
from vauxr.auth import connections
from vauxr.auth.policy import Role
from vauxr.auth.service import authenticate
from vauxr.auth.service import get_store as auth_store
from vauxr.devices import registry
from vauxr.speech.models import Backend
from vauxr.speech.store import get_store
from vauxr.speech.wyoming_protocol import WyomingEvent, encode_event

client = test_agent_rename.client


def png():
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, format="PNG")
    return output.getvalue()


async def test_avatar_persistence_and_validation(client):
    headers = {**owner_headers(client), "Content-Type": "image/png"}
    response = await client.post("/api/agents/route/avatar", headers=headers, data=png())
    assert response.status == 200
    metadata = await response.json()
    agents._reset_for_tests()
    response = await client.get(metadata["avatar_url"], headers=headers)
    assert response.status == 200
    Image.open(io.BytesIO(await response.read())).verify()
    listed = await (await client.get("/api/agents", headers=headers)).json()
    assert next(a for a in listed if a["id"] == "route")["avatar_revision"] == metadata["avatar_revision"]
    for content_type, data in [("image/svg+xml", b"<svg/>"), ("image/png", b"bad"), ("image/jpeg", png())]:
        response = await client.post(
            "/api/agents/route/avatar", headers={**headers, "Content-Type": content_type}, data=data
        )
        assert response.status == 400
    response = await client.post(
        "/api/agents/route/avatar", headers=headers, data=b"x" * (2 * 1024 * 1024 + 1)
    )
    assert response.status == 413
    assert (await client.post("/api/agents/missing/avatar", headers=headers, data=png())).status == 404
    assert agents.remove("route")
    from vauxr.agents.avatars import metadata as view

    assert view("route") == {}


async def test_avatar_auth_csrf(client):
    headers = owner_headers(client)
    for invalid, status in [
        ({}, 401),
        ({**TRANSPORT_HEADERS, "Authorization": "Bearer device-secret"}, 403),
        ({k: v for k, v in headers.items() if k != "X-CSRF-Token"}, 403),
    ]:
        assert (await client.post("/api/agents/route/avatar", headers=invalid, data=png())).status == status


@pytest.fixture
async def browser(client):
    seed("browser-proof", Role.DEVICE, "browser")
    principal = authenticate("browser-proof")
    store = auth_store()
    # Seed only lifecycle metadata; credential admission still uses real authentication.
    from vauxr.provisioning.lifecycle_schema import empty_state

    store.lifecycle = empty_state()
    store.lifecycle["bindings"]["browser"] = {"kind": "browser", "public_key": "ab" * 32}
    store.replace(store.records)
    entry = registry.register("browser", SimpleNamespace(closed=False))

    async def close():
        entry.ws.closed = True

    retained = connections.retain(principal, close)
    yield entry
    connections.release(retained)
    registry.unregister("browser")


@pytest.mark.parametrize("fragmented", [False, True])
async def test_preview_real_protocol_and_no_mutation(client, browser, fragmented):
    events = []

    async def provider(reader, writer):
        try:
            event = json.loads(await reader.readline())
            events.append(event)
            writer.write(
                encode_event(WyomingEvent("audio-start", {"rate": 24000, "width": 2, "channels": 1}))
            )
            writer.write(
                encode_event(
                    WyomingEvent(
                        "audio-chunk",
                        {"rate": 24000, "width": 2, "channels": 1},
                        struct.pack("<hhh", 1, 2, 3),
                    )
                )
            )
            writer.write(encode_event(WyomingEvent("audio-stop", {})))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(provider, "127.0.0.1", 0)
    store = get_store()
    snapshot = store.path.read_bytes()
    store.backends["preview"] = Backend(
        "preview", "tts", "wyoming", "test", "127.0.0.1", server.sockets[0].getsockname()[1], ("chosen",)
    )
    try:
        headers = {
            **owner_headers(client),
            "X-Vauxr-Browser": "browser-proof",
            "Content-Type": "application/json",
        }
        body = json.dumps({"device_id": "browser", "tts_backend": "preview", "voice": "chosen"}).encode()

        async def fragments():
            yield body[:8]
            await asyncio.sleep(0.01)
            yield body[8:]

        response = await client.post(
            "/api/speech/preview", headers=headers, data=fragments() if fragmented else body
        )
        assert response.status == 200, await response.text() if response.status != 200 else ""
        assert response.content_type == "audio/wav"
        with wave.open(io.BytesIO(await response.read())) as wav:
            assert wav.getframerate() == 24000
            assert wav.readframes(3) == struct.pack("<hhh", 1, 2, 3)
        assert events[0]["type"] == "synthesize"
        assert events[0]["data"]["voice"] == {"name": "chosen"}
        assert events[0]["data"]["text"] == "Hello! This is a preview of my voice."
        assert store.path.read_bytes() == snapshot
    finally:
        server.close()
        await server.wait_closed()


async def test_preview_denies_wrong_identity_and_disconnected(client, browser):
    headers = {**owner_headers(client), "X-Vauxr-Browser": "browser-proof"}
    body = {"device_id": "other", "tts_backend": "piper", "voice": "chosen"}
    assert (await client.post("/api/speech/preview", headers=headers, json=body)).status == 400
    browser.ws.closed = True
    body["device_id"] = "browser"
    assert (await client.post("/api/speech/preview", headers=headers, json=body)).status == 409
    for proof in ["", "device-secret"]:
        assert (
            await client.post("/api/speech/preview", headers={**headers, "X-Vauxr-Browser": proof}, json=body)
        ).status == 403


@pytest.mark.parametrize("mode", ["error", "disconnect", "cancel"])
async def test_preview_failure_and_cancel_close_provider(client, browser, mode):
    started, closed = asyncio.Event(), asyncio.Event()

    async def provider(reader, writer):
        try:
            await reader.readline()
            started.set()
            if mode == "error":
                writer.write(encode_event(WyomingEvent("error", {"text": "private upstream details"})))
                await writer.drain()
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            closed.set()

    server = await asyncio.start_server(provider, "127.0.0.1", 0)
    store = get_store()
    snapshot = store.path.read_bytes()
    store.backends["preview"] = Backend(
        "preview", "tts", "wyoming", "test", "127.0.0.1", server.sockets[0].getsockname()[1], ("chosen",)
    )
    headers = {**owner_headers(client), "X-Vauxr-Browser": "browser-proof"}
    task = asyncio.create_task(
        client.post(
            "/api/speech/preview",
            headers=headers,
            json={"device_id": "browser", "tts_backend": "preview", "voice": "chosen"},
        )
    )
    try:
        await asyncio.wait_for(started.wait(), 2)
        if mode == "disconnect":
            browser.ws.closed = True
        if mode == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            response = await asyncio.wait_for(task, 2)
            assert response.status == 503
            assert "private" not in await response.text()
        await asyncio.wait_for(closed.wait(), 2)
        assert store.path.read_bytes() == snapshot
    finally:
        server.close()
        await server.wait_closed()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_preview_owner_csrf_and_selection_validation(client, browser):
    headers = {**owner_headers(client), "X-Vauxr-Browser": "browser-proof"}
    body = {"device_id": "browser", "tts_backend": "missing", "voice": "missing"}
    for invalid, status in [
        ({**headers, "Cookie": ""}, 401),
        ({**headers, "X-CSRF-Token": ""}, 403),
        ({**headers, "Origin": "https://wrong.example"}, 403),
    ]:
        assert (await client.post("/api/speech/preview", headers=invalid, json=body)).status == status
    assert (await client.post("/api/speech/preview", headers=headers, json=body)).status == 400
    assert (await client.post("/api/speech/preview", headers=headers, data="x" * 4097)).status == 400


async def test_preview_fragmented_body_and_trailing_limit(client, browser):
    headers = {
        **owner_headers(client),
        "X-Vauxr-Browser": "browser-proof",
        "Content-Type": "application/json",
    }
    body = json.dumps({"device_id": "browser", "tts_backend": "missing", "voice": "test"}).encode()

    async def fragments(trailing=b""):
        yield body[:10]
        await asyncio.sleep(0.01)
        yield body[10:]
        await asyncio.sleep(0.01)
        yield trailing

    response = await client.post("/api/speech/preview", headers=headers, data=fragments(b"x" * 4096))
    assert response.status == 400
    response = await client.post("/api/speech/preview", headers=headers, data=fragments())
    assert response.status == 400  # Complete valid JSON, invalid provider.
