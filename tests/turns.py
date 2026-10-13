"""Run one agent turn in a test, the way a case runs it: the turn's real
tools and prompt, a scripted model, and the agent's real recorder over a
fake control plane and a real local log store."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain.tools import ToolRuntime

from credit_demo.common import application, home
from credit_demo.common.cases import Case
from credit_demo.common.context import CaseContext
from credit_demo.common.sanning import recorder
from credit_demo.common.turn import Turn, run_turn

CASE = "20260101T000000000000Z-1a2b"


def context(case_id: str = CASE, location=home.SAMPLE_APPLICATION) -> CaseContext:
    """The runtime context of a case opened on the application at
    `location`, built the way a case builds it: from the desk's record."""
    return CaseContext.of(Case(id=case_id, **application.opening(location)))


def runtime(case: CaseContext | None = None) -> ToolRuntime:
    """What LangChain hands a tool that takes the runtime, for a test that
    calls the tool directly: `tool.invoke({"runtime": runtime(), ...})`."""
    return ToolRuntime(
        state={}, context=case or context(), config={}, stream_writer=lambda *_: None,
        tool_call_id=None, store=None,
    )


@dataclass
class Ran:
    status: str
    output: dict[str, Any] | None
    error: str | None
    summary: str
    # What the recorder handed back, one per record, in order.
    events: list[Any] = field(default_factory=list)


def run(turn: Turn, *, anchorer: Any, model: Any, case_id: str = CASE) -> Ran:
    events: list[Any] = []
    rec = recorder(anchorer, case_id, on_event=events.append)
    result = run_turn(turn, model=model, recorder=rec)
    ok = result.status == "complete"
    try:
        rec.close(outcome="completed" if ok else "failed", reason=None if ok else result.error)
    except Exception:
        pass
    return Ran(status=result.status, output=result.output, error=result.error, summary=result.summary, events=events)
