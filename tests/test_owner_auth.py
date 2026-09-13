"""Owner lifecycle, durable failure and adversarial HTTP contract coverage."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

import auth_store
import config
import owner_auth
from auth_policy import Role
from auth_store import Credential, CredentialStore, verifier
from http_server import make_http_app
from owner_auth import OwnerAuth, OwnerError, environment_token, trusted_origin
from owner_http import COOKIE, OWNER

# Synthetic fixtures only: deliberately reproducible, never production credentials.
TOKEN_A = "vx_op_" + "A" * 43
TOKEN_B = "vx_op_" + "B" * 43
ORIGIN = "https://owner.example"
HEADERS = {"Host": "owner.example", "Origin": ORIGIN, "X-Forwarded-Proto": "https"}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    config.reset_config()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OWNER_HTTPS_ORIGIN", ORIGIN)
    monkeypatch.setenv("OWNER_TRUSTED_PROXIES", "127.0.0.1/32")
    monkeypatch.delenv("OPERATOR_TOKEN", raising=False)
    monkeypatch.delenv("DEVICE_TOKEN", raising=False)
    yield
    config.reset_config()


def service(path: Path, override: str | None = None) -> OwnerAuth:
    result = OwnerAuth(CredentialStore(path / "authz.json"), override)
    result.initialize()
    return result


def claim_saved(owner: OwnerAuth) -> str:
    claimed = owner.claim(owner.console_claim())
    owner.acknowledge(claimed["save_acknowledgement"], True)
    return claimed["operator_token"]


def test_no_token_start_and_explicit_console_only(tmp_path):
    assert config.load_config().device.token == ""
    owner = service(tmp_path)
    assert owner.status()["state"] == "unclaimed"
    assert "claim" not in owner.store.owner
    with pytest.raises(OwnerError, match="invalid_claim"):
        owner.claim("remote-first-visitor")
    assert "remote-first-visitor" not in owner.store.path.read_text()


def test_claim_save_restart_and_redaction(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    owner = service(tmp_path)
    code = owner.console_claim()
    claimed = owner.claim(code)
    token, ack = claimed["operator_token"], claimed["save_acknowledgement"]
    assert owner_auth.TOKEN_PATTERN.fullmatch(token)
    with pytest.raises(OwnerError, match="invalid_login"):
        owner.login(token)
    with pytest.raises(OwnerError, match="invalid_claim"):
        owner.claim(code)
    restarted = service(tmp_path)
    with pytest.raises(OwnerError):
        restarted.acknowledge(ack, "true")
    restarted.acknowledge(ack, True)
    with pytest.raises(OwnerError):
        restarted.acknowledge(ack, True)
    cookie, session = restarted.login(token)
    assert restarted.session(cookie)
    # Process-local sessions intentionally do not survive restart.
    assert not service(tmp_path).session(cookie)
    assert service(tmp_path).login(token)
    for secret in (token, code, ack, cookie, session.csrf):
        assert secret not in owner.store.path.read_text()
        assert secret not in caplog.text
    assert token not in repr(restarted)
    assert session.csrf not in repr(session)
    assert owner.store.path.stat().st_mode & 0o777 == 0o600


def test_environment_precedence_change_removal_recovery(tmp_path):
    owner = service(tmp_path)
    old_token = claim_saved(owner)
    old_cookie, _ = owner.login(old_token)
    managed = service(tmp_path, TOKEN_A)
    assert owner.session(old_cookie) is None
    with pytest.raises(OwnerError):
        managed.login(old_token)
    cookie, _ = managed.login(TOKEN_A)
    assert service(tmp_path, TOKEN_A).login(TOKEN_A)
    assert managed.session(cookie)  # same override does not change generation
    service(tmp_path, TOKEN_B)
    assert managed.session(cookie) is None
    with pytest.raises(OwnerError):
        managed.login(TOKEN_A)
    with pytest.raises(OwnerError, match="authoritative OPERATOR_TOKEN"):
        managed.console_claim(recover=True)
    removed = service(tmp_path)
    assert removed.status()["state"] == "recovery"
    for token in (old_token, TOKEN_A, TOKEN_B):
        with pytest.raises(OwnerError):
            removed.login(token)
        assert verifier(token) not in removed.store.path.read_text()
    with pytest.raises(OwnerError):
        removed.console_claim()
    new = removed.claim(removed.console_claim(recover=True))
    removed.acknowledge(new["save_acknowledgement"], True)
    assert removed.login(new["operator_token"])


def test_environment_a_b_a_across_restarts_revokes_original_session(tmp_path):
    """Restoring A's verifier must not restore the original owner session."""
    original = service(tmp_path, TOKEN_A)
    cookie, _ = original.login(TOKEN_A)

    # These distinct services model server restarts that update the durable
    # store. Keep the original process alive to attempt its stale cookie once
    # A's verifier has been restored.
    restarted_with_b = service(tmp_path, TOKEN_B)
    assert restarted_with_b.status()["state"] == "environment"
    restarted_with_a = service(tmp_path, TOKEN_A)
    assert restarted_with_a.status()["state"] == "environment"

    assert original.session(cookie) is None
    fresh_cookie, _ = restarted_with_a.login(TOKEN_A)
    assert restarted_with_a.session(fresh_cookie)


