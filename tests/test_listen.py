"""`CREDIT_DEMO_HOST=::` listens on IPv4 and IPv6 with one socket, and the
same image starts where the kernel has no IPv6."""

from __future__ import annotations

import errno
import socket

import pytest
import uvicorn

from credit_demo.common import listen


def _ipv6_available() -> bool:
    try:
        probe = socket.socket(socket.AF_INET6)
        probe.bind(("::1", 0))
        probe.close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _ipv6_available(), reason="this machine has no IPv6 loopback")
def test_the_dual_stack_socket_takes_ipv4_and_ipv6_callers():
    sock = listen.dual_stack_socket(0)
    try:
        sock.listen()
        port = sock.getsockname()[1]
        for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
            with socket.socket(family) as caller:
                caller.settimeout(3)
                caller.connect((address, port))
    finally:
        sock.close()


def test_the_wildcard_host_serves_on_the_dual_stack_socket(monkeypatch):
    sentinel = object()
    seen = {}
    monkeypatch.setenv("CREDIT_DEMO_HOST", "::")
    monkeypatch.setattr(listen, "dual_stack_socket", lambda port: sentinel)
    monkeypatch.setattr(
        uvicorn.Server, "run", lambda self, sockets=None: seen.update(sockets=sockets)
    )

    listen.serve(object(), 8001)

    assert seen["sockets"] == [sentinel]


def test_a_kernel_with_no_ipv6_listens_on_every_ipv4_interface(monkeypatch):
    seen = {}

    def no_ipv6(port):
        raise OSError(errno.EAFNOSUPPORT, "Address family not supported")

    monkeypatch.setenv("CREDIT_DEMO_HOST", "::")
    monkeypatch.setattr(listen, "dual_stack_socket", no_ipv6)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: seen.update(kwargs))

    listen.serve(object(), 8001)

    assert seen == {"host": "0.0.0.0", "port": 8001}


def test_a_port_already_in_use_is_not_hidden_by_the_fallback(monkeypatch):
    def in_use(port):
        raise OSError(errno.EADDRINUSE, "Address already in use")

    monkeypatch.setenv("CREDIT_DEMO_HOST", "::")
    monkeypatch.setattr(listen, "dual_stack_socket", in_use)

    with pytest.raises(OSError):
        listen.serve(object(), 8001)


def test_any_other_host_is_left_to_uvicorn(monkeypatch):
    seen = {}
    monkeypatch.setenv("CREDIT_DEMO_HOST", "10.0.0.5")
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: seen.update(kwargs))

    listen.serve(object(), 8002)

    assert seen == {"host": "10.0.0.5", "port": 8002}
