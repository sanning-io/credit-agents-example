"""Each decision an agent makes is one `agent.decision` record: the name of
what was decided and the bare word the model chose. The document stays in the
file record; the decision record carries only the word, so a reader can ask
the trail what was decided without opening a document.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from credit_demo.checker.agent import conclusion_turn, review_turn
from credit_demo.common import decisions
from credit_demo.underwriter.agent import assessment_turn, revision_turn
from tests.fakes import fake_anchorer, fake_checker_anchorer
from tests.scripted import ScriptedChatModel, conclusion_script, review_script, revision_script, underwriting_script
from tests.turns import context, run

CASE_ID = "20260101T000000000000Z-7d8e"
CASE = context(CASE_ID)
ASSESSMENT = {"recommendation": "approve_with_conditions", "deposit_percent": 15}
CHALLENGE = {"kind": "challenge", "about": "the adjustment to the prior year's earnings"}
REVISION = {"response": "maintained", "recommendation": "approve_with_conditions", "deposit_percent": 15}


def _assess(root: Path):
    anchorer, _ = fake_anchorer(root)
    return run(
        assessment_turn(root / "case", CASE),
        anchorer=anchorer,
        model=ScriptedChatModel(responses=underwriting_script()),
        case_id=CASE_ID,
    )


def _revise(root: Path):
    anchorer, _ = fake_anchorer(root)
    return run(
        revision_turn(root / "case", CASE, assessment=ASSESSMENT, challenge=CHALLENGE),
        anchorer=anchorer,
        model=ScriptedChatModel(responses=revision_script()),
        case_id=CASE_ID,
    )


def _review(finding: str):
    def go(root: Path):
        anchorer, _ = fake_checker_anchorer(root)
        return run(
            review_turn(root / "case", CASE, assessment=ASSESSMENT),
            anchorer=anchorer,
            model=ScriptedChatModel(responses=review_script(finding=finding)),
            case_id=CASE_ID,
        )

    return go


def _conclude(root: Path):
    anchorer, _ = fake_checker_anchorer(root)
    return run(
        conclusion_turn(root / "case", CASE, assessment=ASSESSMENT, review=None, revision=REVISION, challenge=CHALLENGE),
        anchorer=anchorer,
        model=ScriptedChatModel(responses=conclusion_script(verdict="refer")),
        case_id=CASE_ID,
    )


def _recorded_decisions(root: Path) -> list[tuple[str, str]]:
    """(name, value) of every decision record in the agent's log store, the
    value read from the content the record commits to."""
    found = []
    for logs in root.glob("*logstore"):
        for path in logs.rglob("*"):
            if not path.is_file():
                continue
            try:
                record = json.loads(path.read_bytes())
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(record, dict) and record.get("event_type") == "agent.decision":
                content = json.loads((logs / "content" / record["event"]["content_hash"]).read_bytes())
                found.append((record["metadata"]["decision"]["name"], content["value"]))
    return found


@pytest.mark.parametrize(
    ("turn", "name", "value"),
    [
        pytest.param(_assess, "credit_recommendation", "approve_with_conditions", id="write_assessment"),
        pytest.param(_revise, "credit_recommendation", "refer", id="write_revision"),
        pytest.param(_review("challenge"), "review_finding", "challenge", id="record_review_challenge"),
        pytest.param(_review("agreed"), "review_finding", "agreed", id="record_review_agreed"),
        pytest.param(_conclude, "credit_verdict", "refer", id="record_conclusion"),
    ],
)
def test_a_turn_records_its_decision_as_one_decision_record(repo_tmp_path, turn, name, value):
    outcome = turn(repo_tmp_path)

    assert outcome.status == "complete", outcome.error
    assert [e.event_type for e in outcome.events].count("agent.decision") == 1
    assert _recorded_decisions(repo_tmp_path) == [(name, value)]


def test_every_tool_that_decides_is_a_tool_of_a_turn(repo_tmp_path):
    """The recorder names tools it cannot look up at construction, so a
    renamed tool would stop recording its decision without a sound."""
    turns = [
        assessment_turn(repo_tmp_path, CASE),
        revision_turn(repo_tmp_path, CASE, assessment=ASSESSMENT, challenge=CHALLENGE),
        review_turn(repo_tmp_path, CASE, assessment=ASSESSMENT),
        conclusion_turn(repo_tmp_path, CASE, assessment=ASSESSMENT, review=None, revision=REVISION, challenge=CHALLENGE),
    ]
    tool_names = {tool.name for turn in turns for tool in turn.tools}

    assert set(decisions.RECORDED_DECISIONS) <= tool_names
