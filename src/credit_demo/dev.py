"""The `credit-demo` command: starts the agents locally, on a laptop and in a
container, with no orchestration package.

- `credit-demo` starts both agents as two plain subprocesses, each running its
  own service's `uvicorn` app. Ctrl+C stops both.
- `credit-demo underwriter` and `credit-demo checker` start one agent alone.
  This is the start command for a host that runs each agent as its own
  service. It reads the same files and the same environment, and tests that
  agent's settings first.

**Each agent reads the Quickstart's standard names.** On a laptop, `.env`
holds the settings both agents use and `underwriter.env` or `checker.env`
holds one agent's own (its ID, its signing seed, its counterparty key). The
launcher gives each agent the shared file plus its own; a value already in
the environment wins over both files, and an agent's own file wins over the
shared one. On a host, each service gets its settings from the host, and the
files are absent.

If the checker exits, the underwriter stays up: it serves the credit desk,
and a case it starts while the checker is down stops with the reason on
its page. If the underwriter exits, there is no page left to serve, so the
launcher stops.

**Every service binds to the loopback interface by default** (`bind_host`):
none of their endpoints takes credentials. `CREDIT_DEMO_HOST` changes that,
and the image sets it so that the underwriter's port can be reached and the
underwriter can reach the checker over the host's private network. The
underwriter reaches the checker over `CHECKER_URL`; the checker asks the
credit desk for each case's documents over `DESK_URL`. The launcher fills in
both for the two local ports when they are unset.

**Nothing starts until the agents' settings pass their start-up tests**
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
into each agent's file.
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
from .common.config import bind_host, public_key_of, read_env_file
from .common.errors import describe, tell_the_operator
from .common.startup import run_startup_tests
from .underwriter import config as underwriter_config

ENV_FILE = Path(".env")
UNDERWRITER = "underwriter"
CHECKER = "checker"
AGENTS = (UNDERWRITER, CHECKER)
_DEFAULT_PORT = {UNDERWRITER: "8001", CHECKER: "8002"}
_LOADERS = {UNDERWRITER: underwriter_config.load, CHECKER: checker_config.load}
_MODULES = {UNDERWRITER: "credit_demo.underwriter.web", CHECKER: "credit_demo.checker.web"}


def agent_file(agent: str) -> Path:
    """The file that holds one agent's own settings."""
    return Path(f"{agent}.env")


