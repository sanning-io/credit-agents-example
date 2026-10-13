"""The tools both agents share, on every turn: the four case documents and
the cover calculator.

An underwriter and a checker at a lender work from the same case file and the
same credit spreadsheet. What differs is their job, not their materials, so
both agents get these same tools, and each turn adds only the tool that
writes what that turn owes. No tool here returns a finding, a verdict or a
recommendation: the calculator does arithmetic on the figures an agent
enters, and the agent reads the policy and decides.

**Every document is found through the case.** Each tool takes LangChain's
`ToolRuntime`, and the runtime's context is the case this turn works
(`credit_demo.common.context.CaseContext`): its id, its application number
and each case document's location. No tool here names a document path, and
the terms the calculator uses come from the application file at the
context's location.

A tool that names an existing case document returns its path as the
LangChain `artifact` (via `response_format="content_and_artifact"`): the
callback handler resolves that path under its `artifacts.root` and anchors
its name, size and hash, never the file's bytes. What a reading tool RETURNS
is content, and it goes to the model provider.

The calculator returns the application file the same way, beside its JSON
text: it reads the financing requested (the amount, the term and the rate)
from that file, so its step carries a file record for the application, with
the fingerprint of the bytes it calculated on. That is the file
`read_application` recorded unless it changes during a run. The credit
policy is recorded by the step that reads it, `read_credit_policy`.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import ToolException, tool
from pydantic import BaseModel, Field

from . import application as application_file
from . import calculator, documents
from .context import CaseContext
from .errors import describe
from .files import CREDIT_POLICY_SOURCE, input_file

# How a file record says where the application came from. One wording, so the
# tool that reads the application and the calculator describe the same file
# the same way.
APPLICATION_SOURCE = "the applicant, through the lender's document store"

ALL_DOCUMENTS = frozenset({"application", "bank_statements", "annual_report", "credit_policy"})


class YearEarnings(BaseModel):
    year: str = Field(description="Which twelve months, as the document labels them")
    ebitda_sek: int = Field(description="EBITDA for those twelve months, in SEK, as the document states it")


class Adjustment(BaseModel):
    year: str = Field(description="The year label the adjustment applies to")
    amount_sek: int = Field(description="The amount added to that year's EBITDA, in SEK (negative to reduce)")
    reason: str = Field(description="Why, and the evidence for it")


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


class CaseFile:
    """What one turn has read and calculated. Each turn starts with a fresh
    one, so a writing tool can tell what THIS turn did."""

    def __init__(self) -> None:
        self.read: set[str] = set()
        self.calculations: list[dict[str, Any]] = []

    def require_work(self, what: str) -> None:
        missing = ALL_DOCUMENTS - self.read
        if missing:
            raise ToolException(f"{what}: cannot run before reading {', '.join(sorted(missing))}")
        if not self.calculations:
            raise ToolException(f"{what}: calculate cover with calculate_cover first")

    def tools(self) -> list:
        case_file = self

        @tool(response_format="content_and_artifact")
        def read_application(runtime: ToolRuntime[CaseContext]) -> tuple[str, dict]:
            """Read the application this case was opened on (JSON) and return
            it: the application number, when it was received, the applicant,
            the financing requested (product, amount, term and nominal rate)
            and the documents the applicant supplied."""
            case = runtime.context
            content = application_file.read(case.path("application"))
            case_file.read.add("application")
            return json.dumps(content), input_file(case.path("application"), source=APPLICATION_SOURCE)

        @tool(response_format="content_and_artifact")
        def read_bank_statements(runtime: ToolRuntime[CaseContext]) -> tuple[str, dict]:
            """Read the applicant's bank statements: each month's receipts,
            operating expenses, EBITDA and existing loan repayment, and the
            totals for the period."""
            case = runtime.context
            path = case.path("bank_statements")
            body = {"months": documents.bank_statement_months(path), "totals": documents.bank_statement_totals(path)}
            case_file.read.add("bank_statements")
            return json.dumps(body), input_file(path, source="the applicant's bank")

        @tool(response_format="content_and_artifact")
        def read_annual_report(runtime: ToolRuntime[CaseContext]) -> tuple[str, dict]:
            """Read the applicant's most recent annual report: its full text."""
            case = runtime.context
            text = documents.read_pdf_text(case.path("annual_report"))
            case_file.read.add("annual_report")
            return text, input_file(case.path("annual_report"), source="the applicant's filed accounts")

        @tool(response_format="content_and_artifact")
        def read_credit_policy(runtime: ToolRuntime[CaseContext]) -> tuple[str, dict]:
            """Read the lender's credit policy: its full text."""
            case = runtime.context
            text = documents.read_pdf_text(case.path("credit_policy"))
            case_file.read.add("credit_policy")
            return text, input_file(case.path("credit_policy"), source=CREDIT_POLICY_SOURCE)

        @tool(response_format="content_and_artifact")
        def calculate_cover(
            earnings_by_year: list[YearEarnings],
            existing_annual_debt_service_sek: int,
            runtime: ToolRuntime[CaseContext],
            deposit_percent: float = 0,
            adjustments: list[Adjustment] | None = None,
        ) -> tuple[str, dict]:
            """Calculate debt service cover on the financing this application
            requests (its amount, term and rate). Arithmetic only. Give each
            year's EBITDA as the documents state it (annual earnings are
            their average), the applicant's existing annual debt service, any
            deposit as a percentage of the amount requested, and any
            adjustments to a year's EBITDA. Returns the cover on unadjusted
            earnings, and on adjusted earnings when you give adjustments."""
            case = runtime.context
            path = case.path("application")
            terms = application_file.terms(application_file.read(path))
            try:
                result = calculator.calculate_cover(
                    terms=terms,
                    earnings_by_year=_plain(earnings_by_year),
                    existing_annual_debt_service_sek=existing_annual_debt_service_sek,
                    deposit_percent=deposit_percent,
                    adjustments=_plain(adjustments or []),
                )
            except calculator.CalculatorError as exc:
                raise ToolException(describe(exc)) from exc
            case_file.calculations.append(result)
            return json.dumps(result), input_file(path, source=APPLICATION_SOURCE)

        return [read_application, read_bank_statements, read_annual_report, read_credit_policy, calculate_cover]
