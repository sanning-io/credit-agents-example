"""What the case page says when something goes wrong: plain words for people
who are not developers, with the technical text kept under the page's
Technical detail and in the log.

No message the page shows carries an exception class name, a file path or
raw model output."""

from __future__ import annotations

import re
import time

import httpx
import pytest

import credit_demo.underwriter.case as case_module
from credit_demo.common import cases as case_states
from credit_demo.common.cases import Case
from credit_demo.common.messages import (
    AUDIT_TRAIL_FAILED,
    AUDIT_TRAIL_NOT_READY,
    AUDIT_TRAIL_PREPARING,
    CASE_FINISHED,
    LOST_STEPS,
    OUT_OF_STEP,
    UNEXPECTED,
    turn_unfinished,
)
from credit_demo.evidence_builder.case_file import BundleRun
from credit_demo.underwriter import desk
from credit_demo.underwriter.web import create_app as create_underwriter_app
from tests.fakes import ScriptedChatModel, nonsense_script
from tests.rig import Plane, evidence_builder_app, services_for
from tests.servers import serving
from tests.test_case_paths import _apps, _run_a_case

# A class name as it is printed: `RuntimeError`, `httpx.ConnectError`, and the
# `Name:` that starts a printed exception.
CLASS_NAME = re.compile(r"\b\w*(?:Error|Exception|Timeout)\b")


def without_technical_detail(page: str) -> str:
    """The page as a person reads it: the Technical detail is opened on
    purpose, so it is left out. So are the styles."""
    page = re.sub(r"<details.*?</details>", "", page, flags=re.S)
    return re.sub(r"<(style|script).*?</\1>", "", page, flags=re.S)


def _wait_for_audit_trail(underwriter, case_id: str) -> Case:
    deadline = time.monotonic() + 60
    while underwriter.state.cases.get(case_id).audit_trail_state not in ("ready", "failed"):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    return underwriter.state.cases.get(case_id)


def _audit_trail_that_fails(repo_tmp_path, bundle_code: int):
    """Run a case to its end with an evidence builder whose `bundle` answers
    `bundle_code` every time; returns the case and its page."""
    plane = Plane()
    checker, settings = _apps(repo_tmp_path, plane)

    def bundle(args):
        return BundleRun(code=bundle_code, errors=["bundle: wrote the case file, and it reads incomplete"])

    with serving(checker) as checker_url, serving(
        evidence_builder_app(plane, repo_tmp_path, run_bundle=bundle)
    ) as builder_url:
        underwriter = create_underwriter_app(
            settings,
            services=services_for("underwriter", plane, repo_tmp_path),
            checker_url=checker_url,
            evidence_builder_url=builder_url,
        )
        with serving(underwriter) as url:
            case, _ = _run_a_case(underwriter, url, until=("concluded",))
            done = _wait_for_audit_trail(underwriter, case.id)
            return done, httpx.get(f"{url}/cases/{case.id}", timeout=30).text


def test_an_audit_trail_that_is_still_not_ready_says_try_again_in_a_few_minutes(repo_tmp_path, capsys):
    case, page = _audit_trail_that_fails(repo_tmp_path, bundle_code=2)

    assert case.audit_trail_state == case_states.FAILED
    assert case.audit_trail_error == AUDIT_TRAIL_NOT_READY == "The audit trail isn't ready yet. Try again in a few minutes."
    visible = without_technical_detail(page)
    assert "The audit trail isn&#39;t ready yet. Try again in a few minutes." in visible
    assert not CLASS_NAME.search(case.audit_trail_error)
    assert not CLASS_NAME.search(visible)
    # The technical text is kept: on the case, under the page's Technical
    # detail, and in the log.
    assert "gave up waiting for the case file" in case.audit_trail_detail
    assert "reads incomplete" in case.audit_trail_detail
    technical = re.search(r"<details class=\"tech\">.*?</details>", page, flags=re.S)
    assert technical and "Technical detail" in technical.group(0)
    assert "gave up waiting for the case file" in technical.group(0)
    assert "gave up waiting for the case file" in capsys.readouterr().err


