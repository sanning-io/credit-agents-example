"""The case page and the application screen fit a phone: at 390px and 360px
wide no element is past the screen's left or right edge and the page does not
scroll sideways, with each hand-off and recorded-at popover open, and the
technical detail of a stopped case open. The stopped cases carry what the
desk itself stores when a case stops: `describe()` of a real exception.

The check renders the desk's own pages in headless Chromium. It skips where
no browser is installed (`playwright install chromium`, or point
`CHROMIUM_EXECUTABLE` at one), except under `CI`, where that fails."""

from __future__ import annotations

import os

import httpx
import pytest
from fastapi.testclient import TestClient

from credit_demo.common import application, home
from credit_demo.common import cases as case_states
from credit_demo.common.cases import CaseStore
from credit_demo.common.errors import describe, without_local_paths
from credit_demo.common.sanning import NotReady
from credit_demo.common.config import settings_from_env as underwriter_settings
from credit_demo.underwriter.web import create_app as create_underwriter_app

try:
    from credit_demo.common.transport import TransportError
except ImportError:  # a checkout whose hand-offs are plain HTTP calls has no wrapper
    TransportError = httpx.ConnectError

sync_api = pytest.importorskip("playwright.sync_api")

PHONES = (390, 360)
LONG_URL = "http://checker.internal.example:8002/hand-offs/20261001T130000000000Z-a1b3/assessment?attempt=3"
STAMP = "2026-10-01T13:00:00.000Z"
OVERFLOWING = """() => {
  const width = document.documentElement.clientWidth;
  const past = [...document.querySelectorAll("body *")]
    .filter((e) => { const r = e.getBoundingClientRect(); return r.width && (r.right > width + 0.5 || r.left < -0.5); })
    .map((e) => `${e.tagName.toLowerCase()}.${e.className} right=${Math.round(e.getBoundingClientRect().right)}`);
  return { scroll: document.documentElement.scrollWidth, width, past };
}"""


def _turn(side, step, output):
    return {"side": side, "step": step, "recorded_at": STAMP, "key": "ab" * 32, "steps": 3, "output": output}


def _handoff(step, sender):
    return {"step": step, "sender": sender, "sent_at": STAMP, "received_at": STAMP, "message_hash": "cd" * 32, "recorded_by_both": True}


def _what_stops_a_case():
    request = httpx.Request("POST", LONG_URL)
    return [
        NotReady(
            "this service cannot record anything yet: SANNING_API_KEY, UNDERWRITER_SANNING_SIGNING_SEED, "
            "SANNING_S3_BUCKET, SANNING_S3_ENDPOINT, SANNING_S3_ACCESS_KEY_ID, SANNING_S3_SECRET_ACCESS_KEY, "
            "OPENROUTER_API_KEY, UNDERWRITER_COUNTERPARTY_PUBLIC_KEY not set (see .env.example)"
        ),
        TransportError(describe(httpx.ConnectError(f"All connection attempts failed: {LONG_URL}"))),
        httpx.HTTPStatusError(
            f"Server error '500 Internal Server Error' for url '{LONG_URL}'",
            request=request, response=httpx.Response(500, request=request),
        ),
    ]


def _desk(root):
    """A desk with a concluded case (four turns, four hand-offs) whose audit
    trail could not be prepared, and a stopped case for each of three real
    reasons."""
    store = CaseStore(root / "cases")
    done = store.create("20261001T130000000000Z-0c4d", **application.opening(home.SAMPLE_APPLICATION))
    done.status = case_states.CONCLUDED
    done.conclusion = {"verdict": "approve_with_conditions", "conditions": ["Quarterly DSCR covenant"], "summary": "Approve."}
    done.turns += [
        _turn("underwriter", "assessment", {"dscr": 1.3, "recommendation": "refer", "summary": "Refer."}),
        _turn("checker", "challenge", {"about": "assessment_window", "summary": "Cover over 24 months is below the minimum."}),
        _turn("underwriter", "revision", {"proposed": {"recommendation": "approve_with_conditions"}, "summary": "Approve with conditions."}),
        _turn("checker", "conclusion", done.conclusion),
    ]
    done.handoffs += [
        _handoff("assessment", "underwriter"), _handoff("challenge", "checker"),
        _handoff("revision", "underwriter"), _handoff("conclusion", "checker"),
    ]
    done.audit_trail_state = case_states.FAILED
    done.audit_trail_error = f"HTTPStatusError: Client error '403 Forbidden' for url '{LONG_URL}'"
    store.save(done)
    stopped = []
    for number, exc in enumerate(_what_stops_a_case(), start=1):
        case = store.create(f"2026100{number + 1}T130000000000Z-a1b{number}", **application.opening(home.SAMPLE_APPLICATION))
        case.status = case_states.STOPPED
        case.error = without_local_paths(describe(exc))
        store.save(case)
        stopped.append(case)
    settings = underwriter_settings(
        service="underwriter", default_agent_id="underwriter", default_port=8001,
        env={"RUNS_DIR": str(root / "cases")},
    )
    return TestClient(create_underwriter_app(settings, checker_url="http://127.0.0.1:9")), done, stopped


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch(executable_path=os.environ.get("CHROMIUM_EXECUTABLE") or None)
        except sync_api.Error as error:
            if os.environ.get("CI"):
                raise
            pytest.skip(f"no Chromium to render with: {str(error).splitlines()[0]}")
        yield launched
        launched.close()


def _pages(root):
    client, done, stopped = _desk(root)
    pages = {
        "the concluded case": client.get(f"/cases/{done.id}").text,
        "the application": client.get(f"/cases/{done.id}/application").text,
    }
    for case in stopped:
        pages[f"the case stopped by {case.error.split(':')[0]}"] = client.get(f"/cases/{case.id}").text
    return pages


@pytest.mark.parametrize("width", PHONES)
def test_no_element_runs_past_the_edge_of_a_phone(browser, repo_tmp_path, width):
    problems = []
    for name, html in _pages(repo_tmp_path).items():
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.route("**/fonts.*", lambda route: route.abort())
        page.set_content(html)
        page.evaluate("document.querySelectorAll('details').forEach((d) => { d.open = true; })")
        marks = page.locator(".mark[tabindex]")
        for index in range(-1, marks.count()):
            if index >= 0:
                marks.nth(index).focus()
            found = page.evaluate(OVERFLOWING)
            if found["past"] or found["scroll"] > found["width"]:
                problems.append(f"{name}, popover {index}: scroll {found['scroll']} of {found['width']}, past {found['past'][:6]}")
        page.close()

    assert not problems, "\n".join(problems)


def test_the_status_box_stacks_on_a_phone_and_sits_in_a_row_on_a_desktop(browser, repo_tmp_path):
    html = _pages(repo_tmp_path)["the concluded case"]
    direction = {}
    for width in (390, 1280):
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.route("**/fonts.*", lambda route: route.abort())
        page.set_content(html)
        direction[width] = page.evaluate("getComputedStyle(document.querySelector('.box.ok')).flexDirection")
        page.close()

    assert direction == {390: "column", 1280: "row"}
