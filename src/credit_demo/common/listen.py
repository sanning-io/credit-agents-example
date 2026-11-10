"""Serves an app on the interface `CREDIT_DEMO_HOST` names. uvicorn makes a `::`
socket IPv6-only, so this module makes it dual-stack. Where the kernel has no
IPv6, it listens on every IPv4 interface.
"""
from __future__ import annotations

import errno
import socket

import uvicorn

from .config import bind_host

ANY_HOST = "::"
ANY_IPV4_HOST = "0.0.0.0"

# What a kernel without IPv6 answers when asked for an IPv6 socket or address.
_NO_IPV6 = (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL)


def dual_stack_socket(port: int) -> socket.socket:
    """A socket bound to `::` that also takes IPv4 callers."""
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.bind((ANY_HOST, port))
    except OSError:
        sock.close()
        raise
    sock.set_inheritable(True)
    return sock


def serve(app, port: int) -> None:
    """Run `app` on `port` until it is stopped."""
    host = bind_host()
    if host == ANY_HOST:
        try:
            sock = dual_stack_socket(port)
        except OSError as exc:
            if exc.errno not in _NO_IPV6:
                raise
            host = ANY_IPV4_HOST
        else:
            uvicorn.Server(uvicorn.Config(app, port=port)).run(sockets=[sock])
            return
    uvicorn.run(app, host=host, port=port)
