"""What each agent writes and sends, shaped like the real thing, for the tests that check a task
names neither the applicant nor the amount. A stand-in without them would pass whether or not a
task's own words name them. Each is built with the code that builds the real one."""

from __future__ import annotations

import json
from typing import Any

from credit_demo.checker.review import challenge_for
from credit_demo.common import application, calculator, home
from credit_demo.underwriter.case import revision_message

SAMPLE = json.loads((home.REPO_ROOT / home.SAMPLE_APPLICATION).read_text(encoding="utf-8"))
APPLICANT = SAMPLE["applicant"]
AMOUNT = SAMPLE["financing_requested"]["amount_sek"]

CALCULATION = calculator.calculate_cover(
    terms=application.terms(SAMPLE),
    earnings_by_year=[
        {"year": "Oct 2025 to Sep 2026", "ebitda_sek": 2_750_000},
        {"year": "Oct 2024 to Sep 2025", "ebitda_sek": 1_600_000},
    ],
    existing_annual_debt_service_sek=1_100_000,
    deposit_percent=15,
)


def _decision(**more: Any) -> dict[str, Any]:
    """The fields `write_assessment` and `write_revision` write."""
    return {
        "applicant": APPLICANT["name"],
        "amount_requested_sek": AMOUNT,
        "term_months": SAMPLE["financing_requested"]["term_months"],
        "recommendation": "approve_with_conditions",
        "deposit_percent": 15,
        "conditions": ["15% deposit"],
        "adjustments": [],
        "rationale": f"{APPLICANT['name']} asks for SEK {AMOUNT:,}; cover after the deposit is 1.128.",
        "summary": "Approve with a 15% deposit.",
        "calculations": [CALCULATION],
        **more,
    }


# The underwriting agent's assessment, as `write_assessment` writes it and as
# the checker agent holds it once it arrives.
ASSESSMENT = _decision()

# The checker agent's review, as `record_review` writes it, and the challenge
# it sends.
REVIEW = {
    "finding": "challenge",
    "about": "the deposit",
    "reason": f"Cover on SEK {AMOUNT:,} is 1.128.",
    "summary": "The deposit does not follow from the policy.",
    "calculations": [CALCULATION],
}
CHALLENGE = challenge_for(REVIEW)

# The underwriting agent's revision, as `write_revision` writes it, and the
# message it sends.
REVISION = {"answers_challenge": CHALLENGE["about"], "response": "maintained", **_decision()}
REVISION_MESSAGE = revision_message(REVISION, CHALLENGE, "Maintained: approve with a 15% deposit.")


def forbidden() -> list[str]:
    """The applicant and the amount, in every way a text might write them."""
    millions = f"{AMOUNT / 1_000_000:g}"
    amounts = [f"{AMOUNT:,}", str(AMOUNT), f"{AMOUNT:,}".replace(",", " "), f"{millions}m", f"{millions} m",
               f"{millions} million", f"SEK {millions}"]
    return [APPLICANT["name"], *APPLICANT["name"].split()[:2], APPLICANT["registration_number"], *amounts]


def embedded(task: str) -> tuple[list[dict], str]:
    """The JSON objects a task embeds, and the task's own text without them."""
    decoder = json.JSONDecoder()
    found: list[dict] = []
    text: list[str] = []
    position = 0
    while (start := task.find("{", position)) != -1:
        try:
            value, end = decoder.raw_decode(task, start)
        except ValueError:
            text.append(task[position : start + 1])
            position = start + 1
            continue
        found.append(value)
        text.append(task[position:start])
        position = end
    text.append(task[position:])
    return found, "".join(text)
