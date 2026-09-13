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
    for name in ("OWNER_ORIGIN", "OWNER_HTTPS_ORIGIN", "OWNER_TRUSTED_PROXIES", "OPERATOR_TOKEN"):
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
    monkeypatch.setenv("OWNER_ORIGIN", origin)
    assert configured_origin() == origin


def test_legacy_https_setting_cannot_downgrade_or_conflict(monkeypatch):
    monkeypatch.setenv("OWNER_HTTPS_ORIGIN", "http://192.168.1.20:8080")
    with pytest.raises(ValueError, match="requires HTTPS"):
        configured_origin()
    monkeypatch.setenv("OWNER_HTTPS_ORIGIN", "https://voice.example")
    monkeypatch.setenv("OWNER_ORIGIN", "http://192.168.1.20:8080")
    with pytest.raises(ValueError, match="conflicts"):
        configured_origin()


def test_lan_console_claim_requires_explicit_local_participation(monkeypatch, capsys):
    import owner_cli

    monkeypatch.setenv("OWNER_ORIGIN", "http://192.168.1.20:8080")
    monkeypatch.setattr("sys.argv", ["vauxr-owner", "claim"])
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    owner_cli.main()
    output = capsys.readouterr().out
    assert "http://192.168.1.20:8080" in output and "unencrypted" in output
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
    monkeypatch.setenv("OWNER_ORIGIN", origin)
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
    monkeypatch.setenv("OWNER_ORIGIN", f"https://127.0.0.1:{unused_tcp_port}")
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
