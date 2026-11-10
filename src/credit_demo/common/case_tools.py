"""The tools both agents share, on every turn: the four case documents and
the cover calculator.

An underwriter and a checker at a lender work from the same case file and the
same credit spreadsheet. What differs is their job, not their materials, so
both agents get these same tools, and each turn adds only the tool that
writes what that turn owes. No tool here returns a finding, a verdict or a
recommendation: the calculator does arithmetic on the figures an agent
enters, and the agent reads the policy and decides.

Every tool is defined once, here or beside the agent that owns it, and takes
what is fixed for a turn from the runtime context.

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

**What a turn has done is read off its own tool results.** A tool that
writes a decision calls `require_work`, which looks at the successful tool
results since the turn's task (the last human message) in the runtime's
`state["messages"]`: the four documents read, and cover calculated.
LangChain starts the calls of one model reply together and gives each the
state as it stood when the reply was made, so a decision requested in the
same reply as the reads is always refused, and the model asks again once the
reads are in.
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

# The case document each reading tool reads, by the tool's name.
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


# The tools of every turn, before the one that writes what the turn owes.
SHARED_TOOLS = [read_application, read_bank_statements, read_annual_report, read_credit_policy, calculate_cover]
