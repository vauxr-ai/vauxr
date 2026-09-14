"""Real application on an OS-assigned loopback socket; no live configuration."""

import asyncio
import os
import signal
import socket

from aiohttp import web


async def main() -> None:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", int(os.environ.get("VAUXR_TEST_PORT", "0"))))
    listener.setblocking(False)
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    os.environ["OWNER_HTTP_ORIGIN"] = origin
    # Import only after fixing the exact owner authority.
    from server import _cleanup, _startup, make_app

    app = make_app()
    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    runner = web.AppRunner(app, access_log=None)
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    try:
        await runner.setup()
        await web.SockSite(runner, listener).start()
        print(origin, flush=True)  # The only fixture output; never credentials.
        await stop.wait()
    finally:
        await runner.cleanup()
        listener.close()


asyncio.run(main())
