"""This agent's pack for one case, built by the SDK's `bundle` verb.

`bundle --case` selects every record this agent signed for the case, from
Sanning's index and the agent's own bucket, and signs a declaration of what
the pack holds with this agent's own key. Exit 0 and exit 3 both mean the
pack was built (3 is a pack verified in the sandbox). Exit 2 means the
records are incomplete or not stamped yet; the caller tries again later. Any
other exit means the pack could not be built.
"""

from __future__ import annotations

from pathlib import Path

from .config import ServiceSettings
from .sanning import BundleRun, Services

BUILT = (0, 3)
NOT_STAMPED_YET = 2


def build_pack(
    services: Services,
    settings: ServiceSettings,
    *,
    case_id: str,
    out: Path,
) -> BundleRun:
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "--agent", settings.agent_id,
        "--case", case_id,
        "--logs", services.logs,
        "--out", str(out),
    ]
    return services.bundle(args)
