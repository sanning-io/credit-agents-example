"""The checker agent's system prompts and tasks.

Two turns: the review of an assessment, and the conclusion. **Neither prompt
asks the checker agent to object or to agree.** They say what an independent
checker's job is: read the same documents, calculate the figures itself, and
decide. A prompt that told the model to challenge would replace the
demonstration with a stage direction.

**Neither prompt, nor its task, names the applicant or the amount.** The task
names the case and the application number, and the agent learns who is asking
for what by reading the application, a step the audit trail records. A task
embeds the case so far: the underwriting
agent's messages exactly as they arrived, and this agent's own review and
challenge as it recorded them. Those are the agents' own words, written after
reading the application, not prompt text.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from ..common.context import CaseContext

_HOW = """Read the application, the applicant's bank statements and annual report, and the \
credit policy. Calculate cover yourself with `calculate_cover` rather than taking the figures you \
were sent: never do arithmetic yourself, and quote the calculator's figures exactly. In your \
note, say what you checked, what your own figures show and why you decided as you did. The \
summary is one plain sentence for the credit officer, at most 200 characters, with only figures \
the calculator returned."""

REVIEW_SYSTEM_PROMPT = f"""You are the checker agent for Bruksvik Financial Services, independent \
of the underwriting agent. The underwriting agent has assessed the financing application in this \
case and sent you its assessment. The task names the case; the tools find its case documents. \
Review the assessment against Bruksvik's credit policy.

{_HOW} Decide whether the assessment stands under the policy: agree with it, or challenge it. A \
challenge goes back to the underwriting agent, and your note is what it reads. Record the review \
with `record_review`. Then reply with one short paragraph stating what you found.
"""

CONCLUSION_SYSTEM_PROMPT = f"""You are the checker agent for Bruksvik Financial Services, \
independent of the underwriting agent. The task names the case; the tools find its case \
documents. Conclude the case under Bruksvik's credit policy.

{_HOW} Decide the verdict under the policy, with the deposit and any conditions it carries. \
Record the conclusion with `record_conclusion`. Then reply with one short paragraph stating what \
you concluded and why.
"""


def _opening(case: CaseContext) -> str:
    return f"Case {case.case_id}, opened on application {case.application_number}. "


def review_task(assessment: Mapping[str, Any], case: CaseContext) -> str:
    """The task for the review turn: the assessment this side verified at
    receipt, handed over whole rather than summarized here."""
    return (
        f"{_opening(case)}The underwriting agent sent this assessment, exactly as it arrived and "
        "as its signed record commits to it:\n\n"
        f"{json.dumps(dict(assessment), indent=2, sort_keys=True)}\n\n"
        "Review it now."
    )


def conclusion_task(
    assessment: Mapping[str, Any] | None,
    review: Mapping[str, Any] | None,
    challenge: Mapping[str, Any] | None,
    revision: Mapping[str, Any] | None,
    case: CaseContext,
) -> str:
    """The task for the conclusion turn: everything on the record so far.

    A case the checker agent agreed with has no challenge or revision, and
    the task says so rather than handing the model an empty object to reason
    about.
    """
    parts = [_opening(case).strip()]
    if assessment is not None:
        parts.append(
            "This is the underwriting agent's assessment, as it arrived:\n\n"
            f"{json.dumps(dict(assessment), indent=2, sort_keys=True)}"
        )
    if challenge is None or revision is None:
        if review is not None:
            parts.append(
                "This is your review, as you recorded it:\n\n"
                f"{json.dumps(dict(review), indent=2, sort_keys=True)}"
            )
        parts.append("You agreed with the assessment, so there is no challenge or revision. Conclude the case now.")
    else:
        parts.append(
            "This is the challenge you sent, as you signed it:\n\n"
            f"{json.dumps(dict(challenge), indent=2, sort_keys=True)}"
        )
        parts.append(
            "This is the underwriting agent's answer, exactly as it arrived:\n\n"
            f"{json.dumps(dict(revision), indent=2, sort_keys=True)}"
        )
        parts.append("Conclude the case now.")
    return "\n\n".join(parts)
