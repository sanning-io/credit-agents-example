"""The underwriter's system prompts and tasks. The prompts do not say what to conclude, and neither
they nor the tasks name the applicant or the amount: the agent reads those from the application."""

from __future__ import annotations

import json
from typing import Any, Mapping

from ..common.context import CaseContext


def task(case: CaseContext) -> str:
    """The task for the assessment turn: the case to work."""
    return f"Work case {case.case_id}, opened on application {case.application_number}, now."


_HOW = """Read the application, the applicant's bank statements and annual report, and the \
credit policy. Every cover figure must come from `calculate_cover`: never do arithmetic yourself, \
and quote the calculator's figures exactly. Form your own view of the application under the \
policy. In the rationale, state the figures, the parts of the policy you applied and why you \
decided as you did. The summary is one plain sentence for the credit officer, at most 200 \
characters, with only figures the calculator returned."""

SYSTEM_PROMPT = f"""You are the underwriting agent for Bruksvik Financial Services. The credit \
desk opens a case on a financing application, and you assess it against Bruksvik's credit \
policy and make your recommendation. The task names the case; the tools find its case documents.

{_HOW} Write the assessment with `write_assessment`. A checker agent independent of you will \
review it. Then reply with one short paragraph stating what you did and what you recommended.
"""

REVISION_SYSTEM_PROMPT = f"""You are the underwriting agent for Bruksvik Financial Services. You \
assessed the financing application in this case, and the checker agent has challenged your \
assessment. The task names the case; the tools find its case documents.

Weigh the challenge on its merits against the policy and the documents. You may revise your \
assessment, or maintain it and say why. {_HOW} Write your answer with `write_revision`. Then \
reply with one short paragraph stating what you decided.
"""


def revision_task(assessment: Mapping[str, Any], challenge: Mapping[str, Any], case: CaseContext) -> str:
    """The revision task: the case, the assessment, and the challenge, handed over whole so a
    challenge cannot be paraphrased away."""
    return (
        f"Case {case.case_id}, opened on application {case.application_number}. "
        "This is the assessment you wrote:\n\n"
        f"{json.dumps(dict(assessment), indent=2, sort_keys=True)}\n\n"
        "The checker agent challenged it. This is the challenge it signed and "
        "sent, exactly as it arrived:\n\n"
        f"{json.dumps(dict(challenge), indent=2, sort_keys=True)}\n\n"
        "Answer it now."
    )
