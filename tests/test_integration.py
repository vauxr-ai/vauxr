"""Synthetic integration enrollment, persistence and client boundary regressions."""

import copy
import json
import os
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

import auth
import auth_store
import channel_registry
import config
import integration
from auth_policy import Operation, Role, allowed
from auth_store import CredentialStore
from enrollment import EnrollmentError
from http_server import make_http_app
from integration import Integration
from integration_http import INTEGRATION
from lifecycle import Lifecycle
from owner_http import COOKIE, LAN_COOKIE, OWNER
from tests.test_enrollment import ORIGIN, owner_resolver, setup

assert setup


@pytest.fixture
def env(setup, monkeypatch):
    enrollment, owner, cookie = setup
    service = Integration(enrollment.store, ORIGIN)
    monkeypatch.setattr(auth, "get_store", lambda: service.store)
    principal = owner_resolver(owner, cookie)()
    return service, lambda: principal


def request_body(index=1):
    return {"request_id": f"{index:032x}", "request_secret": f"{index:064x}", "origin": ORIGIN,
            "display_name": "OpenClaw Study", "expires_at": int(time.time()) + 300}


def private(body):
    return {key: body[key] for key in ("request_id", "request_secret")}


def prepare(env, index=1):
    service, owner = env
    body = request_body(index)
    row = service.execute("request", body)
    approval = {"request_id": row["request_id"], "user_code": row["user_code"]}
    service.execute("approve", approval, owner)
    return body, approval


def deliver(env, index=1):
    body, approval = prepare(env, index)
    result = env[0].execute("deliver", private(body))
    return body, approval, result


def ack_body(body, result):
    return {**private(body), "credential": result["credential"], "saved": True}


def test_fixture_round_trip_restart_and_secret_projections(env, caplog, tmp_path):
    service, owner = env
    caplog.set_level("INFO")
    fixture = json.loads(Path("tests/fixtures/integration-v1.json").read_text())
    body, approval, result = deliver(env)
    assert set(result) == set(fixture["public_fields"] + fixture["delivery_only_fields"])
    assert not service.store.authenticate(result["credential"])
    assert not allowed(service.store.authenticate(result["credential"]), Operation.CHANNEL_CONNECT)
    listed = service.execute("list", {}, owner)
    assert set(listed["requests"][0]) == set(fixture["public_fields"])
    assert service.execute("status", private(body))["state"] == "delivered"
    with pytest.raises(EnrollmentError, match="unavailable"):
        service.execute("deliver", private(body))
    # Model actual client atomic-save, crash, readback, ACK and lost ACK response.
    saved_path = tmp_path / "client-secret.json"
    auth_store.atomic_private_json(saved_path, ack_body(body, result))
    saved = json.loads(saved_path.read_text())
    service.store = CredentialStore(service.store.path)
    assert service.execute("ack", saved)["state"] == "completed"
    service.store = CredentialStore(service.store.path)
    assert service.execute("ack", saved)["state"] == "completed"
    principal = service.store.authenticate(result["credential"])
    assert principal.role == Role.INTEGRATION and principal.subject == result["channel_id"]
    for operation in (Operation.VOICE_RESPONSE, Operation.CHANNEL_CONNECT, Operation.DEVICES_LIST,
                      Operation.ANNOUNCE, Operation.CONTROL, Operation.PLAYBACK, Operation.FIRMWARE_INITIATE):
        assert allowed(principal, operation)
    for operation in (Operation.PAIR_INITIATE, Operation.PAIR_APPROVE):
        assert not allowed(principal, operation)
        assert allowed(principal, operation, physical_verified=True)
    for operation in (Operation.OWNER_ADMIN, Operation.DEVICE_CONFIG, Operation.CREDENTIAL_CREATE,
                      Operation.CREDENTIAL_ROTATE, Operation.CREDENTIAL_REVOKE, Operation.CREDENTIAL_DISCLOSE):
        assert not allowed(principal, operation)
    for secret in (body["request_secret"], approval["user_code"], result["credential"]):
        assert secret not in service.store.path.read_text()
        assert secret not in caplog.text
        assert secret not in json.dumps(listed)
    assert service.store.path.stat().st_mode & 0o777 == 0o600


