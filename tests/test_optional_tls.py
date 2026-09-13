"""LAN lifecycle and real TLS handshake regressions; no verification bypasses."""

import ipaddress
import json
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from aiohttp import ClientConnectorCertificateError, ClientSession, CookieJar, WSMsgType
from aiohttp.test_utils import TestClient, TestServer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import NameOID

import auth
import config
from http_server import make_http_app
from owner_auth import configured_origin
from owner_http import COOKIE, LAN_COOKIE, ORIGIN, OWNER
from server import make_app
from tests.test_enrollment import signed


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    for name in ("OWNER_HTTP_ORIGIN", "OWNER_HTTPS_ORIGIN", "OWNER_TRUSTED_PROXIES", "OPERATOR_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("REALTIME_ENABLED", "0")
    config.reset_config()
    auth._store = None
    yield
    config.reset_config()
    auth._store = None


@pytest.mark.parametrize("origin", ["http://192.168.1.20:8080", "http://[fd00::1]:8080",
                                    "https://voice.example"])
def test_explicit_origin_configuration(monkeypatch, origin):
    monkeypatch.setenv("OWNER_HTTPS_ORIGIN" if origin.startswith("https:") else "OWNER_HTTP_ORIGIN", origin)
    assert configured_origin() == origin


def test_explicit_https_takes_precedence_and_cannot_downgrade(monkeypatch):
    monkeypatch.setenv("OWNER_HTTPS_ORIGIN", "http://192.168.1.20:8080")
    with pytest.raises(ValueError, match="canonical HTTPS"):
        configured_origin()
    monkeypatch.setenv("OWNER_HTTPS_ORIGIN", "https://voice.example")
    monkeypatch.setenv("OWNER_HTTP_ORIGIN", "http://192.168.1.20:8080")
    assert configured_origin() == "https://voice.example"


def test_lan_console_claim_requires_explicit_local_participation(monkeypatch, capsys):
    import owner_cli

    monkeypatch.setenv("OWNER_HTTP_ORIGIN", "http://192.168.1.20:8080")
    monkeypatch.setattr("sys.argv", ["vauxr-owner", "claim"])
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    owner_cli.main()
    output = capsys.readouterr().out
    assert "http://192.168.1.20:8080" in output and "exposes credentials" in output
    code = output.splitlines()[-1]
    from owner_auth import OwnerAuth

    assert OwnerAuth(auth.get_store()).claim(code)["save_required"]


async def test_lan_owner_enrollment_ws_rotation_revocation_recovery(monkeypatch):
    # Configure the actual bound origin before startup; CookieJar's unsafe flag
    # permits IP-host cookies in this test client, not TLS verification bypass.
    server = TestServer(make_http_app())
    await server.start_server()
    origin = str(server.make_url("")).rstrip("/")
    # Recreate app with exact origin, reusing the now-known listener port.
    port = server.port
    await server.close()
    monkeypatch.setenv("OWNER_HTTPS_ORIGIN" if origin.startswith("https:") else "OWNER_HTTP_ORIGIN", origin)
    async with TestClient(TestServer(make_http_app(), port=port), cookie_jar=CookieJar(unsafe=True)) as http:
        assert http.app[ORIGIN] == origin
        headers = {"Origin": origin}

        async def post(path, body, *, owner=False, token=None, status=200):
            request_headers = dict(headers)
            if owner:
                request_headers["X-CSRF-Token"] = csrf
            if token:
                request_headers["Authorization"] = "Bearer " + token
            # Native/device requests omit the browser owner cookie.
            async with ClientSession() as native:
                caller = http if owner or path.startswith("/api/auth/") else native
                url = path if caller is http else str(http.make_url(path))
                response = await caller.post(url, headers=request_headers, json=body)
                result = await response.json()
                assert response.status == status, result
                assert "Access-Control-Allow-Origin" not in response.headers
                return result

        code = http.app[OWNER].console_claim()
        claim = await post("/api/auth/claim", {"code": code})
        await post("/api/auth/save", {"saved": True, "save_acknowledgement": claim["save_acknowledgement"]})
        login = await http.post("/api/auth/login", headers=headers,
                                json={"operator_token": claim["operator_token"]})
        csrf = (await login.json())["csrf_token"]
        cookie = login.cookies[LAN_COOKIE]
        assert not cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Strict"
        assert not cookie["domain"] and COOKIE not in login.cookies
        response = await http.get("/api/devices", headers=headers)
        assert response.status == 200 and "Access-Control-Allow-Origin" not in response.headers
        for extra in ({"Origin": "http://evil.invalid"}, {"Host": "evil.invalid"},
                      {"X-Forwarded-Proto": "https"}, {"Forwarded": "proto=http"}):
            response = await http.get("/api/auth/session", headers={**headers, **extra})
            assert response.status == 403

        key = ed25519.Ed25519PrivateKey.generate()
        enrollment = "/api/enrollment/v1/"
        lifecycle = "/api/lifecycle/v1/"

        async def enroll():
            row = await post(enrollment + "request", {"kind": "physical", "display_name": "Speaker",
                             "public_key": key.public_key().public_bytes_raw().hex()})
            assert row["origin"] == origin
            proof = await post(enrollment + "prove", signed(key, row, "prove"))
            await post(enrollment + "redeem", signed(key, row, "redeem"), status=409)
            confirmation = {"request_id": row["request_id"], "code": proof["code"]}
            await post(enrollment + "initiate", confirmation, owner=True)
            await post(enrollment + "approve", confirmation, owner=True)
            return await post(enrollment + "redeem", signed(key, row, "redeem"))

        device = await enroll()
        token = device["device_token"]
        body = {"operation_id": "1" * 32, "role": "device", "subject": device["device_id"]}
        response = await http.post(lifecycle + "rotate", headers=headers, json=body)
        assert response.status == 403  # Cookie alone cannot mutate.
        await post(lifecycle + "rotate", body, token=token, status=403)
        async with TestClient(TestServer(make_app())) as voice, voice.ws_connect("/ws") as ws:
            await ws.send_json({"type": "hello", "device_id": device["device_id"], "token": token})
            assert (await ws.receive_json(timeout=2))["type"] == "hello"
            await post(lifecycle + "rotate", body, owner=True)
            await post(lifecycle + "poll", {}, token=token)
            delivery = await post(lifecycle + "deliver", {"operation_id": "1" * 32}, token=token)
            replacement = delivery["credential"]
            await post(lifecycle + "ack", {"operation_id": "1" * 32, "saved": True}, token=replacement)
            assert (await ws.receive(timeout=2)).type == WSMsgType.CLOSE
        await post(lifecycle + "poll", {}, token=token, status=401)
        await post(lifecycle + "revoke", {**body, "operation_id": "2" * 32}, owner=True)
        await post(lifecycle + "poll", {}, token=replacement, status=401)
        await post(lifecycle + "recover", {**body, "operation_id": "3" * 32}, owner=True)
        recovered = await enroll()
        assert recovered["device_id"] == device["device_id"]
        await post(lifecycle + "ack", {"operation_id": "3" * 32, "saved": True},
                   token=recovered["device_token"])
        await post("/api/auth/logout", {}, owner=True)
        assert (await http.get("/api/auth/session", headers=headers)).status == 401


def tls_contexts(tmp_path, case):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic test")])
    now = datetime.now(UTC)
    address = "127.0.0.2" if case == "hostname" else "127.0.0.1"
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=2))
            .not_valid_after(now + timedelta(days=-1 if case == "expired" else 1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(address))]), False)
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM)
    (tmp_path / "cert.pem").write_bytes(pem)
    (tmp_path / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                     serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")
    client = ssl.create_default_context()
    if case != "untrusted":
        client.load_verify_locations(cadata=pem.decode())
    assert client.check_hostname and client.verify_mode == ssl.CERT_REQUIRED
    return server, client


@pytest.mark.parametrize("case", ["valid", "untrusted", "hostname", "expired"])
@pytest.mark.parametrize("transport", ["https", "wss"])
async def test_optional_tls_requires_valid_trusted_certificate(
        tmp_path, monkeypatch, unused_tcp_port, case, transport):
    server_context, client_context = tls_contexts(tmp_path, case)
    # Use actual project listeners. Failed TLS never reaches HTTP/WS handlers.
    monkeypatch.setenv("OWNER_HTTPS_ORIGIN", f"https://127.0.0.1:{unused_tcp_port}")
    app = make_http_app() if transport == "https" else make_app()
    server = TestServer(app, scheme="https", port=unused_tcp_port)
    await server.start_server(ssl=server_context)
    try:
        async with ClientSession() as client:
            async def connect():
                if transport == "wss":
                    async with client.ws_connect(str(server.make_url("/ws")).replace("https:", "wss:"),
                                                 ssl=client_context) as ws:
                        await ws.send_json({"type": "hello", "device_id": "speaker", "token": "invalid"})
                        assert (await ws.receive_json(timeout=2))["code"] == "UNAUTHORIZED"
                else:
                    async with client.get(server.make_url("/api/auth/status"), ssl=client_context) as response:
                        assert response.status == 200
            if case == "valid":
                await connect()
            else:
                with pytest.raises(ClientConnectorCertificateError):
                    await connect()
    finally:
        await server.close()


def test_lifecycle_fixture_transport_policy():
    fixture = json.loads((Path(__file__).parent / "fixtures/lifecycle-v1.json").read_text())
    policy = fixture["transport_policy"]
    assert policy["default"] == "http/ws" and policy["optional"] == "https/wss"
    assert policy["tls_failure_fallback"] is False
    assert set(policy["https_certificate_requirements"]) == {
        "trusted_chain", "hostname_or_ip_san", "validity", "trusted_time"}


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("transition", ["origin", "scheme", "owner_mode"])
@pytest.mark.parametrize("phase", ["queued", "pending", "delivered", "recovery"])
async def test_restart_transition_permanently_invalidates_pending_work(monkeypatch, scheme, transition, phase):
    from auth_policy import Role
    from auth_store import Credential, verifier
    from enrollment import EnrollmentError, transcript
    from enrollment_http import ENROLLMENT
    from lifecycle_http import LIFECYCLE
    from tests.test_enrollment import approve, owner_resolver, ready, request

    first = f"{scheme}://owner.example"
    second = (f"{scheme}://other.example" if transition == "origin" else
              f"{'https' if scheme == 'http' else 'http'}://owner.example")
    if transition == "owner_mode":
        second = first
    old_cookie = old_csrf = token = replacement = None
    for index, origin in enumerate((first, second, first)):
        tls = origin.startswith("https:")
        for name in ("OWNER_HTTP_ORIGIN", "OWNER_HTTPS_ORIGIN", "OWNER_TRUSTED_PROXIES", "OPERATOR_TOKEN"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("OWNER_HTTPS_ORIGIN" if tls else "OWNER_HTTP_ORIGIN", origin)
        if tls:
            monkeypatch.setenv("OWNER_TRUSTED_PROXIES", "127.0.0.1/32")
        if transition == "owner_mode" and index == 1:
            monkeypatch.setenv("OPERATOR_TOKEN", "vx_op_" + "E" * 43)
        headers = {"Host": origin.split("://")[1], "Origin": origin}
        if tls:
            headers["X-Forwarded-Proto"] = "https"
        name = COOKIE if tls else LAN_COOKIE
        async with TestClient(TestServer(make_http_app())) as http:
            owner = http.app[OWNER]
            enroll = http.app[ENROLLMENT]
            service = http.app[LIFECYCLE]
            if index == 0:
                claim = owner.claim(owner.console_claim())
                token = claim["operator_token"]
                owner.acknowledge(claim["save_acknowledgement"], True)
                old_cookie, session = owner.login(token)
                old_csrf = session.csrf
                resolve = owner_resolver(owner, old_cookie)
                with service.store.transaction():
                    service.store.replace((Credential("d", Role.DEVICE, "speaker", verifier("old-device")),))
                # Preserve an approved signed enrollment as well as a lifecycle operation.
                key, row, code = ready(enroll)
                approve(enroll, row, code, resolve)
                subject = "speaker"
                if phase == "recovery":
                    enroll.execute("redeem", signed(key, row, "redeem"))
                    subject = row["device_id"]
                body = {"operation_id": "1" * 32, "role": "device", "subject": subject}
                service.execute("recover" if phase == "recovery" else "rotate", body, resolve)
                if phase == "recovery":
                    _, row = request(enroll, resolve, key=key)
                    code = enroll.execute("prove", signed(key, row, "prove"))["code"]
                    approve(enroll, row, code, resolve)
                elif phase != "queued":
                    device = lambda service=service: service.store.authenticate("old-device")
                    service.execute("poll", {}, device)
                    if phase == "delivered":
                        replacement = service.execute("deliver", {"operation_id": "1" * 32}, device)["credential"]
                assert row["origin"] == origin
                assert transcript(row, "redeem") != transcript({**row, "origin": "https://elsewhere"}, "redeem")
                generation = service.store.owner["generation"]
                continue
            # No old artifact is queried during the intermediate configuration.
            # Startup itself must persist invalidation, including a return to A.
            assert service.store.lifecycle["operations"]["1" * 32]["state"] == "expired"
            assert service.store.enrollment["requests"][row["request_id"]]["state"] == "stale"
            if replacement:
                assert service.store.authenticate(replacement) is None
                assert service.store.authenticate("old-device") is None
            else:
                assert service.store.authenticate("old-device") is not None
            with pytest.raises(EnrollmentError):
                enroll.execute("redeem", signed(key, row, "redeem"))
            stale = {**headers, "Cookie": f"{name}={old_cookie}", "X-CSRF-Token": old_csrf}
            assert (await http.get("/api/devices", headers=stale)).status == 401
            assert (await http.post("/api/lifecycle/v1/rotate", headers=stale, json=body)).status == 403
            assert (await http.post("/api/enrollment/v1/list", headers=stale, json={})).status == 403
            if transition != "owner_mode":
                assert service.store.owner["generation"] == generation
                # Transport transitions retain the operator token but require a fresh session.
                fresh_cookie, fresh_session = owner.login(token)
                fresh = {**headers, "Cookie": f"{name}={fresh_cookie}", "X-CSRF-Token": fresh_session.csrf}
                response = await http.post("/api/lifecycle/v1/status", headers=fresh,
                                           json={"operation_id": "1" * 32})
                assert response.status == 200 and (await response.json())["state"] == "expired"
            else:
                assert service.store.owner["generation"] != generation
                assert service.store.owner["mode"] == ("environment" if index == 1 else "recovery")


@pytest.mark.parametrize("tls", [False, True])
@pytest.mark.parametrize("api", ["enrollment", "lifecycle"])
@pytest.mark.parametrize("invalidate", ["logout", "expiry", "recovery", "origin_roundtrip"])
async def test_owner_permission_rechecked_after_middleware(monkeypatch, tls, api, invalidate):
    import enrollment_http
    import lifecycle_http
    from auth_policy import Role
    from auth_store import Credential, verifier

    origin = ("https" if tls else "http") + "://owner.example"
    monkeypatch.setenv("OWNER_HTTPS_ORIGIN" if tls else "OWNER_HTTP_ORIGIN", origin)
    headers = {"Host": "owner.example", "Origin": origin}
    if tls:
        monkeypatch.setenv("OWNER_TRUSTED_PROXIES", "127.0.0.1/32")
        headers["X-Forwarded-Proto"] = "https"
    module = enrollment_http if api == "enrollment" else lifecycle_http
    async with TestClient(TestServer(make_http_app())) as http:
        owner = http.app[OWNER]
        claim = owner.claim(owner.console_claim())
        owner.acknowledge(claim["save_acknowledgement"], True)
        cookie, session = owner.login(claim["operator_token"])
        headers.update({"Cookie": f"{COOKIE if tls else LAN_COOKIE}={cookie}", "X-CSRF-Token": session.csrf})
        with owner.store.transaction():
            owner.store.replace((Credential("d", Role.DEVICE, "speaker", verifier("old-device")),))
        service = http.app[enrollment_http.ENROLLMENT if api == "enrollment" else lifecycle_http.LIFECYCLE]
        execute = service.execute
        resolve_owner = module.session_principal
        checks = []

        def recheck(request):
            # The authoritative session read must be inside execute's transaction.
            assert owner.store._transaction_active
            checks.append(True)
            return resolve_owner(request)

        def revoke_before_execute(action, body, resolve):
            # Simulate invalidation while the handler awaited the request body,
            # after middleware already accepted the cookie and CSRF token.
            if invalidate == "logout":
                owner.logout(cookie)
            elif invalidate == "expiry":
                session.expires = 0
            elif invalidate == "recovery":
                owner.console_claim(recover=True)
            else:
                owner.bind_origin(origin.replace("owner.example", "other.example"))
                owner.bind_origin(origin)
            return execute(action, body, resolve)

        monkeypatch.setattr(module, "session_principal", recheck)
        monkeypatch.setattr(service, "execute", revoke_before_execute)
        action = "request" if api == "enrollment" else "rotate"
        body = ({"kind": "browser", "display_name": "Browser",
                 "public_key": ed25519.Ed25519PrivateKey.generate().public_key().public_bytes_raw().hex()}
                if api == "enrollment" else {"operation_id": "1" * 32, "role": "device", "subject": "speaker"})
        response = await http.post(f"/api/{api}/v1/{action}", headers=headers, json=body)
        assert response.status == 401
        assert await response.json() == {"error": "unauthorized"}
        assert checks == [True]
        assert not owner.store.enrollment.get("requests")
        assert not owner.store.lifecycle.get("operations")
