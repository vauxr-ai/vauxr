"""Adversarial enrollment tests with synthetic keys and credentials only."""

import copy
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import auth_store
import config
import enrollment
from auth_policy import Role
from auth_store import Credential, CredentialStore, verifier
from enrollment import Enrollment, EnrollmentError, transcript
from http_server import make_http_app
from owner_auth import OwnerAuth
from owner_http import COOKIE, OWNER

ORIGIN = "https://owner.example"
HEADERS = {"Host": "owner.example", "Origin": ORIGIN, "X-Forwarded-Proto": "https"}


@pytest.fixture
def setup(tmp_path):
    store = CredentialStore(tmp_path / "authz.json")
    owner = OwnerAuth(store)
    owner.initialize()
    result = owner.claim(owner.console_claim())
    owner.acknowledge(result["save_acknowledgement"], True)
    cookie, _ = owner.login(result["operator_token"])
    with store.transaction():
        store.replace(
            (Credential("integration", Role.INTEGRATION, "channel", verifier("synthetic-integration")),)
        )
    return Enrollment(store, ORIGIN), owner, cookie


def owner_resolver(owner, cookie):
    def resolve():
        result = owner.session(cookie)
        return result[0] if result else None

    return resolve


def integration_resolver(service):
    return lambda: service.store.authenticate("synthetic-integration")


def request(service, resolve=lambda: None, kind="physical", key=None):
    key = key or Ed25519PrivateKey.generate()
    row = service.execute(
        "request",
        {"kind": kind, "display_name": "Speaker", "public_key": key.public_key().public_bytes_raw().hex()},
        resolve,
    )
    return key, row


def signed(key, row, action):
    return {"request_id": row["request_id"], "signature": key.sign(transcript(row, action)).hex()}


def ready(service, resolve=lambda: None, kind="physical"):
    key, row = request(service, resolve, kind)
    result = service.execute("prove", signed(key, row, "prove"))
    return key, row, result["code"]


def approve(service, row, code, resolve):
    body = {"request_id": row["request_id"], "code": code}
    assert service.execute("initiate", body, resolve) == {
        "status": "initiated",
        "device_id": row["device_id"],
    }
    assert service.execute("approve", body, resolve) == {"status": "approved", "device_id": row["device_id"]}


@pytest.mark.parametrize("actor", ["owner", "integration"])
def test_complete_secret_projection_restart_and_scope(setup, actor, caplog):
    service, owner, cookie = setup
    caplog.set_level(logging.INFO)
    resolve = owner_resolver(owner, cookie) if actor == "owner" else integration_resolver(service)
    key, row, code = ready(service)
    assert len(code) == 8 and code.isdigit()
    approve(service, row, code, resolve)
    listed = service.execute("list", {}, resolve)
    assert set(listed["requests"][0]) == {
        "request_id",
        "device_id",
        "kind",
        "display_name",
        "status",
        "expires_at",
    }
    assert code not in json.dumps(listed)
    restarted = Enrollment(CredentialStore(service.store.path), ORIGIN)
    result = restarted.execute("redeem", signed(key, row, "redeem"))
    principal = restarted.store.authenticate(result["device_token"])
    assert principal.role == Role.DEVICE and principal.subject == row["device_id"]
    assert principal.credential_id == result["credential_id"]
    with pytest.raises(EnrollmentError, match="unavailable"):
        restarted.execute("redeem", signed(key, row, "redeem"))
    assert restarted.execute("status", signed(key, row, "status"))["status"] == "consumed"
    for secret in (code, result["device_token"], key.private_bytes_raw().hex()):
        assert secret not in service.store.path.read_text()
        assert secret not in caplog.text
    assert service.store.path.stat().st_mode & 0o777 == 0o600
    assert service.store.path.with_suffix(".lock").stat().st_mode & 0o777 == 0o600