def test_duplicate_requests_metadata_mixup_and_code_attempt_limit(env):
    service, owner = env
    body = request_body()
    first = service.execute("request", body)
    assert service.execute("request", body) == first
    second = service.execute("request", request_body(2))
    assert first["channel_id"] != second["channel_id"]
    with pytest.raises(EnrollmentError, match="conflict"):
        service.execute("request", {**body, "display_name": "Different"})
    with pytest.raises(EnrollmentError, match="unauthorized"):
        service.execute("status", {**private(body), "request_secret": "f" * 64})
    for _ in range(5):
        with pytest.raises(EnrollmentError, match="invalid_code"):
            service.execute("approve", {"request_id": first["request_id"], "user_code": second["user_code"]}, owner)
    service.store = CredentialStore(service.store.path)
    assert service.execute("status", private(body))["state"] == "failed"
    with pytest.raises(EnrollmentError, match="unavailable"):
        service.execute("approve", {"request_id": first["request_id"], "user_code": first["user_code"]}, owner)


@pytest.mark.parametrize("state", ["pending", "approved", "delivered"])
@pytest.mark.parametrize("action,terminal", [("deny", "denied"), ("cancel", "cancelled")])
def test_denial_cancellation_no_late_approval_delivery_or_ack(env, state, action, terminal):
    service, owner = env
    body = request_body()
    row = service.execute("request", body)
    approval = {"request_id": row["request_id"], "user_code": row["user_code"]}
    if state != "pending":
        service.execute("approve", approval, owner)
    result = service.execute("deliver", private(body)) if state == "delivered" else None
    request = {"request_id": row["request_id"]} if action == "deny" else private(body)
    assert service.execute(action, request, owner)["state"] == terminal
    assert service.execute(action, request, owner)["state"] == terminal
    for call, value in (("approve", approval), ("deliver", private(body))):
        with pytest.raises(EnrollmentError, match="unavailable"):
            service.execute(call, value, owner)
    if result:
        with pytest.raises(EnrollmentError):
            service.execute("ack", ack_body(body, result))
        assert not service.store.authenticate(result["credential"])
        assert auth_store.verifier(result["credential"]) in service.store.lifecycle["blocked"]


@pytest.mark.parametrize("phase", ["pending", "approved", "delivered"])
@pytest.mark.parametrize("cause", ["expiry", "origin", "owner"])
def test_deadline_epoch_restart_never_revives(env, monkeypatch, phase, cause):
    service, owner = env
    body = request_body()
    row = service.execute("request", body)
    approval = {"request_id": row["request_id"], "user_code": row["user_code"]}
    if phase != "pending":
        service.execute("approve", approval, owner)
    result = service.execute("deliver", private(body)) if phase == "delivered" else None
    if cause == "expiry":
        monkeypatch.setattr(integration.time, "time", lambda: body["expires_at"])
    elif cause == "origin":
        service.origin = "http://changed.example"
    else:
        with service.store.transaction():
            state = copy.deepcopy(service.store.owner)
            state["generation"] = "b" * 32
            service.store.save_owner(state)
    service.sweep()
    service.origin = ORIGIN
    service.store = CredentialStore(service.store.path)
    assert service.execute("status", private(body))["state"] == ("expired" if cause == "expiry" else "stale")
    if result:
        with pytest.raises(EnrollmentError):
            service.execute("ack", ack_body(body, result))
        assert not service.store.authenticate(result["credential"])


@pytest.mark.parametrize("saved", [False, 1, "true", None])
def test_false_storage_ack_never_enables(env, saved):
    service, _ = env
    body, _, result = deliver(env)
    with pytest.raises(EnrollmentError, match="invalid_ack"):
        service.execute("ack", {**ack_body(body, result), "saved": saved})
    assert not service.store.authenticate(result["credential"])
    assert service.execute("status", private(body))["state"] == "delivered"


