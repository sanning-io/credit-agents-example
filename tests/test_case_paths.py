"""The ways a case can go other than straight to its conclusion, seen from
the credit desk: a case that stops, a message changed on the way, and a
audit trail that has to wait for Sanning to stamp the records."""

from __future__ import annotations

import json
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from credit_demo.checker.config import settings_from_env as checker_settings
from credit_demo.checker.web import create_app as create_checker_app
from credit_demo.common import cases as case_states
from credit_demo.common.sanning import BundleRun
from credit_demo.underwriter.config import settings_from_env as underwriter_settings
from credit_demo.underwriter.web import create_app as create_underwriter_app
from tests.fakes import ScriptedChatModel, nonsense_script
from tests.scripted import SharedScriptedModel
from tests.rig import CHECKER_SEED, UNDERWRITER_SEED, Plane, services_for
from tests.desks import sample_desk
from tests.servers import serving
from tests.test_bar_a_case_end_to_end import _env


def _apps(root, plane, *, checker_model=None):
    checker = create_checker_app(
        checker_settings(
            service="checker",
            prefix="CHECKER",
            default_agent_id="checker",
            default_port=8002,
            env=_env("CHECKER", CHECKER_SEED, UNDERWRITER_SEED, root / "checker-cases"),
        ),
        services=services_for("checker", plane, root, model=checker_model),
        desk_url=sample_desk(),
    )
    settings = underwriter_settings(
        service="underwriter",
        prefix="UNDERWRITER",
        default_agent_id="underwriter",
        default_port=8001,
        env=_env("UNDERWRITER", UNDERWRITER_SEED, CHECKER_SEED, root / "underwriter-cases"),
    )
    return checker, settings


def _run_a_case(underwriter, url, *, until=("concluded", "stopped")):
    started = httpx.post(f"{url}/cases", follow_redirects=False, timeout=30)
    case_id = started.headers["location"].rsplit("/", 1)[-1]
    deadline = time.monotonic() + 120
    while underwriter.state.cases.get(case_id).status not in until:
        assert time.monotonic() < deadline, "the case did not end"
        time.sleep(0.05)
    return underwriter.state.cases.get(case_id), httpx.get(f"{url}/cases/{case_id}", timeout=30).text


def _closing_outcomes(logs):
    """The outcome each closing record in one agent's log store states."""
    stated = []
    for path in sorted(logs.rglob("*")):
        try:
            record = json.loads(path.read_bytes())
        except (ValueError, RecursionError, IsADirectoryError):
            continue
        if isinstance(record, dict) and record.get("event_type") == "agent.run_closed":
            closing = (record.get("metadata") or {}).get("closing") or {}
            stated.append(((closing.get("outcome") or {}).get("stated") or {}).get("status"))
    return stated


def _closing_reasons(logs):
    """The reason each closing record in one agent's log store states."""
    reasons = []
    for path in sorted(logs.rglob("*")):
        try:
            record = json.loads(path.read_bytes())
        except (ValueError, RecursionError, IsADirectoryError):
            continue
        if isinstance(record, dict) and record.get("event_type") == "agent.run_closed":
            closing = (record.get("metadata") or {}).get("closing") or {}
            reasons.append(((closing.get("outcome") or {}).get("stated") or {}).get("reason"))
    return reasons


def test_a_case_whose_review_fails_stops_and_the_desk_says_where(repo_tmp_path):
    plane = Plane()
    checker, settings = _apps(
        repo_tmp_path, plane, checker_model=ScriptedChatModel(responses=nonsense_script())
    )
    with serving(checker) as checker_url:
        underwriter = create_underwriter_app(
            settings, services=services_for("underwriter", plane, repo_tmp_path), checker_url=checker_url
        )
        with serving(underwriter) as url:
            case, page = _run_a_case(underwriter, url)

    assert case.status == case_states.STOPPED
    assert "could not finish its review" in case.error
    # The review is the case's second step.
    assert "Stopped at step 2" in page
    assert "could not finish its review" in page
    # Both agents closed their record of the case as failed.
    assert set(_closing_outcomes(repo_tmp_path / "underwriter-logs")) == {"failed"}
    assert set(_closing_outcomes(repo_tmp_path / "checker-logs")) == {"failed"}
    # The record carries the SDK's reason code; the words stay on the case.
    assert set(_closing_reasons(repo_tmp_path / "underwriter-logs")) == {"error"}
    assert set(_closing_reasons(repo_tmp_path / "checker-logs")) == {"error"}


