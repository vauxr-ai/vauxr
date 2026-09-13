#!/usr/bin/env python3
"""In-memory TLS trust prototype for Vauxr issue #45.

This is deliberately not production gateway code.  It uses Python's maintained
``ssl`` binding to OpenSSL and MemoryBIOs, so no TCP port is opened.  It proves
the ordering requirement: an application credential is sent only after a
hostname-validated TLS handshake completes.
"""

from __future__ import annotations

import ssl
import subprocess
import tempfile
import unittest
from pathlib import Path


def run(*args: str, cwd: Path) -> None:
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True, text=True)


def issue_ca(directory: Path, name: str) -> tuple[Path, Path]:
    key, cert = directory / f"{name}.key", directory / f"{name}.crt"
    run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "3650",
        "-subj", f"/CN={name}", "-keyout", str(key), "-out", str(cert), cwd=directory)
    return key, cert


def issue_server(directory: Path, ca_key: Path, ca_cert: Path, hostname: str, *, expired: bool = False) -> tuple[Path, Path]:
    key, csr, cert = directory / f"{hostname}.key", directory / f"{hostname}.csr", directory / f"{hostname}.crt"
    run("req", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={hostname}", "-keyout", str(key), "-out", str(csr), cwd=directory)
    ext = directory / f"{hostname}.ext"
    ext.write_text(f"subjectAltName=DNS:{hostname}\nextendedKeyUsage=serverAuth\n", encoding="utf-8")
    args = [
        "x509", "-req", "-in", str(csr), "-CA", str(ca_cert), "-CAkey", str(ca_key),
        "-CAcreateserial", "-sha256", "-extfile", str(ext), "-days", "30", "-out", str(cert),
    ]
    if expired:
        # OpenSSL's x509 signer cannot set a past end date.  Its CA command can.
        db, serial, config = directory / "index.txt", directory / "serial", directory / "ca.cnf"
        db.touch(); serial.write_text("01\n", encoding="ascii")
        config.write_text(
            """[ ca ]\ndefault_ca = local_ca\n[ local_ca ]\ndatabase = {db}\nserial = {serial}\nnew_certs_dir = {directory}\ncertificate = {ca_cert}\nprivate_key = {ca_key}\ndefault_md = sha256\npolicy = policy_any\n[ policy_any ]\ncommonName = supplied\n[ server_ext ]\nsubjectAltName = DNS:{hostname}\nextendedKeyUsage = serverAuth\n""".format(
                db=db, serial=serial, directory=directory, ca_cert=ca_cert,
                ca_key=ca_key, hostname=hostname,
            ),
            encoding="utf-8",
        )
        run("ca", "-batch", "-config", str(config), "-extensions", "server_ext", "-startdate", "20200101000000Z", "-enddate", "20200102000000Z", "-in", str(csr), "-out", str(cert), cwd=directory)
    else:
        run(*args, cwd=directory)
    return key, cert


def transfer(source: ssl.MemoryBIO, target: ssl.MemoryBIO) -> None:
    while True:
        data = source.read()
        if not data:
            return
        target.write(data)


def handshake(client: ssl.SSLObject, server: ssl.SSLObject, cin: ssl.MemoryBIO, cout: ssl.MemoryBIO, sin: ssl.MemoryBIO, sout: ssl.MemoryBIO) -> None:
    client_done = server_done = False
    for _ in range(100):
        if not client_done:
            try:
                client.do_handshake(); client_done = True
            except ssl.SSLWantReadError:
                pass
        transfer(cout, sin)
        if not server_done:
            try:
                server.do_handshake(); server_done = True
            except ssl.SSLWantReadError:
                pass
        transfer(sout, cin)
        if client_done and server_done:
            return
    raise AssertionError("TLS handshake did not converge")


def client_context(cafile: Path) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=str(cafile))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    return context


def server_context(key: Path, cert: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(cert), str(key))
    return context


class TlsTrustPrototypeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.root_key, self.root_cert = issue_ca(self.dir, "trusted-root")
        self.server_key, self.server_cert = issue_server(self.dir, self.root_key, self.root_cert, "gateway.test")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def pair(
        self, trust: Path, hostname: str, key: Path | None = None, cert: Path | None = None
    ) -> tuple[ssl.SSLObject, ssl.SSLObject, ssl.MemoryBIO, ssl.MemoryBIO, ssl.MemoryBIO, ssl.MemoryBIO]:
        cin, cout, sin, sout = ssl.MemoryBIO(), ssl.MemoryBIO(), ssl.MemoryBIO(), ssl.MemoryBIO()
        client = client_context(trust).wrap_bio(cin, cout, server_hostname=hostname)
        server = server_context(key or self.server_key, cert or self.server_cert).wrap_bio(sin, sout, server_side=True)
        return client, server, cin, cout, sin, sout

    def test_verified_hostname_authenticates_before_credential(self) -> None:
        client, server, cin, cout, sin, sout = self.pair(self.root_cert, "gateway.test")
        handshake(client, server, cin, cout, sin, sout)
        credential = b"synthetic-enrollment-credential"
        client.write(credential); transfer(cout, sin)
        self.assertEqual(server.read(), credential)

    def test_untrusted_server_never_receives_credential(self) -> None:
        rogue_key, rogue_cert = issue_ca(self.dir, "rogue-root")
        key, cert = issue_server(self.dir, rogue_key, rogue_cert, "gateway.test")
        client, server, cin, cout, sin, sout = self.pair(self.root_cert, "gateway.test", key, cert)
        with self.assertRaises(ssl.SSLCertVerificationError):
            handshake(client, server, cin, cout, sin, sout)
        self.assertEqual(sin.pending, 0, "client sent no application credential before trust")

    def test_hostname_mismatch_never_receives_credential(self) -> None:
        client, server, cin, cout, sin, sout = self.pair(self.root_cert, "other-gateway.test")
        with self.assertRaises(ssl.SSLCertVerificationError):
            handshake(client, server, cin, cout, sin, sout)
        self.assertEqual(sin.pending, 0)

    def test_expired_certificate_is_rejected(self) -> None:
        key, cert = issue_server(self.dir, self.root_key, self.root_cert, "gateway.test", expired=True)
        client, server, cin, cout, sin, sout = self.pair(self.root_cert, "gateway.test", key, cert)
        with self.assertRaises(ssl.SSLCertVerificationError):
            handshake(client, server, cin, cout, sin, sout)
        self.assertEqual(sin.pending, 0)

    def test_root_rollover_accepts_only_explicit_overlap_bundle(self) -> None:
        next_key, next_root = issue_ca(self.dir, "next-root")
        key, cert = issue_server(self.dir, next_key, next_root, "gateway.test")
        client, server, cin, cout, sin, sout = self.pair(self.root_cert, "gateway.test", key, cert)
        with self.assertRaises(ssl.SSLCertVerificationError):
            handshake(client, server, cin, cout, sin, sout)
        bundle = self.dir / "overlap-roots.pem"
        bundle.write_bytes(self.root_cert.read_bytes() + next_root.read_bytes())
        client, server, cin, cout, sin, sout = self.pair(bundle, "gateway.test", key, cert)
        handshake(client, server, cin, cout, sin, sout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
