"""The `credit-demo` command: starts the services locally, on a laptop and in a
container, with no orchestration package.

- `credit-demo` starts the three services as plain subprocesses, each running
  its own `uvicorn` app: the underwriter (with the credit desk), the checker
  and the evidence builder. Ctrl+C stops all three.
- `credit-demo underwriter`, `credit-demo checker` and
  `credit-demo evidence-builder` start one service alone. This is the start
  command for a host that runs each service on its own. It reads the same
  files and the same environment, and tests that service's settings first.

**Each agent reads the Quickstart's standard names.** On a laptop, `.env`
holds the settings both agents use and `underwriter.env` or `checker.env`
holds one agent's own (its ID, its signing seed, its counterparty key). The
launcher gives each agent the shared file plus its own; a value already in
the environment wins over both files, and an agent's own file wins over the
shared one. On a host, each service gets its settings from the host, and the
files are absent.

**The evidence builder reads its own file and nothing shared.** `.env` holds
the agents' API key and the bucket keys that can write, and the evidence
builder must have neither: `evidence-builder.env` holds its own API key, its
own bucket keys (limited to reading and to adding the declarations it signs)
and the organization's hand-over seed, and the launcher never gives it
`.env`.

If the checker or the evidence builder exits, the underwriter stays up: it
serves the credit desk, and a case it starts while the checker is down stops
with the reason on its page, while the desk keeps asking the evidence builder
for a closed case's file and says on the page when it gives up. If the
underwriter exits, there is no page left to serve, so the launcher stops.

**Every service binds to the loopback interface by default** (`bind_host`):
none of their endpoints takes credentials. `CREDIT_DEMO_HOST` changes that,
and the image sets it so that the underwriter's port can be reached and the
underwriter can reach the other two over the host's private network. The
underwriter reaches the checker over `CHECKER_URL` and the evidence builder
over `EVIDENCE_BUILDER_URL`; the checker asks the credit desk for each case's
documents over `DESK_URL`. The launcher fills in all three for the local ports
when they are unset.

**Nothing starts until the services' settings pass their start-up tests**
(`credit_demo.common.startup`): every setting set, the Sanning API key, the
bucket and the model each answering. A wrong setting stops the command with
the variable to change; a service that did not answer is tried again for up
to two minutes first.

**Each side must know the other's public key before a run starts.** On a
laptop both signing seeds are at hand, so the launcher derives each side's
public key from the other's seed wherever `COUNTERPARTY_PUBLIC_KEY` is unset.
A value the operator did set is never replaced: two services on two hosts
hold two sets of settings, and there the operator copies each key from the
other side's `/health` (`public_key`). `credit-demo keys` writes a fresh seed
into each agent's file and the hand-over seed into the evidence builder's.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Mapping, MutableMapping, Sequence

from .checker import config as checker_config
from .common.config import (
    KEY_KIND_VARIABLE,
    ORGANIZATION_KEY_KIND,
    bind_host,
    public_key_of,
    read_env_file,
)
from .common.errors import describe, tell_the_operator
from .common.startup import run_startup_tests
from .evidence_builder import config as evidence_builder_config
from .evidence_builder.config import CHECKER_AGENT_VARIABLE, UNDERWRITER_AGENT_VARIABLE
from .underwriter import config as underwriter_config

ENV_FILE = Path(".env")
UNDERWRITER = "underwriter"
CHECKER = "checker"
EVIDENCE_BUILDER = "evidence-builder"
AGENTS = (UNDERWRITER, CHECKER)
SERVICES = (UNDERWRITER, CHECKER, EVIDENCE_BUILDER)
_DEFAULT_PORT = {UNDERWRITER: "8001", CHECKER: "8002", EVIDENCE_BUILDER: "8003"}
_LOADERS = {
    UNDERWRITER: underwriter_config.load,
    CHECKER: checker_config.load,
    EVIDENCE_BUILDER: evidence_builder_config.load,
}
_AGENT_ID_VARIABLES = {UNDERWRITER: UNDERWRITER_AGENT_VARIABLE, CHECKER: CHECKER_AGENT_VARIABLE}


def service_file(service: str) -> Path:
    """The file that holds one service's own settings."""
    return Path(f"{service}.env")


