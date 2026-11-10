"""A tool call whose arguments are not valid JSON goes back to the model.

LangChain files such a call under `invalid_tool_calls`. With no readable call
in the reply there is no tool to run, so the agent loop would end the turn
with nothing written. The turn instead tells the model which call could not be
read, and why, and the model tries again, at most twice in one turn. The
third unreadable reply ends the turn with the usual reason.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage

from credit_demo.underwriter.agent import assessment_turn
from tests.fakes import ScriptedChatModel, fake_anchorer
from tests.scripted import underwriting_script
from tests.turns import context, run

BAD_ARGUMENTS = '{"recommendation": "approve", "rationale": "Cover clears.",","}'
PARSE_ERROR = (
    "Function write_assessment arguments:\n\n" + BAD_ARGUMENTS + "\n\nare not valid JSON. "
    "Received JSONDecodeError Expecting property name enclosed in double quotes: line 1 column 61 (char 60)"
)
# The position of the decision in the underwriting script: four reads and the
# calculation come first.
WRITE_AT = 5


class RecordingModel(ScriptedChatModel):
    """A scripted model that keeps what it was sent on each call."""

    seen: list = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _unreadable(call_id: str | None = "call-bad", name: str = "write_assessment") -> AIMessage:
    return AIMessage(
        content="",
        invalid_tool_calls=[{"type": "invalid_tool_call", "name": name, "args": BAD_ARGUMENTS, "id": call_id, "error": PARSE_ERROR}],
    )


def _run(workspace, responses):
    anchorer, _client = fake_anchorer(workspace)
    model = RecordingModel(responses=responses, seen=[])
    outcome = run(assessment_turn(workspace / "run", context()), anchorer=anchorer, model=model)
    return outcome, model.seen


def _answers(request) -> list[ToolMessage]:
    return [m for m in request if isinstance(m, ToolMessage)]


def test_an_unreadable_call_then_a_readable_one_completes_the_turn(repo_tmp_path):
    script = underwriting_script()
    script.insert(WRITE_AT, _unreadable())

    outcome, seen = _run(repo_tmp_path, script)

    assert outcome.status == "complete", outcome.error
    assert outcome.output["recommendation"] == "approve_with_conditions"
    told = [m for m in _answers(seen[WRITE_AT + 1]) if m.tool_call_id == "call-bad"]
    assert len(told) == 1
    assert told[0].status == "error"
    assert "write_assessment" in told[0].content
    assert "could not be read" in told[0].content
    assert "JSONDecodeError Expecting property name" in told[0].content
    # The parser's text quotes the arguments; the model is not sent them twice.
    assert BAD_ARGUMENTS not in told[0].content


def test_the_unreadable_call_is_not_counted_as_work(repo_tmp_path):
    """The decision needs the reads and the calculation in this turn; a call
    that could not be read is neither."""
    script = underwriting_script()
    script.insert(0, _unreadable(name="read_application"))

    outcome, _seen = _run(repo_tmp_path, script)

    assert outcome.status == "complete", outcome.error


def test_two_unreadable_replies_in_a_row_are_retried(repo_tmp_path):
    script = underwriting_script()
    script[WRITE_AT:WRITE_AT] = [_unreadable("call-bad-1"), _unreadable("call-bad-2")]

    outcome, seen = _run(repo_tmp_path, script)

    assert outcome.status == "complete", outcome.error
    last_request = seen[-1]
    assert {m.tool_call_id for m in _answers(last_request)} >= {"call-bad-1", "call-bad-2"}


def test_three_unreadable_replies_in_a_row_stop_the_turn(repo_tmp_path):
    script = underwriting_script()
    script[WRITE_AT:WRITE_AT] = [_unreadable("call-bad-1"), _unreadable("call-bad-2"), _unreadable("call-bad-3")]

    outcome, seen = _run(repo_tmp_path, script)

    assert outcome.status == "failed"
    assert outcome.error == (
        "the agent finished without writing an assessment: the model's last "
        "reply called write_assessment with arguments that could not be read"
    )
    # The model was called for the reads, the calculation and three bad writes.
    assert len(seen) == WRITE_AT + 3


def test_each_turn_has_its_own_retries(repo_tmp_path):
    """The limit counts one turn's replies, never a case's: a second turn
    with two unreadable replies of its own still completes."""
    for name in ("first", "second"):
        script = underwriting_script()
        script[WRITE_AT:WRITE_AT] = [_unreadable("call-bad-1"), _unreadable("call-bad-2")]
        outcome, _seen = _run(repo_tmp_path / name, script)
        assert outcome.status == "complete", outcome.error


def test_an_unreadable_call_beside_a_readable_one_is_answered_and_the_readable_one_runs(repo_tmp_path):
    script = underwriting_script()
    reads = script[0]
    script[0] = AIMessage(
        content="",
        tool_calls=reads.tool_calls,
        invalid_tool_calls=_unreadable("call-bad").invalid_tool_calls,
    )

    outcome, seen = _run(repo_tmp_path, script)

    assert outcome.status == "complete", outcome.error
    request = seen[1]
    answered = {m.tool_call_id: m for m in _answers(request)}
    assert set(answered) == {"call-1", "call-bad"}
    assert "could not be read" in answered["call-bad"].content
    # The readable call was not asked to run again.
    assert "Call it again" not in answered["call-bad"].content


def test_an_unreadable_call_with_no_id_is_still_answered(repo_tmp_path):
    """A provider rejects an assistant tool call with no matching tool
    message, and a call with no id cannot be matched, so it is given one."""
    script = underwriting_script()
    script.insert(WRITE_AT, _unreadable(call_id=None))

    outcome, seen = _run(repo_tmp_path, script)

    assert outcome.status == "complete", outcome.error
    request = seen[WRITE_AT + 1]
    asked = [c["id"] for m in request if isinstance(m, AIMessage) for c in m.invalid_tool_calls]
    assert asked and all(asked)
    assert set(asked) <= {m.tool_call_id for m in _answers(request)}


def test_the_trail_stays_complete_after_a_retry(repo_tmp_path):
    script = underwriting_script()
    script.insert(WRITE_AT, _unreadable())

    outcome, _seen = _run(repo_tmp_path, script)

    assert outcome.status == "complete", outcome.error
    kinds = [e.event_type.removeprefix("agent.") for e in outcome.events]
    # One more model call than the same turn without the retry, and no tool
    # run for the unreadable call.
    clean, _ = _run(repo_tmp_path / "clean", underwriting_script())
    clean_kinds = [e.event_type.removeprefix("agent.") for e in clean.events]
    assert kinds.count("model_call.started") == clean_kinds.count("model_call.started") + 1
    assert kinds.count("tool_call.started") == clean_kinds.count("tool_call.started")
    assert kinds[-1] == "run_closed"


def test_a_model_that_still_sends_a_reason_to_the_calculator_is_not_stopped(repo_tmp_path):
    """The calculator takes a year and an amount. A model that adds the old
    `reason` key anyway is not refused, and nothing of it reaches the
    calculation: the reason lives in the decision."""
    script = underwriting_script()
    args = dict(script[4].tool_calls[0]["args"])
    args["adjustments"] = [{**args["adjustments"][0], "reason": "A downturn that ended."}]
    script[4] = AIMessage(content="", tool_calls=[{"name": "calculate_cover", "args": args, "id": "call-calc"}])

    outcome, _seen = _run(repo_tmp_path, script)

    assert outcome.status == "complete", outcome.error
    assert outcome.output["calculations"][0]["adjustments"] == [
        {"year": args["adjustments"][0]["year"], "amount_sek": 600_000}
    ]
