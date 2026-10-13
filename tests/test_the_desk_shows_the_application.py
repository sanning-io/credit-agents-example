"""The credit desk shows each case's application and case documents: the
list is headed Cases and names each case's application, the case page lists
the case documents with the application first, and the application opens as
a screen in the desk. Every value comes from the application file and the
case record."""

from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from credit_demo.common import application, home
from credit_demo.common.cases import CaseStore
from credit_demo.underwriter.config import settings_from_env as underwriter_settings
from credit_demo.underwriter.web import create_app as create_underwriter_app

SAMPLE = json.loads((home.REPO_ROOT / home.SAMPLE_APPLICATION).read_text(encoding="utf-8"))


def _desk(root):
    """A desk on a runs folder holding two cases on the sample application
    and one that names no application, none running."""
    store = CaseStore(root / "cases")
    first = store.create("20261001T130000000000Z-8f02", **application.opening(home.SAMPLE_APPLICATION))
    second = store.create("20261002T130000000000Z-a1b3", **application.opening(home.SAMPLE_APPLICATION))
    for case in (first, second):
        case.status = "stopped"
        store.save(case)
    old = store.create("20261003T130000000000Z-0c0c")
    old.status = "stopped"
    store.save(old)
    settings = underwriter_settings(
        service="underwriter", prefix="UNDERWRITER", default_agent_id="underwriter", default_port=8001,
        env={"UNDERWRITER_RUNS_DIR": str(root / "cases")},
    )
    return TestClient(create_underwriter_app(settings, checker_url="http://127.0.0.1:9")), first, second, old


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _in_order(text: str, *parts: str) -> None:
    at = -1
    for part in parts:
        found = text.find(part, at + 1)
        assert found > at, f"{part!r} is missing or out of order in {text!r}"
        at = found


def test_the_list_is_headed_cases_and_names_each_cases_application(repo_tmp_path):
    client, first, _, old = _desk(repo_tmp_path)

    page = client.get("/").text

    assert "<h1>Cases</h1>" in page
    assert 'href="/">Cases</a>' in page
    assert "Applications" not in page
    assert page.count(SAMPLE["application_number"]) == 2
    assert f'href="/cases/{old.id}"' in page


def test_the_case_page_lists_the_case_documents_with_the_application_first(repo_tmp_path):
    client, first, _, _ = _desk(repo_tmp_path)

    page = client.get(f"/cases/{first.id}").text

    text = _text(page)
    _in_order(
        text,
        "Case documents",
        "Application",
        f"{SAMPLE['application_number']} · received 30 Sep 2026",
        "Bank statements, 12 months",
        "Annual report, 2024/2025",
        "Credit policy",
        "In force when this case opened",
    )
    assert page.count(">Open</a>") == 4
    assert f'href="/cases/{first.id}/application"' in page
    assert f'href="/cases/{first.id}/documents/bank_statements"' in page
    assert f'href="/cases/{first.id}/documents/credit_policy"' in page
    assert 'href="/">Cases</a> / ' + SAMPLE["applicant"]["name"] in page


def test_the_application_screen_shows_the_application_file(repo_tmp_path):
    client, first, _, _ = _desk(repo_tmp_path)

    response = client.get(f"/cases/{first.id}/application")

    assert response.status_code == 200
    page = response.text
    text = _text(page)
    name = SAMPLE["applicant"]["name"]
    assert f'<a href="/">Cases</a> / <a href="/cases/{first.id}">{name}</a> / Application' in page
    assert f"Application {SAMPLE['application_number']}" in text
    assert f"{name} · received 30 Sep 2026" in text
    _in_order(text, "Applicant", "Name", name, "Registration number", SAMPLE["applicant"]["registration_number"])
    _in_order(text, "Financing requested", "Product", "Truck financing", "Amount", "SEK 4,200,000", "Term", "60 months", "Rate", "6.0% a year, nominal")
    _in_order(text, "Applicant's documents", "Bank statements, 12 months", "Annual report, 2024/2025", "Cases on this application")
    assert f'href="/cases/{first.id}/documents/annual_report"' in page
    assert "nature of business" not in text.lower()


def test_the_application_screen_lists_every_case_on_the_application(repo_tmp_path):
    client, first, second, old = _desk(repo_tmp_path)

    page = client.get(f"/cases/{first.id}/application").text

    assert page.count(">Open case</a>") == 2
    assert f'href="/cases/{first.id}"' in page and f'href="/cases/{second.id}"' in page
    assert f'href="/cases/{old.id}"' not in page


