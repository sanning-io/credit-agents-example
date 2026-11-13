"""A slow log store slows a case down; it never fails it. The checker agent
writes a record for every step, so a slow bucket stretches its turn. The
underwriting agent waits while the checker agent keeps writing, and gives up
only when it stops or when a generous ceiling passes. Time is scaled down:
a second here stands for minutes."""

from __future__ import annotations

import asyncio
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from credit_demo.common.messages import CHECKER_DID_NOT_ANSWER, CHECKER_TOOK_TOO_LONG
from credit_demo.underwriter import case as underwriter_case
from credit_demo.underwriter.web import create_app as create_underwriter_app
from tests.rig import Plane, services_for
from tests.servers import serving
from tests.test_case_paths import _apps, _closing_reasons, _run_a_case

STALL_S = 1.0


def _scaled(monkeypatch, *, ceiling_s: float = 60.0) -> None:
    monkeypatch.setattr(underwriter_case, "HANDOFF_STALL_S", STALL_S)
    monkeypatch.setattr(underwriter_case, "HANDOFF_CEILING_S", ceiling_s)
    monkeypatch.setattr(underwriter_case, "HANDOFF_POLL_S", 0.1)


def _run(repo_tmp_path, plane):
    checker, settings = _apps(repo_tmp_path, plane)
    with serving(checker) as checker_url:
        underwriter = create_underwriter_app(
            settings, services=services_for("underwriter", plane, repo_tmp_path), checker_url=checker_url
        )
        with serving(underwriter) as url:
            started = time.monotonic()
            case, page = _run_a_case(underwriter, url)
            return case, page, time.monotonic() - started


def test_a_checker_that_is_slow_but_keeps_writing_records_still_completes_the_case(repo_tmp_path, monkeypatch):
    _scaled(monkeypatch)
    # Every checker record takes a quarter of the stall limit, and the checker
    # writes many: the turn runs far past the old fixed limit, scaled the same.
    plane = Plane(submit_delay_s={"checker": STALL_S / 4})

    case, _page, took = _run(repo_tmp_path, plane)

    assert case.status == "concluded", case.error_detail
    assert took > 2 * STALL_S, "the checker was not slower than the old limit, so this proved nothing"
    assert set(_closing_reasons(repo_tmp_path / "checker-logs")) <= {None}


def test_a_checker_that_keeps_writing_still_fails_the_case_after_the_ceiling(repo_tmp_path, monkeypatch):
    _scaled(monkeypatch, ceiling_s=1.5)
    plane = Plane(submit_delay_s={"checker": STALL_S / 4})

    case, page, took = _run(repo_tmp_path, plane)

    assert case.status == "stopped"
    assert case.error == CHECKER_TOOK_TOO_LONG
    assert took < 10
    assert set(_closing_reasons(repo_tmp_path / "underwriter-logs")) == {"timeout"}


def test_a_checker_that_stops_writing_fails_the_case_within_the_stall_limit(repo_tmp_path, monkeypatch):
    """A checker that took its turn and then went silent: its progress is flat."""
    _scaled(monkeypatch)
    plane = Plane()
    checker, settings = _apps(repo_tmp_path, plane)
    stuck = FastAPI()

    @stuck.get("/cases/{case_id}/progress")
    async def progress(case_id: str) -> JSONResponse:
        return JSONResponse(content={"progress": 3})

    @stuck.post("/cases/{case_id}/handoffs")
    async def handoffs(case_id: str, request: Request) -> JSONResponse:
        await asyncio.sleep(8)
        return JSONResponse(content={})

    with serving(stuck) as stuck_url:
        underwriter = create_underwriter_app(
            settings, services=services_for("underwriter", plane, repo_tmp_path), checker_url=stuck_url
        )
        with serving(underwriter) as url:
            started = time.monotonic()
            case, _page = _run_a_case(underwriter, url)
            took = time.monotonic() - started

    assert case.status == "stopped"
    assert case.error == CHECKER_DID_NOT_ANSWER
    assert took < 6, "the case waited for the stuck call instead of the stall limit"
    assert set(_closing_reasons(repo_tmp_path / "underwriter-logs")) == {"timeout"}


def test_a_checker_that_is_down_fails_the_case_within_the_stall_limit(repo_tmp_path, monkeypatch):
    """Nothing listens: the call is refused at once, and says why, not slowness."""
    _scaled(monkeypatch)
    plane = Plane()
    _checker, settings = _apps(repo_tmp_path, plane)
    underwriter = create_underwriter_app(
        settings, services=services_for("underwriter", plane, repo_tmp_path), checker_url="http://127.0.0.1:9"
    )
    with serving(underwriter) as url:
        case, _page = _run_a_case(underwriter, url)

    assert case.status == "stopped"
    assert case.error != CHECKER_DID_NOT_ANSWER
