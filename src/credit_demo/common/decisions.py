"""The decisions the agents make. Each is a `Literal` of the words below, so
LangChain puts the list in the tool's schema and sends any other word back to the
model; a word is never mapped to the nearest one. No tool returns a finding or
verdict of its own.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

CHALLENGE = "challenge"

Recommendation = Literal["approve", "approve_with_conditions", "decline", "refer"]
Finding = Literal["challenge", "agreed"]
Response = Literal["revised", "maintained"]

# The agent's own one-sentence summary of its turn, which the credit desk
# shows on the turn's card. The limit is in the tool's schema, so LangChain
# shows it to the model and sends a longer sentence back to be rewritten.
SUMMARY_LIMIT = 200
Summary = Annotated[
    str,
    Field(
        min_length=1,
        max_length=SUMMARY_LIMIT,
        description=(
            "One plain sentence for the credit officer: what you found and why. "
            f"At most {SUMMARY_LIMIT} characters. Quote only figures the tools returned."
        ),
    ),
]


# A condition, or what a challenge is about, as a short label: the page shows
# it as one line, and the agent's reasoning carries the detail. The limit is in
# the tool's schema, so a longer label goes back to the model to be shortened.
LABEL_LIMIT = 80
Label = Annotated[
    str,
    Field(
        min_length=1,
        max_length=LABEL_LIMIT,
        description=f"A short label, at most {LABEL_LIMIT} characters. Put the detail in your reasoning.",
    ),
]


def conditions_list(value: Any) -> list[str]:
    """The model's conditions, as a list of text."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return []
