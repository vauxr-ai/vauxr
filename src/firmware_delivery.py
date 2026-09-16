"""Process-local, bounded, single-use capabilities for legacy HTTP OTA clients."""

import asyncio
import hashlib
import os
import re
import secrets
import stat
from time import monotonic
from typing import BinaryIO

from aiohttp import web

from config import get_config
from owner_http import ORIGIN

TTL_SECONDS = 120
MAX_TOKENS = 128
_NAME = re.compile(r"[A-Za-z0-9._-]+\.bin")
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
DELIVERIES = web.AppKey("firmware_deliveries", dict[str, tuple[str, float]])


def _missing() -> web.Response:
    return web.json_response({"error": "not found"}, status=404, headers=HEADERS)


def _open(name: str) -> BinaryIO:
    # Open relative to the firmware directory, refusing symlinks and non-files.
    if not _NAME.fullmatch(name):
        raise FileNotFoundError
    root = os.open(os.path.join(get_config().data_dir, "firmware"), os.O_RDONLY | os.O_DIRECTORY)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
    finally:
        os.close(root)
    file = os.fdopen(fd, "rb")
    if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
        file.close()
        raise FileNotFoundError
    return file


def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def mint(request: web.Request) -> web.Response:
    """Caller must enforce the owner-only firmware.delivery_mint operation."""
    name = request.match_info["filename"]
    try:
        with _open(name):
            pass
    except OSError:
        return _missing()
    entries = request.app[DELIVERIES]
    now = monotonic()
    for key, (_, deadline) in list(entries.items()):
        if deadline <= now:
            del entries[key]
    if len(entries) >= MAX_TOKENS:
        return web.json_response({"error": "delivery capacity reached"}, status=503, headers=HEADERS)
    token = secrets.token_urlsafe(32)
    entries[_key(token)] = (name, now + TTL_SECONDS)
    return web.json_response({
        "url": f"{request.app[ORIGIN]}/firmware-delivery/{token}/{name}",
        "expires_in": TTL_SECONDS,
    }, status=201, headers=HEADERS)


async def download(request: web.Request) -> web.StreamResponse:
    name = request.match_info["filename"]
    token = request.match_info["token"]
    # No await between lookup/removal: concurrent requests cannot both redeem.
    # Even a mismatched filename burns the capability; every failure looks alike.
    entry = request.app[DELIVERIES].pop(_key(token), None)
    if entry is None or entry[0] != name or monotonic() >= entry[1]:
        return _missing()
    try:
        file = _open(name)
    except OSError:
        return _missing()
    # Stream the opened file itself: no redirects, compressed siblings, conditional
    # responses or reopening a path after validation. A failed transfer stays spent.
    with file:
        response = web.StreamResponse(headers={
            **HEADERS,
            "Content-Type": "application/octet-stream",
            "Content-Disposition": f'attachment; filename="{name}"',
            "Content-Length": str(os.fstat(file.fileno()).st_size),
        })
        await response.prepare(request)
        while chunk := await asyncio.to_thread(file.read, 64 * 1024):
            await response.write(chunk)
        await response.write_eof()
        return response
