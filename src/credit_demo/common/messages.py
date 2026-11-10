"""The words the case page shows when something goes wrong. Each says what happened
and what to do, and names no exception, file or model output. The technical text
sits beside it.
"""

from __future__ import annotations

from typing import NamedTuple

RUN_AGAIN = "Run the case again."

UNEXPECTED = f"Something went wrong and the case stopped. {RUN_AGAIN}"
OUT_OF_STEP = f"The agents got out of step. {RUN_AGAIN}"
CASE_FINISHED = "This case has already finished."
LOST_STEPS = f"Part of this case wasn't saved, so its audit trail won't be complete. {RUN_AGAIN}"

AUDIT_TRAIL_NOT_READY = "The audit trail isn't ready yet. Try again in a few minutes."
AUDIT_TRAIL_PREPARING = "Preparing the audit trail. This can take a few minutes."
AUDIT_TRAIL_FAILED = "The audit trail couldn't be created. Try again."


def turn_unfinished(agent: str, work: str) -> str:
    """An agent ended its turn without writing its file: `agent` is
    "underwriter" or "checker", `work` is what the turn owed ("assessment")."""
    return f"The {agent} stopped before finishing its {work}. {RUN_AGAIN}"


class Failure(NamedTuple):
    """A failure as the page says it (`message`), and as the log and the
    Technical detail say it (`detail`)."""

    message: str
    detail: str | None = None
