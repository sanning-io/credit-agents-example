"""A decision tool counts the work of its own turn and nothing else.

It finds the reads and the calculation in the tool results after the turn's
task, the last human message in the history. Work from an earlier turn that
shares the history, a call that was refused, and a history it was not given
all count for nothing, so a decision cannot rest on work this turn did not do.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import ToolException

from credit_demo.checker.tools import record_conclusion, record_review
from credit_demo.common.case_tools import work_of
from credit_demo.underwriter.tools import write_assessment
from tests.turns import context, runtime, worked

CASE = context()
DECISION_ARGS = {
    "write_assessment": (write_assessment, {"recommendation": "approve", "deposit_percent": 0, "rationale": "r", "summary": "s"}),
    "record_review": (record_review, {"finding": "agreed", "note": "n", "summary": "s"}),
    "record_conclusion": (record_conclusion, {"verdict": "approve", "deposit_percent": 0, "note": "n", "summary": "s"}),
}


def _decide(name, repo_tmp_path, messages):
    decision, args = DECISION_ARGS[name]
    return decision.invoke({**args, "runtime": runtime(CASE, messages, workspace=repo_tmp_path)})


@pytest.mark.parametrize("name", DECISION_ARGS)
def test_work_from_an_earlier_turn_in_the_same_history_does_not_count(repo_tmp_path, name):
    earlier = worked(CASE)
    history = [*earlier, AIMessage(content="done"), HumanMessage(content="the second task")]

    with pytest.raises(ToolException, match="annual_report, application, bank_statements, credit_policy"):
        _decide(name, repo_tmp_path, history)

    # The same history with the second turn's own work after its task counts.
    assert _decide(name, repo_tmp_path, [*history, *worked(CASE)[1:]])


@pytest.mark.parametrize("name", DECISION_ARGS)
def test_a_history_it_was_not_given_counts_for_nothing(repo_tmp_path, name):
    decision, args = DECISION_ARGS[name]
    nothing = runtime(CASE, workspace=repo_tmp_path)
    nothing.state.pop("messages")

    with pytest.raises(ToolException, match="cannot run before reading"):
        decision.invoke({**args, "runtime": nothing})
    assert not list(repo_tmp_path.iterdir())


def test_a_refused_call_is_not_work():
    history = worked(CASE)
    refused = [
        ToolMessage(content="boom", name=m.name, tool_call_id=m.tool_call_id, status="error")
        if getattr(m, "name", None) in ("read_application", "calculate_cover")
        else m
        for m in history
    ]

    work = work_of(refused)

    assert "application" not in work.read
    assert work.calculations == []
    assert work_of(history).calculations != []


def test_every_calculation_of_the_turn_is_kept_in_the_order_it_was_made():
    first = worked(CASE, calculation={"earnings_by_year": [{"year": "a", "ebitda_sek": 2_000_000}], "existing_annual_debt_service_sek": 1})
    second = worked(CASE, calculation={"earnings_by_year": [{"year": "a", "ebitda_sek": 3_000_000}], "existing_annual_debt_service_sek": 1})
    history = [*first, second[-1]]

    covers = [c["cover"] for c in work_of(history).calculations]

    assert len(covers) == 2 and covers[0] < covers[1]
