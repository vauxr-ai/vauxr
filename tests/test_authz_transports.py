"""Real aiohttp boundary tests: reject before routing, I/O or disclosure."""

import logging
from dataclasses import replace

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

import auth
import channel_registry
import config
import device_registry
from auth_policy import HTTP_OPERATIONS, WS_OPERATIONS, Role
from http_server import _require_auth, make_http_app
from realtime_app import _offer_handler
from server import make_app
from tests.auth_helpers import seed
from tests.test_announce import FakeWs


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    config.reset_config()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DEVICE_TOKEN", "legacy-shared-token")
    monkeypatch.setenv("OPENCLAW_URL", "")
    monkeypatch.setenv("REALTIME_ENABLED", "0")
    auth._store = None
    for role, subject in [(Role.OWNER, "owner"), (Role.INTEGRATION, "channel"), (Role.DEVICE, "speaker")]:
        seed(role.value + "-secret", role, subject)
    seed("other-device-secret", Role.DEVICE, "other")
    channel_registry._reset_for_tests()
    device_registry.reset()
    yield
    auth._store = None
    channel_registry._reset_for_tests()
    device_registry.reset()
    config.reset_config()


# Expected authorization and concrete endpoint outcome, including owner-only and unshipped routes.
ROUTES = [
    ("GET", "/api/devices", "oi", 200),
    ("PATCH", "/api/devices/missing", "o", 404),
    ("POST", "/api/devices/missing/announce", "oi", 404),
    ("POST", "/api/devices/missing/command", "oi", 404),
    ("GET", "/api/channels", "o", 200),
    ("POST", "/api/channels", "o", 501),
    ("DELETE", "/api/channels/missing", "o", 501),
    ("POST", "/api/channels/missing/activate", "o", 404),
    ("POST", "/api/channels/missing/rotate", "o", 501),
    ("GET", "/api/webhooks", "o", 200),
    ("POST", "/api/webhooks", "o", 400),
    ("PATCH", "/api/webhooks/missing", "o", 404),
    ("DELETE", "/api/webhooks/missing", "o", 404),
    ("POST", "/api/webhooks/missing/duplicate", "o", 404),
    ("GET", "/firmware/missing.bin", "od", 404),
]


@pytest.mark.parametrize("method,path,grants,outcome", ROUTES)
@pytest.mark.parametrize(
    "role,key", [("owner", "o"), ("integration", "i"), ("device", "d"), ("invalid", "-")]
)
async def test_http_matrix(method, path, grants, outcome, role, key):
    async with TestClient(TestServer(make_http_app())) as client:
        response = await client.request(
            method, path, headers={"Authorization": f"Bearer {role}-secret"}, json={}
        )
        assert response.status == (outcome if key in grants else (401 if key == "-" else 403))
        text = await response.text()
        assert "-secret" not in text and "tokenHash" not in text and "verifier" not in text


def test_route_inventory_complete():
    from http_server import attach_http_routes

    app = web.Application()
    attach_http_routes(app)
    actual = {
        (route.method, route.resource.canonical)
        for route in app.router.routes()
        if route.method not in {"HEAD", "OPTIONS"}
    }
    expected = {
        (
            method,
            path.replace(
                "/missing",
                "/{channel_id}"
                if "/channels/" in path
                else "/{webhook_id}"
                if "/webhooks/" in path
                else "/{device_id}",
            ).replace("/firmware/{device_id}.bin", "/firmware/{filename}"),
        )
        for method, path, _, _ in ROUTES
    }
    assert actual == expected
    assert len(HTTP_OPERATIONS) == len(ROUTES)


async def test_unknown_handler_and_api_fallback_deny():
    @_require_auth
    async def future_admin(request):
        pytest.fail("Unlisted handler executed")

    app = web.Application()
    app.router.add_post("/future", future_admin)
    async with TestClient(TestServer(app)) as client:
        assert (await client.post("/future", headers={"Authorization": "Bearer owner-secret"})).status == 403
    async with TestClient(TestServer(make_http_app())) as client:
        assert (await client.get("/api/future")).status == 404
        assert (await client.get("/api/devices?token=owner-secret")).status == 401
        assert (
            await client.get("/api/devices", headers={"Authorization": "Bearer legacy-shared-token"})
        ).status == 401


