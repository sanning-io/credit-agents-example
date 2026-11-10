"""The tools both agents share on every turn: the four case documents and the cover
calculator. No tool returns a finding, verdict or recommendation. Each tool finds
its documents through the runtime context. A reading tool returns its file path as
the LangChain `artifact`, so the recorder anchors the file's name, size and hash,
never its bytes. `require_work` refuses a decision until the turn's own tool
results show all four documents read and cover calculated. A decision asked for in
the same model reply as the reads is refused, because LangChain gives every call
in one reply the state from before it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from langchain.tools import ToolRuntime
from langchain_core.messages import HumanMessage, ToolMessage
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

READS = {
    "read_application": "application",
    "read_bank_statements": "bank_statements",
    "read_annual_report": "annual_report",
    "read_credit_policy": "credit_policy",
}
ALL_DOCUMENTS = frozenset(READS.values())
CALCULATOR = "calculate_cover"


class YearEarnings(BaseModel):
    year: str = Field(description="Which twelve months, as the document labels them")
    ebitda_sek: int = Field(description="EBITDA for those twelve months, in SEK, as the document states it")


class Adjustment(BaseModel):
    year: str = Field(description="The year label the adjustment applies to")
    amount_sek: int = Field(description="The amount added to that year's EBITDA, in SEK (negative to reduce)")


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True)
class Work:
    """What one turn has read and calculated."""

    read: frozenset[str]
    calculations: list[dict[str, Any]]


def work_of(messages: Sequence[Any]) -> Work:
    """What the turn has done, from its own successful tool results: those
    after the last human message, which is the turn's task. A refused call
    did no work, and results from an earlier turn in the same history do not
    count."""
    task = max((i for i, message in enumerate(messages) if isinstance(message, HumanMessage)), default=-1)
    done = [m for m in messages[task + 1 :] if isinstance(m, ToolMessage) and m.status != "error"]
    return Work(
        read=frozenset(READS[m.name] for m in done if m.name in READS),
        calculations=[json.loads(m.content) for m in done if m.name == CALCULATOR],
    )


def require_work(what: str, runtime: ToolRuntime) -> Work:
    """The turn's work so far, or a refusal for the model to read: it has
    not read all four documents, or has not calculated cover."""
    state = runtime.state
    work = work_of(state.get("messages", []) if isinstance(state, Mapping) else [])
    missing = ALL_DOCUMENTS - work.read
    if missing:
        raise ToolException(f"{what}: cannot run before reading {', '.join(sorted(missing))}")
    if not work.calculations:
        raise ToolException(f"{what}: calculate cover with calculate_cover first")
    return work


@tool(response_format="content_and_artifact")
def read_application(runtime: ToolRuntime[CaseContext]) -> tuple[str, dict]:
    """Read the application this case was opened on (JSON) and return
    it: the application number, when it was received, the applicant,
    the financing requested (product, amount, term and nominal rate)
    and the documents the applicant supplied."""
    case = runtime.context
    content = application_file.read(case.path("application"))
    return json.dumps(content), input_file(case.path("application"), source=APPLICATION_SOURCE)


@tool(response_format="content_and_artifact")
def read_bank_statements(runtime: ToolRuntime[CaseContext]) -> tuple[str, dict]:
    """Read the applicant's bank statements: each month's receipts,
    operating expenses, EBITDA and existing loan repayment, and the
    totals for the period."""
    case = runtime.context
    path = case.path("bank_statements")
    body = {"months": documents.bank_statement_months(path), "totals": documents.bank_statement_totals(path)}
    return json.dumps(body), input_file(path, source="the applicant's bank")


@tool(response_format="content_and_artifact")
def read_annual_report(runtime: ToolRuntime[CaseContext]) -> tuple[str, dict]:
    """Read the applicant's most recent annual report: its full text."""
    case = runtime.context
    text = documents.read_pdf_text(case.path("annual_report"))
    return text, input_file(case.path("annual_report"), source="the applicant's filed accounts")


@tool(response_format="content_and_artifact")
def read_credit_policy(runtime: ToolRuntime[CaseContext]) -> tuple[str, dict]:
    """Read the lender's credit policy: its full text."""
    case = runtime.context
    text = documents.read_pdf_text(case.path("credit_policy"))
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
    adjustments to a year's EBITDA, each a year and an amount only. Do
    not give a reason or evidence here: you state them for each
    adjustment when you write your decision. Returns the cover on
    unadjusted earnings, and on adjusted earnings when you give
    adjustments."""
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
    return json.dumps(result), input_file(path, source=APPLICATION_SOURCE)


SHARED_TOOLS = [read_application, read_bank_statements, read_annual_report, read_credit_policy, calculate_cover]
