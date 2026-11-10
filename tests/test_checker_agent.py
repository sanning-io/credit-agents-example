"""The checker agent: what it records, and that the decision is its own. It has the same documents
and calculator as the underwriting agent, recalculates cover itself, and no tool tells it whether
to challenge or what to conclude. The tests pin what a turn records, that every step is anchored
under the checker's own key, and that a turn that records nothing fails. The model is scripted
and the control plane is a network-free double."""

from __future__ import annotations

import json

import pytest
from langchain_core.tools import ToolException

from credit_demo.checker.agent import conclusion_turn, review_turn
from credit_demo.checker.plan import conclusion_task
from credit_demo.checker.review import challenge_for
from credit_demo.checker.tools import record_review
from tests.fakes import (
    ScriptedChatModel,
    fake_checker_anchorer,
    fake_checker_model,
    fake_conclusion_model,
    nonsense_script,
)
from tests.turns import context, run, runtime, worked

ASSESSMENT = {
    "applicant": "Nordvik Haulage AB",
    "recommendation": "approve_with_conditions",
    "deposit_percent": 15,
    "adjustments": [{"year": "Oct 2024 to Sep 2025", "amount_sek": 600_000, "reason": "A downturn.", "evidence": "x"}],
}
REVISION = {"response": "maintained", "recommendation": "approve_with_conditions", "deposit_percent": 15}
CHALLENGE = {"kind": "challenge", "about": "the adjustment to the prior year's earnings"}

TEST_CASE_ID = "20260101T000000000000Z-5e6f"
CASE = context(TEST_CASE_ID)

def _review(repo_tmp_path, *, model=None):
    anchorer, plane = fake_checker_anchorer(repo_tmp_path)
    outcome = run(
        review_turn(repo_tmp_path / "case", CASE, assessment=ASSESSMENT),
        anchorer=anchorer,
        model=model or fake_checker_model(),
        case_id=TEST_CASE_ID,
    )
    return outcome, anchorer, plane


def _conclude(repo_tmp_path, *, model=None):
    anchorer, plane = fake_checker_anchorer(repo_tmp_path)
    outcome = run(
        conclusion_turn(
            repo_tmp_path / "case", CASE, assessment=ASSESSMENT, review=None, revision=REVISION, challenge=CHALLENGE
        ),
        anchorer=anchorer,
        model=model or fake_conclusion_model(),
        case_id=TEST_CASE_ID,
    )
    return outcome, plane


def test_the_review_records_the_models_finding_and_its_own_figures(repo_tmp_path):
    outcome, _anchorer, _plane = _review(repo_tmp_path)

    assert outcome.status == "complete", outcome.error
    review = outcome.output
    assert review["finding"] == "challenge"
    assert review["about"] == "the adjustment to the prior year's earnings"
    assert len(review["calculations"]) == 1
    assert review["calculations"][0]["cover"] == 1.128
    assert "adjusted_cover" not in review["calculations"][0]


def test_every_step_of_the_review_is_anchored_under_the_checkers_own_key(repo_tmp_path):
    outcome, anchorer, plane = _review(repo_tmp_path)

    assert outcome.status == "complete", outcome.error
    types = [event.event_type for event in outcome.events]
    # Four documents, one calculation, the review.
    assert types.count("agent.tool_call.started") == 6
    assert types.count("agent.tool_call.ended") == 6
    # Four documents read, the calculation's application file and the review
    # written: six artifacts.
    assert types.count("agent.file") == 6
    assert all(not event.is_gap for event in outcome.events)
    assert len(plane.submitted) == len(outcome.events)
    assert {envelope["public_key"] for envelope in plane.submitted} == {anchorer.public_key_hex()}


def test_a_model_that_agrees_records_agreement(repo_tmp_path):
    outcome, _anchorer, _plane = _review(repo_tmp_path, model=fake_checker_model(finding="agreed"))

    assert outcome.status == "complete", outcome.error
    assert outcome.output["finding"] == "agreed"
    assert outcome.output["about"] is None


def test_a_challenge_must_say_what_it_challenges(repo_tmp_path):
    messages = worked(CASE, calculation={"earnings_by_year": [{"year": "x", "ebitda_sek": 1}],
                                         "existing_annual_debt_service_sek": 1})
    with pytest.raises(ToolException, match="about"):
        record_review.invoke({
            "finding": "challenge", "note": "No.", "summary": "No.",
            "runtime": runtime(CASE, messages, workspace=repo_tmp_path),
        })


def test_the_review_refuses_before_the_checker_has_calculated(repo_tmp_path):
    with pytest.raises(ToolException, match="calculate_cover"):
        record_review.invoke({
            "finding": "agreed", "note": "Fine.", "summary": "Fine.",
            "runtime": runtime(CASE, worked(CASE, calculate=False), workspace=repo_tmp_path),
        })


def test_a_review_the_model_never_records_is_a_failed_turn(repo_tmp_path):
    outcome, _anchorer, _plane = _review(repo_tmp_path, model=ScriptedChatModel(responses=nonsense_script()))

    assert outcome.status == "failed"
    assert outcome.output is None
    assert "review" in outcome.error


def test_the_challenge_that_crosses_is_the_review_as_recorded():
    review = {"finding": "challenge", "about": "the adjustment", "reason": "Why.", "summary": "S.", "calculations": [{"cover": 1.1}]}
    assert challenge_for(review) == {
        "kind": "challenge",
        "about": "the adjustment",
        "reason": "Why.",
        "summary": "S.",
        "calculations": [{"cover": 1.1}],
    }
    with pytest.raises(ValueError, match="no challenge to send"):
        challenge_for({**review, "finding": "agreed"})


def test_the_conclusion_records_the_models_verdict(repo_tmp_path):
    outcome, _plane = _conclude(repo_tmp_path, model=fake_conclusion_model(verdict="refer"))

    assert outcome.status == "complete", outcome.error
    conclusion = outcome.output
    assert conclusion["verdict"] == "refer"
    assert conclusion["answers_challenge"] == CHALLENGE["about"]
    assert conclusion["calculations"][0]["cover"] == 1.128


def test_a_conclusion_the_model_never_records_is_a_failed_turn(repo_tmp_path):
    outcome, _plane = _conclude(repo_tmp_path, model=ScriptedChatModel(responses=nonsense_script()))

    assert outcome.status == "failed"
    assert outcome.output is None
    assert "conclusion" in outcome.error


def test_the_recorded_conclusion_is_the_file_that_was_anchored(repo_tmp_path):
    """What the turn returns is what sits on disk and was hashed as an
    artifact, rather than a summary assembled beside it."""
    outcome, _plane = _conclude(repo_tmp_path)
    written = json.loads((repo_tmp_path / "case" / "conclusion.json").read_text())

    assert written == outcome.output
    artifacts = [e for e in outcome.events if e.event_type == "agent.file"]
    assert artifacts and all(event.content_hash for event in artifacts)


def test_the_conclusion_task_carries_what_is_on_the_record():
    agreed = conclusion_task(ASSESSMENT, {"finding": "agreed"}, None, None, CASE)
    assert "approve_with_conditions" in agreed and "no challenge or revision" in agreed

    challenged = conclusion_task(ASSESSMENT, None, CHALLENGE, REVISION, CASE)
    assert CHALLENGE["about"] in challenged and "maintained" in challenged


def test_a_challenge_label_over_the_limit_goes_back_to_the_model(repo_tmp_path):
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="80"):
        record_review.invoke({
            "finding": "challenge", "about": "x" * 81, "note": "No.", "summary": "No.",
            "runtime": runtime(CASE, worked(CASE), workspace=repo_tmp_path),
        })
