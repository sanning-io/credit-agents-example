"""The underwriter's tools: the shared case-document and calculator tools, plus the tool that
writes the assessment or revision. Every figure comes from the calculator, and no tool returns a
recommendation."""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sanning_anchor import DecisionTool

from ..common import application as application_file
from ..common import decisions
from ..common.case_tools import SHARED_TOOLS, Work, require_work
from ..common.context import CaseContext
from ..common.files import output_file

ASSESSMENT_FILENAME = "assessment.json"
REVISION_FILENAME = "revision.json"


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
    work: Work,
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
        "calculations": work.calculations,
    }


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
    its year, amount, reason and evidence: this is the one place the
    reason and evidence go, since `calculate_cover` takes a year and an
    amount only. `rationale` is your reasoning,
    quoting the calculator's figures. `summary` is one sentence for the
    credit officer. Refuses until you have read all four case documents
    and calculated cover in this turn."""
    work = require_work("write_assessment", runtime)
    assessment = _decision(
        runtime.context,
        recommendation=recommendation,
        deposit_percent=deposit_percent,
        conditions=conditions,
        adjustments=adjustments,
        rationale=rationale,
        summary=summary,
        work=work,
    )
    path = runtime.context.output(ASSESSMENT_FILENAME)
    path.write_text(json.dumps(assessment, indent=2))
    return recommendation, output_file(path, destination="this agent's own records")


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
    `adjustments` are your assessment as it now stands, each with its
    year, amount, reason and evidence. `rationale` is
    your reasoning, answering the challenge and quoting the calculator's
    figures. `summary` is one sentence for the credit officer. Refuses
    until you have read all four case documents and calculated cover in
    this turn."""
    work = require_work("write_revision", runtime)
    challenge = runtime.context.challenge or {}
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
            work=work,
        ),
    }
    path = runtime.context.output(REVISION_FILENAME)
    path.write_text(json.dumps(revision, indent=2))
    return recommendation, output_file(path, destination="this agent's own records")


ASSESSMENT_TOOLS = [*SHARED_TOOLS, write_assessment]
REVISION_TOOLS = [*SHARED_TOOLS, write_revision]

# Each decision is named for what was decided, so both recommendations share one name.
DECISION_TOOLS = [
    DecisionTool(write_assessment, "credit_recommendation"),
    DecisionTool(write_revision, "credit_recommendation"),
]