@pytest.mark.parametrize("token", ["", "password", " " + TOKEN_A, TOKEN_A + "\n", "vx_op_" + "!" * 43])
def test_invalid_explicit_environment(monkeypatch, token):
    monkeypatch.setenv("OPERATOR_TOKEN", token)
    with pytest.raises(ValueError, match="OPERATOR_TOKEN"):
        environment_token()
    with pytest.raises(ValueError, match="OPERATOR_TOKEN"):
        make_http_app()


def test_recovery_preserves_clients_and_settings(tmp_path):
    owner = service(tmp_path)
    token = claim_saved(owner)
    cookie, _ = owner.login(token)
    clients = (Credential("device-1", Role.DEVICE, "speaker", verifier("synthetic-device")),
               Credential("integration-1", Role.INTEGRATION, "channel", verifier("synthetic-integration")))
    with owner.store.transaction():
        owner.store.replace(clients)
    settings = tmp_path / "devices.json"
    settings.write_text('{"speaker":{"name":"Kitchen"}}')
    before = settings.read_bytes()
    console = OwnerAuth(CredentialStore(owner.store.path))
    console.console_claim(recover=True)
    assert owner.session(cookie) is None
    with pytest.raises(OwnerError):
        owner.login(token)
    assert owner.store.records == clients
    assert settings.read_bytes() == before


def test_concurrent_claim_and_recovery(tmp_path):
    owner = service(tmp_path)
    code = owner.console_claim()

    def attempt(_: int) -> dict | None:
        try:
            return OwnerAuth(CredentialStore(owner.store.path)).claim(code)
        except OwnerError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    successful = [result for result in results if result]
    assert len(successful) == 1
    result = successful[0]
    # Console recovery racing the save ACK makes the pending credential unusable.
    OwnerAuth(CredentialStore(owner.store.path)).console_claim(recover=True)
    with pytest.raises(OwnerError):
        owner.acknowledge(result["save_acknowledgement"], True)
    with pytest.raises(OwnerError):
        owner.login(result["operator_token"])


def test_serialized_owner_and_client_writers(tmp_path):
    owner = service(tmp_path)

    def add(index: int) -> None:
        store = CredentialStore(owner.store.path)
        with store.transaction():
            store.replace((*store.records, Credential(f"device-{index}", Role.DEVICE, f"speaker-{index}",
                                                       verifier(f"synthetic-device-{index}"))))

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(add, index) for index in range(20)]
        futures.append(pool.submit(owner.console_claim, True))
        for future in futures:
            future.result()
    loaded = CredentialStore(owner.store.path)
    assert len(loaded.records) == 20
    assert loaded.owner["mode"] == "recovery"


def test_expiry_and_durable_attempt_limits(tmp_path, monkeypatch):
    owner = service(tmp_path)
    code = owner.console_claim()
    for _ in range(5):
        with pytest.raises(OwnerError):
            service(tmp_path).claim("wrong")
    with pytest.raises(OwnerError):
        owner.claim(code)
    code = owner.console_claim()
    now = owner_auth.time.time()
    monkeypatch.setattr(owner_auth.time, "time", lambda: now + 301)
    with pytest.raises(OwnerError):
        owner.claim(code)
    code = owner.console_claim()
    result = owner.claim(code)
    monkeypatch.setattr(owner_auth.time, "time", lambda: now + 602)
    with pytest.raises(OwnerError):
        owner.acknowledge(result["save_acknowledgement"], True)
    for _ in range(10):
        service(tmp_path).rate_limit()
    with pytest.raises(OwnerError, match="rate_limited"):
        service(tmp_path).rate_limit()
    monkeypatch.setattr(owner_auth.time, "time", lambda: now + 663)
    owner.rate_limit()


