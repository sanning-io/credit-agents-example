"""What the checker agent says back after a review or a conclusion. It decides
nothing: the finding, verdict and every figure come off the file the agent wrote
and anchored.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..common import decisions


def challenge_for(review: Mapping[str, Any]) -> dict[str, Any]:
    """The challenge from a review whose finding is a challenge. It is never called
    for an agreeing review, since that would invent a disagreement.
    """
    if review.get("finding") != decisions.CHALLENGE:
        raise ValueError(
            "challenge_for: the review found "
            f"{review.get('finding')!r}, and there is no challenge to send "
            "for a review that did not challenge"
        )
    return {
        "kind": "challenge",
        "about": review.get("about"),
        "reason": review.get("reason"),
        "summary": review.get("summary"),
        "calculations": list(review.get("calculations") or []),
    }


def conclusion_message(conclusion: Mapping[str, Any]) -> dict[str, Any]:
    """The last message of a case: the checker agent's conclusion, as it
    recorded it."""
    return {
        "kind": "conclusion",
        "verdict": conclusion.get("verdict"),
        "deposit_percent": conclusion.get("deposit_percent"),
        "conditions": conclusion.get("conditions"),
        "reason": conclusion.get("reason"),
        "summary": conclusion.get("summary"),
        "answers_challenge": conclusion.get("answers_challenge"),
        "calculations": list(conclusion.get("calculations") or []),
    }