@pytest.mark.parametrize(
    "command,params",
    [
        ("set_volume", {"volume": 15}),
        ("mute", {}),
        ("unmute", {}),
        ("reboot", {}),
        ("set_barge_in", {"enabled": False}),
        ("ota", {"url": "https://images.invalid/speaker.bin"}),
    ],
)
async def test_integration_control_and_update_initiation(command, params):
    ws = FakeWs()
    device_registry.register("speaker", ws=ws)
    async with TestClient(TestServer(make_http_app())) as client:
        response = await client.post(
            "/api/devices/speaker/command",
            headers={"Authorization": "Bearer integration-secret"},
            json={"command": command, "params": params},
        )
        assert response.status == 200
        if command != "set_barge_in":
            assert command in ws.text[-1]


async def test_sensitive_metadata_projection():
    import webhooks

    webhooks._webhooks = []
    webhooks.create(
        "hook",
        "https://user:URL_SECRET@example.invalid/?token=QUERY_SECRET",
        "AUTH_SECRET",
        {"nested": {"password": "BODY_SECRET"}},
    )
    device = device_registry.register("speaker", ws=FakeWs())
    device.config["button_actions"] = {"double_press": {"kind": "prompt", "text": "PROMPT_SECRET"}}
    device.config["token"] = "DEVICE_SECRET"
    async with TestClient(TestServer(make_http_app())) as client:
        for path in ["/api/webhooks", "/api/devices", "/api/channels"]:
            response = await client.get(path, headers={"Authorization": "Bearer owner-secret"})
            assert response.status == 200
            assert "SECRET" not in await response.text()


@pytest.mark.parametrize("message_type", list(WS_OPERATIONS))
@pytest.mark.parametrize(
    "token,identity",
    [
        ("owner-secret", "speaker"),
        ("integration-secret", "speaker"),
        ("device-secret", "other"),
        ("legacy-shared-token", "speaker"),
        (None, "speaker"),
    ],
)
async def test_ws_bypass_matrix(message_type, token, identity):
    async with TestClient(TestServer(make_app())) as client, client.ws_connect("/ws") as ws:
        payload = {"type": message_type, "device_id": identity}
        if token is not None:
            payload["token"] = token
        await ws.send_json(payload)
        response = await ws.receive_json(timeout=2)
        assert response["code"] in {"UNAUTHORIZED", "FORBIDDEN"}
        assert (await ws.receive(timeout=2)).type in {WSMsgType.CLOSE, WSMsgType.CLOSED}
    assert device_registry.get_all() == []


async def test_bound_socket_cannot_switch_to_another_valid_credential():
    async with TestClient(TestServer(make_app())) as client, client.ws_connect("/ws") as ws:
        await ws.send_json({"type": "hello", "device_id": "speaker", "token": "device-secret"})
        assert (await ws.receive_json(timeout=2))["type"] == "hello"
        await ws.send_json({"type": "voice.start", "device_id": "other", "token": "other-device-secret"})
        assert (await ws.receive_json(timeout=2))["code"] == "FORBIDDEN"
        assert device_registry.get("other") is None


async def test_unauthenticated_binary_socket_closes():
    async with TestClient(TestServer(make_app())) as client, client.ws_connect("/ws") as ws:
        await ws.send_bytes(b"\x01\x00\x00audio")
        assert (await ws.receive(timeout=2)).type == WSMsgType.CLOSE


async def test_disabled_record_rejected_on_existing_socket_and_http():
    async with TestClient(TestServer(make_app())) as client, client.ws_connect("/ws") as ws:
        await ws.send_json({"type": "hello", "device_id": "speaker", "token": "device-secret"})
        await ws.receive_json(timeout=2)
        store = auth.get_store()
        store.replace(tuple(replace(r, enabled=False) for r in store.records))
        await ws.send_json({"type": "device.button"})
        assert (await ws.receive_json(timeout=2))["code"] == "UNAUTHORIZED"
        response = await client.get("/api/devices", headers={"Authorization": "Bearer owner-secret"})
        assert response.status == 401


