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


# ── The keys, and where the services listen ────────────────────────────────

import runpy
import sys

import pytest
from nacl.signing import SigningKey

from credit_demo import dev
from credit_demo.common.config import settings_from_env


def _public_key(seed_hex: str) -> str:
    return SigningKey(bytes.fromhex(seed_hex)).verify_key.encode().hex()


def test_the_launcher_wires_each_side_to_the_other_sides_key():
    """On one machine both seeds are in one `.env`, so the one command sets
    each side's counterparty key from the other side's seed, and a run then
    has what it needs without the operator copying keys by hand."""
    env = {"UNDERWRITER_SANNING_SIGNING_SEED": "11" * 32, "CHECKER_SANNING_SIGNING_SEED": "22" * 32}

    wired = dev.wire_counterparty_keys(env)

    assert sorted(wired) == ["CHECKER_COUNTERPARTY_PUBLIC_KEY", "UNDERWRITER_COUNTERPARTY_PUBLIC_KEY"]
    assert env["UNDERWRITER_COUNTERPARTY_PUBLIC_KEY"] == _public_key("22" * 32)
    assert env["CHECKER_COUNTERPARTY_PUBLIC_KEY"] == _public_key("11" * 32)


def test_the_launcher_never_replaces_a_key_the_operator_set():
    """Two machines hold two `.env` files, and there the key the operator
    copied from the other side's `/health` is the pin: deriving over it
    would pin the service to a seed it does not deal with."""
    env = {
        "UNDERWRITER_SANNING_SIGNING_SEED": "11" * 32,
        "CHECKER_SANNING_SIGNING_SEED": "22" * 32,
        "UNDERWRITER_COUNTERPARTY_PUBLIC_KEY": "ab" * 32,
    }

    wired = dev.wire_counterparty_keys(env)

    assert wired == ["CHECKER_COUNTERPARTY_PUBLIC_KEY"]
    assert env["UNDERWRITER_COUNTERPARTY_PUBLIC_KEY"] == "ab" * 32


@pytest.mark.parametrize("seed", ["", "not hex", "ab" * 16])
def test_a_seed_that_is_not_one_wires_nothing(seed):
    env = {"CHECKER_SANNING_SIGNING_SEED": seed}

    assert dev.wire_counterparty_keys(env) == []
    assert "UNDERWRITER_COUNTERPARTY_PUBLIC_KEY" not in env


def test_generated_keys_are_a_pair_of_seeds_the_launcher_turns_into_a_working_pair():
    """`credit-demo keys` prints two seeds and nothing else: the launcher
    derives each side's counterparty key from the other side's seed."""
    lines = dev.generate_keys()
    env = dict(line.split("=", 1) for line in lines)

    assert sorted(env) == ["CHECKER_SANNING_SIGNING_SEED", "UNDERWRITER_SANNING_SIGNING_SEED"]
    assert env["UNDERWRITER_SANNING_SIGNING_SEED"] != env["CHECKER_SANNING_SIGNING_SEED"]
    dev.wire_counterparty_keys(env)
    assert env["UNDERWRITER_COUNTERPARTY_PUBLIC_KEY"] == _public_key(env["CHECKER_SANNING_SIGNING_SEED"])
    assert env["CHECKER_COUNTERPARTY_PUBLIC_KEY"] == _public_key(env["UNDERWRITER_SANNING_SIGNING_SEED"])
    for prefix in ("UNDERWRITER", "CHECKER"):
        settings = settings_from_env(
            service=prefix.lower(), prefix=prefix, default_agent_id="a", default_port=1, env=env
        )
        assert f"{prefix}_COUNTERPARTY_PUBLIC_KEY" not in settings.ready_to_record()


def test_the_keys_command_prints_the_lines_for_env(capsys):
    assert dev.main(["keys"]) == 0

    printed = capsys.readouterr().out.splitlines()
    assert sorted(line.split("=", 1)[0] for line in printed) == [
        "CHECKER_SANNING_SIGNING_SEED",
        "UNDERWRITER_SANNING_SIGNING_SEED",
    ]


