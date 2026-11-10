"""Shared fixtures. The recorder only records a file that resolves under the repo root, so a run's
workspace must live under the repo, not under pytest's system temp directory. `repo_tmp_path` is
`tmp_path` rooted under the repo's gitignored `runs/`."""

from __future__ import annotations

import os
import shutil
import threading
import uuid
from pathlib import Path

import pytest

from credit_demo.common.documents import REPO_ROOT


def _pin_the_kernel_for_subprocesses() -> None:
    """Make every subprocess import the kernel this process imported. A relative `PYTHONPATH` entry
    resolves against a subprocess's own `cwd` and can pick up a different kernel, so each entry
    is made absolute and this process's kernel goes first. `tests/test_pinned_kernel.py` checks
    it."""
    import sanning_proof

    kernel_root = str(Path(sanning_proof.__file__).resolve().parent.parent)
    entries = [
        str(Path(entry).resolve())
        for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep)
        if entry
    ]
    os.environ["PYTHONPATH"] = os.pathsep.join(
        [kernel_root] + [entry for entry in entries if entry != kernel_root]
    )


_pin_the_kernel_for_subprocesses()


# The threads the credit desk starts for a case (`in_background` in
# `credit_demo.underwriter.web`), named `<work>:<case id>`.
CASE_THREADS = ("run_case:", "prepare_file:")
CASE_THREAD_WAIT_S = 60.0


def _case_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith(CASE_THREADS)]


def wait_for_case_threads() -> list[str]:
    """Wait for every case thread alive now; the names of any that will not
    end."""
    threads = _case_threads()
    for thread in threads:
        thread.join(CASE_THREAD_WAIT_S)
    return [t.name for t in threads if t.is_alive()]


@pytest.fixture(autouse=True)
def each_agent_knows_the_other(monkeypatch):
    """An agent started alone must be told the other's address, and the
    underwriter the evidence builder's; a test that checks for their absence
    deletes these. The evidence builder's address is one nothing answers at, so
    a test that wants a case file serves one itself."""
    monkeypatch.setenv("CHECKER_URL", "http://localhost:8002")
    monkeypatch.setenv("DESK_URL", "http://localhost:8001")
    monkeypatch.setenv("EVIDENCE_BUILDER_URL", "http://127.0.0.1:9")


@pytest.fixture(autouse=True)
def the_desk_does_not_wait_minutes_for_a_case_file(monkeypatch):
    """The credit desk asks for a case file every `TRY_EVERY_S` seconds for up
    to `TRIES` times. A case that closes with no evidence builder to ask would
    hold its test for fifteen minutes, so the tries come at once and few."""
    from credit_demo.underwriter import audit_trail

    monkeypatch.setattr(audit_trail, "TRY_EVERY_S", 0.0)
    monkeypatch.setattr(audit_trail, "TRIES", 3)


@pytest.fixture(autouse=True)
def no_case_thread_outlives_its_test():
    """`POST /cases` starts the case in a background thread. A test that ends
    while it runs lets its folder be deleted under it, and the thread raises
    in the middle of whichever test runs next. The folder
    fixtures below wait for the case threads before they delete anything, and
    this fails the test if one will not end."""
    yield
    assert wait_for_case_threads() == [], "a case thread outlived its test"


@pytest.fixture
def repo_tmp_path() -> Path:
    path = REPO_ROOT / "runs" / f"pytest-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        wait_for_case_threads()
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(scope="module")
def repo_tmp_module() -> Path:
    """`repo_tmp_path`, shared by every test in one module: for a module
    that runs one case once and reads it from several tests."""
    path = REPO_ROOT / "runs" / f"pytest-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        wait_for_case_threads()
        shutil.rmtree(path, ignore_errors=True)
