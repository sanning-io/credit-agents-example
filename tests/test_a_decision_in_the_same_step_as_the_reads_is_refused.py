"""A decision tool called in the same step as the reads is always refused. LangChain starts every
call of one reply at once, each reading the state as it stood when the step began, so the
decision cannot rest on work that is not in that state yet. The model's next reply is accepted,
and the turn records one decision."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from credit_demo.checker.agent import conclusion_turn, review_turn
from credit_demo.underwriter.agent import assessment_turn, revision_turn
from tests.fakes import ScriptedChatModel, fake_anchorer, fake_checker_anchorer
from tests.scripted import conclusion_script, review_script, revision_script, underwriting_script
from tests.turns import context, run

CASE_ID = "20260101T000000000000Z-7d8e"
CASE = context(CASE_ID)
ASSESSMENT = {"recommendation": "approve_with_conditions", "deposit_percent": 15}
CHALLENGE = {"kind": "challenge", "about": "the adjustment to the prior year's earnings"}
REVISION = {"response": "maintained", "recommendation": "approve_with_conditions", "deposit_percent": 15}


class AllInOneStep(ScriptedChatModel):
    """Asks for every call of the script in its first reply. If the decision
    was refused, asks for it once more; otherwise ends the turn. `results`
    keeps what the decision tool answered each time."""

    responses: list = []
    decision: str = ""
    results: list = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.outputs import ChatGeneration, ChatResult

        calls = [call for message in self.responses[:-1] for call in message.tool_calls]
        answers = [m for m in messages if isinstance(m, ToolMessage) and m.name == self.decision]
        self.results[:] = [(m.status, m.content) for m in answers]
        replies = sum(1 for m in messages if getattr(m, "type", None) == "ai")
        if replies == 0:
            reply = AIMessage(content="", tool_calls=calls)
        elif answers and answers[-1].status == "error" and replies == 1:
            reply = AIMessage(content="", tool_calls=[{**calls[-1], "id": "again"}])
        else:
            reply = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=reply)])


def _assessment(root):
    anchorer, _ = fake_anchorer(root)
    return "write_assessment", anchorer, assessment_turn(root / "case", CASE), underwriting_script()


def _revision(root):
    anchorer, _ = fake_anchorer(root)
    return "write_revision", anchorer, revision_turn(root / "case", CASE, assessment=ASSESSMENT, challenge=CHALLENGE), revision_script()


def _review(root):
    anchorer, _ = fake_checker_anchorer(root)
    return "record_review", anchorer, review_turn(root / "case", CASE, assessment=ASSESSMENT), review_script()


def _conclusion(root):
    anchorer, _ = fake_checker_anchorer(root)
    turn = conclusion_turn(root / "case", CASE, assessment=ASSESSMENT, review=None, revision=REVISION, challenge=CHALLENGE)
    return "record_conclusion", anchorer, turn, conclusion_script()


@pytest.mark.parametrize("setup", [_assessment, _revision, _review, _conclusion])
def test_a_decision_in_the_same_step_as_the_reads_is_refused(repo_tmp_path, setup):
    decision, anchorer, turn, script = setup(repo_tmp_path)
    model = AllInOneStep(responses=script, decision=decision, results=[])

    outcome = run(turn, anchorer=anchorer, model=model, case_id=CASE_ID)

    # Refused the first time, with the reason the model reads, then accepted.
    assert [status for status, _ in model.results] == ["error", "success"]
    assert "cannot run before reading" in model.results[0][1]
    assert outcome.status == "complete", outcome.error
    assert [e.event_type for e in outcome.events].count("agent.decision") == 1
    assert len(outcome.output["calculations"]) == 1
