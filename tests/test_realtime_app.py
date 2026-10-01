"""Tests for process-wide WebRTC compatibility setup."""

import socket

import pytest

aioice = pytest.importorskip("aioice")

from vauxr.realtime.app import restrict_aioice_host_candidates


async def test_restrict_host_candidates_before_aioice_opens_sockets(monkeypatch) -> None:
    import aioice.ice as aioice_ice

    def all_interfaces(use_ipv4: bool, use_ipv6: bool) -> list[str]:
        del use_ipv6
        return ["127.0.0.1", "127.0.0.2", "127.0.0.3"] if use_ipv4 else []

    monkeypatch.setattr(aioice_ice, "get_host_addresses", all_interfaces)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP, "", ("127.0.0.2", 0))
        ],
    )

    assert restrict_aioice_host_candidates("vauxr.test") is True

    connection = aioice.Connection(ice_controlling=False, stun_server=None)
    try:
        await connection.gather_candidates()
        assert [(candidate.host, candidate.type) for candidate in connection.local_candidates] == [
            ("127.0.0.2", "host")
        ]
    finally:
        await connection.close()


def test_restrict_host_candidates_fails_open_when_host_is_not_local(monkeypatch) -> None:
    import aioice.ice as aioice_ice

    def all_interfaces(use_ipv4: bool, use_ipv6: bool) -> list[str]:
        del use_ipv6
        return ["192.0.2.10"] if use_ipv4 else []

    monkeypatch.setattr(aioice_ice, "get_host_addresses", all_interfaces)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP, "", ("198.51.100.20", 0))
        ],
    )

    assert restrict_aioice_host_candidates("wrong.test") is False
    assert aioice_ice.get_host_addresses(True, False) == ["192.0.2.10"]
