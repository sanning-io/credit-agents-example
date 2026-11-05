"""The underwriting agent's revision: a second anchored turn, answering the
challenge that arrived.

The revision is the beat a reader is most likely to take on trust, because
by the time it happens two agents have already argued. So the tests here ask
what it is made of: the same tools as the assessment, the figures from the
calculator, the agent's own decision recorded as it made it (revised or
maintained), and a turn that did not produce one produces nothing at all.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.tools import ToolException

from credit_demo.common import home
from credit_demo.underwriter.agent import revision_turn
from credit_demo.underwriter.plan import revision_task
from credit_demo.underwriter.tools import build_revision_tools
from tests.test_a_case_opens_from_an_application import _file_records
from tests.turns import context, run, runtime
from tests.fakes import (
    ScriptedChatModel,
    TimingOutChatModel,
    fake_anchorer,
    nonsense_script,
    revision_script,
)

ASSESSMENT = {
    "applicant": "Nordvik Haulage AB",
    "recommendation": "approve_with_conditions",
    "deposit_percent": 15,
    "adjustments": [{"year": "Oct 2024 to Sep 2025", "amount_sek": 600_000, "reason": "A downturn.", "evidence": "x"}],
}

CHALLENGE = {
    "kind": "challenge",
    "about": "the adjustment to the prior year's earnings",
    "reason": "The documents do not show the downturn has ended.",
}


def _revise(repo_tmp_path, *, challenge=None, model=None):
    anchorer, plane = fake_anchorer(repo_tmp_path)
    outcome = run(
        revision_turn(
            repo_tmp_path / "case",
            context("20260101T000000000000Z-8b9c"),
            assessment=ASSESSMENT,
            challenge=challenge if challenge is not None else CHALLENGE,
        ),
        anchorer=anchorer,
        model=model or ScriptedChatModel(responses=revision_script()),
        case_id="20260101T000000000000Z-8b9c",
    )
    return outcome, anchorer, plane


def test_the_revision_records_the_models_response_and_its_own_figures(repo_tmp_path):
    outcome, _anchorer, _plane = _revise(repo_tmp_path)

    assert outcome.status == "complete", outcome.error
    revision = outcome.output
    assert revision["answers_challenge"] == CHALLENGE["about"]
    assert revision["response"] == "revised"
    assert revision["recommendation"] == "refer"
    assert revision["calculations"][0]["cover"] == 1.128
    assert "adjusted_cover" not in revision["calculations"][0]


def test_every_step_of_the_revision_is_anchored(repo_tmp_path):
    outcome, anchorer, plane = _revise(repo_tmp_path)

    types = [event.event_type for event in outcome.events]
    assert types.count("agent.tool_call.started") == 6
    # Four documents read, the application file the calculator read, and the
    # revision written.
    assert types.count("agent.file") == 6
    assert all(not event.is_gap for event in outcome.events)
    assert len(plane.submitted) == len(outcome.events)
    assert {envelope["public_key"] for envelope in plane.submitted} == {
        anchorer.public_key_hex()
    }


def test_a_model_that_holds_the_line_records_that_it_maintained(repo_tmp_path):
    """A model that disagrees with the challenge keeps its assessment and says
    so: the response is the agent's, recorded as it made it, with the
    adjustment it still relies on."""
    outcome, _anchorer, _plane = _revise(
        repo_tmp_path, model=ScriptedChatModel(responses=revision_script(response="maintained"))
    )

    assert outcome.status == "complete", outcome.error
    assert outcome.output["response"] == "maintained"
    assert outcome.output["recommendation"] == "approve_with_conditions"
    assert outcome.output["calculations"][0]["adjusted_cover"] == 1.2836


def test_the_response_is_one_of_two_words(repo_tmp_path):
    from pydantic import ValidationError

    tools = {t.name: t for t in build_revision_tools(repo_tmp_path, challenge=CHALLENGE)}
    with pytest.raises(ValidationError, match="revised"):
        tools["write_revision"].invoke(
            {"response": "agreed", "recommendation": "refer", "deposit_percent": 0, "rationale": "r",
             "summary": "s", "runtime": runtime()}
        )


def test_the_task_hands_over_the_assessment_and_the_challenge_whole():
    task = revision_task(ASSESSMENT, CHALLENGE, context())
    assert CHALLENGE["reason"] in task
    assert "A downturn." in task


def test_a_revision_the_model_never_writes_is_a_failed_turn(repo_tmp_path):
    outcome, _anchorer, _plane = _revise(
        repo_tmp_path, model=ScriptedChatModel(responses=nonsense_script())
    )

    assert outcome.status == "failed"
    assert outcome.output is None
    assert "revision" in outcome.error


def test_a_model_that_does_not_answer_is_a_failed_turn(repo_tmp_path):
    """A timeout, a refused key or a provider outage: the turn has no answer
    and says which, rather than sending the unrevised assessment across."""
    outcome, _anchorer, _plane = _revise(repo_tmp_path, model=TimingOutChatModel(responses=[]))

    assert outcome.status == "failed"
    assert outcome.output is None
    assert "did not answer in time" in outcome.error


def test_the_revision_returned_is_the_file_that_was_anchored(repo_tmp_path):
    outcome, _anchorer, _plane = _revise(repo_tmp_path)
    written = json.loads((repo_tmp_path / "case" / "revision.json").read_text())

    assert written == outcome.output


def test_the_revision_turn_reads_all_four_case_documents_again(repo_tmp_path):
    """The terms and the statements the revision's figures rest on are read
    in the revision turn, so that turn's records fingerprint them."""
    outcome, _anchorer, _plane = _revise(repo_tmp_path)

    assert outcome.status == "complete", outcome.error
    read = {f["path"] for f in _file_records(repo_tmp_path / "logstore") if f.get("role") == "input"}
    assert read == {
        str(home.SAMPLE_APPLICATION),
        str(home.BANK_STATEMENTS),
        str(home.ANNUAL_REPORT),
        str(home.CREDIT_POLICY),
    }


@pytest.mark.parametrize("unread", ["application", "bank_statements", "annual_report", "credit_policy"])
def test_write_revision_refuses_until_each_of_the_four_case_documents_is_read(repo_tmp_path, unread):
    tools = {t.name: t for t in build_revision_tools(repo_tmp_path, challenge=CHALLENGE)}
    for name in ("application", "bank_statements", "annual_report", "credit_policy"):
        if name != unread:
            tools[f"read_{name}"].invoke({"runtime": runtime()})
    tools["calculate_cover"].invoke(
        {"earnings_by_year": [{"year": "x", "ebitda_sek": 1}], "existing_annual_debt_service_sek": 1,
         "runtime": runtime()}
    )

    with pytest.raises(ToolException, match=rf"reading {unread}$"):
        tools["write_revision"].func(
            response="revised", rationale="r", recommendation="refer", deposit_percent=0, summary="s",
            runtime=runtime(),
        )