def test_the_underwriter_binds_to_loopback_unless_the_operator_says_otherwise(monkeypatch):
    """None of the endpoints takes credentials, so a laptop run is not served
    to the network it sits on. The underwriter is the one service a
    platform's network has to reach, so it is the one `CREDIT_DEMO_HOST`
    governs."""
    monkeypatch.delenv("CREDIT_DEMO_HOST", raising=False)
    command = dev._service_command(
        "credit_demo.underwriter.web", "UNDERWRITER_PORT", "8001", service="underwriter"
    )
    assert command[command.index("--host") + 1] == "127.0.0.1"

    monkeypatch.setenv("CREDIT_DEMO_HOST", "0.0.0.0")
    command = dev._service_command(
        "credit_demo.underwriter.web", "UNDERWRITER_PORT", "8001", service="underwriter"
    )
    assert command[command.index("--host") + 1] == "0.0.0.0"


def test_the_checker_binds_to_loopback_always(monkeypatch):
    """The underwriter reaches the checker over `CHECKER_URL`, loopback in
    the same container as on a laptop, so `CREDIT_DEMO_HOST` never widens the
    checker's own interface: nothing else is ever meant to reach it."""
    monkeypatch.setenv("CREDIT_DEMO_HOST", "0.0.0.0")
    command = dev._service_command(
        "credit_demo.checker.web", "CHECKER_PORT", "8002", service="checker"
    )
    assert command[command.index("--host") + 1] == "127.0.0.1"


@pytest.mark.parametrize("module", ["credit_demo.checker", "credit_demo.underwriter"])
def test_each_service_on_its_own_binds_to_loopback_by_default(monkeypatch, module, repo_tmp_path):
    import uvicorn

    seen = {}
    monkeypatch.delenv("CREDIT_DEMO_HOST", raising=False)
    # The app is built for real, and the underwriter picks up any audit trail
    # left preparing in its cases folder: never the checkout's own.
    monkeypatch.setenv("UNDERWRITER_RUNS_DIR", str(repo_tmp_path / "underwriter"))
    monkeypatch.setenv("CHECKER_RUNS_DIR", str(repo_tmp_path / "checker"))
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: seen.update(kwargs))
    monkeypatch.setattr(sys, "argv", [module])

    with pytest.raises(SystemExit):
        runpy.run_module(module, run_name="__main__")

    assert seen["host"] == "127.0.0.1", seen


def test_the_checkers_own_main_ignores_credit_demo_host(monkeypatch):
    """`python -m credit_demo.checker` under `CREDIT_DEMO_HOST=0.0.0.0`, the
    exact setting the container's Dockerfile sets: the checker still binds to
    loopback, because it is never the service that variable governs."""
    import uvicorn

    seen = {}
    monkeypatch.setenv("CREDIT_DEMO_HOST", "0.0.0.0")
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: seen.update(kwargs))
    monkeypatch.setattr(sys, "argv", ["credit_demo.checker"])

    with pytest.raises(SystemExit):
        runpy.run_module("credit_demo.checker", run_name="__main__")

    assert seen["host"] == "127.0.0.1", seen


