"""This agent's pack for one case, built by the SDK's `bundle` verb.

`bundle --case` selects every record this agent signed for the case, from
Sanning's index and the agent's own bucket, and signs a declaration of what
the pack holds with this agent's own key. It exits 3 while the records are
not yet stamped; the caller tries again later.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ServiceSettings
from .sanning import BundleRun, Services, bundle_env

NOT_STAMPED_YET = 3

# The window is widened by this much on each side, so a record whose time on
# the plane's clock is a little off this machine's is still inside it.
MARGIN = timedelta(seconds=120)


def _instant(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_pack(
    services: Services,
    settings: ServiceSettings,
    *,
    case_id: str,
    opened_at: str,
    ended_at: str,
    out: Path,
) -> BundleRun:
    opened = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
    ended = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "--agent", settings.agent_id,
        "--case", case_id,
        "--from", _instant(opened - MARGIN),
        "--to", _instant(ended + MARGIN),
        "--logs", services.logs,
        "--out", str(out),
    ]
    return services.bundle(args, bundle_env(settings))