def test_fresh_request_is_not_button_or_key_proof(setup):
    service, _, _ = setup
    key, row = request(service)
    resolve = integration_resolver(service)
    assert service.execute("list", {}, resolve)["requests"][0]["status"] == "challenge"
    for action in ("initiate", "approve"):
        with pytest.raises(EnrollmentError, match="unavailable"):
            service.execute(action, {"request_id": row["request_id"], "code": "00000000"}, resolve)
    with pytest.raises(EnrollmentError, match="invalid_request"):
        service.execute("prove", {**signed(key, row, "prove"), "physical_verified": True})
    assert not any(record.role == Role.DEVICE for record in service.store.records)


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", "a" * 32),
        ("server_id", "b" * 32),
        ("origin", "https://other.example"),
        ("public_key", "c" * 64),
        ("kind", "browser"),
        ("device_id", "dev_" + "d" * 64),
        ("nonce", "e" * 64),
        ("expires_at", 1),
        ("owner_generation", "f" * 32),
        ("display_name", "Other"),
    ],
)
def test_every_binding_field_is_signed(setup, field, value):
    service, _, _ = setup
    key, row = request(service)
    altered = {**row, field: value}
    proof = signed(key, altered, "prove")
    proof["request_id"] = row["request_id"]
    with pytest.raises(EnrollmentError, match="invalid_proof"):
        service.execute("prove", proof)


def test_wrong_key_cross_request_and_action_replay(setup):
    service, _, _ = setup
    key, row, _ = ready(service)
    other, row2 = request(service)
    for proof in (
        signed(other, row, "prove"),
        {**signed(key, row, "prove"), "request_id": row2["request_id"]},
    ):
        with pytest.raises(EnrollmentError, match="invalid_proof"):
            service.execute("prove", proof)
    with pytest.raises(EnrollmentError, match="unavailable"):
        service.execute("prove", signed(key, row, "prove"))
    with pytest.raises(EnrollmentError, match="invalid_proof"):
        service.execute("cancel", signed(key, row, "prove"))


def test_code_substitution_attempts_persist_and_exhaust(setup):
    service, _, _ = setup
    _, row, code = ready(service)
    wrong = f"{(int(code) + 1) % 100_000_000:08d}"
    resolve = integration_resolver(service)
    for _ in range(5):
        service = Enrollment(CredentialStore(service.store.path), ORIGIN)
        with pytest.raises(EnrollmentError, match="invalid_proof"):
            service.execute(
                "initiate", {"request_id": row["request_id"], "code": wrong}, integration_resolver(service)
            )
    with pytest.raises(EnrollmentError, match="unavailable"):
        service.execute("initiate", {"request_id": row["request_id"], "code": code}, resolve)
    assert service.store.enrollment["requests"][row["request_id"]]["attempts"] == 5


def test_bad_signatures_exhaust_challenge(setup):
    service, _, _ = setup
    _, row = request(service)
    for _ in range(5):
        with pytest.raises(EnrollmentError, match="invalid_proof"):
            service.execute("prove", {"request_id": row["request_id"], "signature": "bad"})
    assert service.store.enrollment["requests"][row["request_id"]]["state"] == "failed"


@pytest.mark.parametrize("stage", ["ready", "initiated", "approved"])
def test_owner_recovery_invalidates_every_pending_stage(setup, stage):
    service, owner, _ = setup
    key, row, code = ready(service)
    body = {"request_id": row["request_id"], "code": code}
    resolve = integration_resolver(service)
    if stage in ("initiated", "approved"):
        service.execute("initiate", body, resolve)
    if stage == "approved":
        service.execute("approve", body, resolve)
    owner.console_claim(recover=True)
    assert service.execute("status", signed(key, row, "status"))["status"] == "stale"
    with pytest.raises(EnrollmentError, match="unavailable"):
        service.execute("redeem", signed(key, row, "redeem"))


