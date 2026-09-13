"""Synthetic lifecycle durability, identity, concurrency and transport regressions."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

import auth
import auth_connections
import auth_store
import config
import lifecycle
from auth_policy import Role
from auth_store import Credential, CredentialStore, verifier
from enrollment import EnrollmentError
from http_server import make_http_app
from lifecycle import Lifecycle
from lifecycle_http import LIFECYCLE
from owner_http import COOKIE, LAN_COOKIE, OWNER
from tests.test_enrollment import (
    ORIGIN,
    approve,
    owner_resolver,
    ready,
    request,
    setup,
    signed,
)

# Imported pytest fixture supplies an isolated generated owner and synthetic integration.
assert setup

@pytest.fixture
def env(setup, monkeypatch):
    enrollment, owner, cookie = setup
    service = Lifecycle(enrollment.store, ORIGIN)
    with service.store.transaction():
        service.store.replace((*service.store.records,
                               Credential("device", Role.DEVICE, "speaker", verifier("synthetic-device"))))
    monkeypatch.setattr(auth, "get_store", lambda: service.store)
    return service, enrollment, owner_resolver(owner, cookie)


def control(service, owner, action="rotate", role="device", subject="speaker", oid="1" * 32):
    return service.execute(action, {"operation_id": oid, "role": role, "subject": subject}, owner)


def client(service, role="device", token=None):
    return lambda: service.store.authenticate(token or "synthetic-" + role)


def delivered(service, owner, role="device", subject="speaker"):
    row = control(service, owner, role=role, subject=subject)
    assert service.execute("poll", {}, client(service, role))["state"] == "pending"
    result = service.execute("deliver", {"operation_id": row["operation_id"]}, client(service, role))
    assert result["state"] == "delivered"
    return result


@pytest.mark.parametrize("role,subject", [("device", "speaker"), ("integration", "channel")])
def test_durable_save_ack_lost_response_and_exact_restoration(env, role, subject):
    service, _, owner = env
    old = next(r for r in service.store.records if r.role == role)
    stale = service.store.authenticate("synthetic-" + role)
    result = delivered(service, owner, role, subject)
    new = client(service, token=result["credential"])
    assert service.store.current(stale) and new()
    body = {"operation_id": result["operation_id"], "saved": True}
    for saved in (False, 1, "true", None):
        with pytest.raises(EnrollmentError, match="invalid_ack"):
            service.execute("ack", {**body, "saved": saved}, new)
    with pytest.raises(EnrollmentError, match="invalid_ack"):
        service.execute("ack", body, client(service, role))
    assert service.execute("ack", body, new)["state"] == "acknowledged"
    # Treat ACK reply as lost; fresh store + saved credential recover completion.
    service.store = CredentialStore(service.store.path)
    assert service.execute("ack", body, client(service, token=result["credential"]))["state"] == "completed"
    assert not service.store.current(stale)
    with service.store.transaction():
        service.store.replace(tuple(replace(r, enabled=True) if r.id == old.id else r
                                    for r in service.store.records))
    assert not service.store.authenticate("synthetic-" + role)
    assert not service.store.current(stale)
    disk = service.store.path.read_text()
    assert result["credential"] not in disk
    assert old.verifier in service.store.lifecycle["blocked"]


@pytest.mark.parametrize("phase", ["queued", "pending", "delivered"])
def test_offline_restart_expiry_and_no_secret_redisclosure(env, monkeypatch, phase):
    service, _, owner = env
    now = lifecycle.time.time()
    monkeypatch.setattr(lifecycle.time, "time", lambda: now)
    row = control(service, owner)
    token = None
    if phase != "queued":
        service.execute("poll", {}, client(service))
    if phase == "delivered":
        result = service.execute("deliver", {"operation_id": row["operation_id"]}, client(service))
        token = result["credential"]
        with pytest.raises(EnrollmentError, match="unavailable"):
            service.execute("deliver", {"operation_id": row["operation_id"]}, client(service))
    principal = owner()
    service.store = CredentialStore(service.store.path)
    owner = lambda: principal
    assert service.execute("status", {"operation_id": row["operation_id"]}, owner)["state"] == phase
    monkeypatch.setattr(lifecycle.time, "time", lambda: now + (300 if token else 86400))
    if token:
        assert not service.store.authenticate(token)  # Deadline enforced before maintenance.
    service.sweep()
    assert service.store.lifecycle["operations"][row["operation_id"]]["state"] == "expired"
    assert bool(service.store.authenticate("synthetic-device")) is (token is None)
    if token:
        assert not service.store.authenticate(token)


@pytest.mark.parametrize("after", [False, True])
@pytest.mark.parametrize("action", ["deliver", "ack", "revoke"])
def test_persistence_failure_before_and_after_commit(env, monkeypatch, after, action):
    service, _, owner = env
    row = control(service, owner)
    service.execute("poll", {}, client(service))
    token = None
    if action in ("ack", "revoke"):
        result = service.execute("deliver", {"operation_id": row["operation_id"]}, client(service))
        token = result["credential"]
    original = auth_store.atomic_private_json

    def failure(path, payload):
        if after:
            original(path, payload)
        raise OSError("synthetic persistence fault")

    monkeypatch.setattr(auth_store, "atomic_private_json", failure)
    with pytest.raises(OSError):
        if action == "revoke":
            control(service, owner, action="revoke", oid="2" * 32)
        else:
            body = {"operation_id": row["operation_id"]}
            if action == "ack":
                body["saved"] = True
            service.execute(action, body, client(service, token=token))
    disk = CredentialStore(service.store.path)
    assert disk.records == service.store.records
    assert disk.lifecycle == service.store.lifecycle
    if action == "deliver":
        expected = "delivered" if after else "pending"
        assert disk.lifecycle["operations"][row["operation_id"]]["state"] == expected
    else:
        assert bool(disk.authenticate("synthetic-device")) is not after
        assert bool(disk.authenticate(token)) == (not after if action == "revoke" else True)


def test_revoke_invalidates_both_enrollment_actors_transactionally(env):
    service, enrollment, owner = env
    old = service.store.records[0]
    stale = service.store.authenticate("synthetic-integration")
    rows = []
    for actor_first in (True, False):
        key, row, code = ready(enrollment)
        enrollment.execute("initiate", {"request_id": row["request_id"], "code": code},
                           client(service, "integration") if actor_first else owner)
        enrollment.execute("approve", {"request_id": row["request_id"], "code": code},
                           owner if actor_first else client(service, "integration"))
        rows.append((key, row))
    control(service, owner, action="revoke", role="integration", subject="channel")
    assert all(service.store.enrollment["requests"][row["request_id"]]["state"] == "stale" for _, row in rows)
    with service.store.transaction():
        service.store.replace(tuple(replace(r, enabled=True) if r.id == old.id else r
                                    for r in service.store.records))
    assert not service.store.current(stale)
    for key, row in rows:
        with pytest.raises(EnrollmentError):
            enrollment.execute("redeem", signed(key, row, "redeem"))


@pytest.mark.parametrize("kind", ["physical", "browser"])
def test_consumed_lost_response_and_revoked_known_identity_recovery(env, kind):
    service, enrollment, owner = env
    key, row = request(enrollment, owner, kind)
    code = enrollment.execute("prove", signed(key, row, "prove"))["code"]
    approve(enrollment, row, code, owner)
    lost = enrollment.execute("redeem", signed(key, row, "redeem"))
    old = service.store.authenticate(lost["device_token"])
    # Same-key enrollment cannot silently overwrite the consumed identity.
    with pytest.raises(EnrollmentError, match="already_owned"):
        request(enrollment, owner, kind, key)
    recovery = control(service, owner, action="recover", subject=row["device_id"])
    assert not service.store.current(old)
    with pytest.raises(EnrollmentError, match="already_owned"):
        request(enrollment, owner, "browser" if kind == "physical" else "physical", key)
    _, fresh = request(enrollment, owner, kind, key)
    code = enrollment.execute("prove", signed(key, fresh, "prove"))["code"]
    with pytest.raises(EnrollmentError, match="forbidden"):
        enrollment.execute("initiate", {"request_id": fresh["request_id"], "code": code},
                           client(service, "integration"))
    approve(enrollment, fresh, code, owner)
    result = enrollment.execute("redeem", signed(key, fresh, "redeem"))
    assert result["device_id"] == lost["device_id"]
    assert result["device_token"] != lost["device_token"]
    assert result["operation_id"] == recovery["operation_id"]
    assert service.execute("ack", {"operation_id": recovery["operation_id"], "saved": True},
                           client(service, token=result["device_token"]))["state"] == "acknowledged"
    assert any(r.id == old.credential_id and not r.enabled for r in service.store.records)
    assert not service.store.current(old)


def test_recovery_revoke_race_and_bound_request(env):
    service, enrollment, owner = env
    key, row, code = ready(enrollment)
    approve(enrollment, row, code, owner)
    enrollment.execute("redeem", signed(key, row, "redeem"))
    control(service, owner, action="recover", subject=row["device_id"])
    _, fresh = request(enrollment, owner, key=key)
    code = enrollment.execute("prove", signed(key, fresh, "prove"))["code"]
    approve(enrollment, fresh, code, owner)
    control(service, owner, action="revoke", subject=row["device_id"], oid="2" * 32)
    with pytest.raises(EnrollmentError):
        enrollment.execute("redeem", signed(key, fresh, "redeem"))
    assert service.store.enrollment["requests"][fresh["request_id"]]["state"] == "stale"


def test_idempotency_competing_rotations_revoke_and_cross_identity(env):
    service, _, owner = env
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: control(service, owner), range(8)))
    assert all(result == results[0] for result in results)
    with pytest.raises(EnrollmentError, match="conflict"):
        control(service, owner, oid="2" * 32)
    with pytest.raises(EnrollmentError, match="conflict"):
        control(service, owner, action="revoke")
    for action in ("rotate", "revoke", "recover"):
        with pytest.raises(EnrollmentError, match="forbidden"):
            control(service, client(service, "integration"), action=action)
    with pytest.raises(EnrollmentError, match="forbidden"):
        service.execute("deliver", {"operation_id": "1" * 32}, client(service, "integration"))
    with pytest.raises(EnrollmentError, match="forbidden"):
        service.execute("deliver", {"operation_id": "1" * 32}, owner)
    assert control(service, owner, action="revoke", oid="2" * 32)["state"] == "revoked"
    assert control(service, owner, action="revoke", oid="2" * 32)["state"] == "revoked"
    assert not service.store.authenticate("synthetic-device")


async def test_idle_retained_transport_callbacks_after_revoke(env):
    service, _, owner = env
    callbacks = [AsyncMock(), AsyncMock(), AsyncMock()]
    old = client(service)()
    retained = [auth_connections.retain(old, callback) for callback in callbacks]
    other = AsyncMock()
    connection = auth_connections.retain(client(service, "integration")(), other)
    control(service, owner, action="revoke")
    await auth_connections.disconnect_stale(service.store)
    for callback in callbacks:
        callback.assert_awaited_once()
    other.assert_not_awaited()
    assert all(item not in auth_connections._connections for item in retained)
    auth_connections.release(connection)


@pytest.mark.parametrize("mutation", ["version", "blocked", "operations", "binding"])
def test_corruption_clears_grants(env, mutation):
    service, _, owner = env
    control(service, owner)
    payload = json.loads(service.store.path.read_text())
    if mutation == "version":
        payload["lifecycle"]["version"] = 99
    elif mutation == "blocked":
        payload["lifecycle"]["blocked"] = ["not-a-digest"]
    elif mutation == "binding":
        payload["lifecycle"]["bindings"]["speaker"] = {"kind": "integration", "public_key": "a" * 64}
    else:
        payload["lifecycle"]["operations"]["1" * 32]["state"] = "invented"
    service.store.path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="Invalid credential store"):
        service.store.load()
    assert not service.store.authenticate("synthetic-device")


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("role,subject", [("device", "speaker"), ("integration", "channel")])
async def test_http_transport_csrf_roles_subject_delivery_and_no_cors(
        tmp_path, monkeypatch, scheme, role, subject):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OWNER_HTTPS_ORIGIN", raising=False)
    monkeypatch.delenv("OWNER_TRUSTED_PROXIES", raising=False)
    monkeypatch.setenv("OWNER_HTTPS_ORIGIN" if scheme == "https" else "OWNER_HTTP_ORIGIN",
                       f"{scheme}://owner.example")
    base_headers = {"Host": "owner.example", "Origin": f"{scheme}://owner.example"}
    if scheme == "https":
        base_headers["X-Forwarded-Proto"] = "https"
        monkeypatch.setenv("OWNER_TRUSTED_PROXIES", "127.0.0.1/32")
    config._config = None
    auth._store = None
    app = make_http_app()
    async with TestClient(TestServer(app)) as http:
        owner = app[OWNER]
        claim = owner.claim(owner.console_claim())
        owner.acknowledge(claim["save_acknowledgement"], True)
        cookie, session = owner.login(claim["operator_token"])
        store = app[LIFECYCLE].store
        with store.transaction():
            store.replace((Credential("d", Role(role), subject, verifier("synthetic-device")),))
        cookie_key = COOKIE if scheme == "https" else LAN_COOKIE
        headers = {**base_headers, "Cookie": cookie_key + "=" + cookie, "X-CSRF-Token": session.csrf}
        body = {"operation_id": "1" * 32, "role": role, "subject": subject}
        path = "/api/lifecycle/v1/"
        for changes in ({"X-Forwarded-Proto": "http"}, {"Origin": "https://evil.invalid"},
                        {"X-CSRF-Token": "wrong"}):
            response = await http.post(path + "rotate", headers={**headers, **changes}, json=body)
            assert response.status == 403
        response = await http.post(path + "rotate", headers=headers, json=body)
        assert response.status == 200
        assert "credential" not in await response.json()
        assert "Access-Control-Allow-Origin" not in response.headers
        subject_headers = {**base_headers, "Authorization": "Bearer synthetic-device"}
        assert (await http.post(path + "rotate", headers=subject_headers, json=body)).status == 403
        assert (await http.post(path + "poll", headers=subject_headers, json={})).status == 200
        response = await http.post(path + "deliver", headers=subject_headers,
                                   json={"operation_id": "1" * 32})
        result = await response.json()
        assert response.status == 200 and result["save_required"]
        new_headers = {**base_headers, "Authorization": "Bearer " + result["credential"]}
        response = await http.post(path + "ack", headers=new_headers,
                                   json={"operation_id": "1" * 32, "saved": True})
        assert response.status == 200
        assert (await http.post(path + "poll", headers=subject_headers, json={})).status == 401
        assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("after", [False, True])
def test_recovery_consumption_failure_preserves_transaction(env, monkeypatch, after):
    service, enrollment, owner = env
    key, row, code = ready(enrollment)
    approve(enrollment, row, code, owner)
    original = enrollment.execute("redeem", signed(key, row, "redeem"))
    control(service, owner, action="recover", subject=row["device_id"])
    _, fresh = request(enrollment, owner, key=key)
    code = enrollment.execute("prove", signed(key, fresh, "prove"))["code"]
    approve(enrollment, fresh, code, owner)
    save = auth_store.atomic_private_json

    def failure(path, payload):
        if after:
            save(path, payload)
        raise OSError("synthetic recovery fault")

    monkeypatch.setattr(auth_store, "atomic_private_json", failure)
    with pytest.raises(OSError):
        enrollment.execute("redeem", signed(key, fresh, "redeem"))
    disk = CredentialStore(service.store.path)
    assert disk.enrollment == service.store.enrollment
    assert disk.lifecycle == service.store.lifecycle
    assert disk.enrollment["requests"][fresh["request_id"]]["state"] == ("consumed" if after else "approved")
    assert disk.lifecycle["operations"]["1" * 32]["state"] == ("delivered" if after else "pending")
    assert not disk.authenticate(original["device_token"])


@pytest.mark.parametrize("role,subject", [("device", "speaker"), ("integration", "channel")])
def test_competing_deliver_revoke_cannot_leave_live_credential(env, role, subject):
    service, _, owner = env
    control(service, owner, role=role, subject=subject)
    service.execute("poll", {}, client(service, role))

    def deliver():
        try:
            return service.execute("deliver", {"operation_id": "1" * 32}, client(service, role))
        except EnrollmentError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        delivery = pool.submit(deliver)
        revocation = pool.submit(control, service, owner, "revoke", role, subject, "2" * 32)
        result = delivery.result()
        assert revocation.result()["state"] == "revoked"
    if result:
        assert not service.store.authenticate(result["credential"])
    assert not service.store.authenticate("synthetic-" + role)
    assert service.store.authenticate("synthetic-" + ("device" if role == "integration" else "integration"))


@pytest.mark.parametrize("transition", ["owner", "origin", "expiry"])
def test_recovery_requires_current_grant_and_fresh_participation(env, monkeypatch, transition):
    service, enrollment, owner = env
    key, row, code = ready(enrollment)
    approve(enrollment, row, code, owner)
    enrollment.execute("redeem", signed(key, row, "redeem"))
    control(service, owner, action="recover", subject=row["device_id"])
    _, fresh = request(enrollment, owner, key=key)
    code = enrollment.execute("prove", signed(key, fresh, "prove"))["code"]
    approve(enrollment, fresh, code, owner)
    if transition == "owner":
        with service.store.transaction():
            service.store.save_owner({**service.store.owner, "generation": "f" * 32})
    elif transition == "origin":
        enrollment.origin = "https://changed.invalid"
    else:
        now = lifecycle.time.time()
        monkeypatch.setattr(lifecycle.time, "time", lambda: now + 301)
    with pytest.raises(EnrollmentError):
        enrollment.execute("redeem", signed(key, fresh, "redeem"))


def test_tombstone_is_independent_of_record_id_but_not_full_backup(env):
    service, _, owner = env
    old = next(r for r in service.store.records if r.role == Role.DEVICE)
    control(service, owner, action="revoke")
    with service.store.transaction():
        service.store.replace(tuple(r for r in service.store.records if r.id != old.id))
    with service.store.transaction():
        service.store.replace((*service.store.records, replace(old, id="restored", enabled=True)))
    assert not service.store.authenticate("synthetic-device")
    assert CredentialStore(service.store.path).lifecycle["blocked"]


def test_owner_settings_and_disabled_records_survive_lifecycle(env, tmp_path):
    service, _, owner = env
    settings = tmp_path / "devices.json"
    settings.write_text('{"speaker":{"name":"Preserved"}}')
    with service.store.transaction():
        service.store.replace((*service.store.records,
                               Credential("disabled", Role.DEVICE, "disabled", verifier("disabled"), False)))
    before_owner = dict(service.store.owner)
    control(service, owner, action="revoke")
    with service.store.transaction():
        service.store.save_owner(service.store.owner)
    loaded = CredentialStore(service.store.path)
    assert loaded.owner == before_owner
    assert loaded.lifecycle == service.store.lifecycle
    assert any(r.id == "disabled" and not r.enabled for r in loaded.records)
    assert settings.read_text() == '{"speaker":{"name":"Preserved"}}'
    with pytest.raises(EnrollmentError, match="recovery_unavailable"):
        control(service, owner, action="recover", subject="disabled", oid="2" * 32)
    with pytest.raises(EnrollmentError, match="recovery_unavailable"):
        control(service, owner, action="recover", role="integration", subject="channel", oid="2" * 32)


@pytest.mark.parametrize("body", [None, [], {"operation_id": []}, {"operation_id": "a" * 32, "extra": True}])
def test_strict_request_fields(env, body):
    service, _, owner = env
    with pytest.raises(EnrollmentError, match="invalid_request"):
        service.execute("status", body, owner)


@pytest.mark.parametrize("role,subject", [("device", "speaker"), ("integration", "channel")])
def test_interoperability_fixture(env, monkeypatch, role, subject):
    from pathlib import Path

    fixture = json.loads((Path(__file__).parent / "fixtures/lifecycle-v1.json").read_text())
    service, _, owner = env
    from lifecycle_schema import LIMIT, OPERATION_LIMIT, TOMBSTONE_LIMIT

    assert fixture["history"]["general_limit"] == LIMIT
    assert fixture["history"]["total_limit"] == OPERATION_LIMIT
    assert fixture["history"]["tombstone_limit"] == TOMBSTONE_LIMIT
    assert fixture["teardown"]["attempt_seconds"] == auth_connections.CLOSE_SECONDS
    monkeypatch.setattr(lifecycle.time, "time", lambda: 2000000000)
    # Test fixture time is later than the synthetic owner's session; retain its
    # current epoch for the service-only controller resolver.
    from auth_policy import Principal

    principal = Principal(Role.OWNER, "owner", service.store.owner["generation"])
    owner = lambda: principal
    queued = control(service, owner, role=role, subject=subject, oid=fixture["operation_id"])
    pending = service.execute("poll", {}, client(service, role))
    delivery = service.execute("deliver", {"operation_id": fixture["operation_id"]}, client(service, role))
    replacement = client(service, token=delivery["credential"])
    ack = service.execute("ack", fixture["ack"], replacement)
    completed = service.execute("status", {"operation_id": fixture["operation_id"]}, replacement)
    assert [row["state"] for row in (queued, pending, delivery, ack, completed)] == fixture["sequence"]
    assert all(set(row) == set(fixture["public_fields"]) for row in (queued, pending, ack, completed))
    assert set(delivery) == set(fixture["public_fields"] + fixture["delivery_extra_fields"])
    assert queued["expires_at"] == 2000000000 + fixture["queue_seconds"]
    assert delivery["overlap_until"] == 2000000000 + fixture["overlap_seconds"]


async def test_teardown_failure_retries_without_restoring_authority(env):
    service, _, owner = env
    callback = AsyncMock(side_effect=[RuntimeError("synthetic close failure"), None])
    connection = auth_connections.retain(client(service)(), callback)
    control(service, owner, action="revoke")
    with pytest.raises(RuntimeError, match="transport_teardown_unavailable"):
        await auth_connections.disconnect_stale(service.store)
    assert connection in auth_connections._connections
    assert not service.store.current(connection.principal)
    await auth_connections.disconnect_stale(service.store)
    assert connection not in auth_connections._connections
    assert callback.await_count == 2


def _process_operation(path, action):
    from pathlib import Path

    from auth_policy import Principal

    service = Lifecycle(CredentialStore(Path(path)), ORIGIN)
    try:
        if action == "revoke":
            return control(service, lambda: Principal(Role.OWNER, "owner", service.store.owner["generation"]),
                           action="revoke", oid="2" * 32)
        return service.execute("deliver", {"operation_id": "1" * 32}, client(service))
    except EnrollmentError:
        return None


def test_separate_process_deliver_revoke_serialization(env):
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    service, _, owner = env
    control(service, owner)
    service.execute("poll", {}, client(service))
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
        first = pool.submit(_process_operation, str(service.store.path), "deliver")
        second = pool.submit(_process_operation, str(service.store.path), "revoke")
        delivery, revocation = first.result(timeout=10), second.result(timeout=10)
    assert revocation["state"] == "revoked"
    service.store.load()
    assert not service.store.authenticate("synthetic-device")
    if delivery:
        assert not service.store.authenticate(delivery["credential"])


@pytest.mark.parametrize("phase", ["queued", "pending", "delivered"])
def test_owner_origin_transition_does_not_revive_operation(env, phase):
    service, _, owner = env
    control(service, owner)
    if phase != "queued":
        service.execute("poll", {}, client(service))
    token = None
    if phase == "delivered":
        token = service.execute("deliver", {"operation_id": "1" * 32}, client(service))["credential"]
    service.origin = "https://changed.invalid"
    service.sweep()
    service.origin = ORIGIN
    service.sweep()
    assert service.store.lifecycle["operations"]["1" * 32]["state"] == "expired"
    if token:
        assert not service.store.authenticate(token)
        assert not service.store.authenticate("synthetic-device")


def test_expiring_pending_integration_invalidates_its_approvals(env, monkeypatch):
    service, enrollment, owner = env
    result = delivered(service, owner, "integration", "channel")
    key, row, code = ready(enrollment)
    approve(enrollment, row, code, client(service, token=result["credential"]))
    monkeypatch.setattr(lifecycle.time, "time", lambda: result["overlap_until"])
    service.sweep()
    assert service.store.enrollment["requests"][row["request_id"]]["state"] == "stale"
    with pytest.raises(EnrollmentError):
        enrollment.execute("redeem", signed(key, row, "redeem"))


def test_known_binding_survives_record_removal_without_silent_reenrollment(env):
    service, enrollment, owner = env
    key, row, code = ready(enrollment)
    approve(enrollment, row, code, owner)
    enrollment.execute("redeem", signed(key, row, "redeem"))
    control(service, owner, action="revoke", subject=row["device_id"])
    with service.store.transaction():
        service.store.replace(tuple(r for r in service.store.records if r.subject != row["device_id"]))
    with pytest.raises(EnrollmentError, match="already_owned"):
        request(enrollment, owner, key=key)


@pytest.mark.parametrize("saved_before_crash", [False, True])
def test_client_storage_crash_and_restart_ack(env, tmp_path, saved_before_crash):
    service, _, owner = env
    delivery = delivered(service, owner)
    path = tmp_path / "synthetic-client-store.json"
    # Model the client's durable commit boundary, with a crash that loses its
    # in-memory response/ACK state on either side of the actual atomic file save.
    if saved_before_crash:
        auth_store.atomic_private_json(path, {"credential": delivery["credential"],
                                             "operation_id": delivery["operation_id"]})
    del delivery
    assert service.store.lifecycle["operations"]["1" * 32]["state"] == "delivered"
    if saved_before_crash:
        saved = json.loads(path.read_text())
        result = service.execute("ack", {"operation_id": saved["operation_id"], "saved": True},
                                 client(service, token=saved["credential"]))
        assert result["state"] == "acknowledged"
        assert not service.store.authenticate("synthetic-device")
    else:
        assert not path.exists()
        with pytest.raises(EnrollmentError, match="unavailable"):
            service.execute("deliver", {"operation_id": "1" * 32}, client(service))
        assert service.store.lifecycle["operations"]["1" * 32]["state"] == "delivered"


@pytest.mark.parametrize("role,subject", [("device", "speaker"), ("integration", "channel")])
def test_exhausted_history_reserves_revocation_and_permanent_retries(env, role, subject):
    from lifecycle_schema import LIMIT

    service, _, owner = env
    original = delivered(service, owner, role, subject)
    # Retain an actual delivered operation and fill the general history with
    # expired, never-delivered rotations (which leave existing access usable).
    with service.store.transaction():
        template = dict(service.store.lifecycle["operations"]["1" * 32])
        template.update(state="expired", credential_id="", overlap_until=0)
        for index in range(LIMIT - 1):
            oid = f"{index:032x}"
            service.store.lifecycle["operations"][oid] = {**template, "operation_id": oid}
        service.store.replace(service.store.records)
    for _ in range(2):
        service.store.load()
        with pytest.raises(EnrollmentError, match="capacity"):
            control(service, owner, role=role, subject=subject, oid="e" * 32)
        result = control(service, owner, "revoke", role, subject, "f" * 32)
        assert result["state"] == "revoked"
        assert len(service.store.lifecycle["operations"]) == LIMIT + 1
        assert not service.store.authenticate("synthetic-" + role)
        assert not service.store.authenticate(original["credential"])
        assert control(service, owner, role=role, subject=subject)["state"] == "revoked"
        with pytest.raises(EnrollmentError, match="conflict"):
            control(service, owner, role=role, subject=subject, oid="f" * 32)
        with pytest.raises(EnrollmentError, match="capacity"):
            control(service, owner, "revoke", role, subject, "d" * 32)
    other_role, other_subject = ("integration", "channel") if role == "device" else ("device", "speaker")
    assert control(service, owner, "revoke", other_role, other_subject, "c" * 32)["state"] == "revoked"


@pytest.mark.parametrize("role,subject", [("device", "speaker"), ("integration", "channel")])
@pytest.mark.parametrize("resource", ["tombstones", "operations"])
def test_admission_enforces_last_revocation_headroom(env, role, subject, resource):
    from lifecycle_schema import OPERATION_LIMIT, TOMBSTONE_LIMIT

    service, _, owner = env
    result = control(service, owner, "revoke", role, subject)
    # Model retained history at the admission boundary. Records may have been
    # removed by a future enrollment writer, but history must remain permanent.
    with service.store.transaction():
        state = service.store.lifecycle
        if resource == "tombstones":
            count = TOMBSTONE_LIMIT - len({r.verifier for r in service.store.records}) - 1
            state["blocked"].extend(f"{index:064x}" for index in range(count))
        else:
            template = state["operations"][result["operation_id"]]
            for index in range(OPERATION_LIMIT - 3):
                oid = f"{index:032x}"
                state["operations"][oid] = {**template, "operation_id": oid}
        service.store.replace(service.store.records)
    fresh = Credential("fresh", Role(role), subject, verifier("synthetic-fresh"))
    with service.store.transaction():
        service.store.replace((*service.store.records, fresh))
    denied = Credential("denied", Role(role), subject, verifier("synthetic-denied"))
    with pytest.raises(EnrollmentError, match="capacity"), service.store.transaction():
        service.store.replace((*service.store.records, denied))
    service.store.load()
    assert service.store.authenticate("synthetic-fresh")
    assert control(service, owner, "revoke", role, subject, "f" * 32)["state"] == "revoked"
    service.store.load()
    assert not service.store.authenticate("synthetic-fresh")
    assert control(service, owner, "revoke", role, subject, "f" * 32)["state"] == "revoked"
    other_role, other_subject = ("integration", "channel") if role == "device" else ("device", "speaker")
    assert control(service, owner, "revoke", other_role, other_subject, "e" * 32)["state"] == "revoked"


async def test_noncooperative_and_raising_closers_do_not_block_peers_or_concurrent_sweeps(env, monkeypatch):
    import asyncio

    service, _, owner = env
    monkeypatch.setattr(auth_connections, "CLOSE_SECONDS", 0.02)
    finish = asyncio.Event()
    calls = 0

    async def hanging():
        nonlocal calls
        calls += 1
        try:
            await finish.wait()
        except asyncio.CancelledError:
            await finish.wait()  # Model a close implementation that suppresses cancellation.

    def raising():
        raise ValueError("synthetic synchronous failure")

    healthy = AsyncMock()
    connections = [auth_connections.retain(client(service)(), callback)
                   for callback in (hanging, raising, healthy)]
    control(service, owner, "revoke")
    try:
        outcomes = await asyncio.wait_for(asyncio.gather(
            auth_connections.disconnect_stale(service.store),
            auth_connections.disconnect_stale(service.store), return_exceptions=True), 0.5)
        assert all(isinstance(outcome, RuntimeError) for outcome in outcomes)
        healthy.assert_awaited_once()
        auth_connections.release(connections[0])  # WS finally must not hide unfinished media cleanup.
        assert connections[0] in auth_connections._connections
        assert connections[1] in auth_connections._connections
        assert connections[2] not in auth_connections._connections
        with pytest.raises(RuntimeError):
            await auth_connections.disconnect_stale(service.store)
        assert calls == 1  # No unbounded accumulation of abandoned hanging tasks.
        assert not service.store.current(connections[0].principal)
        connections[1].close = AsyncMock()
        finish.set()
        await asyncio.sleep(0)
        await auth_connections.disconnect_stale(service.store)
        assert all(connection not in auth_connections._connections for connection in connections)
    finally:
        finish.set()
        for connection in connections:
            auth_connections.release(connection)


async def test_real_realtime_session_close_preserves_failed_cleanup(env, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import realtime_session

    monkeypatch.setattr(auth_connections, "CLOSE_SECONDS", 0.02)
    manager = realtime_session.RealtimeManager()
    monkeypatch.setattr(realtime_session, "get_manager", lambda: manager)
    session = realtime_session.RealtimeSession("speaker", None)
    manager._sessions["speaker"] = session
    finish = asyncio.Event()

    async def hang():
        try:
            await finish.wait()
        except asyncio.CancelledError:
            await finish.wait()

    session._task = SimpleNamespace(cancel=hang)
    disconnect = AsyncMock(side_effect=[ValueError("synthetic disconnect"), None])
    session._connection = SimpleNamespace(disconnect=disconnect)
    healthy = SimpleNamespace(close=AsyncMock())
    manager._sessions["peer"] = healthy
    try:
        with pytest.raises(RuntimeError, match="transport_teardown_unavailable"):
            await asyncio.wait_for(manager.stop_all(), 0.5)
        assert session.is_closed  # Logical authority has stopped; cleanup is not done.
        assert manager._sessions["speaker"] is session
        healthy.close.assert_awaited_once()
        disconnect.assert_awaited_once()
        finish.set()
        await asyncio.sleep(0)
        await manager.stop("speaker")
        assert "speaker" not in manager._sessions
        assert disconnect.await_count == 2
    finally:
        finish.set()


def test_exhausted_history_can_revoke_recovery_without_unblocked_credentials(env):
    from lifecycle_schema import LIMIT

    service, enrollment, owner = env
    key, row, code = ready(enrollment)
    approve(enrollment, row, code, owner)
    enrollment.execute("redeem", signed(key, row, "redeem"))
    control(service, owner, action="recover", subject=row["device_id"])
    with service.store.transaction():
        state = service.store.lifecycle
        template = state["operations"]["1" * 32]
        for index in range(LIMIT - 1):
            oid = f"{index:032x}"
            state["operations"][oid] = {**template, "operation_id": oid, "state": "expired"}
        service.store.replace(service.store.records)
    blocked = service.store.lifecycle["blocked"][:]
    result = control(service, owner, "revoke", subject=row["device_id"], oid="f" * 32)
    assert result["state"] == "revoked"
    service.store.load()
    assert service.store.lifecycle["blocked"] == blocked
    assert row["device_id"] not in service.store.lifecycle["recovery"]
    with pytest.raises(EnrollmentError, match="already_owned"):
        request(enrollment, owner, key=key)
