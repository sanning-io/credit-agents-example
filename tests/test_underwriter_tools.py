"""The underwriter's tools, exercised directly rather than through a model:
`write_assessment` records the decision the model passes, never one of its
own, and refuses to run before the four case documents are read and cover is
calculated. The outcome must not depend on what the model feels like
writing, and "the documents read are recorded" must not be true only when
the model cooperates.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.tools import ToolException
from pydantic import ValidationError

from credit_demo.underwriter.tools import build_revision_tools, build_tools
from tests.turns import runtime

CASE = {"runtime": runtime()}
READING = ("read_application", "read_bank_statements", "read_annual_report", "read_credit_policy")
YEARS = [
    {"year": "recent", "ebitda_sek": 2_750_000},
    {"year": "prior", "ebitda_sek": 1_600_000},
]


def _tools(workspace):
    return {t.name: t for t in build_tools(workspace)}


def _read_all(tools):
    for name in READING:
        tools[name].invoke(CASE)


def _calculate(tools, **args):
    return tools["calculate_cover"].invoke(
        {"earnings_by_year": YEARS, "existing_annual_debt_service_sek": 1_100_000, **args, **CASE}
    )


def _write(tools, **args):
    return tools["write_assessment"].invoke(
        {"summary": "A short summary.", "deposit_percent": 0, **args, **CASE}
    )


def test_the_models_decision_is_what_is_recorded_whatever_the_figures_say(repo_tmp_path):
    """The calculator's cover on the unadjusted figures is 1.05x. A model that
    approves anyway gets exactly that recorded as its recommendation: the
    decision is the agent's, and nothing records a verdict of the tools' own
    beside it."""
    tools = _tools(repo_tmp_path)
    _read_all(tools)
    _calculate(tools)

    returned = _write(tools, recommendation="approve", rationale="Looks fine to me.")
    # The tool returns the bare word, which is the decision on the record;
    # the document is the file it wrote.
    assert returned == "approve"
    assessment = json.loads((repo_tmp_path / "assessment.json").read_text())

    assert assessment["recommendation"] == "approve"
    assert assessment["rationale"] == "Looks fine to me."
    assert assessment["calculations"][0]["cover"] == 1.0485
    assert not {"policy_check", "meets_minimum", "minimum_dscr", "dscr"} & set(assessment)


def test_no_tool_in_either_turn_returns_a_verdict(repo_tmp_path):
    tools = _tools(repo_tmp_path)
    _read_all(tools)
    result = json.loads(_calculate(tools))
    assert not {"verdict", "meets_minimum", "minimum_dscr", "recommendation", "finding"} & set(result)


def test_a_recommendation_outside_the_four_words_is_refused(repo_tmp_path):
    """The word is refused by the tool's own schema, never mapped to the
    nearest one. In an agent, LangChain sends the refusal back to the model
    (`tests/test_a_refused_tool_call_goes_back_to_the_model.py`)."""
    tools = _tools(repo_tmp_path)
    _read_all(tools)
    _calculate(tools)
    with pytest.raises(ValidationError, match="approve_with_conditions"):
        _write(tools, recommendation="yes please", rationale="x")


def test_a_condition_longer_than_eighty_characters_is_refused(repo_tmp_path):
    """A condition is a short label, enforced in the tool's schema like the
    one-sentence summary: the page shows it as one line."""
    tools = _tools(repo_tmp_path)
    _read_all(tools)
    _calculate(tools)
    with pytest.raises(ValidationError, match="at most 80 characters|80 characters"):
        _write(tools, recommendation="approve_with_conditions", rationale="x", conditions=["c" * 81])
    _write(tools, recommendation="approve_with_conditions", rationale="x", conditions=["c" * 80])
    ok = json.loads((repo_tmp_path / "assessment.json").read_text())
    assert ok["conditions"] == ["c" * 80]


def test_write_assessment_refuses_until_all_four_case_documents_are_read(repo_tmp_path):
    """An assessment requires the case documents: `write_assessment` must not
    be reachable having read none, or only some, of the four. The
    application is one of them: the last one left unread is named."""
    tools = _tools(repo_tmp_path)

    with pytest.raises(ToolException, match="annual_report, application, bank_statements, credit_policy"):
        _write(tools, recommendation="approve", rationale="no documents read")

    for name in ("read_bank_statements", "read_annual_report", "read_credit_policy"):
        tools[name].invoke(CASE)
    with pytest.raises(ToolException, match=r"reading application$"):
        _write(tools, recommendation="approve", rationale="three documents read")

    tools["read_application"].invoke(CASE)
    # All four read, but no cover calculated yet.
    with pytest.raises(ToolException, match="calculate cover with calculate_cover first"):
        _write(tools, recommendation="approve", rationale="no calculation")

    _calculate(tools)
    content = _write(tools, recommendation="approve", rationale="all four case documents read")
    assert content == "approve"
    assert json.loads((repo_tmp_path / "assessment.json").read_text())["recommendation"] == "approve"


def test_the_revision_tools_are_the_same_four_documents_and_calculator(repo_tmp_path):
    assessing = {t.name for t in build_tools(repo_tmp_path)}
    revising = {t.name for t in build_revision_tools(repo_tmp_path, challenge={"about": "x"})}
    assert assessing - {"write_assessment"} == revising - {"write_revision"}
    assert {*READING, "calculate_cover"} <= assessing
