"""Automatic ICE host selection uses the trusted origin and a local interface."""

import socket

from vauxr.auth import owner
from vauxr.realtime import host


def test_explicit_ice_host_wins(monkeypatch):
    monkeypatch.setattr(owner, "configured_origin", lambda: "http://localhost:8080")
    assert host.effective_host("192.0.2.42") == "192.0.2.42"


def test_default_localhost_uses_loopback(monkeypatch):
    monkeypatch.setattr(owner, "configured_origin", lambda: "http://localhost:8080")
    assert host.effective_host("") == "127.0.0.1"


def test_ice_host_from_local_owner_origin(monkeypatch):
    import aioice.ice

    monkeypatch.setattr(owner, "configured_origin", lambda: "http://vauxr.example:8080")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_DGRAM, 0, "", ("192.0.2.10", 0))
    ])
    monkeypatch.setattr(aioice.ice, "get_host_addresses", lambda **kwargs: ["127.0.0.1", "192.0.2.10"])
    assert host.effective_host("") == "192.0.2.10"


def test_nonlocal_owner_origin_needs_explicit_ice_host(monkeypatch):
    import aioice.ice

    monkeypatch.setattr(owner, "configured_origin", lambda: "https://proxy.example")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_DGRAM, 0, "", ("198.51.100.20", 0))
    ])
    monkeypatch.setattr(aioice.ice, "get_host_addresses", lambda **kwargs: ["192.0.2.10"])
    assert host.effective_host("") == ""
