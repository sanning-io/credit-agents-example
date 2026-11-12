"""Three failures with a known cause each get their own words on the page, and none of them says
"Run the case again" unless running it again can help: an agent ID that Sanning holds under another
seed, a different active evidence key, and a checker that does not answer the hand-off in time."""

from __future__ import annotations

import httpx
from sanning_anchor import RotationUnauthorizedError
from sanning_anchor.handover_key import HandoverKeyCheck, Outcome

import credit_demo.underwriter.case as case_module
from credit_demo.common import cases as case_states
from credit_demo.common.messages import (
    CHECKER_DID_NOT_ANSWER,
    EVIDENCE_KEY_ALREADY_ACTIVE,
    RUN_AGAIN,
    SDK_WORDS_FOR_ANOTHER_ACTIVE_KEY,
    UNEXPECTED,
    agent_id_already_enrolled,
    reason_for,
)
from credit_demo.underwriter.web import create_app as create_underwriter_app
from tests.rig import Plane, services_for
from tests.servers import serving
from tests.test_case_paths import _apps, _run_a_case
from tests.test_the_page_speaks_plainly import CLASS_NAME, _audit_trail_that_fails, without_technical_detail

AGENT_ID = "underwriter-sandbox-3f2a"


class _RefusesTheAgentId:
    """A control plane that holds the agent ID under another key."""

    def submit(self, envelope, index=None, **_kwargs):
        raise RotationUnauthorizedError(AGENT_ID)


def test_an_agent_id_held_under_another_seed_names_the_id_and_does_not_offer_a_rerun():
    said = agent_id_already_enrolled(AGENT_ID)

    assert AGENT_ID in said
    assert "different signing seed" in said
    assert "new agent ID" in said and "seed that enrolled" in said
    assert RUN_AGAIN not in said
    assert not CLASS_NAME.search(said)


def test_the_cause_is_found_anywhere_along_the_exception_chain():
    try:
        try:
            raise RotationUnauthorizedError(AGENT_ID)
        except RotationUnauthorizedError as inner:
            raise RuntimeError("the callback failed") from inner
    except RuntimeError as outer:
        assert reason_for(outer) == agent_id_already_enrolled(AGENT_ID)
    assert reason_for(ValueError("something else")) == UNEXPECTED
    assert reason_for(None) == UNEXPECTED


def test_the_underwriters_agent_id_held_under_another_seed_stops_the_case_with_its_own_words(repo_tmp_path):
    plane = Plane()
    _checker, settings = _apps(repo_tmp_path, plane)
    services = services_for("underwriter", plane, repo_tmp_path)
    services.anchorer._client = _RefusesTheAgentId()
    underwriter = create_underwriter_app(settings, services=services, checker_url="http://127.0.0.1:9")
    with serving(underwriter) as url:
        case, page = _run_a_case(underwriter, url)

    assert case.status == case_states.STOPPED
    assert case.error == agent_id_already_enrolled(AGENT_ID)
    visible = without_technical_detail(page)
    assert AGENT_ID in visible and RUN_AGAIN not in visible
    assert not CLASS_NAME.search(visible)


def test_the_checkers_agent_id_held_under_another_seed_reaches_the_underwriters_page(repo_tmp_path):
    plane = Plane()
    _checker, settings = _apps(repo_tmp_path, plane)
    checker_services = services_for("checker", plane, repo_tmp_path)
    checker_services.anchorer._client = _RefusesTheAgentId()
    checker = _checker_over(checker_services, repo_tmp_path)
    with serving(checker) as checker_url:
        underwriter = create_underwriter_app(
            settings, services=services_for("underwriter", plane, repo_tmp_path), checker_url=checker_url
        )
        with serving(underwriter) as url:
            case, page = _run_a_case(underwriter, url)

    assert case.status == case_states.STOPPED
    assert case.error == agent_id_already_enrolled(AGENT_ID)
    assert RUN_AGAIN not in without_technical_detail(page)