def agent_env(agent: str, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """One agent's settings: the shared file, then the agent's own file over
    it, then the environment over both. `PORT` is the agent's own, so the
    environment's `PORT` never reaches the launcher's two agents."""
    environ = os.environ if environ is None else environ
    own = read_env_file(agent_file(agent))
    env = {**read_env_file(ENV_FILE), **own, **environ}
    env["PORT"] = own.get("PORT") or read_env_file(ENV_FILE).get("PORT") or _DEFAULT_PORT[agent]
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
    """Each agent's own file, created if it is not there, with a fresh signing
    seed and the other agent's public key as its counterparty key. A value a
    file already has is kept. The seeds are secrets, so only the file names
    and the public keys are printed; a host that holds each agent's settings
    takes the public keys from here."""
    paths = {agent: directory / agent_file(agent) for agent in AGENTS}
    for agent, path in paths.items():
        if read_env_file(path).get("SANNING_SIGNING_SEED"):
            log(f"credit-demo: {path} already has a signing seed; left as it is")
            continue
        lines = [] if path.exists() else [f"SANNING_AGENT_ID={agent}-sandbox"]
        lines.append(f"SANNING_SIGNING_SEED={os.urandom(32).hex()}")
        _append(path, lines)
        log(f"credit-demo: wrote a signing seed to {path}")
    keys = {
        agent: public_key_of(read_env_file(path).get("SANNING_SIGNING_SEED", ""))
        for agent, path in paths.items()
    }
    for agent, other in ((UNDERWRITER, CHECKER), (CHECKER, UNDERWRITER)):
        if keys[other] and not read_env_file(paths[agent]).get("COUNTERPARTY_PUBLIC_KEY"):
            _append(paths[agent], [f"COUNTERPARTY_PUBLIC_KEY={keys[other]}"])
    for agent, key in keys.items():
        if key:
            log(f"credit-demo: the {agent}'s public key is {key}")
    return 0


def _append(path: Path, lines: list[str]) -> None:
    with open(path, "a", opener=lambda name, flags: os.open(name, flags, 0o600)) as out:
        out.write("\n".join(lines) + "\n")


def _service_command(agent: str, env: Mapping[str, str]) -> list[str]:
    return [
        sys.executable, "-m", "uvicorn", f"{_MODULES[agent]}:create_app", "--factory",
        "--host", bind_host(env), "--port", env["PORT"],
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


def test_services(envs: Mapping[str, Mapping[str, str]], startup=run_startup_tests) -> bool:
    """Each named agent's settings, tested before it starts
    (`credit_demo.common.startup`). A setting that cannot even be read, such
    as a counterparty key that is not a key, stops here too."""
    try:
        services = [_LOADERS[agent](env) for agent, env in envs.items()]
    except ValueError as exc:
        tell_the_operator(f"credit-demo: FAIL settings: {describe(exc)}")
        return False
    return startup(services)


def _not_started() -> int:
    tell_the_operator(
        "credit-demo: not started. Fix the settings named above, in the "
        "environment or in .env and the agent's own .env file, then run "
        "credit-demo again."
    )
    return 1


def _replace_this_process(program: str, argv: list[str], env: dict[str, str]) -> None:
    os.execve(program, argv, env)


def serve_one(
    agent: str,
    *,
    startup=run_startup_tests,
    execute: Callable[[str, list[str], dict[str, str]], None] = _replace_this_process,
) -> int:
    """One agent alone, in this process's place, so a host's stop signal
    reaches it directly. Its settings are those of `agent_env`, with the
    environment's `PORT` as its port when there is one. Neither
    agent's key is derived from the other's here: `COUNTERPARTY_PUBLIC_KEY`
    must be set."""
    env = agent_env(agent)
    # One service alone is the service a host's PORT is meant for.
    env["PORT"] = os.environ.get("PORT") or env["PORT"]
    env.setdefault("PYTHONUNBUFFERED", "1")
    if not test_services({agent: env}, startup):
        return _not_started()
    tell_the_operator(f"credit-demo: every start-up test passed; starting the {agent}")
    command = _service_command(agent, env)
    execute(command[0], command, env)
    return 0


def main(argv: Sequence[str] | None = None, *, startup=run_startup_tests) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["keys"]:
        return write_keys()
    if len(argv) == 1 and argv[0] in AGENTS:
        return serve_one(argv[0], startup=startup)
    if argv:
        tell_the_operator(
            "credit-demo: the command takes no arguments to start both agents, "
            "`underwriter` or `checker` to start one, or `keys` to write a "
            "fresh signing seed into each agent's file"
        )
        return 2

    envs = {agent: agent_env(agent) for agent in AGENTS}
    # Both services inherit this process's output, which is a pipe in a
    # container and under any launcher, and a pipe is block-buffered: a line
    # a service printed could sit unwritten until the buffer filled, and be
    # lost if the process was killed first. Set here, before the two
    # subprocesses start, so a laptop run gets the same log as the image.
    for env in envs.values():
        env.setdefault("PYTHONUNBUFFERED", "1")
    for note in wire_counterparty_keys(envs[UNDERWRITER], envs[CHECKER]):
        tell_the_operator(f"credit-demo: {note} derived from the other side's signing seed")
    # Where each agent finds the other, for the two local ports.
    envs[UNDERWRITER].setdefault("CHECKER_URL", f"http://localhost:{envs[CHECKER]['PORT']}")
    envs[CHECKER].setdefault("DESK_URL", f"http://localhost:{envs[UNDERWRITER]['PORT']}")

    if not test_services(envs, startup):
        return _not_started()
    tell_the_operator("credit-demo: every start-up test passed; starting both services")

    procs = {
        agent: subprocess.Popen(_service_command(agent, envs[agent]), env=envs[agent])
        for agent in (CHECKER, UNDERWRITER)
    }
    tell_the_operator(
        f"credit-demo: underwriter (the credit desk) on http://{bind_host(envs[UNDERWRITER])}:"
        f"{envs[UNDERWRITER]['PORT']}, checker on http://{bind_host(envs[CHECKER])}:"
        f"{envs[CHECKER]['PORT']}. Ctrl+C stops both."
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
