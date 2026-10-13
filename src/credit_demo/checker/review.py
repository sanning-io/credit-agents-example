"""What the checker agent says back, once it has reviewed the assessment or
concluded the case.

This module turns what the checker agent recorded into the message that
crosses. It decides nothing: the finding, the verdict and every figure come
off the file the checker agent wrote and anchored.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..common import decisions


def challenge_for(review: Mapping[str, Any]) -> dict[str, Any]:
    """The checker agent's challenge, from a review it recorded.

    Only ever called for a review whose finding is a challenge: agreement has
    no challenge to send, and a challenge assembled from an agreeing review
    would be this service inventing a disagreement.
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
