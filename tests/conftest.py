"""Shared fixtures.

The artifact resolver in `sanning_anchor`'s LangChain callback handler only
records a file as an artifact when it resolves under one fixed root
(`credit_demo.common.sanning.recorder`, always the repo root). A run's
own workspace, where an agent's tools save what they write, must therefore
live under the repo, not under pytest's own
system tmp directory (which sits elsewhere entirely, e.g. under `/tmp`).
`repo_tmp_path` gives a test that same guarantee `tmp_path` gives, unique
and cleaned up, but rooted under this repo's own gitignored `runs/`."""

from __future__ import annotations

import os
import shutil
import threading
import uuid
from pathlib import Path

import pytest

from credit_demo.common.documents import REPO_ROOT


def _pin_the_kernel_for_subprocesses() -> None:
    """Every subprocess this suite starts imports the kernel THIS process imports.

    Tests run `sanning-proof` in a subprocess with a `cwd` of their own (the
    command `HOW-TO-VERIFY.txt` gives an auditor, run where the auditor runs
    it). A relative `PYTHONPATH` entry such as `.deps` then resolves against
    that `cwd`, finds nothing, and the subprocess imports whatever kernel the
    user site holds instead, silently, and the suite grades a different
    kernel from the one it pins. So every entry is made absolute here, and
    the directory of the kernel this process imported goes first.
    `tests/test_pinned_kernel.py` fails when a subprocess still imports a
    kernel other than the pinned one.
    """
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