def _checker_over(services, root):
    """The checker app of `_apps`, over other services."""
    from credit_demo.checker.config import settings_from_env
    from credit_demo.checker.web import create_app as create_checker_app
    from tests.desks import sample_desk
    from tests.rig import CHECKER_SEED, UNDERWRITER_SEED
    from tests.test_bar_a_case_end_to_end import _env

    settings = settings_from_env(
        service="checker",
        default_agent_id="checker",
        default_port=8002,
        env=_env(CHECKER_SEED, UNDERWRITER_SEED, root / "checker-cases"),
    )
    return create_checker_app(settings, services=services, desk_url=sample_desk())


class _TimesOut:
    """`httpx` as the case module sees it, with a checker that never answers."""

    def __getattr__(self, name):
        return getattr(httpx, name)

    def post(self, *args, **kwargs):
        raise httpx.ReadTimeout("timed out")


def test_a_checker_that_does_not_answer_says_so_and_names_the_slow_log_store(repo_tmp_path, monkeypatch):
    plane = Plane()
    _checker, settings = _apps(repo_tmp_path, plane)
    underwriter = create_underwriter_app(
        settings, services=services_for("underwriter", plane, repo_tmp_path), checker_url="http://127.0.0.1:9"
    )
    monkeypatch.setattr(case_module, "httpx", _TimesOut())
    with serving(underwriter) as url:
        case, page = _run_a_case(underwriter, url)

    assert case.error == CHECKER_DID_NOT_ANSWER
    assert "within 10 minutes" in case.error and "slow log store" in case.error
    assert case_module.HANDOFF_TIMEOUT_S == 600, "the message says 10 minutes"
    assert CHECKER_DID_NOT_ANSWER in without_technical_detail(page)
    assert "ReadTimeout" in case.error_detail
    assert not CLASS_NAME.search(without_technical_detail(page))


def test_a_checker_that_is_refused_for_another_reason_is_not_called_slow(repo_tmp_path, monkeypatch):
    class _Refuses(_TimesOut):
        def post(self, *args, **kwargs):
            raise httpx.ConnectError("refused")

    plane = Plane()
    _checker, settings = _apps(repo_tmp_path, plane)
    underwriter = create_underwriter_app(
        settings, services=services_for("underwriter", plane, repo_tmp_path), checker_url="http://127.0.0.1:9"
    )
    monkeypatch.setattr(case_module, "httpx", _Refuses())
    with serving(underwriter) as url:
        case, _page = _run_a_case(underwriter, url)

    assert case.error == UNEXPECTED


def test_the_sdks_words_for_another_active_key_are_the_ones_the_desk_looks_for():
    from sanning_anchor.cli import handover_key_report

    code, line = handover_key_report(
        HandoverKeyCheck(ours="ab" * 32, outcome=Outcome("other_key", theirs="cd" * 32)),
        "https://api.sanning.io",
    )

    assert code == 4
    assert SDK_WORDS_FOR_ANOTHER_ACTIVE_KEY in line


def test_a_different_active_evidence_key_says_to_revoke_it_in_the_console(repo_tmp_path):
    from sanning_anchor.cli import handover_key_report

    _code, line = handover_key_report(
        HandoverKeyCheck(ours="ab" * 32, outcome=Outcome("other_key", theirs="cd" * 32)),
        "https://api.sanning.io",
    )
    case, page = _audit_trail_that_fails(repo_tmp_path, bundle_code=4, errors=[line])

    assert case.audit_trail_state == case_states.FAILED
    assert case.audit_trail_error == EVIDENCE_KEY_ALREADY_ACTIVE
    assert "Settings" in case.audit_trail_error and "Signing keys" in case.audit_trail_error
    assert "try the export again" in case.audit_trail_error
    assert EVIDENCE_KEY_ALREADY_ACTIVE in without_technical_detail(page)
    assert SDK_WORDS_FOR_ANOTHER_ACTIVE_KEY in case.audit_trail_detail
