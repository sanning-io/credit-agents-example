"""What each card on the case page shows: the conditions once, on the
conclusion and in the box at the top, and a result line that names the
decision without restating the deposit."""

from __future__ import annotations

from credit_demo.common import cases as case_states
from credit_demo.common.cases import Case
from credit_demo.underwriter import desk

CONDITIONS = ["Deposit of 15% of the purchase price", "Quarterly DSCR covenant"]
ASSESSMENT = {"recommendation": "approve_with_conditions", "deposit_percent": 15, "conditions": CONDITIONS, "summary": "A."}
CHALLENGE = {"about": "the fallback reasoning", "reason": "Why.", "summary": "C."}
REVISION = {"response": "revised", "recommendation": "approve_with_conditions", "deposit_percent": 15, "conditions": CONDITIONS, "summary": "R."}
CONCLUSION = {"verdict": "approve_with_conditions", "deposit_percent": 15, "conditions": CONDITIONS, "summary": "D."}


def _turn(side, step, output):
    return {"side": side, "step": step, "recorded_at": "2026-10-01T13:00:00.000Z", "key": "ab" * 32, "output": output}


def _handoff(step):
    return {"step": step, "sent_at": "2026-10-01T13:00:01.000Z", "received_at": "2026-10-01T13:00:02.000Z", "message_hash": "cd" * 32, "recorded_by_both": True}


def _case():
    case = Case(id="20261001T130000000000Z-0c4d", status=case_states.CONCLUDED, conclusion=CONCLUSION)
    case.turns += [
        _turn("underwriter", "assessment", ASSESSMENT),
        _turn("checker", "challenge", CHALLENGE),
        _turn("underwriter", "revision", REVISION),
        _turn("checker", "conclusion", CONCLUSION),
    ]
    case.handoffs += [_handoff(step) for step in ("assessment", "challenge", "revision", "conclusion")]
    return case


def test_the_conditions_show_once_on_the_conclusion_card():
    cards = {turn["key"]: turn["card"] for turn in desk.case_view(_case())["turns"]}
    assert "conditions" not in cards["assessment"]
    assert "conditions" not in cards["revision"]
    assert cards["conclusion"]["conditions"] == CONDITIONS


def test_the_result_lines_name_the_decision_without_the_deposit():
    cards = {turn["key"]: turn["card"] for turn in desk.case_view(_case())["turns"]}
    assert cards["assessment"]["result"] == "Recommends: approve with conditions"
    assert cards["revision"]["result"] == "Revises its assessment: approve with conditions"
    assert cards["conclusion"]["result"] == "Concludes: approve with conditions"


def test_the_box_shows_the_conditions_as_the_agent_wrote_them():
    box = desk.case_view(_case())["conclusion"]
    assert box["conditions"] == " · ".join(CONDITIONS)


def test_the_page_has_no_cover_panel():
    """The argument differs from run to run, so a fixed set of figures could
    point at something the agents never argued about."""
    view = desk.case_view(_case())
    assert "cover" not in view


def _referred(*, response):
    case = _case()
    case.conclusion = {**CONCLUSION, "verdict": "refer", "conditions": []}
    case.turns[2]["output"] = {**REVISION, "response": response}
    case.turns[3]["output"] = case.conclusion
    case.sent_for_signoff_at = "2026-10-01T13:00:09.000Z"
    return case


def test_a_referral_the_agents_agreed_on_reads_as_a_referral():
    box = desk.case_view(_referred(response="revised"))["conclusion"]
    assert box["verdict"] == "Referred to a senior credit officer"
    assert box["label"] == "Agents' recommendation · referred"
    assert box["challenges"] == "1 challenge, resolved"


def test_a_referral_after_a_maintained_assessment_reads_as_a_disagreement():
    case = _referred(response="maintained")
    view = desk.case_view(case)
    assert view["conclusion"]["label"] == "The agents did not agree · referred"
    assert view["conclusion"]["challenges"] == "1 challenge, not resolved"
    assert view["turns"][-1]["title"] == "Referred to a senior credit officer"
    assert "Referred to a senior credit officer" in desk.application_row(case)["status"]


def test_the_revision_card_says_revises_or_maintains():
    revised = {t["key"]: t["card"] for t in desk.case_view(_case())["turns"]}["revision"]
    assert revised["result"].startswith("Revises its assessment")
    case = _case()
    case.turns[2]["output"] = {**REVISION, "response": "maintained"}
    maintained = {t["key"]: t["card"] for t in desk.case_view(case)["turns"]}["revision"]
    assert maintained["result"].startswith("Maintains its assessment")