def service_env(service: str, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """One service's settings: for an agent, the shared file, then the agent's
    own file over it, then the environment over both. The evidence builder gets
    its own file and the environment, never the shared file. `PORT` is the
    service's own, so the environment's `PORT` never reaches the launcher's
    services."""
    environ = os.environ if environ is None else environ
    own = read_env_file(service_file(service))
    shared = {} if service == EVIDENCE_BUILDER else read_env_file(ENV_FILE)
    env = {**shared, **own, **environ}
    env["PORT"] = own.get("PORT") or shared.get("PORT") or _DEFAULT_PORT[service]
    return env


def wire_counterparty_keys(
    underwriter: MutableMapping[str, str], checker: MutableMapping[str, str]
) -> list[str]:
    """Set each side's counterparty key from the other side's seed, where the
    operator left it unset. Returns a note for each one it set."""
    wired = []
    for agent, env, other in ((UNDERWRITER, underwriter, checker), (CHECKER, checker, underwriter)):
        if env.get("COUNTERPARTY_PUBLIC_KEY", "").strip():
            continue
        key = public_key_of(other.get("SANNING_SIGNING_SEED", ""))
        if key is None:
            continue
        env["COUNTERPARTY_PUBLIC_KEY"] = key
        wired.append(f"{agent}: COUNTERPARTY_PUBLIC_KEY")
    return wired


def write_keys(
    directory: Path = Path("."),
    *,
    log: Callable[[str], None] = tell_the_operator,
) -> int:
    """Each service's own file, created if it is not there. An agent's file
    gets a fresh signing seed and the other agent's public key as its
    counterparty key. The evidence builder's file gets a fresh hand-over seed,
    its kind, and the two agents' IDs, which it builds the `--agent` and
    `--logs` values from. A value a file already has is kept. The seeds are
    secrets, so only the file names and the public keys are printed; a host
    that holds each service's settings takes the public keys from here."""
    paths = {service: directory / service_file(service) for service in SERVICES}
    for agent in AGENTS:
        path = paths[agent]
        if read_env_file(path).get("SANNING_SIGNING_SEED"):
            log(f"credit-demo: {path} already has a signing seed; left as it is")
            continue
        lines = [] if path.exists() else [f"SANNING_AGENT_ID={agent}-sandbox"]
        lines.append(f"SANNING_SIGNING_SEED={os.urandom(32).hex()}")
        _append(path, lines)
        log(f"credit-demo: wrote a signing seed to {path}")
    keys = {
        agent: public_key_of(read_env_file(paths[agent]).get("SANNING_SIGNING_SEED", ""))
        for agent in AGENTS
    }
    for agent, other in ((UNDERWRITER, CHECKER), (CHECKER, UNDERWRITER)):
        if keys[other] and not read_env_file(paths[agent]).get("COUNTERPARTY_PUBLIC_KEY"):
            _append(paths[agent], [f"COUNTERPARTY_PUBLIC_KEY={keys[other]}"])
    for agent, key in keys.items():
        if key:
            log(f"credit-demo: the {agent}'s public key is {key}")

    builder_path = paths[EVIDENCE_BUILDER]
    have = read_env_file(builder_path)
    lines = []
    if KEY_KIND_VARIABLE not in have:
        lines.append(f"{KEY_KIND_VARIABLE}={ORGANIZATION_KEY_KIND}")
    for agent, variable in _AGENT_ID_VARIABLES.items():
        if variable not in have:
            lines.append(f"{variable}={read_env_file(paths[agent]).get('SANNING_AGENT_ID') or agent}")
    if have.get("SANNING_SIGNING_SEED"):
        log(f"credit-demo: {builder_path} already has a signing seed; left as it is")
    else:
        lines.append(f"SANNING_SIGNING_SEED={os.urandom(32).hex()}")
        log(f"credit-demo: wrote a hand-over seed to {builder_path}")
    if lines:
        _append(builder_path, lines)
    builder_key = public_key_of(read_env_file(builder_path).get("SANNING_SIGNING_SEED", ""))
    if builder_key:
        log(f"credit-demo: the evidence builder's public key is {builder_key}")
    return 0


def _append(path: Path, lines: list[str]) -> None:
    with open(path, "a", opener=lambda name, flags: os.open(name, flags, 0o600)) as out:
        out.write("\n".join(lines) + "\n")


def url_host(env: Mapping[str, str]) -> str:
    """The interface a service binds to, as it is written in a URL."""
    host = bind_host(env)
    return f"[{host}]" if ":" in host else host


def _service_command(service: str) -> list[str]:
    """The service's own start command. It reads its port from `PORT` and its
    interface from `CREDIT_DEMO_HOST`, in the environment it is started with."""
    return [sys.executable, "-m", f"credit_demo.{service.replace('-', '_')}"]


def supervise(procs, stop, sleep=time.sleep, log=tell_the_operator) -> int:
    """Watch the services until the one that matters exits, or a stop is asked for.

    The underwriter is load bearing: it serves the credit desk, so its exit
    ends the run. The others are not: an exit is reported once and the
    underwriter carries on, so the desk can show why a case stopped.
    """
    reported: set[str] = set()
    while not stop["requested"]:
        code = procs[UNDERWRITER].poll()
        if code is not None:
            log(f"credit-demo: underwriter exited ({code}); stopping the other services too")
            return code
        for name, proc in procs.items():
            if name == UNDERWRITER:
                continue
            code = proc.poll()
            if code is not None and name not in reported:
                reported.add(name)
                log(f"credit-demo: {name} exited ({code}); the underwriter stays up")
        sleep(0.5)
    return 0


def test_services(envs: Mapping[str, Mapping[str, str]], startup=run_startup_tests) -> bool:
    """Each named service's settings, tested before it starts
    (`credit_demo.common.startup`). A setting that cannot even be read, such
    as a counterparty key that is not a key, stops here too."""
    try:
        services = [_LOADERS[service](env) for service, env in envs.items()]
    except ValueError as exc:
        tell_the_operator(f"credit-demo: FAIL settings: {describe(exc)}")
        return False
    return startup(services)


def _not_started() -> int:
    tell_the_operator(
        "credit-demo: not started. Fix the settings named above, in the "
        "environment or in .env and the service's own .env file, then run "
        "credit-demo again."
    )
    return 1


def _replace_this_process(program: str, argv: list[str], env: dict[str, str]) -> None:
    os.execve(program, argv, env)


def serve_one(
    service: str,
    *,
    startup=run_startup_tests,
    execute: Callable[[str, list[str], dict[str, str]], None] = _replace_this_process,
) -> int:
    """One service alone, in this process's place, so a host's stop signal
    reaches it directly. Its settings are those of `service_env`, with the
    environment's `PORT` as its port when there is one. No service's key is
    derived from another's here: `COUNTERPARTY_PUBLIC_KEY` must be set."""
    env = service_env(service)
    # One service alone is the service a host's PORT is meant for.
    env["PORT"] = os.environ.get("PORT") or env["PORT"]
    env.setdefault("PYTHONUNBUFFERED", "1")
    if not test_services({service: env}, startup):
        return _not_started()
    tell_the_operator(f"credit-demo: every start-up test passed; starting the {service}")
    command = _service_command(service)
    execute(command[0], command, env)
    return 0


def main(argv: Sequence[str] | None = None, *, startup=run_startup_tests) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["keys"]:
        return write_keys()
    if len(argv) == 1 and argv[0] in SERVICES:
        return serve_one(argv[0], startup=startup)
    if argv:
        tell_the_operator(
            "credit-demo: the command takes no arguments to start all three "
            "services, `underwriter`, `checker` or `evidence-builder` to start "
            "one, or `keys` to write a fresh signing seed into each service's file"
        )
        return 2

    envs = {service: service_env(service) for service in SERVICES}
    # The services inherit this process's output, which is a pipe in a
    # container and under any launcher, and a pipe is block-buffered: a line
    # a service printed could sit unwritten until the buffer filled, and be
    # lost if the process was killed first. Set here, before the
    # subprocesses start, so a laptop run gets the same log as the image.
    for env in envs.values():
        env.setdefault("PYTHONUNBUFFERED", "1")
    for note in wire_counterparty_keys(envs[UNDERWRITER], envs[CHECKER]):
        tell_the_operator(f"credit-demo: {note} derived from the other side's signing seed")
    # Where the services find each other, for the local ports.
    envs[UNDERWRITER].setdefault("CHECKER_URL", f"http://localhost:{envs[CHECKER]['PORT']}")
    envs[UNDERWRITER].setdefault(
        "EVIDENCE_BUILDER_URL", f"http://localhost:{envs[EVIDENCE_BUILDER]['PORT']}"
    )
    envs[CHECKER].setdefault("DESK_URL", f"http://localhost:{envs[UNDERWRITER]['PORT']}")

    if not test_services(envs, startup):
        return _not_started()
    tell_the_operator("credit-demo: every start-up test passed; starting all three services")

    procs = {
        service: subprocess.Popen(_service_command(service), env=envs[service])
        for service in (EVIDENCE_BUILDER, CHECKER, UNDERWRITER)
    }
    tell_the_operator(
        f"credit-demo: underwriter (the credit desk) on http://{url_host(envs[UNDERWRITER])}:"
        f"{envs[UNDERWRITER]['PORT']}, checker on http://{url_host(envs[CHECKER])}:"
        f"{envs[CHECKER]['PORT']}, evidence builder on http://{url_host(envs[EVIDENCE_BUILDER])}:"
        f"{envs[EVIDENCE_BUILDER]['PORT']}. Ctrl+C stops all three."
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
