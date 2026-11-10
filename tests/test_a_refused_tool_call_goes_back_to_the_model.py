"""A tool that refuses the model's input sends the model back to try again.

The tools refuse two kinds of input: a decision word outside the list they
record, and a call made before the documents it rests on were read. Either
refusal is meant for the model, so the turn goes on and the model answers
again. It must never end the turn as failed.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from credit_demo.common import home
from credit_demo.underwriter.agent import assessment_turn
from tests.turns import context, run, runtime
from tests.fakes import ScriptedChatModel, fake_anchorer
from tests.scripted import underwriting_script

TEST_CASE_ID = "20260101T000000000000Z-1a2b"


def _write(recommendation: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_assessment",
                "args": {
                    "recommendation": recommendation,
                    "deposit_percent": 15,
                    "rationale": "Cover clears the minimum.",
                    "summary": "Cover clears the minimum.",
                },
                "id": call_id,
            }
        ],
    )


def _run(workspace, responses):
    anchorer, _client = fake_anchorer(workspace)
    return run(assessment_turn(workspace / "run", context()), anchorer=anchorer, model=ScriptedChatModel(responses=responses))


def test_a_decision_word_outside_the_list_goes_back_to_the_model(repo_tmp_path):
    script = underwriting_script()
    # The model first answers with a word the tool does not record, then
    # with one it does.
    script.insert(5, _write("yes, approve it", "call-bad"))

    outcome = _run(repo_tmp_path, script)

    assert outcome.status == "complete", outcome.error
    assert outcome.output["recommendation"] == "approve_with_conditions"


def test_writing_before_reading_goes_back_to_the_model(repo_tmp_path):
    script = underwriting_script()
    # The model tries to write the assessment before it read anything.
    script.insert(0, _write("approve", "call-early"))

    outcome = _run(repo_tmp_path, script)

    assert outcome.status == "complete", outcome.error
    assert outcome.output["recommendation"] == "approve_with_conditions"


def test_a_summary_over_the_limit_goes_back_to_the_model(repo_tmp_path):
    """The limit on the agent's one-sentence summary is in the tool's own
    schema. A longer one is refused and the model writes it again."""
    script = underwriting_script()
    too_long = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_assessment",
                "args": {"recommendation": "approve", "deposit_percent": 15, "rationale": "Cover clears.", "summary": "x" * 201},
                "id": "call-long",
            }
        ],
    )
    script.insert(5, too_long)

    outcome = _run(repo_tmp_path, script)

    assert outcome.status == "complete", outcome.error
    assert len(outcome.output["summary"]) <= 200


def test_a_condition_over_the_limit_goes_back_to_the_model(repo_tmp_path):
    """A condition is a short label, with the limit in the tool's own schema.
    A longer one is refused and the model writes it again."""
    script = underwriting_script()
    too_long = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_assessment",
                "args": {
                    "recommendation": "approve_with_conditions",
                    "deposit_percent": 15,
                    "rationale": "Cover clears.",
                    "summary": "Cover clears.",
                    "conditions": ["c" * 81],
                },
                "id": "call-long-condition",
            }
        ],
    )
    script.insert(5, too_long)

    outcome = _run(repo_tmp_path, script)

    assert outcome.status == "complete", outcome.error
    assert all(len(c) <= 80 for c in outcome.output["conditions"])


def _calculate(existing_annual_debt_service_sek: int, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "calculate_cover",
                "args": {
                    "earnings_by_year": [{"year": "Oct 2025 to Sep 2026", "ebitda_sek": 2_750_000}],
                    "existing_annual_debt_service_sek": existing_annual_debt_service_sek,
                },
                "id": call_id,
            }
        ],
    )


def test_a_negative_existing_debt_service_goes_back_to_the_model(repo_tmp_path):
    script = underwriting_script()
    script.insert(4, _calculate(-3_000_000, "call-negative"))

    outcome = _run(repo_tmp_path, script)

    assert outcome.status == "complete", outcome.error
    assert all(c["existing_annual_debt_service_sek"] >= 0 for c in outcome.output["calculations"])


def test_no_debt_service_at_all_goes_back_to_the_model(repo_tmp_path):
    """An application asking for nothing, and no existing debt service: the
    total is zero, and the calculator says so to the model instead of
    stopping the case."""
    sample = context()
    copy = repo_tmp_path / "request.json"
    content = json.loads(sample.path("application").read_text(encoding="utf-8"))
    content["financing_requested"]["amount_sek"] = 0
    copy.write_text(json.dumps(content), encoding="utf-8")
    case = replace(sample, application=str(copy.relative_to(home.REPO_ROOT)))
    script = underwriting_script()
    script.insert(4, _calculate(0, "call-nothing"))
    anchorer, _client = fake_anchorer(repo_tmp_path)

    outcome = run(assessment_turn(repo_tmp_path / "run", case), anchorer=anchorer,
                  model=ScriptedChatModel(responses=script))

    assert outcome.status == "complete", outcome.error
    assert len(outcome.output["calculations"]) == 1


@pytest.mark.parametrize(
    "existing, amount, reason",
    [
        (-3_000_000, None, "the existing annual debt service cannot be negative"),
        (0, 0, "the total annual debt service is zero"),
    ],
)
def test_the_calculator_tells_the_model_why_in_plain_words(repo_tmp_path, existing, amount, reason):
    from langchain_core.tools import ToolException

    from credit_demo.common.case_tools import SHARED_TOOLS

    case = context()
    if amount is not None:
        copy = repo_tmp_path / "request.json"
        content = json.loads(case.path("application").read_text(encoding="utf-8"))
        content["financing_requested"]["amount_sek"] = amount
        copy.write_text(json.dumps(content), encoding="utf-8")
        case = replace(case, application=str(copy.relative_to(home.REPO_ROOT)))
    calculator = {t.name: t for t in SHARED_TOOLS}["calculate_cover"]

    with pytest.raises(ToolException, match=reason):
        calculator.invoke({
            "earnings_by_year": [{"year": "a", "ebitda_sek": 2_750_000}],
            "existing_annual_debt_service_sek": existing,
            "runtime": runtime(case),
        })