@pytest.mark.parametrize("after_rename", [False, True])
def test_durable_claim_failure_does_not_disclose_or_grant(tmp_path, after_rename):
    owner = service(tmp_path)
    code = owner.console_claim()
    original = auth_store.atomic_private_json

    def fail(path, payload):
        if after_rename:
            original(path, payload)
        raise OSError("synthetic storage failure")

    with patch.object(auth_store, "atomic_private_json", fail), pytest.raises(OSError):
        owner.claim(code)
    disk = CredentialStore(owner.store.path)
    assert owner.store.owner == disk.owner
    assert disk.owner["mode"] == "unclaimed"
    if after_rename:
        with pytest.raises(OwnerError):
            owner.claim(code)
    else:
        assert owner.claim(code)["save_required"]
    # Explicit console recovery always supplies a fresh attempt after uncertain commit.
    owner.console_claim(recover=True)


@pytest.mark.parametrize("origin", ["http://owner.example", "https://owner.example/", "https://x@y",
                                    "https://owner.example?q=x", "https://owner.example#x", "https://x:bad"])
def test_origin_configuration_rejected(origin):
    with pytest.raises(ValueError):
        trusted_origin(origin)


async def test_http_complete_session_csrf_and_logout():
    async with TestClient(TestServer(make_http_app())) as client:
        service = client.app[OWNER]
        code = service.console_claim()
        response = await client.post("/api/auth/claim", headers=HEADERS, json={"code": code})
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "Access-Control-Allow-Origin" not in response.headers
        result = await response.json()
        token = result["operator_token"]
        assert (await client.post("/api/auth/login", headers=HEADERS,
                                  json={"operator_token": token})).status == 400
        assert (await client.post("/api/auth/save", headers=HEADERS,
                                  json={"saved": True, "save_acknowledgement": result["save_acknowledgement"]}
                                  )).status == 200
        response = await client.post("/api/auth/login", headers=HEADERS, json={"operator_token": token})
        body = await response.json()
        cookie = response.cookies[COOKIE]
        assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Strict"
        assert cookie["path"] == "/" and not cookie["domain"] and int(cookie["max-age"]) == 43200
        headers = {**HEADERS, "Cookie": f"{COOKIE}={cookie.value}"}
        assert (await client.get("/api/devices", headers=headers)).status == 200
        assert (await client.post("/api/auth/logout", headers=headers, json={})).status == 403
        assert (await client.patch("/api/devices/missing", headers=headers, json={})).status == 403
        headers["X-CSRF-Token"] = body["csrf_token"]
        assert (await client.patch("/api/devices/missing", headers=headers, json={})).status == 404
        assert (await client.post("/api/auth/logout", headers=headers, json={})).status == 200
        assert (await client.get("/api/auth/session", headers=headers)).status == 401
        assert (await client.get("/api/devices", headers=headers)).status == 401
        # Everyday token is never a transport/admin bearer substitute.
        assert (await client.get("/api/devices", headers={**HEADERS, "Authorization": f"Bearer {token}"})
                ).status == 401


@pytest.mark.parametrize("changes", [{"Origin": "https://evil.example"}, {"Origin": "null"},
                                     {"Origin": None}, {"Host": "evil.example"},
                                     {"X-Forwarded-Proto": None}, {"X-Forwarded-Proto": "http"},
                                     {"X-Forwarded-Proto": "https, http"},
                                     {"Forwarded": "proto=https"}])
async def test_untrusted_transport_rejected_before_claim(changes):
    async with TestClient(TestServer(make_http_app())) as client:
        service = client.app[OWNER]
        code = service.console_claim()
        headers = {key: value for key, value in {**HEADERS, **changes}.items() if value is not None}
        response = await client.post("/api/auth/claim", headers=headers, json={"code": code})
        assert response.status == 403
        assert service.claim(code)["save_required"]


