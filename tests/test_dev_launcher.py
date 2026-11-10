"""The one command supervises two services, and it treats them differently.

The underwriter serves the credit desk, so its exit ends the run. The
checker does not, so its exit leaves the underwriter up, and the desk shows
why a case stopped.
"""

from __future__ import annotations

from credit_demo.dev import supervise


class FakeProc:
    """A subprocess stand-in whose exit code appears after a set number of polls."""

    def __init__(self, exits_after: int | None = None, code: int = -9) -> None:
        self.exits_after = exits_after
        self.code = code
        self.polls = 0

    def poll(self) -> int | None:
        self.polls += 1
        if self.exits_after is not None and self.polls > self.exits_after:
            return self.code
        return None


def _run(procs, max_ticks: int = 8):
    """Drive supervise with a fake clock that stops it after a few ticks."""
    stop = {"requested": False}
    lines: list[str] = []
    ticks = {"n": 0}

    def sleep(_seconds: float) -> None:
        ticks["n"] += 1
        if ticks["n"] >= max_ticks:
            stop["requested"] = True

    return supervise(procs, stop, sleep=sleep, log=lines.append), lines


def test_a_dead_checker_leaves_the_underwriter_running():
    procs = {"underwriter": FakeProc(), "checker": FakeProc(exits_after=1)}
    code, lines = _run(procs)

    assert code == 0
    assert procs["underwriter"].polls > 2, "the underwriter kept being supervised"
    assert any("checker exited" in line for line in lines)
    assert any("underwriter stays up" in line for line in lines)
    assert not any("stopping the checker too" in line for line in lines)


def test_the_checker_is_reported_once_and_not_on_every_tick():
    procs = {"underwriter": FakeProc(), "checker": FakeProc(exits_after=1)}
    _code, lines = _run(procs)

    assert len([line for line in lines if "checker exited" in line]) == 1


def test_a_dead_underwriter_stops_the_launcher():
    procs = {"underwriter": FakeProc(exits_after=1, code=3), "checker": FakeProc()}
    code, lines = _run(procs)

    assert code == 3
    assert any("stopping the checker too" in line for line in lines)


# ── The keys, the files, and where the services listen ─────────────────────

import os
import runpy
import sys

import pytest
from nacl.signing import SigningKey

from credit_demo import dev
from credit_demo.common.config import read_env_file, settings_from_env


def _public_key(seed_hex: str) -> str:
    return SigningKey(bytes.fromhex(seed_hex)).verify_key.encode().hex()


def test_the_launcher_wires_each_side_to_the_other_sides_key():
    """On one machine both seeds are at hand, so the one command sets each
    side's counterparty key from the other side's seed, and a run then has
    what it needs without the operator copying keys by hand."""
    underwriter = {"SANNING_SIGNING_SEED": "11" * 32}
    checker = {"SANNING_SIGNING_SEED": "22" * 32}

    wired = dev.wire_counterparty_keys(underwriter, checker)

    assert len(wired) == 2
    assert underwriter["COUNTERPARTY_PUBLIC_KEY"] == _public_key("22" * 32)
    assert checker["COUNTERPARTY_PUBLIC_KEY"] == _public_key("11" * 32)


def test_the_launcher_never_replaces_a_key_the_operator_set():
    """Two hosts hold two sets of settings, and there the key the operator
    copied from the other side's `/health` is the pin: deriving over it
    would pin the service to a seed it does not deal with."""
    underwriter = {"SANNING_SIGNING_SEED": "11" * 32, "COUNTERPARTY_PUBLIC_KEY": "ab" * 32}
    checker = {"SANNING_SIGNING_SEED": "22" * 32}

    wired = dev.wire_counterparty_keys(underwriter, checker)

    assert wired == ["checker: COUNTERPARTY_PUBLIC_KEY"]
    assert underwriter["COUNTERPARTY_PUBLIC_KEY"] == "ab" * 32


@pytest.mark.parametrize("seed", ["", "not hex", "ab" * 16])
def test_a_seed_that_is_not_one_wires_nothing(seed):
    underwriter: dict[str, str] = {}
    checker = {"SANNING_SIGNING_SEED": seed}

    assert dev.wire_counterparty_keys(underwriter, checker) == []
    assert "COUNTERPARTY_PUBLIC_KEY" not in underwriter