@pytest.mark.parametrize("change", ["disable", "remove_reissue", "owner_epoch", "origin"])
def test_stale_authority_blocks_final_issuance(setup, change):
    service, owner, _ = setup
    key, row, code = ready(service)
    approve(service, row, code, integration_resolver(service))
    other = CredentialStore(service.store.path)
    if change == "owner_epoch":
        owner.console_claim(recover=True)
    elif change == "origin":
        service.origin = "https://other.example"
    else:
        with other.transaction():
            record = other.records[0]
            other.replace(())
            other.replace(
                (
                    replace(record, enabled=False)
                    if change == "disable"
                    else replace(record, verifier=verifier("synthetic-replacement")),
                )
            )
    with pytest.raises(EnrollmentError, match="unavailable"):
        service.execute("redeem", signed(key, row, "redeem"))
    assert not any(record.role == Role.DEVICE for record in service.store.records)


def test_initiator_and_approver_both_remain_current(setup):
    service, owner, cookie = setup
    key, row, code = ready(service)
    body = {"request_id": row["request_id"], "code": code}
    service.execute("initiate", body, integration_resolver(service))
    service.execute("approve", body, owner_resolver(owner, cookie))
    with service.store.transaction():
        service.store.replace(())
    with pytest.raises(EnrollmentError, match="unavailable"):
        service.execute("redeem", signed(key, row, "redeem"))


def test_controller_checked_inside_transaction_not_cached(setup):
    service, owner, cookie = setup
    _, row, code = ready(service)
    cached = service.store.authenticate("synthetic-integration")
    other = CredentialStore(service.store.path)
    with other.transaction():
        other.replace(())
    with pytest.raises(EnrollmentError, match="forbidden"):
        service.execute("initiate", {"request_id": row["request_id"], "code": code}, lambda: cached)
    owner.logout(cookie)
    with pytest.raises(EnrollmentError, match="unauthorized"):
        service.execute(
            "initiate", {"request_id": row["request_id"], "code": code}, owner_resolver(owner, cookie)
        )


def test_browser_requires_owner_and_has_no_physical_bypass(setup):
    service, owner, cookie = setup
    for resolve in (lambda: None, integration_resolver(service)):
        with pytest.raises(EnrollmentError):
            request(service, resolve, "browser")
    resolve = owner_resolver(owner, cookie)
    key, row, code = ready(service, resolve, "browser")
    for action in ("initiate", "approve", "deny"):
        body = {"request_id": row["request_id"]}
        if action != "deny":
            body["code"] = code
        with pytest.raises(EnrollmentError, match="forbidden"):
            service.execute(action, body, integration_resolver(service))
    assert service.execute("list", {}, integration_resolver(service))["requests"] == []
    approve(service, row, code, resolve)
    assert service.execute("redeem", signed(key, row, "redeem"))["device_id"] == row["device_id"]


@pytest.mark.parametrize("action", ["deny", "cancel"])
def test_terminal_actions_block_redeem(setup, action):
    service, _, _ = setup
    key, row, code = ready(service)
    resolve = integration_resolver(service)
    approve(service, row, code, resolve)
    body = signed(key, row, action) if action == "cancel" else {"request_id": row["request_id"]}
    result = service.execute(action, body, resolve)
    assert result["status"] == ("cancelled" if action == "cancel" else "denied")
    with pytest.raises(EnrollmentError, match="unavailable"):
        service.execute("redeem", signed(key, row, "redeem"))


def test_expiry_boundary_capacity_pruning_and_restart_limits(setup, monkeypatch):
    service, _, _ = setup
    key, row, code = ready(service)
    approve(service, row, code, integration_resolver(service))
    monkeypatch.setattr(enrollment.time, "time", lambda: row["expires_at"])
    assert service.execute("status", signed(key, row, "status"))["status"] == "expired"
    with pytest.raises(EnrollmentError, match="unavailable"):
        service.execute("redeem", signed(key, row, "redeem"))
    for _ in range(64):
        request(service)
    with pytest.raises(EnrollmentError, match="capacity"):
        request(service)
    assert len(service.store.enrollment["requests"]) == 64
    for _ in range(60):
        Enrollment(CredentialStore(service.store.path), ORIGIN).rate_limit()
    with pytest.raises(EnrollmentError, match="rate_limited"):
        service.rate_limit()
    assert len(CredentialStore(service.store.path).enrollment["attempts"]) == 60
    monkeypatch.setattr(enrollment.time, "time", lambda: row["expires_at"] + 301)
    service.rate_limit()
    request(service)
    assert len(service.store.enrollment["requests"]) == 1


