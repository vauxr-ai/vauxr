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
from owner_http import COOKIE, LAN_COOKIE, OWNER

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
    monkeypatch.delenv("OWNER_HTTP_ORIGIN", raising=False)
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
    assert not service(tmp_path).session(cookie)  # process-local sessions intentionally do not survive restart
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


@pytest.mark.parametrize("origin", ["ws://owner.example", "https://owner.example/", "https://x@y",
                                    "https://owner.example?q=x", "https://owner.example#x", "https://x:bad"])
def test_origin_configuration_rejected(origin):
    with pytest.raises(ValueError):
        trusted_origin(origin)


@pytest.mark.parametrize("tls", [False, True])
async def test_http_complete_session_csrf_and_logout(monkeypatch, tls):
    # Non-loopback public authority over a local test transport; not browser acceptance.
    origin = ORIGIN if tls else "http://192.168.10.20:8080"
    base_headers = HEADERS if tls else {"Host": "192.168.10.20:8080", "Origin": origin}
    name = COOKIE if tls else LAN_COOKIE
    if not tls:
        monkeypatch.delenv("OWNER_HTTPS_ORIGIN")
        monkeypatch.delenv("OWNER_TRUSTED_PROXIES")
        monkeypatch.setenv("OWNER_HTTP_ORIGIN", origin)
    async with TestClient(TestServer(make_http_app())) as client:
        service = client.app[OWNER]
        code = service.console_claim()
        response = await client.post("/api/auth/claim", headers=base_headers, json={"code": code})
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "Access-Control-Allow-Origin" not in response.headers
        result = await response.json()
        token = result["operator_token"]
        assert (await client.post("/api/auth/login", headers=base_headers,
                                  json={"operator_token": token})).status == 400
        assert (await client.post("/api/auth/save", headers=base_headers,
                                  json={"saved": True, "save_acknowledgement": result["save_acknowledgement"]}
                                  )).status == 200
        response = await client.post("/api/auth/login", headers=base_headers, json={"operator_token": token})
        body = await response.json()
        cookie = response.cookies[name]
        assert bool(cookie["secure"]) == tls
        assert cookie["httponly"] and cookie["samesite"] == "Strict"
        assert cookie["path"] == "/" and not cookie["domain"] and int(cookie["max-age"]) == 43200
        headers = {**base_headers, "Cookie": f"{name}={cookie.value}"}
        assert (await client.get("/api/auth/session", headers=headers)).status == 200
        assert (await client.get("/api/devices", headers=headers)).status == 200
        assert (await client.post("/api/auth/logout", headers=headers, json={})).status == 403
        assert (await client.patch("/api/devices/missing", headers=headers, json={})).status == 403
        headers["X-CSRF-Token"] = body["csrf_token"]
        assert (await client.patch("/api/devices/missing", headers=headers, json={})).status == 404
        response = await client.post("/api/auth/logout", headers=headers, json={})
        assert response.status == 200
        cleared = response.cookies[name]
        assert cleared["max-age"] == "0" and cleared["path"] == "/" and not cleared["domain"]
        assert bool(cleared["secure"]) == tls and cleared["httponly"] and cleared["samesite"] == "Strict"
        assert (await client.get("/api/auth/session", headers=headers)).status == 401
        assert (await client.get("/api/devices", headers=headers)).status == 401
        # Everyday token is never a transport/admin bearer substitute.
        assert (await client.get("/api/devices", headers={**base_headers, "Authorization": f"Bearer {token}"})
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
    for origin, proxies in [(ORIGIN, "192.0.2.1/32"), (ORIGIN, None)]:
        monkeypatch.setenv("OWNER_HTTPS_ORIGIN", origin)
        if proxies is None:
            monkeypatch.delenv("OWNER_TRUSTED_PROXIES", raising=False)
        else:
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
            response = await client.get("/api/devices", headers={"Authorization": "Bearer synthetic-old-owner"})
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
            response = await client.get("/api/devices", headers={**HEADERS, "Authorization": f"Bearer {bearer}"})
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

        response = await client.post("/api/auth/claim", headers={**HEADERS, "Content-Type": "application/json"},
                                     data=fragments())
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


@pytest.mark.parametrize("value", ["http://LAN:8080", "http://lan:", "http://lan:08080",
    "http://lan:0", "http://lan:65536", "http://lan/", "http://lan?", "http://lan#",
    "http://user@lan", "http://lan\\evil", "http://lan.", "http://a..b", "http://a b",
    "http://lan\n", "http://[fe80::1%eth0]", "http://-lan", "http://lan_"])
def test_lan_origin_must_be_canonical(value):
    with pytest.raises(ValueError):
        trusted_origin(value, "http")


@pytest.mark.parametrize("scheme,default_port", [("http", 80), ("https", 443)])
@pytest.mark.parametrize("authority", [
    "voice.lan:{default_port}", "127.0.0.1:{default_port}", "[::1]:{default_port}",
    "127.1:8080", "127.0.1:8080", "2130706433:8080", "0177.0.0.1:8080",
    "127.000.0.1:8080", "0x7f000001:8080", "0x7f.0.0.1:8080", "127.0.0.0x1:8080",
    "voice.123:8080", "voice.0xff:8080", "voice.0x:8080",
])
def test_browser_noncanonical_origin_rejected_before_startup_or_console_claim(
        monkeypatch, tmp_path, capsys, scheme, default_port, authority):
    import owner_cli

    origin = f"{scheme}://{authority.format(default_port=default_port)}"
    # A valid LAN fallback must not rescue invalid TLS configuration.
    monkeypatch.setenv("OWNER_HTTP_ORIGIN", "http://localhost:8080")
    if scheme == "http":
        monkeypatch.delenv("OWNER_HTTPS_ORIGIN")
        monkeypatch.delenv("OWNER_TRUSTED_PROXIES")
    monkeypatch.setenv(f"OWNER_{scheme.upper()}_ORIGIN", origin)
    with pytest.raises(ValueError, match="exact canonical"):
        make_http_app()
    with pytest.raises(ValueError, match="exact canonical"):
        owner_auth.configured_origin()
    monkeypatch.setattr("sys.argv", ["vauxr-owner", "claim"])
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    with pytest.raises(SystemExit) as exc:
        owner_cli.main()
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "exact canonical" in output.err
    assert not (tmp_path / "authz.json").exists()


@pytest.mark.parametrize("scheme,default_port", [("http", 80), ("https", 443)])
@pytest.mark.parametrize("authority,alternate", [
    ("voice.lan", "voice.lan:{default_port}"),
    ("127.0.0.1", "127.0.0.1:{default_port}"),
    ("[::1]", "[::1]:{default_port}"),
    ("127.0.0.1:8080", "127.1:8080"),
    ("127.0.0.1:8080", "2130706433:8080"),
    ("127.0.0.1:8080", "0177.0.0.1:8080"),
    ("127.0.0.1:8080", "0x7f000001:8080"),
    ("voice.lan:443", "voice.lan:80"),
])
async def test_canonical_origin_accepts_exact_requests_only(
        monkeypatch, scheme, default_port, authority, alternate):
    # Keep a non-default port valid in each mode, even if it is the other's default.
    if authority == "voice.lan:443" and scheme == "https":
        authority, alternate = alternate, authority
    alternate = alternate.format(default_port=default_port)
    origin = f"{scheme}://{authority}"
    if scheme == "http":
        monkeypatch.delenv("OWNER_HTTPS_ORIGIN")
        monkeypatch.delenv("OWNER_TRUSTED_PROXIES")
    monkeypatch.setenv(f"OWNER_{scheme.upper()}_ORIGIN", origin)
    assert owner_auth.configured_origin() == origin
    headers = {"Host": authority, "Origin": origin}
    if scheme == "https":
        headers["X-Forwarded-Proto"] = "https"
    async with TestClient(TestServer(make_http_app())) as client:
        owner = client.app[OWNER]
        code = owner.console_claim()
        before = dict(owner.store.owner)
        for changes in ({"Host": alternate}, {"Origin": f"{scheme}://{alternate}"}):
            response = await client.post("/api/auth/claim", headers={**headers, **changes},
                                         json={"code": code})
            assert response.status == 403
            assert owner.store.owner == before
        response = await client.post("/api/auth/claim", headers=headers, json={"code": code})
        assert response.status == 200
        assert (await response.json())["save_required"] is True


@pytest.mark.parametrize("https,proxies", [("", None), ("http://lan:8080", None),
    ("https://lan/", None), (None, "127.0.0.1/32"), (ORIGIN, ""),
    (ORIGIN, "bogus"), (ORIGIN, "127.0.0.1/32,"), (None, "")])
def test_tls_configuration_errors_never_select_lan(monkeypatch, https, proxies):
    monkeypatch.setenv("OWNER_HTTP_ORIGIN", "http://192.168.10.20:8080")
    for key, value in [("OWNER_HTTPS_ORIGIN", https), ("OWNER_TRUSTED_PROXIES", proxies)]:
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        make_http_app()
    with pytest.raises(ValueError):
        owner_auth.configured_origin()  # Console uses exactly the same selection.


@pytest.mark.parametrize("changes", [{"Host": "evil.example"}, {"Origin": "http://evil.example"},
    {"Origin": None}, {"Origin": "null"}, {"X-Forwarded-Proto": "https"},
    {"X-Forwarded-Proto": "http"}, {"Forwarded": "proto=http"},
    {"Host": "evil.example", "X-Forwarded-Host": "192.168.10.20:8080"}])
async def test_lan_spoofed_boundary_does_not_consume_claim(monkeypatch, changes):
    monkeypatch.delenv("OWNER_HTTPS_ORIGIN")
    monkeypatch.delenv("OWNER_TRUSTED_PROXIES")
    monkeypatch.setenv("OWNER_HTTP_ORIGIN", "http://192.168.10.20:8080")
    headers = {"Host": "192.168.10.20:8080", "Origin": "http://192.168.10.20:8080"}
    headers = {key: value for key, value in {**headers, **changes}.items() if value is not None}
    async with TestClient(TestServer(make_http_app())) as client:
        owner = client.app[OWNER]
        code = owner.console_claim()
        assert (await client.post("/api/auth/claim", headers=headers, json={"code": code})).status == 403
        assert owner.claim(code)["save_required"]


async def test_fixed_default_origin_cannot_be_selected_by_host(monkeypatch):
    monkeypatch.delenv("OWNER_HTTPS_ORIGIN")
    monkeypatch.delenv("OWNER_TRUSTED_PROXIES")
    assert owner_auth.configured_origin() == "http://localhost:8080"
    async with TestClient(TestServer(make_http_app())) as client:
        assert (await client.get("/api/auth/status", headers={"Host": "localhost:8080"})).status == 200
        assert (await client.get("/api/auth/status", headers={"Host": "192.168.10.20:8080"})).status == 403


@pytest.mark.parametrize("tls", [False, True])
async def test_opposite_mode_cookie_rejected_even_with_valid_session_value(monkeypatch, tls):
    headers = HEADERS.copy()
    wrong_name = LAN_COOKIE
    if not tls:
        monkeypatch.delenv("OWNER_HTTPS_ORIGIN")
        monkeypatch.delenv("OWNER_TRUSTED_PROXIES")
        monkeypatch.setenv("OWNER_HTTP_ORIGIN", "http://owner.example")
        headers = {"Host": "owner.example", "Origin": "http://owner.example"}
        wrong_name = COOKIE
    async with TestClient(TestServer(make_http_app())) as client:
        owner = client.app[OWNER]
        cookie, _ = owner.login(claim_saved(owner))
        headers["Cookie"] = f"{wrong_name}={cookie}"
        for path in ("/api/auth/session", "/api/devices"):
            assert (await client.get(path, headers=headers)).status == 403


@pytest.mark.parametrize("next_origin", ["http://other.example", "https://owner.example"])
def test_origin_transition_discards_sessions_and_capacity_without_changing_owner(tmp_path, next_origin):
    owner = service(tmp_path)
    owner.bind_origin("http://owner.example")
    token = claim_saved(owner)
    cookies = [owner.login(token)[0] for _ in range(100)]
    state = owner.store.owner.copy()
    owner.bind_origin(next_origin)
    # Leave old cookies unexamined until after A -> B -> A.
    owner.bind_origin("http://owner.example")
    assert owner.session(owner.login(token)[0])
    assert all(owner.session(cookie) is None for cookie in cookies)
    assert owner.store.owner == state


@pytest.mark.parametrize("origins", [
    ["http://owner.example", "https://owner.example", "http://owner.example"],
    ["https://owner.example", "http://owner.example", "https://owner.example"],
    ["http://owner.example", "http://other.example", "http://owner.example"],
    ["https://owner.example", "https://other.example", "https://owner.example"],
])
async def test_restart_origin_transitions_require_fresh_login(monkeypatch, origins):
    old_cookie = None
    token = None
    generation = None
    for origin in origins:
        tls = origin.startswith("https://")
        for key in ("OWNER_HTTPS_ORIGIN", "OWNER_TRUSTED_PROXIES", "OWNER_HTTP_ORIGIN"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("OWNER_HTTPS_ORIGIN" if tls else "OWNER_HTTP_ORIGIN", origin)
        headers = {"Host": origin.split("://")[1], "Origin": origin}
        if tls:
            monkeypatch.setenv("OWNER_TRUSTED_PROXIES", "127.0.0.1/32")
            headers["X-Forwarded-Proto"] = "https"
        name = COOKIE if tls else LAN_COOKIE
        async with TestClient(TestServer(make_http_app())) as client:
            owner = client.app[OWNER]
            if token is None:
                token = claim_saved(owner)
                generation = owner.store.owner["generation"]
            else:
                # Even copying the old secret into the destination cookie name fails.
                stale = {**headers, "Cookie": f"{name}={old_cookie}"}
                assert (await client.get("/api/auth/session", headers=stale)).status == 401
                assert (await client.get("/api/devices", headers=stale)).status == 401
            response = await client.post("/api/auth/login", headers=headers, json={"operator_token": token})
            assert response.status == 200
            fresh = {**headers, "Cookie": f"{name}={response.cookies[name].value}"}
            assert (await client.get("/api/devices", headers=fresh)).status == 200
            assert owner.store.owner["generation"] == generation
            if old_cookie is None:
                old_cookie = response.cookies[name].value


@pytest.mark.parametrize("tls", [False, True])
@pytest.mark.parametrize("header", ["Origin", "X-Forwarded-Proto"])
async def test_duplicate_boundary_headers_fail_closed(monkeypatch, tls, header):
    headers = HEADERS.copy()
    if not tls:
        monkeypatch.delenv("OWNER_HTTPS_ORIGIN")
        monkeypatch.delenv("OWNER_TRUSTED_PROXIES")
        monkeypatch.setenv("OWNER_HTTP_ORIGIN", "http://owner.example")
        headers = {"Host": "owner.example", "Origin": "http://owner.example"}
    pairs = list(headers.items()) + [(header, headers.get(header, "http"))]
    async with TestClient(TestServer(make_http_app())) as client:
        assert (await client.post("/api/auth/login", headers=pairs,
                                  json={"operator_token": TOKEN_A})).status == 403


@pytest.mark.parametrize("tls", [False, True])
def test_direct_tls_boundary_retained_and_never_accepted_as_lan(tls):
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from owner_http import ORIGIN as ORIGIN_KEY
    from owner_http import PROXIES, secure_request

    app = web.Application()
    app[ORIGIN_KEY] = "https://owner.example" if tls else "http://owner.example"
    app[PROXIES] = ()
    for extra, expected in [({}, tls), ({"X-Forwarded-Proto": "https"}, False),
                            ({"Forwarded": "proto=https"}, False)]:
        request = make_mocked_request("GET", "https://owner.example/api/auth/status",
                                      headers={"Host": "owner.example", **extra}, app=app)
        assert secure_request(request) == expected
