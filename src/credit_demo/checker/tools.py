"""The checker agent's tools, the same on both of its turns: the four case
documents, the cover calculator (`credit_demo.common.case_tools`, shared with
the underwriting agent), and the tool that records what the turn owes (the
review, or the conclusion).

The checker agent has the same materials as the underwriting agent. Its
independence is its own reading and its own figures: it calculates cover
itself rather than taking the figures it was sent, and it decides. **No tool
here returns a finding or a verdict**: whether to challenge, and what to
conclude, are the model's alone.

**Every document is found through the case.** The tools take LangChain's
`ToolRuntime`, and the runtime's context is the case this turn works
(`credit_demo.common.context.CaseContext`), built from the credit desk's
answer at the start of the turn (`credit_demo.checker.case_documents`). No
tool here names a document path. Every tool is defined once: the folder to
write to and the challenge being answered are in the context too. The
recording tools refuse to run until all four case documents have been read
and cover calculated in the same turn, which they find out from the turn's
own tool results (`credit_demo.common.case_tools.require_work`).
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


# The tools of each turn.
REVIEW_TOOLS = [*SHARED_TOOLS, record_review]
CONCLUSION_TOOLS = [*SHARED_TOOLS, record_conclusion]

# The decisions this agent records, each named for what was decided. The
# value on the record is the bare word the tool returns; the file record
# keeps the document.
DECISION_TOOLS = [
    DecisionTool(record_review, "review_finding"),
    DecisionTool(record_conclusion, "credit_verdict"),
]