@pytest.mark.parametrize("action", ["request", "approve", "deliver", "ack", "cancel"])
@pytest.mark.parametrize("after", [False, True])
def test_pre_post_rename_failure_is_atomic(env, monkeypatch, action, after):
    service, owner = env
    body = request_body()
    value = body
    if action != "request":
        row = service.execute("request", body)
        value = {"request_id": row["request_id"], "user_code": row["user_code"]}
    if action not in ("request", "approve"):
        service.execute("approve", value, owner)
        value = private(body)
    result = None
    if action in ("ack", "cancel"):
        result = service.execute("deliver", value)
        value = ack_body(body, result) if action == "ack" else value
    before = service.store.path.read_text()
    save = auth_store.atomic_private_json

    def fail(path, payload):
        if after:
            save(path, payload)
        raise OSError("synthetic durable store failure")

    monkeypatch.setattr(auth_store, "atomic_private_json", fail)
    with pytest.raises(OSError):
        service.execute(action, value, owner)
    disk = CredentialStore(service.store.path)
    assert disk.records == service.store.records
    assert disk.integration == service.store.integration
    assert disk.lifecycle == service.store.lifecycle
    if not after:
        assert service.store.path.read_text() == before
    if result:
        assert bool(disk.authenticate(result["credential"])) is (after and action == "ack")
    if action == "deliver" and after:
        row = disk.integration["requests"][body["request_id"]]
        assert row["state"] == "delivered"
        assert not next(r for r in disk.records if r.id == row["credential_id"]).enabled


def test_concurrent_approval_delivery_and_replay(env):
    service, owner = env
    body, approval = prepare(env)

    def run(action):
        try:
            return service.execute(action, approval if action == "approve" else private(body), owner)
        except EnrollmentError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, ["approve", "deliver"] * 8))
    issued = [r for r in results if r and "credential" in r]
    assert len(issued) == 1
    assert len([r for r in service.store.records if r.subject == issued[0]["channel_id"]]) == 1


@pytest.mark.parametrize("competing", ["ack", "cancel"])
def test_revoke_race_never_resurrects_and_preserves_peers(env, competing):
    service, owner = env
    body, _, result = deliver(env)
    life = Lifecycle(service.store, ORIGIN)

    def run():
        try:
            return service.execute(competing, ack_body(body, result) if competing == "ack" else private(body))
        except EnrollmentError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(run)
        revoke = pool.submit(life.execute, "revoke", {"operation_id": "f" * 32,
                            "role": "integration", "subject": result["channel_id"]}, owner)
        future.result()
        assert revoke.result()["state"] == "revoked"
    assert not service.store.authenticate(result["credential"])
    assert service.store.authenticate("synthetic-integration")
    with pytest.raises(EnrollmentError):
        service.execute("ack", ack_body(body, result))
    with service.store.transaction():
        service.store.replace(tuple(replace(r, enabled=True) for r in service.store.records))
    assert not service.store.authenticate(result["credential"])


def test_rotation_and_routing_metadata_survive_independently(env):
    service, owner = env
    body, _, result = deliver(env)
    service.execute("ack", ack_body(body, result))
    channel = channel_registry.get_by_id(result["channel_id"])
    assert channel and not channel.active and channel.tokenHash == ""
    assert channel_registry.activate(channel.id)
    assert channel_registry.get_active().id == channel.id
    service.store = CredentialStore(service.store.path)
    assert channel_registry.get_active().id == channel.id
    life = Lifecycle(service.store, ORIGIN)
    control = {"operation_id": "a" * 32, "role": "integration", "subject": channel.id}
    assert life.execute("rotate", control, owner)["state"] == "queued"
    resolve = lambda: service.store.authenticate(result["credential"])
    assert life.execute("poll", {}, resolve)["state"] == "pending"
    rotated = life.execute("deliver", {"operation_id": "a" * 32}, resolve)
    replacement = lambda: service.store.authenticate(rotated["credential"])
    life.execute("ack", {"operation_id": "a" * 32, "saved": True}, replacement)
    service.sweep()
    assert service.execute("status", private(body))["state"] == "completed"
    assert not resolve() and replacement()
    with pytest.raises(EnrollmentError, match="forbidden"):
        life.execute("rotate", {**control, "operation_id": "b" * 32}, replacement)
    life.execute("revoke", {**control, "operation_id": "c" * 32}, owner)
    service.sweep()
    assert service.execute("status", private(body))["state"] == "revoked"
    assert not replacement()
    assert service.store.authenticate("synthetic-integration")


