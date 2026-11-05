"""What the recorder says about the example and about how a case ended: the
agent's version on every record, a close reason that matches what happened,
and a close that fails being reported, never dropped."""

from __future__ import annotations

import json
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from credit_demo.common import sanning
from credit_demo.underwriter import case as underwriter_case
from credit_demo.underwriter.agent import assessment_turn
from credit_demo.underwriter.web import create_app as create_underwriter_app
from tests.fakes import ScriptedChatModel, fake_anchorer, nonsense_script, underwriting_script
from tests.rig import Plane, services_for
from tests.servers import serving
from tests.test_case_paths import (
    _altering_proxy,
    _apps,
    _closing_outcomes,
    _closing_reasons,
    _run_a_case,
)
from tests.turns import context, run


def _records(logs):
    for path in sorted(logs.rglob("*")):
        try:
            record = json.loads(path.read_bytes())
        except (ValueError, RecursionError, IsADirectoryError):
            continue
        if isinstance(record, dict) and record.get("event_type"):
            yield record


def test_every_record_names_the_version_of_the_example(repo_tmp_path):
    anchorer, _client = fake_anchorer(repo_tmp_path)
    outcome = run(
        assessment_turn(repo_tmp_path / "run", context()),
        anchorer=anchorer,
        model=ScriptedChatModel(responses=underwriting_script()),
    )

    assert outcome.status == "complete", outcome.error
    records = list(_records(repo_tmp_path / "logstore"))
    assert records
    versions = {(((r.get("metadata") or {}).get("gen_ai") or {}).get("agent") or {}).get("version") for r in records}
    assert versions == {sanning.AGENT_VERSION}


def test_every_decision_is_a_record_with_the_decisions_name(repo_tmp_path):
    anchorer, _client = fake_anchorer(repo_tmp_path)
    outcome = run(
        assessment_turn(repo_tmp_path / "run", context()),
        anchorer=anchorer,
        model=ScriptedChatModel(responses=underwriting_script()),
    )

    assert outcome.status == "complete", outcome.error
    decisions = [r for r in _records(repo_tmp_path / "logstore") if r["event_type"] == "agent.decision"]
    assert [d["metadata"]["decision"]["name"] for d in decisions] == ["credit_recommendation"]


def test_a_refused_answer_closes_the_case_with_the_reason_policy(repo_tmp_path):
    plane = Plane()
    checker, settings = _apps(repo_tmp_path, plane)
    with serving(checker) as checker_url, serving(_altering_proxy(checker_url)) as proxy_url:
        underwriter = create_underwriter_app(
            settings, services=services_for("underwriter", plane, repo_tmp_path), checker_url=proxy_url
        )
        with serving(underwriter) as url:
            case, _page = _run_a_case(underwriter, url)

    assert case.status == "stopped"
    assert set(_closing_outcomes(repo_tmp_path / "underwriter-logs")) == {"failed"}
    assert set(_closing_reasons(repo_tmp_path / "underwriter-logs")) == {"policy"}


def _slow_proxy(checker_url: str) -> FastAPI:
    """A checker agent that takes longer to answer than the underwriter waits."""
    proxy = FastAPI()

    @proxy.post("/cases/{case_id}/handoffs")
    async def handoffs(case_id: str, request: Request) -> JSONResponse:
        time.sleep(1.5)
        answer = httpx.post(f"{checker_url}/cases/{case_id}/handoffs", json=await request.json(), timeout=120)
        return JSONResponse(status_code=answer.status_code, content=answer.json())

    return proxy


def test_a_checker_that_does_not_answer_in_time_closes_the_case_with_the_reason_timeout(repo_tmp_path, monkeypatch):
    monkeypatch.setattr(underwriter_case, "HANDOFF_TIMEOUT_S", 0.3)
    plane = Plane()
    checker, settings = _apps(repo_tmp_path, plane)
    with serving(checker) as checker_url, serving(_slow_proxy(checker_url)) as proxy_url:
        underwriter = create_underwriter_app(
            settings, services=services_for("underwriter", plane, repo_tmp_path), checker_url=proxy_url
        )
        with serving(underwriter) as url:
            case, _page = _run_a_case(underwriter, url)

    assert case.status == "stopped"
    assert set(_closing_reasons(repo_tmp_path / "underwriter-logs")) == {"timeout"}


def test_a_model_that_times_out_closes_the_case_with_the_reason_timeout(repo_tmp_path):
    from tests.fakes import TimingOutChatModel

    plane = Plane()
    checker, settings = _apps(repo_tmp_path, plane)
    services = services_for("underwriter", plane, repo_tmp_path, model=TimingOutChatModel(responses=[]))
    with serving(checker) as checker_url:
        underwriter = create_underwriter_app(settings, services=services, checker_url=checker_url)
        with serving(underwriter) as url:
            case, _page = _run_a_case(underwriter, url)

    assert case.status == "stopped"
    assert set(_closing_reasons(repo_tmp_path / "underwriter-logs")) == {"timeout"}


def test_a_close_that_fails_is_reported(repo_tmp_path, monkeypatch):
    told: list[str] = []
    monkeypatch.setattr(underwriter_case, "tell_the_operator", told.append)
    plane = Plane()
    checker, settings = _apps(
        repo_tmp_path, plane, checker_model=ScriptedChatModel(responses=nonsense_script())
    )

    real = sanning.recorder

    def failing_close(*args, **kwargs):
        handler = real(*args, **kwargs)

        def close(**_kwargs):
            raise RuntimeError("the closing record could not be delivered")

        handler.close = close
        return handler

    monkeypatch.setattr(underwriter_case, "recorder", failing_close)
    with serving(checker) as checker_url:
        underwriter = create_underwriter_app(
            settings, services=services_for("underwriter", plane, repo_tmp_path), checker_url=checker_url
        )
        with serving(underwriter) as url:
            case, _page = _run_a_case(underwriter, url)

    assert case.status == "stopped"
    assert any("closing this agent's record raised" in line for line in told), told
