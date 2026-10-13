"""The kernel this suite runs is the kernel it pins, in every process.

The bar tests run `sanning-proof` in a
subprocess with a `cwd` of their own. With a relative `PYTHONPATH` that
subprocess can import the user site's kernel while the suite pins another,
and nothing says so. `conftest.py` hands every subprocess an absolute path
to this process's kernel, and this file fails when the pin, the
package this process imported, and the package a subprocess imports from
another directory are not one version.
"""

from __future__ import annotations

import importlib.metadata
import os
import re
import subprocess
import sys
from pathlib import Path

import sanning_proof

from credit_demo.common.documents import REPO_ROOT


def _pinned() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text()
    found = re.search(r'"sanning-proof==([^"]+)"', text)
    assert found, "pyproject.toml does not pin sanning-proof to one version"
    return found.group(1)


KERNEL_PIN = _pinned()


def kernel_command() -> str:
    return str(Path(sys.executable).parent / "sanning-proof")


def test_the_pin_and_this_process_are_one_version():
    assert importlib.metadata.version("sanning-proof") == KERNEL_PIN
    assert sanning_proof.__version__ == KERNEL_PIN


def test_a_subprocess_in_another_directory_imports_the_pinned_kernel(tmp_path):
    """The interpreter behind the `sanning-proof` command, run from a
    directory with nothing in it, with the environment every test hands its
    subprocesses."""
    script = Path(kernel_command())
    first = script.read_bytes().split(b"\n", 1)[0].decode("utf-8", "replace")
    interpreter = first[2:].strip().split()[0] if first.startswith("#!") else None
    assert interpreter, f"{script} is not a script this check can read its interpreter from"
    done = subprocess.run(
        [
            interpreter,
            "-c",
            "import importlib.metadata as m, sanning_proof; "
            "print(m.version('sanning-proof'), sanning_proof.__version__, sanning_proof.__file__)",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert done.returncode == 0, done.stderr
    version, declared, where = done.stdout.split()
    assert (version, declared) == (KERNEL_PIN, KERNEL_PIN), (version, declared, where)
    assert Path(where).resolve() == Path(sanning_proof.__file__).resolve(), where


def test_every_path_a_subprocess_is_handed_is_absolute():
    entries = [e for e in os.environ.get("PYTHONPATH", "").split(os.pathsep) if e]
    assert entries, "conftest.py did not set PYTHONPATH"
    assert all(os.path.isabs(entry) for entry in entries), entries
