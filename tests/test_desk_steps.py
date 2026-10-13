"""The case page shows only the steps that have started. What comes next is
the agents' call: the checker agent may challenge or agree, so a step that
has not started is not drawn, and the page does not say how many steps a
case will take. A hand-off line shows once its turn is finished and sent."""

from __future__ import annotations

from credit_demo.common import cases as case_states
from credit_demo.common.cases import Case
from credit_demo.underwriter import desk
from credit_demo.underwriter.web import TEMPLATES

ASSESSMENT = {"dscr": 1.3257, "minimum_dscr": 1.25, "recommendation": "refer", "summary": "Refer."}
CHALLENGE = {"about": "assessment_window", "summary": "Cover over 24 months is below the minimum."}
REVISION = {"proposed": {"recommendation": "approve_with_conditions"}, "summary": "Approve with conditions."}
CONCLUSION = {"verdict": "approve_with_conditions", "conditions": ["Quarterly DSCR covenant"], "summary": "Approve."}


def _turn(side: str, step: str, output: dict) -> dict:
    return {"side": side, "step": step, "recorded_at": "2026-10-01T13:00:00.000Z", "key": "ab" * 32, "output": output}


def _handoff(step: str, sender: str) -> dict:
    return {
        "step": step,
        "sender": sender,
        "sent_at": "2026-10-01T13:00:01.000Z",
        "received_at": "2026-10-01T13:00:02.000Z",
        "message_hash": "cd" * 32,
        "recorded_by_both": True,
    }


def _page(case: Case) -> str:
    view = desk.case_view(case)
    return TEMPLATES.get_template("case.html").render(case=view, live=view["running"], request=None)


def _shown(case: Case) -> list[tuple[str, str]]:
    return [(turn["title"], turn["state"]) for turn in desk.case_view(case)["turns"]]


def test_a_new_case_shows_only_the_assessment_in_progress():
    case = Case(id="20261001T130000000000Z-0001", step="assessment")

    assert _shown(case) == [("Assessment", "current")]
    page = _page(case)
    assert "Not started" not in page
    assert "Challenge" not in page
    assert "Assessment sent to the checker agent" not in page
    assert "step 1 of" not in page and "Agents working · step 1" in page


def test_the_checker_agents_turn_is_a_review_until_it_answers():
    case = Case(id="20261001T130000000000Z-0002", step="challenge")
    case.turns.append(_turn("underwriter", "assessment", ASSESSMENT))

    assert _shown(case) == [("Assessment", "done"), ("Review", "current")]
    page = _page(case)
    # The assessment is on its way: its line sits between the two cards.
    assert "Assessment sent to the checker agent" in page
    assert "Challenge" not in page


def test_a_revision_on_its_way_keeps_its_line_between_the_cards():
    case = Case(id="20261001T130000000000Z-0003", step="conclusion")
    case.turns += [
        _turn("underwriter", "assessment", ASSESSMENT),
        _turn("checker", "challenge", CHALLENGE),
        _turn("underwriter", "revision", REVISION),
    ]
    case.handoffs += [_handoff("assessment", "underwriter"), _handoff("challenge", "checker")]

    assert _shown(case)[-2:] == [("Revision", "done"), ("Conclusion", "current")]
    page = _page(case)
    revision_card = page.index("Underwriting agent · Revision")
    revision_line = page.index("Revision sent to the checker agent")
    conclusion_card = page.index("Checker agent · Conclusion")
    assert revision_card < revision_line < conclusion_card
    # Nothing is drawn for a hand-off that has not happened.
    assert "Conclusion sent to the underwriting agent" not in page


def test_a_case_the_checker_agreed_with_never_showed_a_challenge():
    case = Case(id="20261001T130000000000Z-0004", status=case_states.CONCLUDED)
    case.turns += [_turn("underwriter", "assessment", ASSESSMENT), _turn("checker", "conclusion", CONCLUSION)]
    case.handoffs += [_handoff("assessment", "underwriter"), _handoff("conclusion", "checker")]
    case.sent_for_signoff_at = "2026-10-01T13:00:03.000Z"

    assert _shown(case) == [("Assessment", "done"), ("Conclusion", "done"), ("Sent for sign-off", "done")]


def test_sign_off_shows_only_once_its_record_is_signed():
    case = Case(id="20261001T130000000000Z-0005", status=case_states.CONCLUDED)
    case.turns += [_turn("underwriter", "assessment", ASSESSMENT), _turn("checker", "conclusion", CONCLUSION)]
    case.handoffs += [_handoff("assessment", "underwriter"), _handoff("conclusion", "checker")]

    assert [title for title, _ in _shown(case)] == ["Assessment", "Conclusion"]


def test_a_stopped_case_shows_what_was_done_and_nothing_after():
    case = Case(
        id="20261001T130000000000Z-0006",
        status=case_states.STOPPED,
        step="challenge",
        error="the checker agent could not finish its review: timed out",
    )
    case.turns.append(_turn("underwriter", "assessment", ASSESSMENT))

    assert _shown(case) == [("Assessment", "done")]
    assert "sent to the" not in _page(case)