def test_a_case_file_that_could_not_be_built_says_it_couldnt_be_created(repo_tmp_path, capsys):
    case, page = _audit_trail_that_fails(repo_tmp_path, bundle_code=4)

    assert case.audit_trail_error == AUDIT_TRAIL_FAILED == "The audit trail couldn't be created. Try again."
    visible = without_technical_detail(page)
    assert "The audit trail couldn&#39;t be created. Try again." in visible
    assert not CLASS_NAME.search(visible)
    assert "RuntimeError" in case.audit_trail_detail and "could not build the case file" in case.audit_trail_detail
    assert "could not build the case file" in capsys.readouterr().err


def test_an_unexpected_exception_in_a_case_says_something_went_wrong(repo_tmp_path, monkeypatch, capsys):
    def boom(self):
        raise ValueError("the sandbox exploded at /srv/credit/secret.json")

    monkeypatch.setattr(case_module.CaseRun, "_run", boom)
    plane = Plane()
    _checker, settings = _apps(repo_tmp_path, plane)
    underwriter = create_underwriter_app(
        settings, services=services_for("underwriter", plane, repo_tmp_path), checker_url="http://127.0.0.1:9"
    )
    with serving(underwriter) as url:
        case, page = _run_a_case(underwriter, url)
        listed = httpx.get(f"{url}/", timeout=30).text

    assert case.status == case_states.STOPPED
    assert case.error == UNEXPECTED == "Something went wrong and the case stopped. Run the case again."
    visible = without_technical_detail(page)
    assert UNEXPECTED in visible
    assert not CLASS_NAME.search(visible) and "secret.json" not in visible
    assert "ValueError" in case.error_detail
    assert "ValueError" in capsys.readouterr().err
    assert not CLASS_NAME.search(without_technical_detail(listed))


def test_an_agent_that_ends_a_turn_without_its_file_says_it_stopped_before_finishing(repo_tmp_path):
    plane = Plane()
    checker, settings = _apps(repo_tmp_path, plane)
    underwriter = create_underwriter_app(
        settings,
        services=services_for("underwriter", plane, repo_tmp_path, model=ScriptedChatModel(responses=nonsense_script())),
        checker_url="http://127.0.0.1:9",
    )
    with serving(underwriter) as url:
        case, page = _run_a_case(underwriter, url)

    expected = "The underwriter stopped before finishing its assessment. Run the case again."
    assert case.error == expected == turn_unfinished("underwriter", "assessment")
    visible = without_technical_detail(page)
    assert expected in visible
    assert not CLASS_NAME.search(visible)
    assert "the agent finished without writing an assessment" in case.error_detail


def test_a_checker_that_ends_a_turn_without_its_file_says_it_stopped_before_finishing(repo_tmp_path):
    plane = Plane()
    checker, settings = _apps(repo_tmp_path, plane, checker_model=ScriptedChatModel(responses=nonsense_script()))
    with serving(checker) as checker_url:
        underwriter = create_underwriter_app(
            settings, services=services_for("underwriter", plane, repo_tmp_path), checker_url=checker_url
        )
        with serving(underwriter) as url:
            case, page = _run_a_case(underwriter, url)

    assert case.error == "The checker stopped before finishing its review. Run the case again."
    assert not CLASS_NAME.search(without_technical_detail(page))
    assert "the agent finished without writing a review" in case.error_detail


@pytest.mark.parametrize(
    ("owed", "text"),
    [
        ("assessment", "The underwriter stopped before finishing its assessment. Run the case again."),
        ("revision", "The underwriter stopped before finishing its revision. Run the case again."),
    ],
)
def test_each_underwriter_turn_names_its_own_work(owed, text):
    assert turn_unfinished("underwriter", owed) == text
    assert turn_unfinished("checker", "conclusion") == "The checker stopped before finishing its conclusion. Run the case again."