def test_duplicate_keys_names_and_known_disabled_device(setup):
    service, _, _ = setup
    key, row, code = ready(service)
    with pytest.raises(EnrollmentError, match="conflict"):
        request(service, key=key)
    _, other = request(service)
    assert other["display_name"] == row["display_name"] and other["device_id"] != row["device_id"]
    approve(service, row, code, integration_resolver(service))
    result = service.execute("redeem", signed(key, row, "redeem"))
    with service.store.transaction():
        service.store.replace(
            tuple(
                replace(record, enabled=False) if record.id == result["credential_id"] else record
                for record in service.store.records
            )
        )
    with pytest.raises(EnrollmentError, match="already_owned"):
        request(service, key=key)


def test_known_device_inserted_after_approval_is_not_overwritten(setup):
    service, _, _ = setup
    key, row, code = ready(service)
    approve(service, row, code, integration_resolver(service))
    with service.store.transaction():
        service.store.replace(
            (
                *service.store.records,
                Credential("race", Role.DEVICE, row["device_id"], verifier("synthetic-race")),
            )
        )
    with pytest.raises(EnrollmentError, match="already_owned"):
        service.execute("redeem", signed(key, row, "redeem"))
    assert service.store.authenticate("synthetic-race")


def test_concurrent_redemption_has_exactly_one_winner(setup):
    service, _, _ = setup
    key, row, code = ready(service)
    approve(service, row, code, integration_resolver(service))

    def redeem(_):
        contender = Enrollment(CredentialStore(service.store.path), ORIGIN)
        try:
            return contender.execute("redeem", signed(key, row, "redeem"))
        except EnrollmentError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(redeem, range(8)))
    assert sum(result is not None for result in results) == 1
    records = CredentialStore(service.store.path).records
    assert len([record for record in records if record.role == Role.DEVICE]) == 1


@pytest.mark.parametrize("after_rename", [False, True])
def test_issue_failure_is_atomic_and_never_redisplays_secret(setup, monkeypatch, after_rename):
    service, _, _ = setup
    key, row, code = ready(service)
    approve(service, row, code, integration_resolver(service))
    writer = auth_store.atomic_private_json

    def fail(path, payload):
        if after_rename:
            writer(path, payload)
        raise OSError("synthetic secret must not escape HTTP")

    with monkeypatch.context() as scoped:
        scoped.setattr(auth_store, "atomic_private_json", fail)
        with pytest.raises(OSError):
            service.execute("redeem", signed(key, row, "redeem"))
    restarted = Enrollment(CredentialStore(service.store.path), ORIGIN)
    assert len([r for r in restarted.store.records if r.role == Role.DEVICE]) == int(after_rename)
    assert restarted.store.enrollment["requests"][row["request_id"]]["state"] == (
        "consumed" if after_rename else "approved"
    )
    if after_rename:
        with pytest.raises(EnrollmentError, match="unavailable"):
            restarted.execute("redeem", signed(key, row, "redeem"))
    else:
        assert restarted.execute("redeem", signed(key, row, "redeem"))["device_token"]


def test_owner_writes_preserve_enrollment_and_settings(setup, tmp_path):
    service, owner, _ = setup
    request(service)
    before = copy.deepcopy(service.store.enrollment)
    settings = tmp_path / "devices.json"
    settings.write_text('{"synthetic":"unchanged"}')
    owner.console_claim(recover=True)
    loaded = CredentialStore(service.store.path)
    assert loaded.enrollment == before and len(loaded.records) == 1
    assert settings.read_text() == '{"synthetic":"unchanged"}'
    assert json.loads(service.store.path.read_text())["version"] == 3