def test_a_value_changed_in_the_file_changes_the_screens(repo_tmp_path, monkeypatch):
    """Every value the screens show comes from the file: each one is changed
    here to something the sample does not hold, and each is found on the case
    page, the list and the application screen."""
    folder = repo_tmp_path / "home" / "applications" / "APP-X"
    folder.mkdir(parents=True)
    changed = {
        **SAMPLE,
        "application_number": "APP-X",
        "received": "2026-11-05",
        "applicant": {**SAMPLE["applicant"], "name": "Nordlund Bygg AB", "registration_number": "556999-1234"},
        "financing_requested": {
            **SAMPLE["financing_requested"],
            "product": "Excavator financing",
            "amount_sek": 1_250_000,
            "term_months": 36,
            "nominal_annual_rate_percent": 7.25,
        },
    }
    (folder / "application.json").write_text(json.dumps(changed))
    monkeypatch.setattr(home, "REPO_ROOT", repo_tmp_path / "home")
    from credit_demo.underwriter import desk

    monkeypatch.setattr(desk.home, "REPO_ROOT", repo_tmp_path / "home")
    store = CaseStore(repo_tmp_path / "cases")
    case = store.create(
        "20261001T130000000000Z-0001",
        application_number="APP-X",
        case_documents=application.case_documents("applications/APP-X/application.json"),
    )
    case.status = "stopped"
    store.save(case)
    settings = underwriter_settings(
        service="underwriter", prefix="UNDERWRITER", default_agent_id="underwriter", default_port=8001,
        env={"UNDERWRITER_RUNS_DIR": str(repo_tmp_path / "cases")},
    )
    client = TestClient(create_underwriter_app(settings, checker_url="http://127.0.0.1:9"))

    on_the_case_page = _text(client.get(f"/cases/{case.id}").text)
    on_the_list = _text(client.get("/").text)
    on_the_application = _text(client.get(f"/cases/{case.id}/application").text)

    # The case page names the applicant and the facility; the list names the
    # applicant, the application and the facility without its term.
    for shown in (on_the_case_page, on_the_list, on_the_application):
        assert "Nordlund Bygg AB" in shown
        assert "Excavator financing" in shown
        assert "SEK 1,250,000" in shown
    assert "Excavator financing · SEK 1,250,000 · 36 months" in on_the_case_page
    assert "APP-X" in on_the_list and "APP-X" in on_the_case_page
    assert "received 5 Nov 2026" in on_the_case_page
    # Only the application screen holds the rest of the file.
    assert "APP-X" in on_the_application and "received 5 Nov 2026" in on_the_application
    _in_order(on_the_application, "Registration number", "556999-1234")
    assert "36 months" in on_the_application and "7.25% a year, nominal" in on_the_application
    for sample_value in (SAMPLE["applicant"]["name"], "Truck financing", "60 months", "SEK 4,200,000"):
        for shown in (on_the_case_page, on_the_list, on_the_application):
            assert sample_value not in shown

    view = desk.application_view(case, store.all())
    assert view["number"] == "APP-X" and view["received"] == "5 Nov 2026"
    assert view["applicant"] == "Nordlund Bygg AB" and view["registration_number"] == "556999-1234"
    assert view["product"] == "Excavator financing" and view["amount"] == "SEK 1,250,000"
    assert view["term"] == "36 months" and view["rate"] == "7.25% a year, nominal"


def test_a_case_naming_no_application_still_renders_under_its_case_id(repo_tmp_path):
    client, _, _, old = _desk(repo_tmp_path)

    page = client.get(f"/cases/{old.id}")

    assert page.status_code == 200
    assert "Case documents" not in page.text
    # It names no application, so the case id stands in for the applicant.
    assert "<h1>Case 0c0c</h1>" in page.text
    assert '<a href="/">Cases</a> / Case 0c0c</div>' in page.text
    assert "<title>Case 0c0c · Bruksvik Credit desk</title>" in page.text
    assert 'class="facility"' not in page.text
    assert client.get(f"/cases/{old.id}/application").status_code == 404

    listed = client.get("/")
    assert listed.status_code == 200
    row = re.search(rf'<tr>(?:(?!</tr>).)*href="/cases/{old.id}".*?</tr>', listed.text, re.S).group(0)
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
    assert [cells[0].strip(), cells[1].strip(), cells[2].strip()] == ["–", "–", "–"]


def test_no_page_shows_a_fingerprint(repo_tmp_path):
    client, first, _, _ = _desk(repo_tmp_path)

    for path in (f"/cases/{first.id}", f"/cases/{first.id}/application"):
        page = client.get(path).text.lower()
        assert "fingerprint" not in page and "sha-256" not in page and not re.search(r"[0-9a-f]{64}", page)


def test_the_page_says_checker_agent_wherever_it_names_the_second_agent(repo_tmp_path):
    from credit_demo.underwriter import desk

    client, first, _, _ = _desk(repo_tmp_path)
    pages = [client.get("/").text, client.get(f"/cases/{first.id}").text]

    for page in pages:
        assert "checking agent" not in page.lower()
    assert "checker agent" in pages[0]
    assert desk.AGENT_NAME["checker"] == "checker agent"
    assert not any("hecking agent" in words for words in desk.WORKING.values())