def test_the_one_command_wires_the_keys_before_either_service_starts(monkeypatch):
    """The whole path: `.env` holds the two seeds and no counterparty key,
    and by the time the first service process starts, both keys are in the
    environment it inherits."""
    import os

    class Stop(Exception):
        pass

    seen = {}

    def popen(command, *args, **kwargs):
        seen.update({k: os.environ.get(k) for k in (
            "UNDERWRITER_COUNTERPARTY_PUBLIC_KEY", "CHECKER_COUNTERPARTY_PUBLIC_KEY"
        )})
        raise Stop

    for name in ("UNDERWRITER_COUNTERPARTY_PUBLIC_KEY", "CHECKER_COUNTERPARTY_PUBLIC_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("UNDERWRITER_SANNING_SIGNING_SEED", "11" * 32)
    monkeypatch.setenv("CHECKER_SANNING_SIGNING_SEED", "22" * 32)
    monkeypatch.setattr(dev, "load_env_file", lambda _path: None)
    monkeypatch.setattr(dev.subprocess, "Popen", popen)

    with pytest.raises(Stop):
        dev.main([], startup=lambda _services: True)

    assert seen == {
        "UNDERWRITER_COUNTERPARTY_PUBLIC_KEY": _public_key("22" * 32),
        "CHECKER_COUNTERPARTY_PUBLIC_KEY": _public_key("11" * 32),
    }


def test_a_value_appended_after_an_empty_one_still_wins(tmp_path, monkeypatch):
    """`cp .env.example .env` ships every seed empty so a first run starts
    clean; `credit-demo keys >> .env` appends the real ones after them, in
    the same file, under the same names. Plain `setdefault` on every line in
    order would let the first, empty line claim the key, so the second, real
    line would be a no-op and every appended value would be lost."""
    from credit_demo.common.config import load_env_file

    env_file = tmp_path / ".env"
    env_file.write_text(
        "UNDERWRITER_SANNING_SIGNING_SEED=\n"
        "CHECKER_SANNING_SIGNING_SEED=\n"
        "UNDERWRITER_SANNING_SIGNING_SEED=11" + "1" * 62 + "\n"
        "CHECKER_SANNING_SIGNING_SEED=22" + "2" * 62 + "\n"
    )
    for name in ("UNDERWRITER_SANNING_SIGNING_SEED", "CHECKER_SANNING_SIGNING_SEED"):
        monkeypatch.delenv(name, raising=False)

    load_env_file(env_file)

    import os

    assert os.environ["UNDERWRITER_SANNING_SIGNING_SEED"] == "11" + "1" * 62
    assert os.environ["CHECKER_SANNING_SIGNING_SEED"] == "22" + "2" * 62


def test_a_value_already_set_is_never_overridden_by_the_file(tmp_path, monkeypatch):
    """The docstring's other half: what is already in the environment wins
    over the file, empty file line or not."""
    from credit_demo.common.config import load_env_file

    env_file = tmp_path / ".env"
    env_file.write_text("SOME_KEY=from-the-file\n")
    monkeypatch.setenv("SOME_KEY", "from-the-environment")

    load_env_file(env_file)

    import os

    assert os.environ["SOME_KEY"] == "from-the-environment"


def test_nothing_starts_when_a_start_up_test_fails(monkeypatch):
    """A failed start-up test stops the launcher before either service
    process exists, with a non-zero exit."""
    started = []
    monkeypatch.setattr(dev, "load_env_file", lambda _path: None)
    monkeypatch.setattr(dev.subprocess, "Popen", lambda *a, **k: started.append(a))

    assert dev.main([], startup=lambda _services: False) == 1
    assert started == []


def test_a_counterparty_key_that_is_not_a_key_stops_the_launcher(monkeypatch):
    """Settings that cannot even be read stop the launcher the same way,
    naming the variable, before any test reaches the network."""
    lines = []
    monkeypatch.setattr(dev, "tell_the_operator", lines.append)
    monkeypatch.setenv("UNDERWRITER_COUNTERPARTY_PUBLIC_KEY", "not-a-key")

    assert dev.test_both_services(startup=lambda _services: True) is False
    assert any("UNDERWRITER_COUNTERPARTY_PUBLIC_KEY" in line for line in lines)


def test_the_command_the_launcher_runs_loads_each_service(tmp_path):
    """The launcher starts each service with uvicorn. The target it names
    must load: a web module without it would fail only at a real start."""
    import importlib

    from credit_demo.dev import _service_command

    for module in ("credit_demo.underwriter.web", "credit_demo.checker.web"):
        command = _service_command(module, "UNUSED_PORT", "0", service=module.split(".")[1])
        target = command[command.index("uvicorn") + 1]
        assert "--factory" in command
        name, attribute = target.split(":")
        assert callable(getattr(importlib.import_module(name), attribute))
