"""A case's evidence file, built by the SDK's `bundle --case` verb.

One command, with one `--agent` and one `--logs` for each agent, in the same
order. The verb selects every record each agent signed for the case, from
Sanning's index and the agent's folder in the log store. It seals each agent's
pack with the organization's hand-over key
(`SANNING_SIGNING_KEY_KIND=organization`) and signs each declaration with it.
Each record stays signed by its own agent. No agent's seed is read.

Exit 0 and exit 3 both mean the file was built (3 is a file verified in the
Sandbox). Exit 2 means the records are incomplete or not stamped yet; the
caller tries again later. Any other exit means the file could not be built.
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