@pytest.mark.parametrize(
    "token,device_id,extra,status",
    [
        ("owner-secret", "speaker", {}, 403),
        ("integration-secret", "speaker", {}, 403),
        ("device-secret", "other", {}, 403),
        ("legacy-shared-token", "speaker", {}, 401),
        ("device-secret", "speaker", {"pc_id": "victim-peer"}, 403),
        ("device-secret", "speaker", {"restart_pc": True}, 403),
        ("device-secret", "speaker", {}, 200),
    ],
)
async def test_realtime_signaling_identity(monkeypatch, token, device_id, extra, status):
    import realtime_session

    calls = []

    class Manager:
        def can_accept_offer(self, identity):
            return True

        async def handle_offer(self, identity, body):
            calls.append(identity)
            return {"type": "answer", "sdp": "test"}

    monkeypatch.setattr(realtime_session, "get_manager", lambda: Manager())
    app = web.Application()
    app.router.add_post("/api/offer", _offer_handler)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/offer",
            json={"type": "offer", "sdp": "test", "token": token, "device_id": device_id, **extra},
        )
        assert response.status == status
    assert calls == (["speaker"] if status == 200 else [])


async def test_auth_denials_log_no_client_values(caplog):
    caplog.set_level(logging.INFO)
    async with TestClient(TestServer(make_app())) as client:
        await client.get("/api/devices", headers={"Authorization": "Bearer LEAK_SECRET"})
        await client.patch("/api/devices/LEAK_SECRET", headers={"Authorization": "Bearer integration-secret"})
        async with client.ws_connect("/ws") as ws:
            await ws.send_json({"type": "hello", "device_id": "LEAK_SECRET", "token": "LEAK_SECRET"})
            await ws.receive_json(timeout=2)
    logs = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("vauxr"))
    assert "authorization denied: unauthorized" in logs
    assert "authorization denied: forbidden" in logs
    assert "LEAK_SECRET" not in logs and "integration-secret" not in logs


@pytest.mark.parametrize("token", ["owner-secret", "device-secret", "legacy-shared-token", None, ["invalid"]])
async def test_channel_rejects_wrong_principal(token):
    async with TestClient(TestServer(make_app())) as client, client.ws_connect("/channel") as ws:
        await ws.send_json({"type": "channel.auth", "token": token})
        assert (await ws.receive_json(timeout=2))["code"] in {"UNAUTHORIZED", "FORBIDDEN"}
        assert (await ws.receive(timeout=2)).type == WSMsgType.CLOSE


async def test_inactive_channel_cannot_inject_voice_response():

    from channel_server import ChannelServer

    active, _ = await channel_registry.create("Active")
    inactive, _ = await channel_registry.create("Inactive")
    channel_registry.activate(active.id)
    seed("idle-channel-secret", Role.INTEGRATION, inactive.id)
    cs = ChannelServer()
    delivered = []
    cs.add_response_listener(
        "speaker",
        {
            "on_delta": lambda *args: delivered.append(args),
            "on_end": lambda *args: delivered.append(args),
            "on_error": lambda *args: delivered.append(args),
        },
    )
    app = make_app()
    from server import APP_STATE

    app[APP_STATE].channel_server = cs
    async with TestClient(TestServer(app)) as client, client.ws_connect("/channel") as ws:
        await ws.send_json({"type": "channel.auth", "token": "idle-channel-secret"})
        assert (await ws.receive_json(timeout=2))["type"] == "channel.ready"
        await ws.send_json(
            {"type": "channel.response.delta", "deviceId": "speaker", "runId": "run", "text": "injected"}
        )
        assert (await ws.receive_json(timeout=2))["code"] == "FORBIDDEN"
    assert delivered == []


async def test_new_unprotected_route_is_denied_by_middleware():
    from http_server import policy_middleware

    app = web.Application(middlewares=[policy_middleware])

    async def unprotected(request):
        pytest.fail("New route bypassed inventory")

    app.router.add_post("/future/admin", unprotected)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/future/admin", headers={"Authorization": "Bearer owner-secret"})
        assert response.status == 401


async def test_simultaneous_device_identity_cannot_take_over():
    async with (
        TestClient(TestServer(make_app())) as client,
        client.ws_connect("/ws") as first,
        client.ws_connect("/ws") as second,
    ):
        hello = {"type": "hello", "device_id": "speaker", "token": "device-secret"}
        await first.send_json(hello)
        await first.receive_json(timeout=2)
        original = device_registry.get("speaker").ws
        await second.send_json(hello)
        assert (await second.receive_json(timeout=2))["code"] == "FORBIDDEN"
        assert device_registry.get("speaker").ws is original
        await first.send_json(hello)
        assert (await first.receive_json(timeout=2))["type"] == "hello"


