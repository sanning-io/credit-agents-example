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
tool here names a document path. The recording tools refuse to run until all
four case documents have been read and cover calculated in the same turn.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from langchain_core.tools import ToolException, tool

from ..common import decisions
from ..common.case_tools import CaseFile
from ..common.files import output_file

REVIEW_FILENAME = "review.json"
CONCLUSION_FILENAME = "conclusion.json"


def build_review_tools(workspace: Path, *, assessment: Mapping[str, Any]) -> list:
    """The review of one assessment."""
    case_file = CaseFile()

    @tool(response_format="content_and_artifact")
    def record_review(
        finding: decisions.Finding,
        note: str,
        summary: decisions.Summary,
        about: decisions.Label | None = None,
    ) -> tuple[str, dict]:
        """Record your review. `finding` is your decision: challenge (the
        assessment goes back to the underwriting agent) or agreed. `about`
        names what you challenge, as a short label; leave it empty when you
        agree. `note` is your reasoning, quoting your own calculations; on a
        challenge it is what the underwriting agent reads. `summary` is one
        sentence for the credit officer. Refuses until you have read all four
        case documents and calculated cover in this turn."""
        case_file.require_work("record_review")
        if finding == decisions.CHALLENGE and not (about or "").strip():
            raise ToolException("record_review: a challenge needs `about`: what you challenge, as a short label")
        review = {
            "finding": finding,
            "about": (about or "").strip() or None if finding == decisions.CHALLENGE else None,
            "reason": note,
            "summary": summary,
            "calculations": list(case_file.calculations),
        }
        path = workspace / REVIEW_FILENAME
        path.write_text(json.dumps(review, indent=2))
        return json.dumps(review), output_file(path, destination="this agent's own records")

    return [*case_file.tools(), record_review]


def build_conclusion_tools(
    workspace: Path,
    *,
    revision: Mapping[str, Any] | None,
    challenge: Mapping[str, Any] | None,
) -> list:
    """The conclusion. `revision` and `challenge` are `None` for a case this
    checker agent agreed with at review."""
    case_file = CaseFile()

    @tool(response_format="content_and_artifact")
    def record_conclusion(
        verdict: decisions.Recommendation,
        deposit_percent: float,
        note: str,
        summary: decisions.Summary,
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
        case_file.require_work("record_conclusion")
        conclusion = {
            "verdict": verdict,
            "deposit_percent": deposit_percent,
            "conditions": decisions.conditions_list(conditions),
            "reason": note,
            "summary": summary,
            "answers_challenge": challenge.get("about") if challenge else None,
            "calculations": list(case_file.calculations),
        }
        path = workspace / CONCLUSION_FILENAME
        path.write_text(json.dumps(conclusion, indent=2))
        return json.dumps(conclusion), output_file(path, destination="this agent's own records")

    return [*case_file.tools(), record_conclusion]
