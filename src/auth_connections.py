"""Retained transport authority, including idle/superseded sockets and media."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from auth_policy import Principal
from auth_store import CredentialStore


@dataclass(eq=False)
class Connection:
    path: Path
    principal: Principal
    close: Callable[[], Awaitable[None]]


_connections: set[Connection] = set()


def retain(principal: Principal, close: Callable[[], Awaitable[None]]) -> Connection:
    from auth import get_store

    connection = Connection(get_store().path, principal, close)
    _connections.add(connection)
    return connection


def release(connection: Connection | None) -> None:
    _connections.discard(connection)


async def disconnect_stale(store: CredentialStore) -> None:
    # Remove before awaiting, so competing sweeps cannot close a replacement.
    stale = [connection for connection in _connections if connection.path == store.path
             and not store.current(connection.principal)]
    for connection in stale:
        _connections.discard(connection)
    results = await asyncio.gather(*(connection.close() for connection in stale), return_exceptions=True)
    failed = [connection for connection, result in zip(stale, results, strict=True)
              if isinstance(result, BaseException)]
    _connections.update(failed)
    if failed:
        raise RuntimeError("transport_teardown_unavailable")
