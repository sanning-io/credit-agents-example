"""The case page and the application screen fit a phone: at 390px wide no
element is past the screen's left or right edge and the page does not scroll
sideways, with each hand-off and recorded-at popover open as well.

The check renders the desk's own pages in headless Chromium. It skips where
no browser is installed: `playwright install chromium`, or point
`CHROMIUM_EXECUTABLE` at one."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from credit_demo.common import application, home
from credit_demo.common import cases as case_states
from credit_demo.common.cases import CaseStore
from credit_demo.underwriter.config import settings_from_env as underwriter_settings
from credit_demo.underwriter.web import create_app as create_underwriter_app

sync_api = pytest.importorskip("playwright.sync_api")

PHONE = 390
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


def _desk(root):
    """A desk with a concluded case (four turns, four hand-offs) and a
    stopped one."""
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
    store.save(done)
    stopped = store.create("20261002T130000000000Z-a1b3", **application.opening(home.SAMPLE_APPLICATION))
    stopped.status = case_states.STOPPED
    stopped.error = "NotReady: this service cannot record anything yet: " + ", ".join(f"SETTING_{n}_WITH_A_LONG_NAME" for n in range(8))
    store.save(stopped)
    settings = underwriter_settings(
        service="underwriter", prefix="UNDERWRITER", default_agent_id="underwriter", default_port=8001,
        env={"UNDERWRITER_RUNS_DIR": str(root / "cases")},
    )
    return TestClient(create_underwriter_app(settings, checker_url="http://127.0.0.1:9")), done, stopped


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch(executable_path=os.environ.get("CHROMIUM_EXECUTABLE") or None)
        except sync_api.Error as error:
            pytest.skip(f"no Chromium to render with: {str(error).splitlines()[0]}")
        yield launched
        launched.close()


def _pages(root):
    client, done, stopped = _desk(root)
    return {
        "the concluded case": client.get(f"/cases/{done.id}").text,
        "the stopped case": client.get(f"/cases/{stopped.id}").text,
        "the application": client.get(f"/cases/{done.id}/application").text,
    }


def test_no_element_runs_past_the_edge_of_a_phone(browser, repo_tmp_path):
    problems = []
    for name, html in _pages(repo_tmp_path).items():
        page = browser.new_page(viewport={"width": PHONE, "height": 900})
        page.route("**/fonts.*", lambda route: route.abort())
        page.set_content(html)
        marks = page.locator(".mark[tabindex]")
        for index in range(-1, marks.count()):
            if index >= 0:
                marks.nth(index).focus()
            found = page.evaluate(OVERFLOWING)
            if found["past"] or found["scroll"] > found["width"]:
                problems.append(f"{name}, popover {index}: scroll {found['scroll']} of {found['width']}, past {found['past'][:6]}")
        page.close()

    assert not problems, "\n".join(problems)