def test_expired_unissued_requests_prune_without_admission_replay(env, monkeypatch):
    service, _ = env
    body = request_body()
    service.execute("request", body)
    monkeypatch.setattr(integration.time, "time", lambda: body["expires_at"] + 1)
    service.execute("request", request_body(2))
    assert body["request_id"] not in service.store.integration["requests"]
    with pytest.raises(EnrollmentError, match="unavailable"):
        service.execute("request", body)


@pytest.mark.parametrize("scheme", ["http", "https"])
async def test_http_owner_csrf_native_boundary_and_no_cors(tmp_path, monkeypatch, scheme):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    for key in ("OPERATOR_TOKEN", "OWNER_HTTPS_ORIGIN", "OWNER_HTTP_ORIGIN", "OWNER_TRUSTED_PROXIES"):
        monkeypatch.delenv(key, raising=False)
    origin = scheme + "://owner.example"
    monkeypatch.setenv("OWNER_" + scheme.upper() + "_ORIGIN", origin)
    headers = {"Host": "owner.example", "Origin": origin}
    if scheme == "https":
        monkeypatch.setenv("OWNER_TRUSTED_PROXIES", "127.0.0.1/32")
        headers["X-Forwarded-Proto"] = "https"
    config.reset_config()
    app = make_http_app()
    async with TestClient(TestServer(app)) as http:
        owner = app[OWNER]
        claim = owner.claim(owner.console_claim())
        owner.acknowledge(claim["save_acknowledgement"], True)
        cookie, session = owner.login(claim["operator_token"])
        cookie_key = COOKIE if scheme == "https" else LAN_COOKIE
        owner_headers = {**headers, "Cookie": cookie_key + "=" + cookie, "X-CSRF-Token": session.csrf}
        body = {**request_body(), "origin": origin}
        path = "/api/integrations/v1/"
        response = await http.post(path + "request", headers=headers, json=body)
        assert response.status == 200
        row = await response.json()
        assert "Access-Control-Allow-Origin" not in response.headers
        assert response.headers["Cache-Control"] == "no-store"
        approval = {"request_id": row["request_id"], "user_code": row["user_code"]}
        for changes in ({"Origin": "http://evil.invalid"}, {"X-CSRF-Token": "bad"}, {"Host": "evil.invalid"}):
            assert (await http.post(path + "approve", headers={**owner_headers, **changes}, json=approval)).status == 403
        assert (await http.post(path + "approve", headers=headers, json=approval)).status == 401
        assert (await http.post(path + "approve", headers=owner_headers, json=approval)).status == 200
        assert (await http.post(path + "deliver", headers=owner_headers, json=private(body))).status == 400
        result = await (await http.post(path + "deliver", headers=headers, json=private(body))).json()
        store = app[INTEGRATION].store
        assert not store.authenticate(result["credential"])
        assert (await http.post(path + "ack", headers=headers, json=ack_body(body, result))).status == 200
        bearer = {**headers, "Authorization": "Bearer " + result["credential"]}
        assert (await http.get("/api/devices", headers=bearer)).status == 200
        assert (await http.get("/api/channels", headers=bearer)).status == 403
        assert (await http.post(path + "approve", headers=bearer, json=approval)).status == 400
        assert (await http.post(path + "status?credential=synthetic", headers=headers, json=private(body))).status == 403
        assert (await http.post(path + "status", headers=headers, data='{"request_id":1,"request_id":2}',
                                skip_auto_headers={"Content-Type"})).status == 400
    config.reset_config()