def test_the_keys_command_writes_each_agents_file(tmp_path):
    """`credit-demo keys` creates `underwriter.env` and `checker.env`: each
    has its own ID and seed, and the other agent's public key. It prints the
    public keys and no seed."""
    lines: list[str] = []

    assert dev.write_keys(tmp_path, log=lines.append) == 0

    underwriter = read_env_file(tmp_path / "underwriter.env")
    checker = read_env_file(tmp_path / "checker.env")
    assert underwriter["SANNING_SIGNING_SEED"] != checker["SANNING_SIGNING_SEED"]
    assert underwriter["SANNING_AGENT_ID"] != checker["SANNING_AGENT_ID"]
    assert underwriter["COUNTERPARTY_PUBLIC_KEY"] == _public_key(checker["SANNING_SIGNING_SEED"])
    assert checker["COUNTERPARTY_PUBLIC_KEY"] == _public_key(underwriter["SANNING_SIGNING_SEED"])
    printed = "\n".join(lines)
    assert underwriter["SANNING_SIGNING_SEED"] not in printed
    assert checker["SANNING_SIGNING_SEED"] not in printed
    assert checker["COUNTERPARTY_PUBLIC_KEY"] in printed
    for env in (underwriter, checker):
        assert "COUNTERPARTY_PUBLIC_KEY" not in settings_from_env(
            service="x", default_agent_id="a", default_port=1, env=env
        ).ready_to_record()
    assert (tmp_path / "checker.env").stat().st_mode & 0o077 == 0, "a seed file is private"


def test_the_keys_command_keeps_what_a_file_already_has(tmp_path):
    (tmp_path / "checker.env").write_text("SANNING_SIGNING_SEED=" + "cc" * 32 + "\n")

    dev.write_keys(tmp_path, log=lambda _line: None)

    checker = read_env_file(tmp_path / "checker.env")
    underwriter = read_env_file(tmp_path / "underwriter.env")
    assert checker["SANNING_SIGNING_SEED"] == "cc" * 32
    assert underwriter["COUNTERPARTY_PUBLIC_KEY"] == _public_key("cc" * 32)