@pytest.mark.parametrize(
    "mutation",
    [
        lambda s: s.update(unknown=True),
        lambda s: s.update(version=True),
        lambda s: s.update(attempts=[0] * 61),
        lambda s: s.update(server_id="bad"),
        lambda s: next(iter(s["requests"].values())).update(attempts=6),
        lambda s: next(iter(s["requests"].values())).update(expires_at=float("nan")),
        lambda s: next(iter(s["requests"].values())).update(state="approved"),
        lambda s: next(iter(s["requests"].values())).update(public_key="bad"),
    ],
)
def test_corrupt_namespace_clears_all_grants(setup, mutation):
    service, _, _ = setup
    request(service)
    data = json.loads(service.store.path.read_text())
    mutation(data["enrollment"])
    service.store.path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Invalid credential store"):
        service.store.load()
    assert service.store.records == () and service.store.owner == {} and service.store.enrollment == {}


@pytest.fixture
async def client(monkeypatch, tmp_path):
    config.reset_config()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OWNER_HTTPS_ORIGIN", ORIGIN)
    monkeypatch.setenv("OWNER_TRUSTED_PROXIES", "127.0.0.1/32")
    monkeypatch.delenv("OPERATOR_TOKEN", raising=False)
    async with TestClient(TestServer(make_http_app())) as client:
        owner = client.app[OWNER]
        result = owner.claim(owner.console_claim())
        owner.acknowledge(result["save_acknowledgement"], True)
        cookie, session = owner.login(result["operator_token"])
        client.owner_headers = {**HEADERS, "Cookie": f"{COOKIE}={cookie}", "X-CSRF-Token": session.csrf}
        with owner.store.transaction():
            owner.store.replace(
                (Credential("integration", Role.INTEGRATION, "channel", verifier("synthetic-integration")),)
            )
        yield client
    config.reset_config()


async def post(client, action, body, headers=None):
    return await client.post(
        "/api/enrollment/v1/" + action, json=body, headers=HEADERS if headers is None else headers
    )


async def test_http_end_to_end_owner_and_integration(client):
    key = Ed25519PrivateKey.generate()
    response = await post(
        client,
        "request",
        {
            "kind": "physical",
            "display_name": "Speaker",
            "public_key": key.public_key().public_bytes_raw().hex(),
        },
    )
    assert response.status == 200
    row = await response.json()
    response = await post(client, "prove", signed(key, row, "prove"))
    code = (await response.json())["code"]
    body = {"request_id": row["request_id"], "code": code}
    response = await post(client, "initiate", body, client.owner_headers)
    assert response.status == 200
    response = await post(
        client, "approve", body, {**HEADERS, "Authorization": "Bearer synthetic-integration"}
    )
    assert await response.json() == {"status": "approved", "device_id": row["device_id"]}
    response = await post(client, "redeem", signed(key, row, "redeem"))
    result = await response.json()
    assert response.status == 200 and result["device_token"]
    assert response.headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in response.headers


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Host": "owner.example"},
        {**HEADERS, "Host": "other.example"},
        {**HEADERS, "Origin": "https://evil.example"},
        {**HEADERS, "Forwarded": "proto=https"},
        {**HEADERS, "X-Forwarded-Proto": "http"},
        {**HEADERS, "X-Forwarded-Proto": "https,http"},
    ],
)
async def test_http_https_origin_proxy_boundary(client, headers):
    response = await post(client, "list", {}, headers)
    assert response.status == 403
    assert response.headers["Cache-Control"] == "no-store"