@pytest.mark.parametrize("action", ["rotate", "revoke"])
async def test_retiring_channel_connected_before_activation_tears_down_dependents(
    env, monkeypatch, action, alternate_channel
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    import auth_connections
    import device_registry
    from channel_server import ChannelServer, _Connection

    service, owner = env
    body, _, issued = deliver(env)
    service.execute("ack", ack_body(body, issued))
    server = ChannelServer()
    socket = SimpleNamespace(closed=False, send_str=AsyncMock(), close=AsyncMock())
    connection = _Connection(socket)
    await server._handle_auth(connection, issued["credential"])
    try:
        assert not connection.channel.active
        assert channel_registry.activate(issued["channel_id"])
        assert server.is_active_connected()
        errors = []
        server.add_response_listener("speaker", {"on_error": lambda *args: errors.append(args)})
        abort = Mock()
        monkeypatch.setattr(device_registry, "get_all", lambda: [SimpleNamespace(id="speaker")])
        monkeypatch.setattr(device_registry, "abort_active_turn", abort)
        monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(realtime=SimpleNamespace(enabled=False)))
        lifecycle = Lifecycle(service.store, ORIGIN)
        control = {"operation_id": "a" * 32, "role": "integration", "subject": issued["channel_id"]}
        lifecycle.execute(action, control, owner)
        if action == "rotate":
            current = lambda: service.store.authenticate(issued["credential"])
            assert lifecycle.execute("poll", {}, current)["state"] == "pending"
            replacement = lifecycle.execute("deliver", {"operation_id": control["operation_id"]}, current)
            lifecycle.execute("ack", {"operation_id": control["operation_id"], "saved": True},
                              lambda: service.store.authenticate(replacement["credential"]))
            assert service.store.authenticate(replacement["credential"])
        if action == "revoke":
            assert channel_registry.get_active().id == alternate_channel.id
            server.add_response_listener("alternate-speaker", {"on_error": lambda *args: errors.append(args)})
            monkeypatch.setattr(device_registry, "get_all", lambda: [
                SimpleNamespace(id="speaker"), SimpleNamespace(id="alternate-speaker")])
        await auth_connections.disconnect_stale(service.store)
        socket.close.assert_awaited_once()
        assert errors == [("speaker", "integration_revoked")]
        abort.assert_called_once_with("speaker")
        assert not server.is_active_connected()
    finally:
        auth_connections.release(connection.authority)


