"""PR38 wire contract at the real authenticated aiohttp device/agent boundary."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientWebSocketResponse
from aiohttp.test_utils import TestClient, TestServer

import auth
import agent_registry as agents
import config
import device_registry as devices
import pipeline
from auth_policy import Role
from server import APP_STATE, make_app
from tests.auth_helpers import seed

A = "dev_" + "a" * 64
B = "dev_" + "b" * 64


@pytest.fixture
async def wire(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AsyncIterator[TestClient]:
    config.reset_config()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_URL", "")
    monkeypatch.setenv("REALTIME_ENABLED", "0")
    auth._store = None
    agents._reset_for_tests()
    devices.reset()
    monkeypatch.setattr(pipeline, "transcribe", AsyncMock(return_value="hello"))
    for device_id in (A, B):
        seed(device_id + "-secret", Role.DEVICE, device_id)
    agent, _ = await agents.create("Raw agent label", "openclaw")
    seed("integration-secret", Role.INTEGRATION, agent.id)
    agents.activate(agent.id)
    async with TestClient(TestServer(make_app())) as client:
        yield client
    devices.reset()
    agents._reset_for_tests()
    auth._store = None
    config.reset_config()


async def connect_agent(client: TestClient) -> ClientWebSocketResponse:
    ws = await client.ws_connect("/agent")
    await ws.send_json({"type": "agent.auth", "token": "integration-secret"})
    assert (await ws.receive_json(timeout=3))["type"] == "agent.ready"
    return ws


async def connect_device(client: TestClient, device_id: str) -> ClientWebSocketResponse:
    ws = await client.ws_connect("/ws")
    await ws.send_json({"type": "hello", "device_id": device_id, "token": device_id + "-secret",
                        "name": "Raw hello label", "deviceDisplayName": "Client metadata"})
    assert (await ws.receive_json(timeout=3))["type"] == "hello"
    return ws


async def start_turn(ws: ClientWebSocketResponse) -> None:
    await ws.send_json({"type": "voice.start", "name": "Raw voice label",
                        "deviceDisplayName": "Client metadata"})
    assert (await ws.receive_json(timeout=3))["type"] == "ready"
    await ws.send_bytes(b"\x01\x00\x00\x00\x00")
    await ws.send_json({"type": "voice.end"})
    assert await ws.receive_json(timeout=3) == {"type": "transcript", "text": "hello"}


async def finish_turn(agent: ClientWebSocketResponse, device: ClientWebSocketResponse,
                      device_id: str, run_id: str) -> None:
    await agent.send_json({"type": "agent.response.end", "deviceId": device_id, "runId": run_id})
    assert (await device.receive_json(timeout=3))["type"] == "audio.end"


def expected(device_id: str, name: str | None) -> dict[str, str]:
    frame = {"type": "agent.transcript", "deviceId": device_id,
             "sessionKey": f"vauxr:{device_id}", "text": "hello"}
    if name is not None:
        frame["deviceDisplayName"] = name
    return frame


async def test_persisted_names_rename_duplicates_reconnect_restart(wire: TestClient) -> None:
    devices.update_config(A, {"name": "  Living Room  "})
    devices.update_config(B, {"name": "Office"})
    agent = await connect_agent(wire)
    a, b = await connect_device(wire, A), await connect_device(wire, B)
    await start_turn(a)
    assert devices.get(A).name == "Raw voice label"
    assert await agent.receive_json(timeout=3) == expected(A, "Living Room")
    await finish_turn(agent, a, A, "first")

    # Persisted rename takes effect without refreshing the live name or session.
    devices.update_config(A, {"name": "Office"})
    await start_turn(a)
    await start_turn(b)
    assert await agent.receive_json(timeout=3) == expected(A, "Office")
    assert await agent.receive_json(timeout=3) == expected(B, "Office")
    await finish_turn(agent, b, B, "second-b")
    # Reverse completion must leave A's stable-ID listener untouched.
    cs = wire.app[APP_STATE].agent_server
    assert cs.get_response_listener(A) is not None
    await finish_turn(agent, a, A, "second-a")

    await a.close()
    await b.close()
    await agent.close()
    agent = await connect_agent(wire)
    a = await connect_device(wire, A)
    await start_turn(a)
    assert await agent.receive_json(timeout=3) == expected(A, "Office")
    await finish_turn(agent, a, A, "reconnect")
    await a.close()
    await agent.close()

    # Recreate the application and all stores from the same persisted directory.
    devices.reset()
    agents._reset_for_tests()
    agents.load()
    auth._store = None
    async with TestClient(TestServer(make_app())) as restarted:
        agent = await connect_agent(restarted)
        a = await connect_device(restarted, A)
        await start_turn(a)
        assert await agent.receive_json(timeout=3) == expected(A, "Office")
        await finish_turn(agent, a, A, "restart")
        await a.close()
        await agent.close()


@pytest.mark.parametrize("name,wanted", [
    (None, None), (42, None), ({}, None), ([], None), (True, None), ("", None), ("   ", None),
    ("x" * 129, None), ("😀" * 65, None), ("😀" * 64, "😀" * 64),
    ("x" * 128, "x" * 128), ("  Kitchen\u00a0", "Kitchen"),
    (" " + "x" * 128, None), ("😀" * 64 + " ", None),
    *[("Room" + chr(code) + "Fake", None) for code in
      (0, 9, 10, 31, 127, 159, 0x200B, 0x200F, 0x2028, 0x202E, 0x2060, 0x206F, 0xFEFF)],
])
async def test_each_wire_frame_clears_or_repeats_current_name(
    wire: TestClient, tmp_path: Path, name: object, wanted: str | None,
) -> None:
    agent = await connect_agent(wire)
    devices.update_config(A, {"name": "Previously valid"})
    device = await connect_device(wire, A)
    cs = wire.app[APP_STATE].agent_server
    assert cs.send_transcript(A, "hello")
    assert await agent.receive_json(timeout=3) == expected(A, "Previously valid")
    # Write through disk to cover malformed legacy data and stale registry config.
    (tmp_path / "devices.json").write_text(json.dumps({A: {"name": name}}))
    for _ in range(2):
        assert cs.send_transcript(A, "hello")
        assert await agent.receive_json(timeout=3) == expected(A, wanted)
    (tmp_path / "devices.json").unlink()
    assert cs.send_transcript(A, "hello")
    assert await agent.receive_json(timeout=3) == expected(A, None)
    await device.close()
    await agent.close()


async def test_wrong_device_identity_cannot_select_another_stored_name(wire: TestClient) -> None:
    devices.update_config(B, {"name": "Private name"})
    agent = await connect_agent(wire)
    device = await connect_device(wire, A)
    await device.send_json({"type": "voice.start", "device_id": B, "name": "Private name"})
    assert (await device.receive_json(timeout=3))["code"] == "FORBIDDEN"
    with pytest.raises(asyncio.TimeoutError):
        await agent.receive(timeout=0.1)
    await device.close()
    await agent.close()


async def test_failed_save_and_corrupt_store_never_use_cached_name(
    wire: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = await connect_agent(wire)
    devices.update_config(A, {"name": "Committed"})
    cs = wire.app[APP_STATE].agent_server

    def fail_save(*_: object) -> None:
        raise OSError("simulated failed save")

    monkeypatch.setattr(devices, "save_device_configs", fail_save)
    with pytest.raises(OSError, match="simulated failed save"):
        devices.update_config(A, {"name": "Uncommitted"})
    assert devices.get_config_for(A)["name"] == "Uncommitted"
    assert cs.send_transcript(A, "hello")
    assert await agent.receive_json(timeout=3) == expected(A, "Committed")
    path = tmp_path / "devices.json"
    for raw in (b'{', b'\xff', b'[]', b'null', json.dumps({A: []}).encode()):
        path.write_bytes(raw)
        assert cs.send_transcript(A, "hello")
        assert await agent.receive_json(timeout=3) == expected(A, None)
    # A malformed unrelated field/device must not prevent a valid title lookup.
    path.write_text(json.dumps({A: {"name": "Committed"}, B: {"output_sample_rate": float("inf")}}))
    assert cs.send_transcript(A, "hello")
    assert await agent.receive_json(timeout=3) == expected(A, "Committed")
    path.unlink()
    path.mkdir()  # Unreadable as a file.
    assert cs.send_transcript(A, "hello")
    assert await agent.receive_json(timeout=3) == expected(A, None)
    await agent.close()


async def test_pr38_production_bridge_session_contract(wire: TestClient, tmp_path: Path) -> None:
    """Run with VAUXR_PLUGIN_CHECKOUT=<built PR38 checkout>; never write there."""
    import os

    plugin = os.environ.get("VAUXR_PLUGIN_CHECKOUT")
    if not plugin:
        pytest.skip("cross-repo contract requires a built PR38 checkout via VAUXR_PLUGIN_CHECKOUT")
    revision = await asyncio.create_subprocess_exec(
        "git", "-C", plugin, "rev-parse", "HEAD", stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await revision.communicate()
    assert revision.returncode == 0
    assert stdout.decode().strip() == "e869a2a8c438dafc5ddd02def4f3de0c44d66c88"
    helper = Path(__file__).parent / "fixtures" / "friendly_plugin.mjs"
    process = await asyncio.create_subprocess_exec(
        "node", str(helper), plugin, str(tmp_path), str(wire.make_url("/")).rstrip("/"),
        agents.get_active().id, A, B,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )

    async def event() -> dict[str, str]:
        line = await asyncio.wait_for(process.stdout.readline(), 15)
        assert line, (await process.stderr.read()).decode()
        return json.loads(line)

    async def command(value: dict[str, object]) -> None:
        process.stdin.write((json.dumps(value) + "\n").encode())
        await process.stdin.drain()

    cs = wire.app[APP_STATE].agent_server
    replies: dict[str, list[tuple[str, str]]] = {A: [], B: []}
    ends: dict[str, asyncio.Future[str]] = {}

    async def send(device_id: str) -> None:
        ends[device_id] = asyncio.get_running_loop().create_future()
        cs.add_response_listener(device_id, {
            "on_delta": lambda rid, text: replies[device_id].append((rid, text)),
            "on_end": ends[device_id].set_result,
            "on_error": lambda *_: pytest.fail("plugin returned an error"),
        })
        assert cs.send_transcript(device_id, "hello")
        assert await event() == {"event": "pending", "id": device_id}

    async def complete(device_id: str, label: str) -> None:
        await command({"complete": device_id})
        assert await event() == {"event": "turn", "id": device_id, "label": label, "display": label,
                                 "sessionKey": f"agent:assistant:vauxr:{device_id}"}
        run_id = await asyncio.wait_for(ends[device_id], 3)
        assert replies[device_id][-1] == (run_id, f"reply-{device_id}")

    try:
        assert await event() == {"event": "ready"}
        devices.update_config(A, {"name": "  Living Room  "})
        await send(A)
        await complete(A, "Living Room")
        devices.update_config(A, {"name": "Office"})
        await send(A)
        await complete(A, "Office")
        await command({"restart": True})
        assert await event() == {"event": "ready"}
        await send(A)
        await complete(A, "Office")
        devices.update_config(B, {"name": "Office"})
        await send(A)
        await send(B)
        await complete(B, "Office")
        assert not ends[A].done()
        await complete(A, "Office")
        assert replies[A][-1][0] != replies[B][-1][0]
        for name in (None, 42, {}, [], True, "", " ", "x" * 129, "😀" * 65,
                     " " + "x" * 128, "😀" * 64 + " ", "Room\nFake", "Room\u202eFake"):
            (tmp_path / "devices.json").write_text(json.dumps({A: {"name": name}}))
            await send(A)
            await complete(A, A)
        for name in ("x" * 128, "😀" * 64, "\u00a0Kitchen\u3000"):
            (tmp_path / "devices.json").write_text(json.dumps({A: {"name": name}}))
            await send(A)
            await complete(A, name.strip())
        (tmp_path / "devices.json").unlink()
        await send(A)
        await complete(A, A)
        await command({"stop": True})
        process.stdin.close()
        assert await asyncio.wait_for(process.wait(), 10) == 0, (await process.stderr.read()).decode()
    finally:
        for device_id in (A, B):
            cs.remove_response_listener(device_id)
        if process.returncode is None:
            process.kill()
            await process.wait()
