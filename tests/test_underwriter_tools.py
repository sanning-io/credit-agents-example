"""The underwriter's tools, called directly rather than through a model: `write_assessment` records
the decision the model passes, never one of its own, and refuses to run before the four case
documents are read and cover is calculated. The tests hand a decision tool the history of a turn
(`tests.turns.worked`)."""

from __future__ import annotations

import json

import pytest
from langchain_core.tools import ToolException
from pydantic import ValidationError

from credit_demo.underwriter.tools import ASSESSMENT_TOOLS, REVISION_TOOLS, write_assessment
from tests.turns import runtime, worked

READING = ("read_application", "read_bank_statements", "read_annual_report", "read_credit_policy")
UNADJUSTED = {
    "earnings_by_year": [
        {"year": "recent", "ebitda_sek": 2_750_000},
        {"year": "prior", "ebitda_sek": 1_600_000},
    ],
    "existing_annual_debt_service_sek": 1_100_000,
}


def _write(workspace, messages, **args):
    return write_assessment.invoke(
        {"summary": "A short summary.", "deposit_percent": 0, **args, "runtime": runtime(messages=messages, workspace=workspace)}
    )


def test_the_models_decision_is_what_is_recorded_whatever_the_figures_say(repo_tmp_path):
    """The calculator's cover on the unadjusted figures is 1.05x. A model that
    approves anyway gets exactly that recorded as its recommendation: the
    decision is the agent's, and nothing records a verdict of the tools' own
    beside it."""
    messages = worked(calculation=UNADJUSTED)

    returned = _write(repo_tmp_path, messages, recommendation="approve", rationale="Looks fine to me.")
    # The tool returns the bare word, which is the decision on the record;
    # the document is the file it wrote.
    assert returned == "approve"
    assessment = json.loads((repo_tmp_path / "assessment.json").read_text())

    assert assessment["recommendation"] == "approve"
    assert assessment["rationale"] == "Looks fine to me."
    assert assessment["calculations"][0]["cover"] == 1.0485
    assert not {"policy_check", "meets_minimum", "minimum_dscr", "dscr"} & set(assessment)


def test_no_tool_in_either_turn_returns_a_verdict():
    result = json.loads(worked(calculation=UNADJUSTED)[-1].content)
    assert not {"verdict", "meets_minimum", "minimum_dscr", "recommendation", "finding"} & set(result)


def test_a_recommendation_outside_the_four_words_is_refused(repo_tmp_path):
    """The word is refused by the tool's own schema, never mapped to the
    nearest one. In an agent, LangChain sends the refusal back to the model
    (`tests/test_a_refused_tool_call_goes_back_to_the_model.py`)."""
    with pytest.raises(ValidationError, match="approve_with_conditions"):
        _write(repo_tmp_path, worked(), recommendation="yes please", rationale="x")


def test_a_condition_longer_than_eighty_characters_is_refused(repo_tmp_path):
    """A condition is a short label, enforced in the tool's schema like the
    one-sentence summary: the page shows it as one line."""
    messages = worked()
    with pytest.raises(ValidationError, match="at most 80 characters|80 characters"):
        _write(repo_tmp_path, messages, recommendation="approve_with_conditions", rationale="x", conditions=["c" * 81])
    _write(repo_tmp_path, messages, recommendation="approve_with_conditions", rationale="x", conditions=["c" * 80])
    ok = json.loads((repo_tmp_path / "assessment.json").read_text())
    assert ok["conditions"] == ["c" * 80]


def test_write_assessment_refuses_until_all_four_case_documents_are_read(repo_tmp_path):
    """An assessment requires the case documents: `write_assessment` must not
    be reachable having read none, or only some, of the four. The
    application is one of them: the last one left unread is named."""
    with pytest.raises(ToolException, match="annual_report, application, bank_statements, credit_policy"):
        _write(repo_tmp_path, worked(reads=(), calculate=False), recommendation="approve", rationale="no documents read")

    three = worked(reads=("read_bank_statements", "read_annual_report", "read_credit_policy"), calculate=False)
    with pytest.raises(ToolException, match=r"reading application$"):
        _write(repo_tmp_path, three, recommendation="approve", rationale="three documents read")

    # All four read, but no cover calculated yet.
    with pytest.raises(ToolException, match="calculate cover with calculate_cover first"):
        _write(repo_tmp_path, worked(calculate=False), recommendation="approve", rationale="no calculation")

    assert not (repo_tmp_path / "assessment.json").exists()

    content = _write(repo_tmp_path, worked(), recommendation="approve", rationale="all four case documents read")
    assert content == "approve"
    assert json.loads((repo_tmp_path / "assessment.json").read_text())["recommendation"] == "approve"


def test_the_revision_tools_are_the_same_four_documents_and_calculator():
    assessing = {t.name for t in ASSESSMENT_TOOLS}
    revising = {t.name for t in REVISION_TOOLS}
    assert assessing - {"write_assessment"} == revising - {"write_revision"}
    assert {*READING, "calculate_cover"} <= assessing
