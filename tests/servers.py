"""An app on a real port, in this process, answering real HTTP."""

from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager

import uvicorn


def listening() -> socket.socket:
    """A loopback socket bound to a free port, for an app whose address
    another app needs before either is served: the credit desk and the
    checker agent each call the other."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    return sock


def url_of(sock: socket.socket) -> str:
    return f"http://127.0.0.1:{sock.getsockname()[1]}"


@contextmanager
def serving(app, sock: socket.socket | None = None):
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]} if sock is not None else {}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise AssertionError("the service did not start")
        time.sleep(0.02)
    sock = server.servers[0].sockets[0]
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