@pytest.mark.parametrize("header", ["Basic ignored", "Bearer owner-secret", "Bearer other-device-secret"])
async def test_realtime_conflicting_header_cannot_fall_back_to_body(header):
    app = web.Application()
    app.router.add_post("/api/offer", _offer_handler)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/offer",
            headers={"Authorization": header},
            json={"type": "offer", "sdp": "test", "device_id": "speaker", "token": "device-secret"},
        )
        assert response.status in {401, 403}


def reissue_credential(subject: str, restart: bool) -> None:
    from auth_store import CredentialStore, verifier

    store = auth.get_store()
    original = next(r for r in store.records if r.subject == subject)
    store.replace(tuple(r for r in store.records if r.id != original.id))
    if restart:
        store = CredentialStore(store.path)
    store.replace((*store.records, replace(original, verifier=verifier("replacement-secret"))))
    if restart:
        auth._store = CredentialStore(store.path)


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("frame", ["text", "binary", "replacement-token"])
async def test_reissued_device_rejects_already_authenticated_socket(restart: bool, frame: str) -> None:
    async with TestClient(TestServer(make_app())) as client, client.ws_connect("/ws") as ws:
        await ws.send_json({"type": "hello", "device_id": "speaker", "token": "device-secret"})
        assert (await ws.receive_json(timeout=2))["type"] == "hello"
        reissue_credential("speaker", restart)
        if frame == "binary":
            await ws.send_bytes(b"\x01\x00\x00audio")
        else:
            message = {"type": "hello"}
            if frame == "replacement-token":
                message["token"] = "replacement-secret"
            await ws.send_json(message)
            assert (await ws.receive_json(timeout=2))["code"] == (
                "FORBIDDEN" if frame == "replacement-token" else "UNAUTHORIZED"
            )
        assert (await ws.receive(timeout=2)).type == WSMsgType.CLOSE
        async with client.ws_connect("/ws") as fresh:
            await fresh.send_json({"type": "hello", "device_id": "speaker", "token": "replacement-secret"})
            assert (await fresh.receive_json(timeout=2))["type"] == "hello"


@pytest.mark.parametrize("restart", [False, True])
async def test_reissued_channel_rejects_existing_connection_in_both_directions(restart: bool) -> None:
    from channel_server import ChannelServer
    from server import APP_STATE

    channel, _ = await channel_registry.create("Active")
    channel_registry.activate(channel.id)
    seed("channel-secret", Role.INTEGRATION, channel.id)
    cs = ChannelServer()
    delivered = []
    cs.add_response_listener(
        "speaker",
        {
            "on_delta": lambda *args: delivered.append(args),
            "on_end": lambda *args: delivered.append(args),
            "on_error": lambda *args: delivered.append(args),
        },
    )
    app = make_app()
    app[APP_STATE].channel_server = cs
    async with TestClient(TestServer(app)) as client, client.ws_connect("/channel") as ws:
        await ws.send_json({"type": "channel.auth", "token": "channel-secret"})
        assert (await ws.receive_json(timeout=2))["type"] == "channel.ready"
        assert cs.is_active_connected()
        reissue_credential(channel.id, restart)
        assert not cs.is_active_connected()
        assert not cs.send_transcript("speaker", "must not leak")
        response = {"type": "channel.response.end", "deviceId": "speaker", "runId": "run"}
        await ws.send_json(response)
        assert (await ws.receive_json(timeout=2))["code"] == "UNAUTHORIZED"
        assert (await ws.receive(timeout=2)).type == WSMsgType.CLOSE
        assert delivered == []
        async with client.ws_connect("/channel") as fresh:
            await fresh.send_json({"type": "channel.auth", "token": "replacement-secret"})
            assert (await fresh.receive_json(timeout=2))["type"] == "channel.ready"
            assert cs.is_active_connected()
            assert cs.send_transcript("speaker", "fresh transcript")
            assert (await fresh.receive_json(timeout=2))["text"] == "fresh transcript"
            await fresh.send_json(response)
            # A subsequent rejected frame provides an ordered server processing barrier.
            await fresh.send_json({"type": "unknown"})
            assert (await fresh.receive_json(timeout=2))["code"] == "FORBIDDEN"
            assert delivered == [("run",)]