async def test_http_cookie_csrf_and_roles(client):
    for headers, status in (
        (HEADERS, 401),
        (client.owner_headers, 200),
        ({key: val for key, val in client.owner_headers.items() if key != "X-CSRF-Token"}, 403),
        ({**client.owner_headers, "Origin": "null"}, 403),
    ):
        response = await post(client, "list", {}, headers)
        assert response.status == status
    store = client.app[OWNER].store
    with store.transaction():
        store.replace(
            (
                *store.records,
                Credential("device", Role.DEVICE, "speaker", verifier("synthetic-device")),
                Credential("old-owner", Role.OWNER, "owner", verifier("synthetic-owner")),
            )
        )
    for token, status in (("synthetic-device", 403), ("synthetic-owner", 401)):
        response = await post(client, "list", {}, {**HEADERS, "Authorization": "Bearer " + token})
        assert response.status == status


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        "null",
        "{",
        '{"kind":"physical","kind":"browser"}',
        '{"physical_verified":true}',
        '{"x":NaN}',
        "[" * 1100,
        "x" * 4097,
    ],
)
async def test_http_malformed_and_unexpected_fields_charge_budget(client, raw):
    response = await client.post(
        "/api/enrollment/v1/request", data=raw, headers={**HEADERS, "Content-Type": "application/json"}
    )
    assert response.status == 400
    assert len(client.app[OWNER].store.enrollment["attempts"]) == 1


async def test_http_storage_failure_is_redacted(client, monkeypatch, caplog):
    def fail(*args):
        raise OSError("synthetic-secret")

    monkeypatch.setattr(auth_store, "atomic_private_json", fail)
    response = await post(client, "list", {}, client.owner_headers)
    assert response.status == 503
    assert "synthetic-secret" not in await response.text() + caplog.text


def test_cross_request_code_does_not_authorize_target(setup, monkeypatch):
    service, _, _ = setup
    values = iter((12345678, 87654321))
    monkeypatch.setattr(enrollment.secrets, "randbelow", lambda _: next(values))
    _, row, code = ready(service)
    _, other, other_code = ready(service)
    with pytest.raises(EnrollmentError, match="invalid_proof"):
        service.execute(
            "initiate", {"request_id": other["request_id"], "code": code}, integration_resolver(service)
        )
    assert service.store.enrollment["requests"][row["request_id"]]["state"] == "ready"
    approve(service, other, other_code, integration_resolver(service))


def test_real_second_server_rejects_first_server_proof(setup, tmp_path):
    service, _, _ = setup
    key, row = request(service)
    other_store = CredentialStore(tmp_path / "second" / "authz.json")
    other_owner = OwnerAuth(other_store, "vx_op_" + "S" * 43)
    other_owner.initialize()
    second = Enrollment(other_store, ORIGIN)
    _, other_row = request(second, key=key)
    assert other_row["server_id"] != row["server_id"]
    body = {**signed(key, row, "prove"), "request_id": other_row["request_id"]}
    with pytest.raises(EnrollmentError, match="invalid_proof"):
        second.execute("prove", body)


def test_concurrent_duplicate_key_and_approval(setup):
    service, _, _ = setup
    key = Ed25519PrivateKey.generate()

    def create(_):
        contender = Enrollment(CredentialStore(service.store.path), ORIGIN)
        try:
            return request(contender, key=key)[1]
        except EnrollmentError as exc:
            assert str(exc) == "conflict"
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = [row for row in pool.map(create, range(4)) if row is not None]
    assert len(rows) == 1
    row = rows[0]
    code = service.execute("prove", signed(key, row, "prove"))["code"]
    body = {"request_id": row["request_id"], "code": code}
    service.execute("initiate", body, integration_resolver(service))

    def approval(_):
        contender = Enrollment(CredentialStore(service.store.path), ORIGIN)
        try:
            return contender.execute("approve", body, integration_resolver(contender))
        except EnrollmentError as exc:
            assert str(exc) == "unavailable"
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(approval, range(4)))
    assert sum(result is not None for result in results) == 1


def redeem_in_process(arguments):
    from pathlib import Path

    path, body = arguments
    contender = Enrollment(CredentialStore(Path(path)), ORIGIN)
    try:
        contender.execute("redeem", body)
        return True
    except EnrollmentError:
        return False