async def test_no_origin_or_untrusted_proxy_fails_closed(monkeypatch):
    for origin, proxies in [("", "127.0.0.1/32"), (ORIGIN, "192.0.2.1/32")]:
        monkeypatch.setenv("OWNER_HTTPS_ORIGIN", origin)
        monkeypatch.setenv("OWNER_TRUSTED_PROXIES", proxies)
        async with TestClient(TestServer(make_http_app())) as client:
            response = await client.get("/api/auth/status", headers=HEADERS)
            assert response.status == 403


async def test_http_malformed_rate_limit_and_secret_free_errors(caplog):
    caplog.set_level(logging.DEBUG)
    async with TestClient(TestServer(make_http_app())) as client:
        for payload in [[], {"operator_token": [TOKEN_A]}, {"operator_token": TOKEN_A, "extra": TOKEN_B}]:
            response = await client.post("/api/auth/login", headers=HEADERS, json=payload)
            assert response.status == 400
            assert TOKEN_A not in await response.text()
        response = await client.post("/api/auth/login", headers=HEADERS, data="x" * 5000)
        assert response.status == 400
        for _ in range(6):
            await client.post("/api/auth/login", headers=HEADERS, json={"operator_token": TOKEN_A})
        response = await client.post("/api/auth/login", headers=HEADERS, json={"operator_token": TOKEN_B})
        assert response.status == 429
    assert TOKEN_A not in caplog.text and TOKEN_B not in caplog.text


def test_console_rejects_redirection_and_environment_recovery(tmp_path, monkeypatch, capsys):
    import owner_cli

    monkeypatch.setattr("sys.argv", ["vauxr-owner", "generate-token"])
    with pytest.raises(SystemExit) as exc:
        owner_cli.main()
    assert exc.value.code == 2
    assert "vx_op_" not in capsys.readouterr().out
    service(tmp_path, TOKEN_A)
    monkeypatch.setattr("sys.argv", ["vauxr-owner", "recover"])
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    with pytest.raises(SystemExit):
        owner_cli.main()
    assert "authoritative OPERATOR_TOKEN" in capsys.readouterr().err


def test_login_capacity_after_separate_instance_recovery(tmp_path):
    owner = service(tmp_path)
    token = claim_saved(owner)
    cookies = [owner.login(token)[0] for _ in range(100)]
    old_generation = owner.store.owner["generation"]
    with pytest.raises(OwnerError, match="^rate_limited$"):
        owner.login(token)

    console = OwnerAuth(CredentialStore(owner.store.path))
    code = console.console_claim(recover=True)
    claimed = owner.claim(code)
    owner.acknowledge(claimed["save_acknowledgement"], True)
    assert owner.store.owner["generation"] != old_generation
    # Leave every stale cookie unexamined until after the fresh login.
    assert len(owner.sessions) == 100
    assert all(session.expires > owner_auth.time.time() for session in owner.sessions.values())
    fresh_cookie, fresh_session = owner.login(claimed["operator_token"])
    assert len(owner.sessions) == 1
    assert fresh_session.generation == owner.store.owner["generation"]
    assert owner.session(fresh_cookie)
    assert all(owner.session(cookie) is None for cookie in cookies)
    with pytest.raises(OwnerError, match="^invalid_login$"):
        owner.login(token)


def test_login_capacity_retains_valid_sessions_and_purges_expired(tmp_path, monkeypatch):
    owner = service(tmp_path)
    token = claim_saved(owner)
    now = owner_auth.time.time()
    monkeypatch.setattr(owner_auth.time, "time", lambda: now)
    expired_cookie, expired_session = owner.login(token)
    monkeypatch.setattr(owner_auth.time, "time", lambda: now + 1)
    valid_cookies = [owner.login(token)[0] for _ in range(99)]
    with pytest.raises(OwnerError, match="^rate_limited$"):
        owner.login(token)

    monkeypatch.setattr(owner_auth.time, "time", lambda: expired_session.expires)
    fresh_cookie, _ = owner.login(token)
    assert len(owner.sessions) == 100
    assert owner.session(expired_cookie) is None
    assert all(owner.session(cookie) for cookie in [*valid_cookies, fresh_cookie])
    with pytest.raises(OwnerError, match="^rate_limited$"):
        owner.login(token)


