"""Retained transport authority, including idle/superseded sockets and media."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from auth_policy import Principal
from auth_store import CredentialStore

CLOSE_SECONDS = 2.0


@dataclass(eq=False)
class Teardown:
    close: Callable[[], Awaitable[None]]
    task: asyncio.Task | None = field(default=None, init=False)

    async def run(self) -> None:
        # Keep a noncooperative attempt instead of accumulating duplicate tasks.
        # Calling inside the task also isolates callbacks that raise synchronously.
        async def invoke() -> None:
            await self.close()

        if self.task is None:
            self.task = asyncio.create_task(invoke())
            self.task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        task = self.task
        done, _ = await asyncio.wait({task}, timeout=CLOSE_SECONDS)
        if not done:
            if not task.cancelling():
                task.cancel()
            raise RuntimeError("transport_teardown_unavailable")
        if self.task is task:
            self.task = None
        if task.cancelled() or task.exception() is not None:
            raise RuntimeError("transport_teardown_unavailable")


async def run_teardowns(attempts: list[Teardown]) -> None:
    snapshot = list(attempts)
    results = await asyncio.gather(*(attempt.run() for attempt in snapshot), return_exceptions=True)
    for attempt, result in zip(snapshot, results, strict=True):
        if not isinstance(result, BaseException) and attempt in attempts:
            attempts.remove(attempt)
    if any(isinstance(result, BaseException) for result in results):
        raise RuntimeError("transport_teardown_unavailable")


@dataclass(eq=False)
class Connection(Teardown):
    path: Path
    principal: Principal
    retiring: bool = field(default=False, init=False)


_connections: set[Connection] = set()


def retain(principal: Principal, close: Callable[[], Awaitable[None]]) -> Connection:
    from auth import get_store

    connection = Connection(close, get_store().path, principal)
    _connections.add(connection)
    return connection


def release(connection: Connection | None) -> None:
    if connection is None or not connection.retiring:
        _connections.discard(connection)


async def disconnect_stale(store: CredentialStore) -> None:
    # Keep in-flight attempts visible to concurrent HTTP/maintenance callers so
    # neither can report success while another caller is still closing authority.
    stale = [connection for connection in _connections if connection.path == store.path
             and not store.current(connection.principal)]

    async def close(connection: Connection) -> None:
        connection.retiring = True
        try:
            await connection.run()
        except BaseException:
            _connections.add(connection)
            raise
        else:
            _connections.discard(connection)

    results = await asyncio.gather(*(close(connection) for connection in stale), return_exceptions=True)
    if any(isinstance(result, BaseException) for result in results):
        raise RuntimeError("transport_teardown_unavailable")
