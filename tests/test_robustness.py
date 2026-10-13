"""What the services do when something outside the happy path happens: a
restart, a setting that points the wrong way, a bad case id, a double click,
a malformed message field. No case is ever left looking busy forever, and no
record goes missing without an error."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from credit_demo.checker.config import settings_from_env as checker_settings
from credit_demo.checker.web import create_app as create_checker_app
from credit_demo.common import cases as case_states
from credit_demo.common.cases import CaseStore
from credit_demo.underwriter import desk
from credit_demo.underwriter.config import settings_from_env as underwriter_settings
from credit_demo.underwriter.web import create_app as create_underwriter_app
from tests.desks import sample_desk
from tests.rig import CHECKER_SEED, UNDERWRITER_SEED, Plane, services_for
from tests.test_bar_a_case_end_to_end import _env


def _underwriter_settings(cases_dir: Path, **overrides):
    env = _env("UNDERWRITER", UNDERWRITER_SEED, CHECKER_SEED, cases_dir)
    env.update(overrides)
    return underwriter_settings(
        service="underwriter", prefix="UNDERWRITER", default_agent_id="underwriter", default_port=8001, env=env
    )


def _checker_settings(cases_dir: Path):
    return checker_settings(
        service="checker",
        prefix="CHECKER",
        default_agent_id="checker",
        default_port=8002,
        env=_env("CHECKER", CHECKER_SEED, UNDERWRITER_SEED, cases_dir),
    )


def _wait(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def _concluded_case(store: CaseStore, *, audit_trail_state: str) -> case_states.Case:
    case = store.create()
    case.status = case_states.CONCLUDED
    case.concluded_at = case_states.now()
    case.conclusion = {"kind": "conclusion", "verdict": "approve"}
    case.audit_trail_state = audit_trail_state
    store.save(case)
    return case


def test_an_audit_trail_left_preparing_by_a_restart_is_resumed(repo_tmp_path, monkeypatch):
    import credit_demo.underwriter.web as web

    started: list[str] = []
    monkeypatch.setattr(web, "prepare", lambda **kwargs: started.append(kwargs["case_id"]))
    cases_dir = repo_tmp_path / "underwriter-cases"
    case = _concluded_case(CaseStore(cases_dir), audit_trail_state=case_states.PREPARING)

    create_underwriter_app(
        _underwriter_settings(cases_dir),
        services=services_for("underwriter", Plane(), repo_tmp_path),
        checker_url="http://127.0.0.1:9",
    )

    _wait(lambda: started == [case.id])


def test_an_audit_trail_marked_ready_with_no_file_is_prepared_again(repo_tmp_path, monkeypatch):
    """A case can say its audit trail is ready while the file is gone: a
    disk that does not survive a deploy. The page would offer a download
    that answers 404."""
    import credit_demo.underwriter.web as web

    started: list[str] = []
    monkeypatch.setattr(web, "prepare", lambda **kwargs: started.append(kwargs["case_id"]))
    cases_dir = repo_tmp_path / "underwriter-cases"
    store = CaseStore(cases_dir)
    gone = _concluded_case(store, audit_trail_state=case_states.READY)
    present = _concluded_case(store, audit_trail_state=case_states.READY)
    web.audit_trail_path(store, present.id).write_bytes(b"zip")

    create_underwriter_app(
        _underwriter_settings(cases_dir),
        services=services_for("underwriter", Plane(), repo_tmp_path),
        checker_url="http://127.0.0.1:9",
    )

    _wait(lambda: started == [gone.id])
    time.sleep(0.2)
    assert started == [gone.id]


def test_a_cases_folder_outside_the_home_folder_refuses_to_start(repo_tmp_path, tmp_path):
    """Every file an agent reads or writes must sit under one root for the
    recorder to record it. A cases folder elsewhere would leave every output
    file unrecorded while the audit trail still reads Verified."""
    outside = tmp_path / "elsewhere"
    with pytest.raises(ValueError) as caught:
        create_underwriter_app(
            _underwriter_settings(outside),
            services=services_for("underwriter", Plane(), repo_tmp_path),
            checker_url="http://127.0.0.1:9",
        )
    assert "UNDERWRITER_RUNS_DIR" in str(caught.value)
    with pytest.raises(ValueError):
        create_checker_app(_checker_settings(outside), services=services_for("checker", Plane(), repo_tmp_path))


def test_a_case_whose_setup_fails_stops_rather_than_running_forever(repo_tmp_path):
    settings = _underwriter_settings(
        repo_tmp_path / "underwriter-cases", UNDERWRITER_SANNING_SIGNING_SEED="not-a-seed"
    )
    app = create_underwriter_app(settings, checker_url="http://127.0.0.1:9")
    client = TestClient(app)

    case_id = client.post("/cases", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]

    _wait(lambda: app.state.cases.get(case_id).status != case_states.RUNNING)
    assert app.state.cases.get(case_id).status == case_states.STOPPED
    assert "Agents working" not in client.get(f"/cases/{case_id}").text


@pytest.mark.parametrize("conditions", [5, "Deposit 15%", None, ["Deposit 15%", 7]])
def test_a_malformed_list_of_conditions_does_not_break_the_desk(repo_tmp_path, conditions):
    store = CaseStore(repo_tmp_path / "cases")
    case = _concluded_case(store, audit_trail_state=case_states.READY)
    case.conclusion = {"kind": "conclusion", "verdict": "approve_with_conditions", "conditions": conditions}
    case.turns = [{"side": "checker", "step": "conclusion", "output": dict(case.conclusion)}]
    case.handoffs = [{"step": "conclusion", "sender": "checker", "recorded_by_both": True}]

    view = desk.case_view(case)
    row = desk.application_row(case)

    expected = ["Deposit 15%"] if conditions in ("Deposit 15%", ["Deposit 15%", 7]) else []
    conclusion_step = next(t for t in view["turns"] if t["key"] == "conclusion")
    assert conclusion_step["card"]["conditions"] == expected
    assert row["conclusion"] == "Approve with conditions"


@pytest.mark.parametrize("case_id", ["..", "%2E%2E", "not-a-case", "20260101T000000000000Z-00zz"])
def test_a_case_id_that_is_not_one_is_refused_and_nothing_is_created(repo_tmp_path, case_id):
    cases_dir = repo_tmp_path / "checker-cases"
    client = TestClient(create_checker_app(_checker_settings(cases_dir), services=services_for("checker", Plane(), repo_tmp_path)))

    answer = client.post(f"/cases/{case_id}/handoffs", json={"message": "{}", "sanning_handoff": {"x": 1}})

    assert answer.status_code in (404, 400)
    assert not any(cases_dir.glob("*/case.json"))
    assert not (repo_tmp_path / "case.json").exists()


def test_an_unverified_message_creates_no_case(repo_tmp_path):
    cases_dir = repo_tmp_path / "checker-cases"
    client = TestClient(create_checker_app(_checker_settings(cases_dir), services=services_for("checker", Plane(), repo_tmp_path)))

    answer = client.post(
        "/cases/20260101T000000000000Z-0001/handoffs",
        json={"message": "{}", "sanning_handoff": {"sanning_handoff": "x"}},
    )

    assert answer.status_code >= 400
    assert not any(cases_dir.glob("*/case.json"))


def test_two_try_again_clicks_at_once_start_one_preparation(repo_tmp_path, monkeypatch):
    import credit_demo.underwriter.web as web

    started: list[str] = []
    release = threading.Event()

    def slow_prepare(**kwargs):
        started.append(kwargs["case_id"])
        release.wait(5)

    monkeypatch.setattr(web, "prepare", slow_prepare)
    cases_dir = repo_tmp_path / "underwriter-cases"
    case = _concluded_case(CaseStore(cases_dir), audit_trail_state=case_states.FAILED)
    app = create_underwriter_app(
        _underwriter_settings(cases_dir),
        services=services_for("underwriter", Plane(), repo_tmp_path),
        checker_url="http://127.0.0.1:9",
    )
    client = TestClient(app)
    barrier = threading.Barrier(2)

    def click():
        barrier.wait()
        client.post(f"/cases/{case.id}/audit-trail", follow_redirects=False)

    clicks = [threading.Thread(target=click) for _ in range(2)]
    for thread in clicks:
        thread.start()
    for thread in clicks:
        thread.join()
    time.sleep(0.3)
    release.set()

    assert started == [case.id]


def test_a_problem_closing_the_record_does_not_undo_a_conclusion(repo_tmp_path, monkeypatch):
    """Once the conclusion is recorded, the case is concluded. A failure to
    close the recorder afterwards is reported, and does not turn the case
    into a stopped one."""
    import credit_demo.underwriter.case as case_module
    from tests.servers import serving

    real_recorder = case_module.recorder

    def recorder_whose_close_fails(*args, **kwargs):
        handler = real_recorder(*args, **kwargs)
        real_close = handler.close

        def close(**close_kwargs):
            if close_kwargs.get("outcome") == "completed":
                raise RuntimeError("the (fake) plane went away")
            return real_close(**close_kwargs)

        handler.close = close
        return handler

    monkeypatch.setattr(case_module, "recorder", recorder_whose_close_fails)
    plane = Plane()
    checker = create_checker_app(
        _checker_settings(repo_tmp_path / "checker-cases"),
        services=services_for("checker", plane, repo_tmp_path),
        desk_url=sample_desk(),
    )
    with serving(checker) as checker_url:
        app = create_underwriter_app(
            _underwriter_settings(repo_tmp_path / "underwriter-cases"),
            services=services_for("underwriter", plane, repo_tmp_path),
            checker_url=checker_url,
        )
        client = TestClient(app)
        case_id = client.post("/cases", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
        _wait(lambda: app.state.cases.get(case_id).status != case_states.RUNNING, timeout=60)

    assert app.state.cases.get(case_id).status == case_states.CONCLUDED