def test_session_expiry_and_console_invalidates_all_sessions(tmp_path, monkeypatch):
    owner = service(tmp_path)
    token = claim_saved(owner)
    first, _ = owner.login(token)
    second, session = owner.login(token)
    owner.logout(first)
    assert owner.session(first) is None
    assert owner.session(second)
    monkeypatch.setattr(owner_auth.time, "time", lambda: session.expires + 1)
    assert owner.session(second) is None
    third, _ = owner.login(token)
    OwnerAuth(CredentialStore(owner.store.path)).console_claim(recover=True)
    assert owner.session(third) is None


@pytest.mark.parametrize("after_rename", [False, True])
def test_recovery_storage_failure_tracks_disk_authority(tmp_path, after_rename):
    owner = service(tmp_path)
    token = claim_saved(owner)
    cookie, _ = owner.login(token)
    original = auth_store.atomic_private_json

    def fail(path, payload):
        if after_rename:
            original(path, payload)
        raise OSError("synthetic failure")

    with patch.object(auth_store, "atomic_private_json", fail), pytest.raises(OSError):
        owner.console_claim(recover=True)
    assert owner.store.owner == CredentialStore(owner.store.path).owner
    assert (owner.session(cookie) is None) == after_rename
    if after_rename:
        with pytest.raises(OwnerError):
            owner.login(token)
    else:
        assert owner.login(token)


async def test_foundation_owner_bearer_never_bypasses_recovery():
    from tests.auth_helpers import seed

    seed("synthetic-old-owner", Role.OWNER, "owner")
    async with TestClient(TestServer(make_http_app())) as client:
        for recover in (False, True):
            client.app[OWNER].console_claim(recover=recover)
            response = await client.get(
                "/api/devices", headers={"Authorization": "Bearer synthetic-old-owner"})
            assert response.status == 401


@pytest.mark.parametrize("role", [Role.DEVICE, Role.INTEGRATION])
async def test_owner_http_session_independent_of_reissued_client(role):
    async with TestClient(TestServer(make_http_app())) as client:
        owner = client.app[OWNER]
        token = claim_saved(owner)
        cookie, _ = owner.login(token)
        headers = {**HEADERS, "Cookie": f"{COOKIE}={cookie}"}
        store = owner.store
        old = Credential("client", role, "speaker", verifier("synthetic-old-client"))
        with store.transaction():
            store.replace((old,))
        stale = store.authenticate("synthetic-old-client")
        assert store.current(stale)
        # Owner policy principals are never current paired-client bearer identities.
        principal, _ = owner.session(cookie)
        assert principal.credential_generation == ""
        assert not store.current(principal)
        assert (await client.get("/api/channels", headers=headers)).status == 200
        with store.transaction():
            store.replace(())
        restarted = CredentialStore(store.path)
        with restarted.transaction():
            restarted.replace((Credential(old.id, role, old.subject, verifier("synthetic-new-client")),))
        # Session lookup reloads the shared schema-2 store without changing its epoch.
        assert (await client.get("/api/channels", headers=headers)).status == 200
        assert not store.current(stale)
        fresh = store.authenticate("synthetic-new-client")
        assert store.current(fresh)
        assert not CredentialStore(store.path).current(stale)
        for bearer, expected in [("synthetic-old-client", 401),
                                 ("synthetic-new-client", 200 if role == Role.INTEGRATION else 403)]:
            response = await client.get("/api/devices", headers={"Authorization": f"Bearer {bearer}"})
            assert response.status == expected
        OwnerAuth(CredentialStore(store.path)).console_claim(recover=True)
        assert (await client.get("/api/channels", headers=headers)).status == 401
        assert store.current(fresh)
        assert not store.current(stale)


