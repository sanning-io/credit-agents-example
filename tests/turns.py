"""Run one agent turn in a test, the way a case runs it: the turn's real
tools and prompt, a scripted model, and the agent's real recorder over a
fake control plane and a real local log store."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from langchain.tools import ToolRuntime
from langchain_core.messages import HumanMessage

from credit_demo.checker import tools as checker_tools
from credit_demo.common import application, home
from credit_demo.common.case_tools import READS, SHARED_TOOLS
from credit_demo.common.cases import Case
from credit_demo.common.context import CaseContext
from credit_demo.common.sanning import FAILED_REASON, recorder
from credit_demo.common.turn import Turn, run_turn
from credit_demo.underwriter import tools as underwriter_tools
from tests.scripted import ADJUSTMENT, YEARS

CASE = "20260101T000000000000Z-1a2b"


def context(case_id: str = CASE, location=home.SAMPLE_APPLICATION) -> CaseContext:
    """The runtime context of a case opened on the application at
    `location`, built the way a case builds it: from the desk's record."""
    return CaseContext.of(Case(id=case_id, **application.opening(location)))


def runtime(
    case: CaseContext | None = None,
    messages: Sequence[Any] = (),
    *,
    workspace: Path | None = None,
    challenge: Mapping[str, Any] | None = None,
) -> ToolRuntime:
    """What LangChain hands a tool that takes the runtime, for a test that
    calls the tool directly: `tool.invoke({"runtime": runtime(), ...})`.
    `messages` is the turn's history so far, which a decision tool reads its
    turn's work from: a task, then the results of the tools that ran.
    `workspace` and `challenge` complete the context the way a turn does."""
    return ToolRuntime(
        state={"messages": list(messages)},
        context=replace(case or context(), workspace=workspace, challenge=challenge),
        config={}, stream_writer=lambda *_: None, tool_call_id=None, store=None,
    )


def worked(
    case: CaseContext | None = None,
    *,
    reads: Sequence[str] = tuple(READS),
    calculate: bool = True,
    calculation: Mapping[str, Any] | None = None,
) -> list[Any]:
    """The history of a turn that has done its work: the task, then the real
    results of the reads named (by tool name, all four by default) and of one
    calculation, with the arguments given or the scripted agents' own. A test
    of a decision tool passes it as `messages`."""
    case = case or context()
    tools = {t.name: t for t in SHARED_TOOLS}
    calls = [(name, {}) for name in reads]
    if calculate:
        calls.append((
            "calculate_cover",
            dict(calculation or {"earnings_by_year": YEARS, "existing_annual_debt_service_sek": 1_100_000,
                                 "deposit_percent": 15, "adjustments": [ADJUSTMENT]}),
        ))
    history: list[Any] = [HumanMessage(content="the task")]
    for i, (name, args) in enumerate(calls):
        history.append(tools[name].invoke(
            {"name": name, "args": {**args, "runtime": runtime(case, history)}, "id": f"w{i}", "type": "tool_call"}
        ))
    return history


@dataclass
class Ran:
    status: str
    output: dict[str, Any] | None
    error: str | None
    summary: str
    # What the recorder handed back, one per record, in order.
    events: list[Any] = field(default_factory=list)


def decisions_of(turn: Turn) -> list[Any]:
    """The decision tools of the agent whose turn this is, as that agent's
    recorder is built with them. A test's own turn, with tools that are none
    of the agents', records no decision."""
    names = {tool.name for tool in turn.tools}
    for own in (underwriter_tools.DECISION_TOOLS, checker_tools.DECISION_TOOLS):
        if any(decision.tool.name in names for decision in own):
            return own
    return []


def run(turn: Turn, *, anchorer: Any, model: Any, case_id: str = CASE) -> Ran:
    events: list[Any] = []
    rec = recorder(anchorer, case_id, decisions=decisions_of(turn), on_event=events.append)
    result = run_turn(turn, model=model, recorder=rec)
    ok = result.status == "complete"
    try:
        rec.close(outcome="completed" if ok else "failed", reason=None if ok else FAILED_REASON)
    except Exception:
        pass
    return Ran(status=result.status, output=result.output, error=result.error, summary=result.summary, events=events)