def test_lost_steps_say_the_audit_trail_will_not_be_complete(repo_tmp_path):
    from sanning_anchor import AnchorCallbackHandler

    from credit_demo.common import documents
    from credit_demo.common.turn import run_turn
    from credit_demo.underwriter.agent import assessment_turn
    from tests.fakes import fake_anchorer, underwriting_script
    from tests.test_turn_reports import _LosesOneRecord
    from tests.turns import CASE, context

    anchorer, _ = fake_anchorer(repo_tmp_path)
    anchorer._client = _LosesOneRecord(nth=3)
    recorder = AnchorCallbackHandler(
        anchorer, case_id=CASE, raise_on_gaps=False, artifacts={"root": str(documents.REPO_ROOT)}
    )
    result = run_turn(
        assessment_turn(repo_tmp_path / "run", context()),
        model=ScriptedChatModel(responses=underwriting_script()),
        recorder=recorder,
    )

    assert result.message == LOST_STEPS == "Part of this case wasn't saved, so its audit trail won't be complete. Run the case again."
    assert result.error == "1 step(s) of this turn could not be recorded"

    # And the page of a case stopped on it says the plain words and keeps the count.
    stopped = Case(id=CASE, status=case_states.STOPPED, step="assessment", error=result.message, error_detail=result.error)
    view = desk.stopped(stopped)
    assert view["title"] == LOST_STEPS
    assert view["detail"] == "1 step(s) of this turn could not be recorded"


class _CheckerRefuses:
    """`httpx` as the case module sees it, with the hand-off answered by a
    checker that refuses it."""

    def __init__(self, refusal: str, message: str) -> None:
        self.refusal, self.message = refusal, message

    def __getattr__(self, name):
        return getattr(httpx, name)

    def post(self, *args, **kwargs):
        return httpx.Response(409, json={"refused": self.refusal, "message": self.message, "recorded": True})


@pytest.mark.parametrize(
    ("refusal", "expected"),
    [
        ("this agent is waiting for the revision, not assessment", OUT_OF_STEP),
        ("this case has ended here; it takes no further hand-off", CASE_FINISHED),
    ],
    ids=["out of step", "case already finished"],
)
def test_a_hand_off_the_checker_refuses_shows_the_plain_words(repo_tmp_path, monkeypatch, refusal, expected):
    plane = Plane()
    _checker, settings = _apps(repo_tmp_path, plane)
    underwriter = create_underwriter_app(
        settings, services=services_for("underwriter", plane, repo_tmp_path), checker_url="http://127.0.0.1:9"
    )
    monkeypatch.setattr(case_module, "httpx", _CheckerRefuses(refusal, expected))
    with serving(underwriter) as url:
        case, page = _run_a_case(underwriter, url)

    assert case.error == expected
    assert refusal in case.error_detail
    assert expected.replace("'", "&#39;") in without_technical_detail(page)
    assert refusal not in without_technical_detail(page)


def test_the_export_notes_are_the_approved_words():
    running = Case(id="x", status=case_states.CONCLUDED)
    assert desk.export_state(running)["note"] == "Preparing the audit trail. This can take a few minutes."
    assert desk.export_state(running)["note"] == AUDIT_TRAIL_PREPARING
    sandbox = desk.export_state(running, sandbox=True)["note"]
    assert sandbox == "Preparing the audit trail. This can take a few minutes. Sandbox records, not evidence."
    failed = Case(id="x", status=case_states.CONCLUDED, audit_trail_state=case_states.FAILED)
    assert desk.export_state(failed)["note"] == "The audit trail couldn't be created. Try again."
    assert desk.export_state(Case(id="x"))["note"] == "Available when the agents finish"
    ready = Case(id="x", status=case_states.CONCLUDED, audit_trail_state=case_states.READY)
    assert desk.export_state(ready)["note"] == "Both agents' signed records"