async def test_owner_http_previous_environment_token_cannot_revive_session(monkeypatch):
    monkeypatch.setenv("OPERATOR_TOKEN", TOKEN_A)
    async with TestClient(TestServer(make_http_app())) as client:
        owner = client.app[OWNER]
        response = await client.post("/api/auth/login", headers=HEADERS, json={"operator_token": TOKEN_A})
        assert response.status == 200
        headers = {**HEADERS, "Cookie": f"{COOKIE}={response.cookies[COOKIE].value}"}
        assert (await client.get("/api/devices", headers=headers)).status == 200
        # Keep the old session unexamined while the durable authority changes A -> B -> A.
        for token in (TOKEN_B, TOKEN_A):
            OwnerAuth(CredentialStore(owner.store.path), token).initialize()
        assert (await client.get("/api/devices", headers=headers)).status == 401
        assert (await client.get("/api/auth/session", headers=headers)).status == 401
        response = await client.post("/api/auth/login", headers=HEADERS, json={"operator_token": TOKEN_A})
        assert response.status == 200
        fresh = {**HEADERS, "Cookie": f"{COOKIE}={response.cookies[COOKIE].value}"}
        assert (await client.get("/api/devices", headers=fresh)).status == 200


async def test_owner_http_storage_failure_redacts_exception(caplog):
    async with TestClient(TestServer(make_http_app())) as client:
        with patch.object(auth_store, "atomic_private_json", side_effect=OSError(TOKEN_A)):
            response = await client.post("/api/auth/login", headers=HEADERS, json={"operator_token": TOKEN_A})
        assert response.status == 503
        assert TOKEN_A not in await response.text()
        assert TOKEN_A not in caplog.text


def _process_add(path: str, index: int) -> None:
    store = CredentialStore(Path(path))
    with store.transaction():
        store.replace((*store.records, Credential(f"child-{index}", Role.DEVICE, f"child-{index}",
                                                  verifier(f"synthetic-child-{index}"))))


def test_cross_process_transaction_preserves_records(tmp_path):
    from concurrent.futures import ProcessPoolExecutor

    owner = service(tmp_path)
    with ProcessPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_process_add, str(owner.store.path), index) for index in range(8)]
        owner.console_claim(recover=True)
        for future in futures:
            future.result(timeout=10)
    disk = CredentialStore(owner.store.path)
    assert len(disk.records) == 8
    assert disk.owner["mode"] == "recovery"


@pytest.mark.parametrize("bad_owner", [[], {"version": 99},
    {"version": 1, "mode": "generated", "generation": "0" * 32, "verifier": "bad"},
    {"version": 1, "mode": "recovery", "generation": "0" * 32, "pending": []},
    {"version": 1, "mode": "unclaimed", "generation": "0" * 32, "attempts": [float("nan")]},
])
def test_corrupt_owner_metadata_clears_all_loaded_grants(tmp_path, bad_owner):
    owner = service(tmp_path)
    with owner.store.transaction():
        owner.store.replace((Credential("device", Role.DEVICE, "speaker", verifier("synthetic-device")),))
    payload = json.loads(owner.store.path.read_text())
    payload["owner"] = bad_owner
    owner.store.path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="Invalid credential store"):
        owner.store.load()
    assert owner.store.authenticate("synthetic-device") is None
    assert owner.store.owner == {}


async def test_route_attachment_cannot_omit_owner_boundary():
    from aiohttp import web

    from http_server import attach_http_routes

    app = web.Application()
    attach_http_routes(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/auth/status")
        assert response.status == 403


async def test_fragmented_json_and_head_status():
    import asyncio

    async with TestClient(TestServer(make_http_app())) as client:
        code = client.app[OWNER].console_claim()

        async def fragments():
            yield b'{"code":'
            await asyncio.sleep(0.01)
            yield json.dumps(code).encode() + b'}'

        response = await client.post(
            "/api/auth/claim", headers={**HEADERS, "Content-Type": "application/json"}, data=fragments())
        assert response.status == 200
        before = dict(client.app[OWNER].store.owner)
        assert (await client.head("/api/auth/status", headers=HEADERS)).status == 200
        assert client.app[OWNER].store.owner == before


def test_nested_shared_transaction_and_owner_write_guard(tmp_path):
    owner = service(tmp_path)
    with pytest.raises(RuntimeError, match="transaction"):
        owner.store.save_owner(dict(owner.store.owner))
    with owner.store.transaction():
        owner.console_claim(recover=True)
        with owner.store.transaction():
            assert owner.store.owner["mode"] == "recovery"
    assert CredentialStore(owner.store.path).owner["mode"] == "recovery"