def test_process_redemption_serializes_with_shared_snapshot(setup):
    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context

    service, _, _ = setup
    key, row, code = ready(service)
    approve(service, row, code, integration_resolver(service))
    args = (str(service.store.path), signed(key, row, "redeem"))
    with ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn")) as pool:
        results = list(pool.map(redeem_in_process, [args, args]))
    assert sum(results) == 1
    assert len(CredentialStore(service.store.path).records) == 2


def test_recovery_and_issuance_linearize_without_losing_namespaces(setup):
    service, _, _ = setup
    key, row, code = ready(service)
    approve(service, row, code, integration_resolver(service))

    def recover():
        console = OwnerAuth(CredentialStore(service.store.path))
        console.console_claim(recover=True)

    def redeem():
        contender = Enrollment(CredentialStore(service.store.path), ORIGIN)
        try:
            contender.execute("redeem", signed(key, row, "redeem"))
            return True
        except EnrollmentError as exc:
            assert str(exc) == "unavailable"
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        recovery = pool.submit(recover)
        result = pool.submit(redeem).result()
        recovery.result()
    loaded = CredentialStore(service.store.path)
    assert loaded.owner["mode"] == "recovery"
    assert len(loaded.records) == 1 + int(result)
    assert loaded.enrollment["requests"][row["request_id"]]["state"] == ("consumed" if result else "stale")


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", None),
        ("kind", []),
        ("public_key", 3),
        ("public_key", "A" * 64),
        ("public_key", "a" * 63),
        ("display_name", None),
        ("display_name", "x" * 65),
        ("display_name", "Speaker\nsecret"),
        ("display_name", "\u202esecret"),
    ],
)
def test_request_types_and_noncanonical_inputs_fail_closed(setup, field, value):
    service, _, _ = setup
    body = {"kind": "physical", "public_key": "a" * 64, "display_name": "Speaker", field: value}
    with pytest.raises(EnrollmentError, match="invalid_request"):
        service.execute("request", body)


async def test_browser_http_requires_session_csrf_and_scoped_redeem(client):
    key = Ed25519PrivateKey.generate()
    body = {
        "kind": "browser",
        "public_key": key.public_key().public_bytes_raw().hex(),
        "display_name": "Browser",
    }
    assert (await post(client, "request", body)).status == 401
    row = await (await post(client, "request", body, client.owner_headers)).json()
    code = (await (await post(client, "prove", signed(key, row, "prove"), client.owner_headers)).json())[
        "code"
    ]
    for action in ("initiate", "approve"):
        response = await post(
            client, action, {"request_id": row["request_id"], "code": code}, client.owner_headers
        )
        assert response.status == 200
    response = await post(client, "redeem", signed(key, row, "redeem"), client.owner_headers)
    assert response.status == 200
    principal = client.app[OWNER].store.authenticate((await response.json())["device_token"])
    assert principal.role == Role.DEVICE


async def test_http_rate_limit_persists_malformed_requests(client):
    for _ in range(60):
        response = await post(client, "request", {"unexpected": True})
        assert response.status == 400
    response = await post(client, "list", {}, client.owner_headers)
    assert response.status == 429
    store = CredentialStore(client.app[OWNER].store.path)
    with pytest.raises(EnrollmentError, match="rate_limited"):
        Enrollment(store, ORIGIN).rate_limit()


async def test_http_query_duplicate_auth_and_untrusted_peer(client, monkeypatch):
    from owner_http import PROXIES

    response = await client.post("/api/enrollment/v1/list?token=synthetic", json={}, headers=HEADERS)
    assert response.status == 403
    response = await client.post(
        "/api/enrollment/v1/list",
        json={},
        headers=[
            *HEADERS.items(),
            ("Authorization", "Bearer synthetic-integration"),
            ("Authorization", "Bearer synthetic-other"),
        ],
    )
    assert response.status == 400
    response = await client.post(
        "/api/enrollment/v1/list", json={}, headers=[*HEADERS.items(), ("Origin", "https://evil.example")]
    )
    assert response.status == 403
    # Adjust the trusted proxy configuration without mutating the running app.
    monkeypatch.setitem(client.app._state, PROXIES, ())
    assert (await post(client, "list", {}, client.owner_headers)).status == 403


