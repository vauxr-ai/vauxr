"""Optional native TLS and opt-in Certbot Route53 lifecycle; no custom ACME."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import os
import re
import ssl
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm

log = logging.getLogger("vauxr.tls")
CERTIFICATE_ERRORS = (OSError, ValueError, x509.ExtensionNotFound, x509.DuplicateExtension, UnsupportedAlgorithm)


@dataclass(frozen=True)
class TLSConfig:
    enabled: bool = False
    port: int = 8443
    cert: str = ""
    key: str = ""
    hostname: str = ""
    acme: bool = False
    domain: str = ""
    email: str = ""
    staging: bool = False
    state_dir: str = ""


def _flag(name: str) -> bool:
    value = os.environ.get(name, "0").lower()
    if value not in {"0", "1", "false", "true", "no", "yes"}:
        raise ValueError(f"Invalid {name}; use 0 or 1")
    return value in {"1", "true", "yes"}


def load_tls_config(data_dir: str) -> TLSConfig:
    enabled = _flag("HTTPS_ENABLED")
    acme = _flag("ACME_ROUTE53_ENABLED")
    staging = _flag("ACME_STAGING")
    tos = _flag("ACME_ACCEPT_TOS")
    cert, key = os.environ.get("HTTPS_CERT_FILE", ""), os.environ.get("HTTPS_KEY_FILE", "")
    domain, email = os.environ.get("ACME_DOMAIN", ""), os.environ.get("ACME_EMAIL", "")
    if not enabled:
        if acme or cert or key or domain or email or staging or tos or os.environ.get("HTTPS_PORT"):
            raise ValueError("TLS settings require HTTPS_ENABLED=1")
        return TLSConfig()
    from vauxr.auth.owner import configured_origin

    origin = configured_origin()
    if not origin.startswith("https://") or os.environ.get("OWNER_TRUSTED_PROXIES"):
        raise ValueError("Native TLS requires OWNER_HTTPS_ORIGIN and no OWNER_TRUSTED_PROXIES")
    port = int(os.environ.get("HTTPS_PORT", "8443"))
    if not 1 <= port <= 65535:
        raise ValueError("HTTPS_PORT must be between 1 and 65535")
    if port in {int(os.environ.get("HTTP_PORT", "8080")), int(os.environ.get("WS_PORT", "8765"))}:
        raise ValueError("HTTPS_PORT must differ from HTTP_PORT and WS_PORT")
    hostname = urlsplit(origin).hostname or ""
    state = str(Path(data_dir) / "acme" / ("staging" if staging else "production"))
    if acme:
        if cert or key:
            raise ValueError("Choose either certificate files or Route53 automation")
        if (not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                             r"[a-z]{2,63}", domain) or hostname != domain):
            raise ValueError("ACME_DOMAIN must be a DNS name matching OWNER_HTTPS_ORIGIN")
        if not tos or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or email.startswith("-"):
            raise ValueError("Route53 automation requires ACME_EMAIL and ACME_ACCEPT_TOS=1")
        live = Path(state) / "config" / "live" / domain
        cert, key = str(live / "fullchain.pem"), str(live / "privkey.pem")
    elif not cert or not key or domain or email or staging or tos:
        raise ValueError("Manual HTTPS requires both certificate files and no ACME settings")
    return TLSConfig(True, port, cert, key, hostname, acme, domain, email, staging, state)


def _matches(cert: x509.Certificate, hostname: str) -> bool:
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        host = hostname.lower()
        for name in san.get_values_for_type(x509.DNSName):
            name = name.lower()
            if name == host or (name.startswith("*.") and host.count(".") == name.count(".")
                                and host.endswith(name[1:])):
                return True
        return False
    return address in san.get_values_for_type(x509.IPAddress)


class CertificateContext:
    """Publish complete immutable contexts; never mutate the active certificate chain."""

    def __init__(self, config: TLSConfig) -> None:
        self.config = config
        self.active: ssl.SSLContext | None = None
        self.expires: datetime | None = None
        self.snapshot: tuple[bytes, bytes] | None = None
        self.listener = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.listener.minimum_version = ssl.TLSVersion.TLSv1_2
        self.listener.num_tickets = 0
        self.listener.options |= ssl.OP_NO_COMPRESSION | ssl.OP_NO_TICKET
        self.listener.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20")
        self.listener.sni_callback = self._select

    def _select(self, socket: ssl.SSLSocket | ssl.SSLObject, _name: str | None,
                _context: ssl.SSLContext) -> int | None:
        if self.active is None or self.expires is None or datetime.now(UTC) >= self.expires:
            return ssl.ALERT_DESCRIPTION_INTERNAL_ERROR
        socket.context = self.active
        return None

    def reload(self) -> bool:
        snapshot = (Path(self.config.cert).read_bytes(), Path(self.config.key).read_bytes())
        if snapshot == self.snapshot:
            return False
        chain = x509.load_pem_x509_certificates(snapshot[0])
        now = datetime.now(UTC)
        if not chain or any(not (cert.not_valid_before_utc <= now < cert.not_valid_after_utc)
                            for cert in chain):
            raise ValueError("TLS certificate outside validity period")
        if not _matches(chain[0], self.config.hostname):
            raise ValueError("TLS certificate SAN does not match owner hostname")
        candidate = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        candidate.minimum_version = ssl.TLSVersion.TLSv1_2
        candidate.num_tickets = 0
        candidate.options |= ssl.OP_NO_COMPRESSION | ssl.OP_NO_TICKET
        candidate.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20")
        candidate.set_alpn_protocols(["http/1.1"])
        # Snapshot both files once, then load those exact bytes to avoid renewal races.
        # TemporaryDirectory is private; no key material enters logs or subprocess args.
        with tempfile.TemporaryDirectory(prefix="vauxr-tls-") as directory:
            cert_path, key_path = Path(directory) / "chain", Path(directory) / "key"
            cert_path.write_bytes(snapshot[0])
            key_path.write_bytes(snapshot[1])
            candidate.load_cert_chain(cert_path, key_path, password=lambda: "")
        self.active = candidate
        self.expires = min(cert.not_valid_after_utc for cert in chain)
        self.snapshot = snapshot
        return True


async def run_certbot(config: TLSConfig, timeout: float = 300) -> None:
    """Bound output, runtime and cancellation; never log Certbot/AWS output."""
    root = Path(config.state_dir)
    for name in ("config", "work", "logs"):
        (root / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    args = [sys.executable, "-c", "from certbot.main import main; raise SystemExit(main())",
            "certonly", "--non-interactive", "--agree-tos",
            "--dns-route53", "--preferred-challenges", "dns", "--keep-until-expiring",
            "--cert-name", config.domain, "--domains", config.domain, "--email", config.email,
            "--config-dir", str(root / "config"), "--work-dir", str(root / "work"),
            "--logs-dir", str(root / "logs"), "--config", os.devnull,
            "--server", "https://acme-staging-v02.api.letsencrypt.org/directory" if config.staging
            else "https://acme-v02.api.letsencrypt.org/directory"]
    process = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(timeout):
            code = await process.wait()
        if code:
            raise RuntimeError("Certbot failed; inspect protected Certbot logs")
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()


class TLSService:
    """One supervised worker per listener, with a cross-process automation lock."""

    def __init__(self, config: TLSConfig, reload_interval: float = 30,
                 renewal_interval: float = 12 * 3600) -> None:
        self.config = config
        self.context = CertificateContext(config)
        self.reload_interval = reload_interval
        self.renewal_interval = renewal_interval
        self.task: asyncio.Task[None] | None = None
        self.lock: BinaryIO | None = None

    async def prepare(self) -> None:
        if self.config.acme and self.lock is None:
            import fcntl

            root = Path(self.config.state_dir)
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            lock = (root / "vauxr.lock").open("a+b")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                lock.close()
                raise RuntimeError("Route53 worker already owns this state directory") from None
            self.lock = lock
        try:
            self.context.reload()
        except CERTIFICATE_ERRORS:
            if not self.config.acme:
                raise ValueError("Invalid TLS certificate/key configuration") from None
            await run_certbot(self.config)
            self.context.reload()  # Must succeed before any listener opens.

    def start(self) -> None:
        if self.context.active is None:
            raise RuntimeError("TLS certificate is not ready")
        if self.task is None:
            self.task = asyncio.create_task(self._worker(), name="vauxr-tls")

    async def _worker(self) -> None:
        next_renewal = 0.0
        while True:
            if self.config.acme and asyncio.get_running_loop().time() >= next_renewal:
                try:
                    await run_certbot(self.config)
                except (OSError, RuntimeError, TimeoutError):
                    log.error("Certificate renewal failed; retaining current TLS context")
                next_renewal = asyncio.get_running_loop().time() + self.renewal_interval
            try:
                if self.context.reload():
                    log.info("TLS certificate reloaded")
            except CERTIFICATE_ERRORS:
                log.error("Certificate reload rejected; retaining current TLS context")
            await asyncio.sleep(self.reload_interval)

    async def close(self) -> None:
        try:
            if self.task is not None:
                self.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.task
        finally:
            self.task = None
            if self.lock is not None:
                self.lock.close()
                self.lock = None