@pytest.fixture
def alternate_channel(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    config.reset_config()
    channel_registry._reset_for_tests()
    channel = channel_registry.Channel("alternate", "Alternate", "openclaw", "", True, "")
    channel_registry._set_active_for_tests(channel)
    yield channel
    channel_registry._reset_for_tests()
    config.reset_config()


def assert_retired_routing(service, channel_id, alternate):
    assert service.store.integration["active_channel"] == ""
    assert CredentialStore(service.store.path).integration["active_channel"] == ""
    assert channel_registry.get_by_id(channel_id) is None
    assert channel_id not in {c.id for c in channel_registry.get_all()}
    assert channel_registry.get_active().id == alternate.id
    assert next(c for c in channel_registry.get_all() if c.id == alternate.id).active
    assert not channel_registry.activate(channel_id)
    assert channel_registry.get_active().id == alternate.id


def test_owner_revoke_clears_selection_before_sweep_and_restart(env, alternate_channel):
    service, owner = env
    body, _, result = deliver(env)
    service.execute("ack", ack_body(body, result))
    assert channel_registry.activate(result["channel_id"])
    assert channel_registry.get_active().id == result["channel_id"]
    assert not next(c for c in channel_registry.get_all() if c.id == alternate_channel.id).active
    Lifecycle(service.store, ORIGIN).execute("revoke", {
        "operation_id": "d" * 32, "role": "integration", "subject": result["channel_id"]}, owner)
    assert_retired_routing(service, result["channel_id"], alternate_channel)
    service.sweep()
    service.store = CredentialStore(service.store.path)
    assert service.store.integration["requests"][body["request_id"]]["state"] == "revoked"
    assert_retired_routing(service, result["channel_id"], alternate_channel)


@pytest.mark.parametrize("cause", ["expiry", "origin", "owner", "revoked"])
@pytest.mark.parametrize("after", [False, True])
def test_retirement_and_selection_clear_are_one_atomic_write(env, alternate_channel, monkeypatch, cause, after):
    service, _ = env
    body, _, result = deliver(env)
    # Reproduce the old version's selected, credential-bearing unfinished row.
    payload = json.loads(service.store.path.read_text())
    payload["integration"]["active_channel"] = result["channel_id"]
    auth_store.atomic_private_json(service.store.path, payload)
    service.store.load()
    if cause == "expiry":
        monkeypatch.setattr(integration.time, "time", lambda: body["expires_at"])
    elif cause == "origin":
        service.origin = "http://changed.example"
    elif cause == "owner":
        payload["owner"]["generation"] = "b" * 32
        auth_store.atomic_private_json(service.store.path, payload)
    elif cause == "revoked":
        payload["lifecycle"] = Lifecycle(service.store, ORIGIN)._state()
        payload["lifecycle"]["blocked"].append(auth_store.verifier(result["credential"]))
        auth_store.atomic_private_json(service.store.path, payload)
    before = service.store.path.read_text()
    save = auth_store.atomic_private_json

    def fail(path, value):
        if after:
            save(path, value)
        raise OSError("synthetic durable store failure")

    monkeypatch.setattr(auth_store, "atomic_private_json", fail)
    with pytest.raises(OSError, match="synthetic"):
        service.sweep()
    disk = CredentialStore(service.store.path)
    assert service.store.integration == disk.integration
    assert service.store.lifecycle == disk.lifecycle
    assert service.store.records == disk.records
    if not after:
        assert service.store.path.read_text() == before
    else:
        assert disk.integration["active_channel"] == ""
        assert not disk.authenticate(result["credential"])
        assert disk.integration["requests"][body["request_id"]]["state"] in {"expired", "stale", "revoked"}
    monkeypatch.setattr(auth_store, "atomic_private_json", save)
    service.sweep()
    expected = {"expiry": "expired", "origin": "stale", "owner": "stale", "revoked": "revoked"}[cause]
    assert service.store.integration["requests"][body["request_id"]]["state"] == expected
    assert not service.store.authenticate(result["credential"])
    assert_retired_routing(service, result["channel_id"], alternate_channel)


@pytest.mark.parametrize("state", sorted(integration.TERMINAL - {"completed"}) + ["pending", "approved", "delivered"])
def test_invalid_credential_bearing_rows_cannot_displace_valid_selection(env, state):
    service, _ = env
    good_body, _, good = deliver(env)
    service.execute("ack", ack_body(good_body, good))
    assert channel_registry.activate(good["channel_id"])
    body, _, invalid = deliver(env, 2)
    with service.store.transaction():
        service.store.integration["requests"][body["request_id"]]["state"] = state
        # Even an enabled credential does not make an unfinished/terminal row routable.
        service.store.replace(tuple(replace(r, enabled=True) for r in service.store.records))
    before = service.store.path.read_text()
    assert channel_registry.get_by_id(invalid["channel_id"]) is None
    assert not channel_registry.activate(invalid["channel_id"])
    assert not channel_registry._activate_integration(invalid["channel_id"])
    assert service.store.path.read_text() == before
    assert channel_registry.get_active().id == good["channel_id"]


@pytest.mark.parametrize("invalidity", ["disabled", "blocked"])
def test_completed_row_requires_current_authority(env, alternate_channel, invalidity):
    service, _ = env
    body, _, result = deliver(env)
    service.execute("ack", ack_body(body, result))
    assert channel_registry.activate(result["channel_id"])
    with service.store.transaction():
        records = service.store.records
        if invalidity == "blocked":
            service.store.lifecycle = Lifecycle(service.store, ORIGIN)._state()
            service.store.lifecycle["blocked"].append(auth_store.verifier(result["credential"]))
        else:
            records = tuple(replace(r, enabled=False) if r.id == result["credential_id"] else r for r in records)
        service.store.replace(records)
    assert_retired_routing(service, result["channel_id"], alternate_channel)


@pytest.mark.parametrize("after_rename", [False, True])
def test_owner_revoke_fsync_failure_keeps_routing_and_authority_atomic(
    env, alternate_channel, monkeypatch, after_rename
):
    service, owner = env
    body, _, result = deliver(env)
    service.execute("ack", ack_body(body, result))
    assert channel_registry.activate(result["channel_id"])
    before = service.store.path.read_bytes()
    fsync = os.fsync

    def fail(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode) == after_rename:
            raise OSError("synthetic fsync failure")
        fsync(fd)

    control = {"operation_id": "d" * 32, "role": "integration", "subject": result["channel_id"]}
    with monkeypatch.context() as patch:
        patch.setattr(auth_store.os, "fsync", fail)
        with pytest.raises(OSError, match="synthetic fsync"):
            Lifecycle(service.store, ORIGIN).execute("revoke", control, owner)
    disk = CredentialStore(service.store.path)
    assert service.store.integration == disk.integration
    assert service.store.lifecycle == disk.lifecycle
    assert service.store.records == disk.records
    assert not list(service.store.path.parent.glob(".auth-*"))
    if after_rename:
        assert not disk.authenticate(result["credential"])
        assert disk.lifecycle["operations"][control["operation_id"]]["state"] == "revoked"
        assert_retired_routing(service, result["channel_id"], alternate_channel)
    else:
        assert service.store.path.read_bytes() == before
        assert disk.authenticate(result["credential"])
        assert channel_registry.get_active().id == result["channel_id"]
    Lifecycle(service.store, ORIGIN).execute("revoke", control, owner)
    assert_retired_routing(service, result["channel_id"], alternate_channel)


@pytest.mark.parametrize("finish", ["ack", "overlap_expiry", "queued_expiry"])
def test_rotation_preserves_valid_routing_and_retires_only_unusable_channel(
    env, alternate_channel, monkeypatch, finish
):
    service, owner = env
    body, _, result = deliver(env)
    service.execute("ack", ack_body(body, result))
    channel_id = result["channel_id"]
    assert channel_registry.activate(channel_id)
    life = Lifecycle(service.store, ORIGIN)
    control = {"operation_id": "a" * 32, "role": "integration", "subject": channel_id}
    queued = life.execute("rotate", control, owner)

    def assert_selected():
        service.sweep()
        assert service.store.integration["requests"][body["request_id"]]["state"] == "completed"
        assert CredentialStore(service.store.path).integration["active_channel"] == channel_id
        assert channel_registry.get_active().id == channel_id
        assert channel_registry.activate(channel_id)

    assert_selected()
    original = lambda: service.store.authenticate(result["credential"])
    if finish == "queued_expiry":
        monkeypatch.setattr(integration.time, "time", lambda: queued["expires_at"])
        life.sweep()
        assert original()
        assert_selected()
        return
    life.execute("poll", {}, original)
    rotated = life.execute("deliver", {"operation_id": control["operation_id"]}, original)
    assert_selected()
    replacement = lambda: service.store.authenticate(rotated["credential"])
    if finish == "overlap_expiry":
        monkeypatch.setattr(integration.time, "time", lambda: rotated["overlap_until"])
        # Read-time filtering protects routing before maintenance persists expiry.
        assert channel_registry.get_active().id == alternate_channel.id
        assert not channel_registry.activate(channel_id)
        life.sweep()
        assert_retired_routing(service, channel_id, alternate_channel)
        assert not original() and not replacement()
    else:
        life.execute("ack", {"operation_id": control["operation_id"], "saved": True}, replacement)
        life.sweep()
        assert not original() and replacement()
        # Enrollment expiry / owner-origin changes do not invalidate a completed row.
        monkeypatch.setattr(integration.time, "time", lambda: rotated["overlap_until"] + 1)
        service.origin = "http://changed.example"
        with service.store.transaction():
            service.store.save_owner({**service.store.owner, "generation": "b" * 32})
        service.store = CredentialStore(service.store.path)
        assert_selected()
        assert replacement()