def test_frozen_signature_transcript_vector():
    from pathlib import Path

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    vector = json.loads((Path(__file__).parent / "fixtures/enrollment-v1-vector.json").read_text())
    row = vector["challenge"]
    message = vector["message_ascii"].encode("ascii")
    assert transcript(row, "prove") == message
    public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(row["public_key"]))
    public.verify(bytes.fromhex(vector["signature_hex"]), message)
    assert (
        Ed25519PrivateKey.from_private_bytes(bytes.fromhex(vector["seed_hex"])).sign(message).hex()
        == (vector["signature_hex"])
    )


async def test_http_chunked_bounds_utf8_and_content_type(client):
    async def chunks():
        for _ in range(5):
            yield b" " * 1024

    response = await client.post(
        "/api/enrollment/v1/request", data=chunks(), headers={**HEADERS, "Content-Type": "application/json"}
    )
    assert response.status == 400
    for body, content_type in (
        (b"{}", "text/plain"),
        ("{}".encode("utf-16"), "application/json"),
        (b"\xef\xbb\xbf{}", "application/json"),
    ):
        response = await client.post(
            "/api/enrollment/v1/list", data=body, headers={**HEADERS, "Content-Type": content_type}
        )
        assert response.status == 400


async def test_http_body_timeout_returns_fixed_error(client, monkeypatch):
    import asyncio

    import enrollment_http

    original_timeout = asyncio.timeout
    monkeypatch.setattr(enrollment_http.asyncio, "timeout", lambda _: original_timeout(0.01))

    async def slow():
        yield b"{"
        await asyncio.sleep(0.1)
        yield b"}"

    response = await client.post(
        "/api/enrollment/v1/request", data=slow(), headers={**HEADERS, "Content-Type": "application/json"}
    )
    assert response.status == 400
    assert await response.json() == {"error": "invalid_request"}


def test_enrollment_admission_caps_credential_storage(setup):
    service, _, _ = setup
    with service.store.transaction():
        service.store.replace(
            tuple(Credential(f"c{i}", Role.DEVICE, f"d{i}", verifier(f"synthetic-{i}")) for i in range(1024))
        )
    with pytest.raises(EnrollmentError, match="capacity"):
        request(service)


def test_fresh_proof_after_lost_code_response_needs_new_request(setup):
    service, _, _ = setup
    key, row, _ = ready(service)
    service = Enrollment(CredentialStore(service.store.path), ORIGIN)
    with pytest.raises(EnrollmentError, match="unavailable"):
        service.execute("prove", signed(key, row, "prove"))
    service.execute("cancel", signed(key, row, "cancel"))
    _, fresh = request(service, key=key)
    assert fresh["request_id"] != row["request_id"] and fresh["nonce"] != row["nonce"]
    with pytest.raises(EnrollmentError, match="invalid_proof"):
        service.execute("prove", {**signed(key, row, "prove"), "request_id": fresh["request_id"]})


def test_enrollment_writes_require_shared_transaction(setup):
    service, _, _ = setup
    request(service)
    with pytest.raises(RuntimeError, match="transaction"):
        service.store.save_enrollment(service.store.enrollment)


def test_origin_a_b_a_cannot_revive_unexamined_approval(setup):
    service, _, _ = setup
    key, row, code = ready(service)
    approve(service, row, code, integration_resolver(service))
    other = Enrollment(CredentialStore(service.store.path), "https://other.example")
    other.initialize()
    restored = Enrollment(CredentialStore(service.store.path), ORIGIN)
    restored.initialize()
    with pytest.raises(EnrollmentError, match="unavailable"):
        restored.execute("redeem", signed(key, row, "redeem"))
    assert restored.execute("status", signed(key, row, "status"))["status"] == "stale"
