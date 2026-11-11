"""The credit desk asks the evidence builder for a closed case's file, and asks
again when a call fails or the records are not stamped, so it works against a
host that stops an idle service and wakes it on the next call, and against one
that never stops it."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

import credit_demo.underwriter.audit_trail as audit_trail
from credit_demo.common import cases as case_states
from credit_demo.common.cases import CaseStore
from credit_demo.common.messages import AUDIT_TRAIL_FAILED, AUDIT_TRAIL_NOT_READY
from tests.servers import listening, serving, url_of

ZIP = b"PK\x03\x04 a case file"


def _closed_case(repo_tmp_path):
    store = CaseStore(repo_tmp_path / "cases")
    case = store.create()
    case.status = case_states.CONCLUDED
    store.save(case)
    return store, case


def _builder(answers, asked):
    """An evidence builder that answers each call with the next of `answers`
    (a status and a body), and the last one for every call after."""
    app = FastAPI()

    @app.post("/cases/{case_id}/case-file")
    def case_file(case_id: str):
        asked.append(case_id)
        status, body = answers[min(len(asked), len(answers)) - 1]
        if status == 200:
            return Response(content=body, media_type="application/zip")
        return JSONResponse(status_code=status, content=body)

    return app


def _prepare(store, case, url, *, tries=5):
    audit_trail.prepare(
        cases=store, case_id=case.id, evidence_builder_url=url, try_every_s=0.0, tries=tries
    )
    return store.get(case.id)


def test_the_case_file_arrives_when_the_evidence_builder_answers(repo_tmp_path):
    store, case = _closed_case(repo_tmp_path)
    asked = []
    with serving(_builder([(200, ZIP)], asked)) as url:
        done = _prepare(store, case, url)

    assert done.audit_trail_state == case_states.READY
    assert audit_trail.audit_trail_path(store, case.id).read_bytes() == ZIP
    assert asked == [case.id]


def test_the_service_down_and_then_up_still_delivers_the_case_file(repo_tmp_path, monkeypatch):
    """The first call finds nothing listening, as on a host that stopped the
    service. The service comes up while the desk waits, and the next call gets
    the file."""
    store, case = _closed_case(repo_tmp_path)
    asked = []
    socket = listening()
    served = serving(_builder([(200, ZIP)], asked), socket)
    waits = []

    def wait(_seconds):
        waits.append(1)
        if len(waits) == 1:
            served.__enter__()

    monkeypatch.setattr(audit_trail, "time", SimpleNamespace(sleep=wait))
    try:
        done = _prepare(store, case, url_of(socket))
    finally:
        served.__exit__(None, None, None)

    assert len(waits) == 1, "one call failed before the service came up"
    assert done.audit_trail_state == case_states.READY
    assert audit_trail.audit_trail_path(store, case.id).read_bytes() == ZIP
    assert asked == [case.id]


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_gateway_error_while_the_service_wakes_is_asked_again(repo_tmp_path, status):
    store, case = _closed_case(repo_tmp_path)
    asked = []
    with serving(_builder([(status, {}), (status, {}), (200, ZIP)], asked)) as url:
        done = _prepare(store, case, url)

    assert done.audit_trail_state == case_states.READY
    assert asked == [case.id] * 3


def test_a_202_while_the_records_are_not_stamped_is_asked_again(repo_tmp_path):
    store, case = _closed_case(repo_tmp_path)
    asked = []
    not_stamped = (202, {"ready": False, "reason": "not stamped"})
    with serving(_builder([not_stamped, not_stamped, (200, ZIP)], asked)) as url:
        done = _prepare(store, case, url)

    assert done.audit_trail_state == case_states.READY
    assert len(asked) == 3


def test_a_call_that_times_out_is_asked_again(repo_tmp_path, monkeypatch):
    store, case = _closed_case(repo_tmp_path)
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise httpx.ReadTimeout("no reply in time")
        return httpx.Response(200, content=ZIP)

    monkeypatch.setattr(audit_trail.httpx, "post", post)
    done = _prepare(store, case, "http://builder.invalid")

    assert done.audit_trail_state == case_states.READY
    assert len(calls) == 2


def test_a_call_is_given_the_time_a_build_takes(monkeypatch, repo_tmp_path):
    store, case = _closed_case(repo_tmp_path)
    seen = {}

    def post(url, **kwargs):
        seen.update(kwargs)
        return httpx.Response(200, content=ZIP)

    monkeypatch.setattr(audit_trail.httpx, "post", post)
    _prepare(store, case, "http://builder.invalid")

    assert seen["timeout"] >= 900


def test_a_service_that_never_comes_up_fails_the_audit_trail_in_plain_words(repo_tmp_path):
    store, case = _closed_case(repo_tmp_path)
    nothing_listening = url_of(listening())

    done = _prepare(store, case, nothing_listening, tries=3)

    assert done.audit_trail_state == case_states.FAILED
    assert done.audit_trail_error == AUDIT_TRAIL_NOT_READY
    assert "the evidence builder did not answer" in done.audit_trail_detail


def test_an_answer_that_is_not_retryable_ends_the_asking(repo_tmp_path):
    """A 422 says the file cannot be built; waiting would never end."""
    store, case = _closed_case(repo_tmp_path)
    asked = []
    refusal = (422, {"ready": False, "reason": "no records for this case"})
    with serving(_builder([refusal], asked)) as url:
        done = _prepare(store, case, url)

    assert len(asked) == 1
    assert done.audit_trail_state == case_states.FAILED
    assert done.audit_trail_error == AUDIT_TRAIL_FAILED
    assert "no records for this case" in done.audit_trail_detail


def test_the_production_limits_are_unchanged():
    """The suite shortens the limits for every test, so the source is read."""
    source = Path(audit_trail.__file__).read_text(encoding="utf-8")
    assert "TRY_EVERY_S = 20.0\n" in source
    assert "TRIES = 45\n" in source
    assert "CALL_TIMEOUT_S = 900\n" in source


def test_a_bug_while_asking_is_not_recorded_as_a_failed_audit_trail(monkeypatch, repo_tmp_path):
    store, case = _closed_case(repo_tmp_path)

    def post(url, **kwargs):
        raise RuntimeError("a bug")

    monkeypatch.setattr(audit_trail.httpx, "post", post)
    with pytest.raises(RuntimeError, match="a bug"):
        _prepare(store, case, "http://builder.invalid")