def _altering_proxy(checker_url: str) -> FastAPI:
    """Stands between the two agents and changes one figure in the
    challenge on its way back: what an attacker on the wire can do."""
    proxy = FastAPI()

    @proxy.post("/cases/{case_id}/handoffs")
    async def handoffs(case_id: str, request: Request) -> JSONResponse:
        answer = httpx.post(
            f"{checker_url}/cases/{case_id}/handoffs", json=await request.json(), timeout=120
        )
        body = answer.json()
        reply = body.get("reply")
        if reply:
            message = json.loads(reply["message"])
            if message.get("kind") == "challenge":
                message["cover_over_policy_window"] = 1.9
                reply["message"] = json.dumps(message)
        return JSONResponse(status_code=answer.status_code, content=body)

    @proxy.post("/cases/{case_id}/receipts")
    async def receipts(case_id: str, request: Request) -> JSONResponse:
        answer = httpx.post(f"{checker_url}/cases/{case_id}/receipts", json=await request.json(), timeout=60)
        return JSONResponse(status_code=answer.status_code, content=answer.json())

    return proxy


def test_a_challenge_changed_on_the_way_is_refused_and_the_case_stops(repo_tmp_path):
    plane = Plane()
    checker, settings = _apps(repo_tmp_path, plane)
    with serving(checker) as checker_url, serving(_altering_proxy(checker_url)) as proxy_url:
        underwriter = create_underwriter_app(
            settings, services=services_for("underwriter", plane, repo_tmp_path), checker_url=proxy_url
        )
        with serving(underwriter) as url:
            case, page = _run_a_case(underwriter, url)

    assert case.status == case_states.STOPPED
    assert "refused on receipt" in case.error
    # The underwriting agent never acted on it: no revision was written.
    assert case.turn("underwriter", "revision") is None
    assert "Stopped at step" in page


def test_the_audit_trail_waits_while_the_records_are_not_stamped(repo_tmp_path, monkeypatch):
    """`bundle` exits 2 until Sanning has stamped the case's records. The
    audit trail is tried again rather than failing."""
    plane = Plane()
    checker, settings = _apps(repo_tmp_path, plane)
    services = services_for("underwriter", plane, repo_tmp_path)
    real_bundle = services.bundle
    answers = iter([BundleRun(code=2, errors=["not stamped yet"])])

    def bundle(args, env):
        return next(answers, None) or real_bundle(args, env)

    services.bundle = bundle
    with serving(checker) as checker_url:
        underwriter = create_underwriter_app(settings, services=services, checker_url=checker_url)
        import credit_demo.underwriter.audit_trail as audit_trail

        monkeypatch.setattr(audit_trail, "TRY_EVERY_S", 0.0)
        with serving(underwriter) as url:
            case, _page = _run_a_case(underwriter, url, until=("concluded",))
            deadline = time.monotonic() + 60
            while underwriter.state.cases.get(case.id).audit_trail_state not in ("ready", "failed"):
                assert time.monotonic() < deadline
                time.sleep(0.05)

    assert underwriter.state.cases.get(case.id).audit_trail_state == "ready"


def test_a_pack_that_could_not_run_fails_the_audit_trail_without_retrying(repo_tmp_path, monkeypatch):
    """`bundle` exits 4 when it could not run (an empty case, a bad key). Only
    exit 2 is worth another try; waiting on exit 4 would never end."""
    plane = Plane()
    checker, settings = _apps(repo_tmp_path, plane)
    services = services_for("underwriter", plane, repo_tmp_path)
    calls = []

    def bundle(args, env):
        calls.append(args)
        return BundleRun(code=4, errors=["no records for this case"])

    services.bundle = bundle
    with serving(checker) as checker_url:
        underwriter = create_underwriter_app(settings, services=services, checker_url=checker_url)
        import credit_demo.underwriter.audit_trail as audit_trail

        monkeypatch.setattr(audit_trail, "TRY_EVERY_S", 0.0)
        with serving(underwriter) as url:
            case, _page = _run_a_case(underwriter, url, until=("concluded",))
            deadline = time.monotonic() + 60
            while underwriter.state.cases.get(case.id).audit_trail_state not in ("ready", "failed"):
                assert time.monotonic() < deadline
                time.sleep(0.05)

    done = underwriter.state.cases.get(case.id)
    assert done.audit_trail_state == "failed"
    assert "no records for this case" in done.audit_trail_error
    assert len(calls) == 1
    assert "--from" not in calls[0] and "--to" not in calls[0]


