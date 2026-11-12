"""The words the case page shows when something goes wrong. Each says what happened
and what to do, and names no exception, file or model output. The technical text
sits beside it.
"""

from __future__ import annotations

from typing import NamedTuple

from sanning_anchor import RotationUnauthorizedError

RUN_AGAIN = "Run the case again."

UNEXPECTED = f"Something went wrong and the case stopped. {RUN_AGAIN}"
OUT_OF_STEP = f"The agents got out of step. {RUN_AGAIN}"
CASE_FINISHED = "This case has already finished."
LOST_STEPS = f"Part of this case wasn't saved, so its audit trail won't be complete. {RUN_AGAIN}"

CHECKER_DID_NOT_ANSWER = (
    "The checker did not answer within 10 minutes. A slow log store can cause this, because every "
    f"record is written to the bucket. {RUN_AGAIN}"
)

AUDIT_TRAIL_NOT_READY = "The audit trail isn't ready yet. Try again in a few minutes."
AUDIT_TRAIL_PREPARING = "Preparing the audit trail. This can take a few minutes."
AUDIT_TRAIL_FAILED = "The audit trail couldn't be created. Try again."
EVIDENCE_KEY_ALREADY_ACTIVE = (
    "Your organization already has a different evidence key. In the console, open Settings, then "
    "Signing keys, and revoke the active evidence key. Then try the export again."
)

# The words of the SDK's `bundle` command when the organization has another active evidence key.
# The SDK says "hand-over key"; this example says "evidence key".
SDK_WORDS_FOR_ANOTHER_ACTIVE_KEY = "already has a different hand-over key"


def turn_unfinished(agent: str, work: str) -> str:
    """An agent ended its turn without writing its file: `agent` is
    "underwriter" or "checker", `work` is what the turn owed ("assessment")."""
    return f"The {agent} stopped before finishing its {work}. {RUN_AGAIN}"


def agent_id_already_enrolled(agent_id: str) -> str:
    """An agent ID that Sanning holds under a different signing seed. Running the case again
    cannot help, so the message does not offer it."""
    return (
        f"The agent ID {agent_id} is already enrolled with a different signing seed. Set a new "
        "agent ID for each agent in the settings files, or put back the seed that enrolled this one."
    )


def reason_for(exc: BaseException | None, default: str = UNEXPECTED) -> str:
    """The plain words for a failure with a known cause, found anywhere along the exception's
    chain; `default` for any other."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, RotationUnauthorizedError):
            return agent_id_already_enrolled(exc.agent_id)
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return default


class Failure(NamedTuple):
    """A failure as the page says it (`message`), and as the log and the
    Technical detail say it (`detail`)."""

    message: str
    detail: str | None = None
