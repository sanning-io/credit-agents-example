"""A case's evidence file, built by the SDK's `bundle --case` verb with one `--agent`
and one `--logs` per agent, in the same order. The hand-over key seals each pack
and signs each declaration; each record stays signed by its own agent. Exit 0 and
3 mean built (3 is a file verified in the Sandbox), 2 means incomplete or not
stamped yet (try again later), and any other exit means it could not be built.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..common.sanning import log_location
from .config import EvidenceBuilderSettings

BUILT = (0, 3)
NOT_STAMPED_YET = 2


@dataclass
class BundleRun:
    """One run of the SDK's `bundle` verb: its exit code, and what it said."""

    code: int
    errors: list[str] = field(default_factory=list)


RunBundle = Callable[[list[str]], BundleRun]


def run_bundle_command(args: list[str]) -> BundleRun:
    """`sanning-anchor bundle`, the SDK's own command, as a subprocess. It
    inherits this service's environment: it reads the API key, the hand-over
    seed, its kind and the base URL from the names the service reads, and the
    bucket from the standard AWS names. Nothing is copied or renamed on the
    way."""
    command = Path(sys.executable).parent / "sanning-anchor"
    ran = subprocess.run(
        [str(command), "bundle", *args],
        capture_output=True,
        text=True,
    )
    return BundleRun(code=ran.returncode, errors=[line for line in ran.stderr.splitlines() if line])


def bundle_arguments(settings: EvidenceBuilderSettings, *, case_id: str, out: Path) -> list[str]:
    args = ["--case", case_id]
    for agent_id in settings.agent_ids:
        args += ["--agent", agent_id]
    for agent_id in settings.agent_ids:
        args += ["--logs", log_location(settings, agent_id)]
    return [*args, "--out", str(out)]


def build_case_file(
    run: RunBundle, settings: EvidenceBuilderSettings, *, case_id: str, out: Path
) -> BundleRun:
    return run(bundle_arguments(settings, case_id=case_id, out=out))