def test_two_cases_at_once_both_conclude(repo_tmp_path, monkeypatch):
    """The checker agent works one case while another's receipt arrives.
    The receipt must not wait behind the other case's model turn."""
    import credit_demo.underwriter.case as case_module

    monkeypatch.setattr(case_module, "RECEIPT_TIMEOUT_S", 2.0)
    plane = Plane()
    checker, settings = _apps(repo_tmp_path, plane, checker_model=SharedScriptedModel(delay=0.5))
    with serving(checker) as checker_url:
        underwriter = create_underwriter_app(
            settings,
            services=services_for("underwriter", plane, repo_tmp_path, model=SharedScriptedModel()),
            checker_url=checker_url,
        )
        with serving(underwriter) as url:
            ids = [
                httpx.post(f"{url}/cases", follow_redirects=False, timeout=30).headers["location"].rsplit("/", 1)[-1]
                for _ in range(2)
            ]
            deadline = time.monotonic() + 120
            while any(underwriter.state.cases.get(i).status == "running" for i in ids):
                assert time.monotonic() < deadline, "the cases did not end"
                time.sleep(0.1)
            cases = [underwriter.state.cases.get(i) for i in ids]

    assert [c.status for c in cases] == ["concluded", "concluded"], [c.error for c in cases]


def _list_and_page(url, case_id):
    return httpx.get(f"{url}/", timeout=30).text, httpx.get(f"{url}/cases/{case_id}", timeout=30).text


def test_a_review_that_fails_says_checker_agent_on_the_list_and_the_page(repo_tmp_path):
    plane = Plane()
    checker, settings = _apps(repo_tmp_path, plane, checker_model=ScriptedChatModel(responses=nonsense_script()))
    with serving(checker) as checker_url:
        underwriter = create_underwriter_app(
            settings, services=services_for("underwriter", plane, repo_tmp_path), checker_url=checker_url
        )
        with serving(underwriter) as url:
            case, _page = _run_a_case(underwriter, url)
            listed, page = _list_and_page(url, case.id)

    assert "the checker agent could not finish its review" in case.error
    for shown in (listed, page):
        assert "checking agent" not in shown.lower()
        assert "checker agent could not finish its review" in shown


def test_a_checker_that_cannot_be_reached_says_checker_agent_on_the_list_and_the_page(repo_tmp_path):
    plane = Plane()
    _checker, settings = _apps(repo_tmp_path, plane)
    underwriter = create_underwriter_app(
        settings, services=services_for("underwriter", plane, repo_tmp_path), checker_url="http://127.0.0.1:9"
    )
    with serving(underwriter) as url:
        case, _page = _run_a_case(underwriter, url)
        listed, page = _list_and_page(url, case.id)

    assert case.status == case_states.STOPPED
    assert "did not reach the checker agent" in case.error
    for shown in (listed, page):
        assert "checking agent" not in shown.lower()
        assert "did not reach the checker agent" in shown


def test_an_audit_trail_the_checker_cannot_build_says_checker_agent(repo_tmp_path, monkeypatch):
    import credit_demo.underwriter.audit_trail as audit_trail

    class _CheckerRefusesThePack:
        def __getattr__(self, name):
            return getattr(httpx, name)

        def post(self, url, *args, **kwargs):
            if str(url).endswith("/pack"):
                return httpx.Response(500, text="no pack")
            return httpx.post(url, *args, **kwargs)

    plane = Plane()
    checker, settings = _apps(repo_tmp_path, plane)
    monkeypatch.setattr(audit_trail, "httpx", _CheckerRefusesThePack())
    with serving(checker) as checker_url:
        underwriter = create_underwriter_app(
            settings, services=services_for("underwriter", plane, repo_tmp_path), checker_url=checker_url
        )
        with serving(underwriter) as url:
            case, _page = _run_a_case(underwriter, url, until=("concluded",))
            deadline = time.monotonic() + 60
            while underwriter.state.cases.get(case.id).audit_trail_state not in ("ready", "failed"):
                assert time.monotonic() < deadline
                time.sleep(0.05)
            listed, page = _list_and_page(url, case.id)

    assert "the checker agent's pack could not be built" in underwriter.state.cases.get(case.id).audit_trail_error
    for shown in (listed, page):
        assert "checking agent" not in shown.lower()
    assert "checker agent&#39;s pack could not be built" in page or "checker agent's pack could not be built" in page
