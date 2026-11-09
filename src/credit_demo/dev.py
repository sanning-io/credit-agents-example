"""The one command: `credit-demo` starts both services locally, on a laptop,
with no orchestration package. Two plain subprocesses, each running its own
service's `uvicorn` app; Ctrl+C stops both.

If the checker exits, the underwriter stays up: it serves the credit desk,
and a case it starts while the checker is down stops with the reason on
its page. If the underwriter exits, there is no page left to serve, so the
launcher stops.

**Both services bind to the loopback interface by default** (`bind_host`):
none of their endpoints takes credentials. `CREDIT_DEMO_HOST` changes that
for the underwriter, whose port is the one thing a platform's network has to
reach, and the container sets it. The checker binds to loopback always: the
underwriter reaches it over `CHECKER_URL`, `http://localhost:8002` in the
same container as on a laptop, so nothing outside this process's own two
children ever needs to reach it. The checker asks the credit desk for each
case's documents over `DESK_URL`, `http://localhost:8001`, the same way.

**Nothing starts until both services' settings pass their start-up tests**
(`credit_demo.common.startup`): every setting set, the Sanning API key, the
bucket and the model each answering. A wrong setting stops the launcher with
the variable to change; a service that did not answer is tried again for up
to two minutes first.

**Each side must know the other's public key before a run starts**, and on a
laptop both signing seeds sit in the same `.env`. So the launcher derives
each side's public key from its seed and sets it as the other side's
counterparty key wherever the operator left that unset. A value the operator
did set is never replaced: two services on two machines hold two `.env`
files, and there the operator copies each key from the other side's
`/health` (`public_key`). `credit-demo keys` prints a fresh pair of seeds for
a `.env` that has none yet.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import MutableMapping, Sequence


from .checker import config as checker_config
from .common.config import bind_host, load_env_file, public_key_of
from .common.errors import describe, tell_the_operator
from .common.startup import run_startup_tests
from .underwriter import config as underwriter_config

ENV_FILE = Path(".env")

# Each side, and the side whose key it expects on the other end.
_SIDES = (
    ("UNDERWRITER", "CHECKER"),
    ("CHECKER", "UNDERWRITER"),
)


def wire_counterparty_keys(env: MutableMapping[str, str]) -> list[str]:
    """Set each side's counterparty key from the other side's seed, where the
    operator left it unset. Returns the variables it set."""
    wired = []
    for side, other in _SIDES:
        variable = f"{side}_COUNTERPARTY_PUBLIC_KEY"
        if env.get(variable, "").strip():
            continue
        key = public_key_of(env.get(f"{other}_SANNING_SIGNING_SEED", ""))
        if key is None:
            continue
        env[variable] = key
        wired.append(variable)
    return wired


def generate_keys() -> list[str]:
    """Two fresh signing seeds, as `.env` lines. The launcher derives each
    side's counterparty key from them (`wire_counterparty_keys`). Nothing is
    written: the seeds are secrets, and where they go is the operator's
    call."""
    return [f"{side}_SANNING_SIGNING_SEED={os.urandom(32).hex()}" for side, _other in _SIDES]


def _service_command(module: str, port_env: str, default_port: str, *, service: str) -> list[str]:
    port = os.environ.get(port_env, default_port)
    return [
        sys.executable, "-m", "uvicorn", f"{module}:create_app", "--factory",
        "--host", bind_host(service), "--port", port,
    ]


def supervise(procs, stop, sleep=time.sleep, log=tell_the_operator) -> int:
    """Watch both services until one that matters exits, or a stop is asked for.

    The underwriter is load bearing: it serves the credit desk, so its exit
    ends the run. The checker is not: its exit is reported once and the
    underwriter carries on, so the desk can show why a case stopped.
    """
    reported: set[str] = set()
    while not stop["requested"]:
        code = procs["underwriter"].poll()
        if code is not None:
            log(f"credit-demo: underwriter exited ({code}); stopping the checker too")
            return code
        code = procs["checker"].poll()
        if code is not None and "checker" not in reported:
            reported.add("checker")
            log(f"credit-demo: checker exited ({code}); the underwriter stays up")
        sleep(0.5)
    return 0


def test_both_services(startup=run_startup_tests) -> bool:
    """Both services' settings, tested before either starts
    (`credit_demo.common.startup`). A setting that cannot even be read, such
    as a counterparty key that is not a key, stops here too."""
    try:
        services = [underwriter_config.load(), checker_config.load()]
    except ValueError as exc:
        tell_the_operator(f"credit-demo: FAIL settings: {describe(exc)}")
        return False
    return startup(services)


def main(argv: Sequence[str] | None = None, *, startup=run_startup_tests) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["keys"]:
        # The one output that is not a log line: it is what the operator
        # pastes into `.env`, so it goes to standard output.
        sys.stdout.write("\n".join(generate_keys()) + "\n")
        return 0
    if argv:
        tell_the_operator(
            "credit-demo: the one command takes no arguments, or `keys` to "
            "print a fresh pair of signing seeds for .env"
        )
        return 2

    load_env_file(ENV_FILE)
    for variable in wire_counterparty_keys(os.environ):
        tell_the_operator(
            f"credit-demo: {variable} derived from the other side's signing seed"
        )
    # Both services inherit this process's output, which is a pipe in a
    # container and under any launcher, and a pipe is block-buffered: a line
    # a service printed could sit unwritten until the buffer filled, and be
    # lost if the process was killed first. Set here, before the two
    # subprocesses start, so a laptop run gets the same log as the image.
    os.environ.setdefault("PYTHONUNBUFFERED", "1")

    if not test_both_services(startup):
        tell_the_operator(
            "credit-demo: not started. Fix the settings named above in .env, "
            "then run credit-demo again."
        )
        return 1
    tell_the_operator("credit-demo: every start-up test passed; starting both services")

    procs = {
        "checker": subprocess.Popen(
            _service_command("credit_demo.checker.web", "CHECKER_PORT", "8002", service="checker")
        ),
        "underwriter": subprocess.Popen(
            _service_command(
                "credit_demo.underwriter.web", "UNDERWRITER_PORT", "8001", service="underwriter"
            )
        ),
    }
    underwriter_port = os.environ.get("UNDERWRITER_PORT", "8001")
    checker_port = os.environ.get("CHECKER_PORT", "8002")
    underwriter_host = bind_host("underwriter")
    checker_host = bind_host("checker")
    tell_the_operator(
        f"credit-demo: underwriter (the credit desk) on http://{underwriter_host}:{underwriter_port}, "
        f"checker on http://{checker_host}:{checker_port}. Ctrl+C stops both."
    )

    stop = {"requested": False}

    def _stop(*_args: object) -> None:
        stop["requested"] = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    exit_code = 0
    try:
        exit_code = supervise(procs, stop)
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.terminate()
        for proc in procs.values():
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
