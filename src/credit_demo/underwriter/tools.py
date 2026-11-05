"""The underwriter's tools, the same on both of its turns: the four case
documents, the cover calculator (`credit_demo.common.case_tools`, shared with
the checker agent), and the tool that writes what the turn owes (the
assessment, or the revision that answers a challenge).

Every figure comes from the calculator, never from the model's own
arithmetic. The decision is the model's: the recommendation, the deposit, the
conditions and any adjustments to earnings are what it passes to the writing
tool, recorded as the underwriting agent's own. The file also carries every
calculation the turn made, so a reader sees the figures the decision rests
on. No tool here returns a recommendation.

**Every document is found through the case.** The writing tools take
LangChain's `ToolRuntime`, like the shared ones, and read the applicant and
the financing requested off the application file at the context's location."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ..common import application as application_file
from ..common import decisions
from ..common.case_tools import CaseFile
from ..common.context import CaseContext
from ..common.files import output_file


class EarningsAdjustment(BaseModel):
    year: str = Field(description="The year label the adjustment applies to")
    amount_sek: int = Field(description="The amount added to that year's EBITDA, in SEK")
    reason: str = Field(description="Why the item or period will not recur")
    evidence: str = Field(description="Where the documents show it")


def _decision(
    case: CaseContext,
    *,
    recommendation: str,
    deposit_percent: float,
    conditions: list[str] | None,
    adjustments: list[Any] | None,
    rationale: str,
    summary: str,
    case_file: CaseFile,
) -> dict[str, Any]:
    application = application_file.read(case.path("application"))
    terms = application_file.terms(application)
    return {
        "applicant": (application.get("applicant") or {}).get("name"),
        "amount_requested_sek": terms.amount_sek,
        "term_months": terms.term_months,
        "recommendation": recommendation,
        "deposit_percent": deposit_percent,
        "conditions": decisions.conditions_list(conditions),
        "adjustments": [a.model_dump() if hasattr(a, "model_dump") else dict(a) for a in adjustments or []],
        "rationale": rationale,
        "summary": summary,
        "calculations": list(case_file.calculations),
    }


def build_tools(workspace: Path) -> list:
    """The assessment turn."""
    case_file = CaseFile()

    @tool(response_format="content_and_artifact")
    def write_assessment(
        recommendation: decisions.Recommendation,
        deposit_percent: float,
        rationale: str,
        summary: decisions.Summary,
        runtime: ToolRuntime[CaseContext],
        conditions: list[decisions.Label] | None = None,
        adjustments: list[EarningsAdjustment] | None = None,
    ) -> tuple[str, dict]:
        """Write your assessment. `recommendation` is your decision: approve,
        approve_with_conditions, decline or refer. `deposit_percent` is the
        deposit you require, as a percentage of the amount requested.
        `conditions` are any conditions you propose, each a short label.
        `adjustments` are any adjustments to earnings you rely on, each with
        its amount, reason and evidence. `rationale` is your reasoning,
        quoting the calculator's figures. `summary` is one sentence for the
        credit officer. Refuses until you have read all four case documents
        and calculated cover in this turn."""
        case_file.require_work("write_assessment")
        assessment = _decision(
            runtime.context,
            recommendation=recommendation,
            deposit_percent=deposit_percent,
            conditions=conditions,
            adjustments=adjustments,
            rationale=rationale,
            summary=summary,
            case_file=case_file,
        )
        path = workspace / "assessment.json"
        path.write_text(json.dumps(assessment, indent=2))
        return recommendation, output_file(path, destination="this agent's own records")

    return [*case_file.tools(), write_assessment]


def build_revision_tools(workspace: Path, *, challenge: Mapping[str, Any]) -> list:
    """The revision turn: the same tools, and the one that writes the answer
    to the challenge that arrived."""
    case_file = CaseFile()

    @tool(response_format="content_and_artifact")
    def write_revision(
        response: decisions.Response,
        recommendation: decisions.Recommendation,
        deposit_percent: float,
        rationale: str,
        summary: decisions.Summary,
        runtime: ToolRuntime[CaseContext],
        conditions: list[decisions.Label] | None = None,
        adjustments: list[EarningsAdjustment] | None = None,
    ) -> tuple[str, dict]:
        """Write your answer to the checker agent's challenge. `response` is
        revised (you changed your assessment) or maintained (you keep it and
        say why). `recommendation`, `deposit_percent`, `conditions` and
        `adjustments` are your assessment as it now stands. `rationale` is
        your reasoning, answering the challenge and quoting the calculator's
        figures. `summary` is one sentence for the credit officer. Refuses
        until you have read all four case documents and calculated cover in
        this turn."""
        case_file.require_work("write_revision")
        revision = {
            "answers_challenge": challenge.get("about"),
            "response": response,
            **_decision(
                runtime.context,
                recommendation=recommendation,
                deposit_percent=deposit_percent,
                conditions=conditions,
                adjustments=adjustments,
                rationale=rationale,
                summary=summary,
                case_file=case_file,
            ),
        }
        path = workspace / "revision.json"
        path.write_text(json.dumps(revision, indent=2))
        return recommendation, output_file(path, destination="this agent's own records")

    return [*case_file.tools(), write_revision]
