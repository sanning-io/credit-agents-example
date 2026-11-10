"""The checker agent's tools: the four case documents and the cover calculator
(shared with the underwriter), plus the tool that records what the turn owes. No
tool returns a finding or verdict. The recording tools refuse until the turn's own
results show all four documents read and cover calculated (`require_work`).
"""

from __future__ import annotations

import json

from langchain.tools import ToolRuntime
from langchain_core.tools import ToolException, tool
from sanning_anchor import DecisionTool

from ..common import decisions
from ..common.case_tools import SHARED_TOOLS, require_work
from ..common.context import CaseContext
from ..common.files import output_file

REVIEW_FILENAME = "review.json"
CONCLUSION_FILENAME = "conclusion.json"


@tool(response_format="content_and_artifact")
def record_review(
    finding: decisions.Finding,
    note: str,
    summary: decisions.Summary,
    runtime: ToolRuntime[CaseContext],
    about: decisions.Label | None = None,
) -> tuple[str, dict]:
    """Record your review. `finding` is your decision: challenge (the
    assessment goes back to the underwriting agent) or agreed. `about`
    names what you challenge, as a short label; leave it empty when you
    agree. `note` is your reasoning, quoting your own calculations; on a
    challenge it is what the underwriting agent reads. `summary` is one
    sentence for the credit officer. Refuses until you have read all four
    case documents and calculated cover in this turn."""
    work = require_work("record_review", runtime)
    if finding == decisions.CHALLENGE and not (about or "").strip():
        raise ToolException("record_review: a challenge needs `about`: what you challenge, as a short label")
    review = {
        "finding": finding,
        "about": (about or "").strip() or None if finding == decisions.CHALLENGE else None,
        "reason": note,
        "summary": summary,
        "calculations": work.calculations,
    }
    path = runtime.context.output(REVIEW_FILENAME)
    path.write_text(json.dumps(review, indent=2))
    return finding, output_file(path, destination="this agent's own records")


@tool(response_format="content_and_artifact")
def record_conclusion(
    verdict: decisions.Recommendation,
    deposit_percent: float,
    note: str,
    summary: decisions.Summary,
    runtime: ToolRuntime[CaseContext],
    conditions: list[decisions.Label] | None = None,
) -> tuple[str, dict]:
    """Record your conclusion on the case. `verdict` is your decision:
    approve, approve_with_conditions, decline or refer. `deposit_percent`
    is the deposit the verdict carries, as a percentage of the amount
    requested. `conditions` are the conditions it carries, each a short
    label. `note` is your reasoning, quoting your own calculations.
    `summary` is one sentence for the credit officer. Refuses until you
    have read all four case documents and calculated cover in this
    turn."""
    work = require_work("record_conclusion", runtime)
    challenge = runtime.context.challenge
    conclusion = {
        "verdict": verdict,
        "deposit_percent": deposit_percent,
        "conditions": decisions.conditions_list(conditions),
        "reason": note,
        "summary": summary,
        "answers_challenge": challenge.get("about") if challenge else None,
        "calculations": work.calculations,
    }
    path = runtime.context.output(CONCLUSION_FILENAME)
    path.write_text(json.dumps(conclusion, indent=2))
    return verdict, output_file(path, destination="this agent's own records")


REVIEW_TOOLS = [*SHARED_TOOLS, record_review]
CONCLUSION_TOOLS = [*SHARED_TOOLS, record_conclusion]

# The decisions this agent records, each named for what was decided. The
# value on the record is the bare word the tool returns; the file record
# keeps the document.
DECISION_TOOLS = [
    DecisionTool(record_review, "review_finding"),
    DecisionTool(record_conclusion, "credit_verdict"),
]