def test_each_agent_gets_the_shared_file_then_its_own(tmp_path, monkeypatch):
    """The shared `.env` holds what both agents use; an agent's own file
    holds its identity and wins over the shared one."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SANNING_API_KEY=shared\nMODEL=m\nSANNING_AGENT_ID=shared-id\n")
    (tmp_path / "checker.env").write_text("SANNING_AGENT_ID=checker-local\nPORT=9002\n")

    underwriter = dev.agent_env("underwriter", {})
    checker = dev.agent_env("checker", {})

    assert (underwriter["SANNING_API_KEY"], checker["SANNING_API_KEY"]) == ("shared", "shared")
    assert underwriter["SANNING_AGENT_ID"] == "shared-id"
    assert checker["SANNING_AGENT_ID"] == "checker-local"
    assert (underwriter["PORT"], checker["PORT"]) == ("8001", "9002")


def test_the_environment_wins_over_both_files_except_for_the_port(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SANNING_API_KEY=from-the-file\n")

    env = dev.agent_env("underwriter", {"SANNING_API_KEY": "from-the-environment", "PORT": "1"})

    assert env["SANNING_API_KEY"] == "from-the-environment"
    assert env["PORT"] == "8001", "a PORT in the environment is for one service, not for the launcher's two"


def test_a_value_appended_after_an_empty_one_still_wins(tmp_path):
    """`cp .env.example .env` ships some settings empty so a first run starts
    clean; a line appended after them must still count, so an empty line
    does not claim the key."""
    env_file = tmp_path / ".env"
    env_file.write_text("SANNING_SIGNING_SEED=\nSANNING_SIGNING_SEED=11" + "1" * 62 + "\n")

    assert read_env_file(env_file) == {"SANNING_SIGNING_SEED": "11" + "1" * 62}


def test_a_value_already_set_is_never_overridden_by_the_file(tmp_path, monkeypatch):
    from credit_demo.common.config import load_env_files

    env_file = tmp_path / ".env"
    env_file.write_text("SOME_KEY=from-the-file\n")
    monkeypatch.setenv("SOME_KEY", "from-the-environment")

    load_env_files(env_file)

    assert os.environ["SOME_KEY"] == "from-the-environment"


def test_the_launcher_starts_each_service_with_its_own_module():
    """The interface and port travel in the environment the service starts
    with, so the command names neither."""
    for agent in ("underwriter", "checker"):
        command = dev._service_command(agent)
        assert command[1:] == ["-m", f"credit_demo.{agent}"]


@pytest.mark.parametrize("module", ["credit_demo.checker", "credit_demo.underwriter"])
def test_each_service_on_its_own_binds_to_loopback_by_default(monkeypatch, module, repo_tmp_path):
    import uvicorn

    seen = {}
    monkeypatch.delenv("CREDIT_DEMO_HOST", raising=False)
    # The app is built for real, and the underwriter picks up any audit trail
    # left preparing in its cases folder: never the checkout's own.
    monkeypatch.setenv("RUNS_DIR", str(repo_tmp_path / "runs"))
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: seen.update(kwargs))
    monkeypatch.setattr(sys, "argv", [module])

    with pytest.raises(SystemExit):
        runpy.run_module(module, run_name="__main__")

    assert seen["host"] == "127.0.0.1", seen


@pytest.mark.parametrize("module", ["credit_demo.checker", "credit_demo.underwriter"])
def test_each_service_follows_credit_demo_host(monkeypatch, module, repo_tmp_path):
    """Two containers: the checker is on the host's private network for the
    underwriter to reach, and the image sets `CREDIT_DEMO_HOST=0.0.0.0` for
    both."""
    import uvicorn

    seen = {}
    monkeypatch.setenv("CREDIT_DEMO_HOST", "0.0.0.0")
    monkeypatch.setenv("RUNS_DIR", str(repo_tmp_path / "runs"))
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: seen.update(kwargs))
    monkeypatch.setattr(sys, "argv", [module])

    with pytest.raises(SystemExit):
        runpy.run_module(module, run_name="__main__")

    assert seen["host"] == "0.0.0.0", seen


def test_the_one_command_wires_the_keys_before_either_service_starts(tmp_path, monkeypatch):
    """The whole path: the two agents' files hold the two seeds and no
    counterparty key, and by the time the services start, each gets the
    other's key and the other's address."""

    class Stop(Exception):
        pass

    seen = {}

    def popen(command, *args, env=None, **kwargs):
        seen[command[-1]] = env
        if len(seen) == 2:
            raise Stop
        return object()

    monkeypatch.chdir(tmp_path)
    (tmp_path / "underwriter.env").write_text("SANNING_SIGNING_SEED=" + "11" * 32 + "\n")
    (tmp_path / "checker.env").write_text("SANNING_SIGNING_SEED=" + "22" * 32 + "\nPORT=9002\n")
    for name in ("COUNTERPARTY_PUBLIC_KEY", "CHECKER_URL", "DESK_URL", "CREDIT_DEMO_HOST", "PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(dev.subprocess, "Popen", popen)

    with pytest.raises(Stop):
        dev.main([], startup=lambda _services: True)

    underwriter = seen["credit_demo.underwriter"]
    checker = seen["credit_demo.checker"]
    assert underwriter["COUNTERPARTY_PUBLIC_KEY"] == _public_key("22" * 32)
    assert checker["COUNTERPARTY_PUBLIC_KEY"] == _public_key("11" * 32)
    assert underwriter["CHECKER_URL"] == "http://localhost:9002"
    assert checker["DESK_URL"] == "http://localhost:8001"
    assert (underwriter["PORT"], checker["PORT"]) == ("8001", "9002")
    assert underwriter["SANNING_SIGNING_SEED"] != checker["SANNING_SIGNING_SEED"]


def test_nothing_starts_when_a_start_up_test_fails(tmp_path, monkeypatch):
    """A failed start-up test stops the launcher before either service
    process exists, with a non-zero exit."""
    started = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dev.subprocess, "Popen", lambda *a, **k: started.append(a))

    assert dev.main([], startup=lambda _services: False) == 1
    assert started == []


def test_a_counterparty_key_that_is_not_a_key_stops_the_launcher(tmp_path, monkeypatch):
    """Settings that cannot even be read stop the launcher the same way,
    naming the variable, before any test reaches the network."""
    lines = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dev, "tell_the_operator", lines.append)
    monkeypatch.setenv("COUNTERPARTY_PUBLIC_KEY", "not-a-key")

    assert dev.test_services({"underwriter": dev.agent_env("underwriter")}, startup=lambda _s: True) is False
    assert any("COUNTERPARTY_PUBLIC_KEY" in line for line in lines)


def test_one_agent_alone_tests_only_itself_and_starts_only_itself(tmp_path, monkeypatch):
    """`credit-demo checker` is the start command of a service that runs the
    checker alone: it tests only the checker, then starts only the checker,
    on the interface and port the host names."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CREDIT_DEMO_HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "4321")
    tested, started = [], []

    code = dev.serve_one(
        "checker",
        startup=lambda services: tested.extend(s.service for s in services) or True,
        execute=lambda program, argv, env: started.append((argv, env)),
    )

    assert code == 0
    assert tested == ["checker"]
    ((argv, env),) = started
    assert argv[-1] == "credit_demo.checker"
    assert env["CREDIT_DEMO_HOST"] == "0.0.0.0"
    assert env["PORT"] == "4321"


def test_one_agent_alone_does_not_start_when_a_start_up_test_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    started = []

    code = dev.serve_one("underwriter", startup=lambda _s: False, execute=lambda *a: started.append(a))

    assert code == 1
    assert started == []


def test_the_command_the_launcher_runs_loads_each_service():
    """The module the launcher names must load and have a `main`: a module
    without one would fail only at a real start."""
    import importlib

    for agent in ("underwriter", "checker"):
        command = dev._service_command(agent)
        assert callable(importlib.import_module(command[-1] + ".__main__").main)


def test_the_start_up_line_writes_an_ipv6_interface_as_a_url_does():
    assert dev.url_host({"CREDIT_DEMO_HOST": "::"}) == "[::]"
    assert dev.url_host({"CREDIT_DEMO_HOST": "0.0.0.0"}) == "0.0.0.0"
    assert dev.url_host({}) == "127.0.0.1"
