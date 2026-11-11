"""One agent turn: a LangChain agent invoked once with the agent's recorder on its callbacks.

A turn owes one file, which its last tool writes. A turn that ends without it
failed, and says why.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx
from langchain.agents import create_agent
from langchain.agents.middleware import ToolErrorMiddleware, after_model
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import ToolException
from sanning_anchor import IncompleteTrailError

from .errors import describe, without_local_paths
from .messages import LOST_STEPS, UNEXPECTED


@dataclass
class Turn:
    """`output` is the file the turn owes, described as `owed` in an error."""

    name: str
    tools: list[Any]
    system_prompt: str
    task: str
    output: Path
    owed: str
    context: Any = None
    unfinished: str = UNEXPECTED


@dataclass
class TurnResult:
    status: str  # "complete" | "failed"
    output: dict[str, Any] | None = None
    summary: str = ""
    # The technical text, for the log.
    error: str | None = None
    # The plain words for the page.
    message: str = UNEXPECTED
    # Sets the close reason to timeout.
    timed_out: bool = False


def _back_to_the_model(error: Exception, _request: Any) -> str | None:
    """A tool's refusal as the message the model reads; any other error ends the turn."""
    return str(error) if isinstance(error, ToolException) else None


# How many times one turn sends the model back to a reply whose tool-call arguments were not valid JSON.
MAX_UNREADABLE_RETRIES = 2
_PARSE_ERROR = re.compile(r"Received (\w+Error.*)$", re.DOTALL)


def _what_was_wrong(call: Mapping[str, Any]) -> str:
    error = str(call.get("error") or "the arguments are not valid JSON")
    found = _PARSE_ERROR.search(error)
    return (found.group(1) if found else error)[:300]


@after_model(can_jump_to=["model"])
def _send_unreadable_calls_back(state: Mapping[str, Any], _runtime: Any) -> dict[str, Any] | None:
    """Answer each tool call whose arguments did not parse, naming the parse error.

    A reply with no readable call is sent back to the model, up to
    `MAX_UNREADABLE_RETRIES` times. Every call id needs a tool message, because
    the provider rejects a request with an unanswered tool call.
    """
    messages = list(state["messages"])
    last = messages[-1] if messages else None
    if not isinstance(last, AIMessage) or not last.invalid_tool_calls:
        return None
    start = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    unreadable_replies = sum(1 for m in messages[start + 1 :] if isinstance(m, AIMessage) and m.invalid_tool_calls)
    if not last.tool_calls and unreadable_replies > MAX_UNREADABLE_RETRIES:
        return None
    # A call without an id cannot be answered, and the provider rejects it.
    calls = [
        call if call.get("id") else {**call, "id": f"unreadable-{unreadable_replies}-{n}"}
        for n, call in enumerate(last.invalid_tool_calls)
    ]
    again = "" if last.tool_calls else " Call it again."
    answers = [
        ToolMessage(
            content=(
                f"Your call to {call.get('name') or 'a tool'} could not be read: {_what_was_wrong(call)}. "
                "A call's arguments must be one valid JSON object that follows the tool's schema, "
                f"and nothing else.{again}"
            ),
            name=call.get("name") or "unknown",
            tool_call_id=call["id"],
            status="error",
        )
        for call in calls
    ]
    update: dict[str, Any] = {"messages": [last.model_copy(update={"invalid_tool_calls": calls}), *answers]}
    if not last.tool_calls:
        update["jump_to"] = "model"
    return update


def run_turn(turn: Turn, *, model: Any, recorder: Any) -> TurnResult:
    # Cleared first, so a file found afterwards was written by this turn.
    try:
        turn.output.parent.mkdir(parents=True, exist_ok=True)
        turn.output.unlink(missing_ok=True)
    except OSError as exc:
        return TurnResult(
            status="failed",
            error=without_local_paths(
                f"the workspace holds an earlier {turn.output.name} that could not be "
                f"cleared, so this turn cannot be told apart from the one before it: {describe(exc)}"
            ),
        )

    agent = create_agent(
        model,
        list(turn.tools),
        system_prompt=turn.system_prompt,
        middleware=[ToolErrorMiddleware(on_error=_back_to_the_model), _send_unreadable_calls_back],
        context_schema=type(turn.context) if turn.context is not None else None,
    )
    messages: list[Any] = []
    lost_before = _lost(recorder)
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": turn.task}]},
            config={"callbacks": [recorder], "run_name": turn.name},
            context=turn.context,
        )
        messages = result.get("messages", []) if isinstance(result, dict) else []
    except Exception as exc:
        return TurnResult(status="failed", error=without_local_paths(describe(exc)), timed_out=_timed_out(exc))

    summary = _text_of(messages[-1]) if messages else ""
    # A lost record raises out of `invoke`. A gap the handler does not raise
    # for (one in an error callback) still means the turn is not complete.
    lost = _lost(recorder) - lost_before
    if lost:
        return TurnResult(
            status="failed",
            summary=summary,
            error=f"{lost} step(s) of this turn could not be recorded",
            message=LOST_STEPS,
        )
    if not turn.output.exists():
        return TurnResult(
            status="failed",
            summary=summary,
            error=f"the agent finished without writing {turn.owed}{why_it_stopped(messages)}",
            message=turn.unfinished,
        )
    try:
        output = json.loads(turn.output.read_text())
    except (OSError, ValueError) as exc:
        return TurnResult(
            status="failed",
            summary=summary,
            error=f"{turn.owed} was written and could not be read back: {describe(exc)}",
        )
    return TurnResult(status="complete", output=output, summary=summary)


def _timed_out(exc: BaseException | None) -> bool:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def _lost(recorder: Any) -> int:
    """The number of this recorder's records missing from the trail."""
    try:
        recorder.assert_complete()
    except IncompleteTrailError as incomplete:
        return len(incomplete.gaps)
    return 0


def _text_of(message: Any) -> str:
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)


_CUT_OFF = ("length", "max_tokens")
_ENDED_NORMALLY = ("stop", "tool_calls", "end_turn", "tool_use", "function_call", "stop_sequence")


def why_it_stopped(messages: Sequence[Any]) -> str:
    """Why the model's last reply ended the turn without the file, read off the reply, never guessed."""
    last = next((m for m in reversed(messages) if getattr(m, "type", None) == "ai"), None)
    if last is None:
        return ""
    metadata = getattr(last, "response_metadata", None) or {}
    finish = metadata.get("finish_reason") or metadata.get("stop_reason")
    invalid = [
        str(call.get("name") or "a tool")
        for call in (getattr(last, "invalid_tool_calls", None) or [])
        if isinstance(call, Mapping)
    ]
    if finish in _CUT_OFF:
        detail = ": the model's last reply was cut off at its output limit"
        if invalid:
            detail += f", partway through its call to {', '.join(invalid)}"
        return detail
    if invalid:
        return f": the model's last reply called {', '.join(invalid)} with arguments that could not be read"
    if not getattr(last, "content", ""):
        if finish and finish not in _ENDED_NORMALLY:
            return f": the model service returned no answer (finish reason: {finish})"
        return ": the model's last reply called no tool and said nothing"
    return ": the model's last reply answered in text and called no tool"
