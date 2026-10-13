"""One service, started the way `credit-demo` starts it, logging every shape a
local path reaches a log record in. Run in a process of its own by
`tests/test_logs_carry_no_local_path.py`:

    python -m tests.log_every_shape underwriter

uvicorn configures its own loggers first, then imports the service's web
module, which installs the scrub filter (`credit_demo.common.errors`), the
order `uvicorn --factory` gives `credit-demo`. The service then answers one
request with a 404, and each shape is logged on the SDK's own logger and on
uvicorn's error logger. Everything the process logs goes to its standard
output and standard error, which the test reads.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

# The SDK's LangChain recorder logs here, and its records reach a handler
# through the root logger. uvicorn's error logger has handlers of its own and
# never reaches the root.
LOGGERS = ("sanning_anchor.langchain", "uvicorn.error")

# A local path on a machine that is not this one, and the name it is cut to.
HOME = "/Users/someone"
POLICY = f"{HOME}/work/credit-policy.pdf"


def _missing(text: str) -> FileNotFoundError:
    return FileNotFoundError(2, text, POLICY)


def shapes(mark: str) -> dict[str, tuple[object, tuple, str]]:
    """Each shape: the message, the args, and how the line must end."""
    return {
        "string-arg": (f"{mark} could not read %s", (POLICY,), f"{mark} could not read credit-policy.pdf"),
        "path-arg": (f"{mark} could not read %s", (Path(POLICY),), f"{mark} could not read credit-policy.pdf"),
        "exception-arg": (
            f"{mark} failed: %s",
            (_missing("No such file or directory"),),
            f"{mark} failed: [Errno 2] No such file or directory: 'credit-policy.pdf'",
        ),
        "exception-as-message": (
            _missing(f"{mark} No such file or directory"),
            (),
            f"[Errno 2] {mark} No such file or directory: 'credit-policy.pdf'",
        ),
        # uvicorn's own reload line has this shape.
        "list-arg": (
            f"{mark} watching these directories: %s",
            ([f"{HOME}/work/credit-demo"],),
            f"{mark} watching these directories: ['credit-demo']",
        ),
        "mapping-arg": (
            f"{mark} read %(path)s %(tries)d times",
            ({"path": Path(POLICY), "tries": 3},),
            f"{mark} read credit-policy.pdf 3 times",
        ),
        "path-split-over-two-args": (
            f"{mark} %s/%s",
            (f"{HOME}/work", "credit-policy.pdf"),
            f"{mark} credit-policy.pdf",
        ),
        "path-in-the-message": (f"{mark} could not read {POLICY}", (), f"{mark} could not read credit-policy.pdf"),
        "numbers-stay-numbers": (
            f"{mark} try %d of %d after %.1f s",
            (2, 3, 1.5),
            f"{mark} try 2 of 3 after 1.5 s",
        ),
    }


SHAPES = tuple(shapes(""))


def mark(logger: str, shape: str) -> str:
    return f"[{logger}|{shape}]"


def _serve(service: str) -> tuple[uvicorn.Server, threading.Thread, int]:
    config = uvicorn.Config(f"credit_demo.{service}.web:create_app", factory=True, host="127.0.0.1", port=0)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise SystemExit("the service did not start")
        time.sleep(0.02)
    return server, thread, server.servers[0].sockets[0].getsockname()[1]


def main(service: str) -> int:
    server, thread, port = _serve(service)
    try:
        httpx.get(f"http://127.0.0.1:{port}/favicon.ico", timeout=10)
        for name in LOGGERS:
            logger = logging.getLogger(name)
            for shape in SHAPES:
                message, args, _ = shapes(mark(name, shape))[shape]
                logger.error(message, *args)
            try:
                raise _missing("No such file or directory")
            except FileNotFoundError:
                logger.exception(f"{mark(name, 'traceback')} reading failed")
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
