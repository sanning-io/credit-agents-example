"""One agent turn: a standard LangChain agent, recorded by Sanning.

A turn is `create_agent` with the turn's tools and prompt, invoked once with
the agent's recorder on the callbacks and, when the turn works a case, the
case as LangChain's runtime context (`credit_demo.common.context`). It owes one file (an assessment, a
review, a revision or a conclusion), which its last tool writes; a turn that
ends without writing it failed, and says why.
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
    """What one turn is: its name on the record, its tools and prompts, the
    file it owes (`output`, described as `owed` in an error), and the case it
    works as runtime context, if its tools read one."""

    name: str
    tools: list[Any]
    system_prompt: str
    task: str
    output: Path
    owed: str
    context: Any = None
    # What the page says when the turn ends without its file.
    unfinished: str = UNEXPECTED


@dataclass
class TurnResult:
    status: str  # "complete" | "failed"
    output: dict[str, Any] | None = None
    summary: str = ""
    # The technical text of a failure, for the log and the Technical detail.
    error: str | None = None
    # What the page says about a failure.
    message: str = UNEXPECTED
    # The turn failed because a call timed out (the close reason says so).
    timed_out: bool = False


def _back_to_the_model(error: Exception, _request: Any) -> str | None:
    """A tool's refusal, as the message the model reads; `None` for anything
    else, which then ends the turn."""
    return str(error) if isinstance(error, ToolException) else None


# How many times one turn sends the model back to a reply whose tool-call
# arguments were not valid JSON. The next such reply ends the turn.
MAX_UNREADABLE_RETRIES = 2
_PARSE_ERROR = re.compile(r"Received (\w+Error.*)$", re.DOTALL)


def _what_was_wrong(call: Mapping[str, Any]) -> str:
    """The parser's complaint about a call, without the arguments it quotes."""
    error = str(call.get("error") or "the arguments are not valid JSON")
    found = _PARSE_ERROR.search(error)
    return (found.group(1) if found else error)[:300]


@after_model(can_jump_to=["model"])
def _send_unreadable_calls_back(state: Mapping[str, Any], _runtime: Any) -> dict[str, Any] | None:
    """Answer each tool call whose arguments did not parse, naming the call and
    the parse error.

    LangChain files such a call under `invalid_tool_calls`. A reply with no
    other call ends the turn there, so this sends it back to the model, up to
    `MAX_UNREADABLE_RETRIES` times in one turn. A reply that also holds
    readable calls goes on to run them, and the unreadable ones are answered
    beside them. Every call id in the history gets a tool message, because the
    provider rejects a request that has an assistant tool call with none."""
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
    """Run one turn and read back the file it owes."""
    # Cleared first, so the check below can only pass on a file THIS turn wrote.
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
        # A tool refuses input the model can fix with a `ToolException`; that
        # goes back to the model, which tries again.
        # A reply whose tool-call arguments cannot be read goes back as well.
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
    # With `raise_on_gaps=True`, a lost record raises out of `invoke` at the
    # step whose record was lost (from sanning-anchor 0.18.0), and the
    # `except` above fails the turn. A gap the handler does not raise for (one
    # in an error callback) is still a lost record, and a turn with one is not
    # complete.
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
    except Exception as exc:
        return TurnResult(
            status="failed",
            summary=summary,
            error=f"{turn.owed} was written and could not be read back: {describe(exc)}",
        )
    return TurnResult(status="complete", output=output, summary=summary)


def _timed_out(exc: BaseException | None) -> bool:
    """Whether `exc`, or what it was raised from, is a timeout."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def _lost(recorder: Any) -> int:
    """Records of this recorder's missing from the trail, by the handler's own
    completeness check, which counts one lost record once."""
    try:
        recorder.assert_complete()
    except IncompleteTrailError as incomplete:
        return len(incomplete.gaps)
    return 0


def _text_of(message: Any) -> str:
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)


# The words providers use for a reply cut off at its output limit.
_CUT_OFF = ("length", "max_tokens")
# The finish reasons of a reply that ended as the model meant it to.
_ENDED_NORMALLY = ("stop", "tool_calls", "end_turn", "tool_use", "function_call", "stop_sequence")


def why_it_stopped(messages: Sequence[Any]) -> str:
    """Why the model's last reply ended the turn without the file, read off
    the reply itself (`finish_reason`, `invalid_tool_calls`), never guessed:
    cut off at the output limit, a tool call whose arguments did not parse,
    an empty reply the model service ended with an error or a filter, or an
    answer in text with no tool call."""
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
