"""**Open** on a case document serves the file at the location the case record names, and only from
where case documents are kept. It uses the same check as the checker agent
(`credit_demo.common.case_locations`), so `pyproject.toml` and `.env` are never served. Every
refusal is the same 404, which repeats nothing from the record."""

from __future__ import annotations

import json
import shutil
import uuid

import pytest
from fastapi.testclient import TestClient

from credit_demo.common import application, home
from credit_demo.underwriter.config import settings_from_env as underwriter_settings
from credit_demo.underwriter.web import create_app as create_underwriter_app

REFUSED = {"detail": "no such document"}


def _desk(root):
    settings = underwriter_settings(
        service="underwriter", default_agent_id="underwriter", default_port=8001,
        env={"RUNS_DIR": str(root / "cases")},
    )
    app = create_underwriter_app(settings, checker_url="http://127.0.0.1:9")
    return app, TestClient(app)


@pytest.fixture
def another_application():
    """A second application in the home's applications folder, whose
    documents share the sample's file names, with bank statements of its own.
    Yields its folder, relative to the home."""
    name = f"APP-TEST-{uuid.uuid4().hex[:8]}"
    folder = home.REPO_ROOT / home.SAMPLE_APPLICATION.parent.parent / name
    shutil.copytree(home.REPO_ROOT / home.SAMPLE_APPLICATION.parent, folder)
    try:
        (folder / "bank-statements.csv").write_text("month\nthe other applicant's statements\n")
        copied = json.loads((folder / "application.json").read_text())
        copied["application_number"] = name
        (folder / "application.json").write_text(json.dumps(copied))
        yield folder.relative_to(home.REPO_ROOT)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_open_serves_the_file_the_case_record_names_not_the_samples(repo_tmp_path, another_application):
    app, client = _desk(repo_tmp_path)
    other = app.state.cases.create(**application.opening(another_application / "application.json"))
    sample = app.state.cases.create(**application.opening(home.SAMPLE_APPLICATION))

    page = client.get(f"/cases/{other.id}").text
    assert f'href="/cases/{other.id}/documents/bank_statements"' in page

    served = client.get(f"/cases/{other.id}/documents/bank_statements")
    assert served.status_code == 200
    assert served.text == "month\nthe other applicant's statements\n"
    assert client.get(f"/cases/{sample.id}/documents/bank_statements").content == (
        home.REPO_ROOT / home.BANK_STATEMENTS
    ).read_bytes()


def test_every_link_on_the_application_screen_names_the_case(repo_tmp_path, another_application):
    app, client = _desk(repo_tmp_path)
    other = app.state.cases.create(**application.opening(another_application / "application.json"))

    page = client.get(f"/cases/{other.id}/application").text

    assert f'href="/cases/{other.id}/documents/annual_report"' in page


def test_a_document_the_case_does_not_name_is_not_served(repo_tmp_path):
    app, client = _desk(repo_tmp_path)
    opened = app.state.cases.create(**application.opening(home.SAMPLE_APPLICATION))
    bare = app.state.cases.create()

    assert client.get(f"/cases/{opened.id}/documents/not_a_document").json() == REFUSED
    assert client.get(f"/cases/{bare.id}/documents/bank_statements").json() == REFUSED
    assert client.get("/cases/20260101T000000000000Z-ffff/documents/bank_statements").status_code == 404


def _case_locating(app, document: str, location: str, *, application_location: str | None = None):
    """A case on the sample application whose record names `location` for
    `document`, and `application_location` for the application if given."""
    opened = application.opening(home.SAMPLE_APPLICATION)
    for entry in opened["case_documents"]:
        if entry["document"] == document:
            entry["location"] = location
        if application_location and entry["document"] == "application":
            entry["location"] = application_location
    return app.state.cases.create(**opened)


def _refused(client, case, document: str) -> None:
    """A 404 whose body is the fixed refusal: nothing of the record, and
    nothing of the file, comes back."""
    answer = client.get(f"/cases/{case.id}/documents/{document}")
    assert answer.status_code == 404, answer.text
    assert answer.json() == REFUSED


SAMPLE_FOLDER = home.SAMPLE_APPLICATION.parent.as_posix()


@pytest.mark.parametrize(
    "location",
    [
        pytest.param("pyproject.toml", id="pyproject.toml"),
        pytest.param(".env.example", id=".env.example"),
        pytest.param("src/credit_demo/common/home.py", id="a source file"),
        pytest.param(f"{SAMPLE_FOLDER}/../../pyproject.toml", id="traversal"),
        pytest.param("../../../../etc/passwd", id="traversal out of the home"),
        pytest.param("/etc/hosts", id="an absolute path"),
        pytest.param(f"{SAMPLE_FOLDER}/a\x00b", id="NUL"),
        pytest.param(f"{SAMPLE_FOLDER}/" + "x" * 5000, id="over-long"),
    ],
)
def test_a_location_outside_where_case_documents_are_kept_is_not_served(repo_tmp_path, location):
    app, client = _desk(repo_tmp_path)
    case = _case_locating(app, "bank_statements", location)

    _refused(client, case, "bank_statements")


def test_a_file_under_runs_is_not_served(repo_tmp_path):
    app, client = _desk(repo_tmp_path)
    secret = repo_tmp_path / "pretend-secret.env"
    secret.write_text("OPENROUTER_API_KEY=sk-pretend\n")
    location = secret.relative_to(home.REPO_ROOT).as_posix()

    _refused(client, _case_locating(app, "bank_statements", location), "bank_statements")


def test_another_cases_record_is_not_served(repo_tmp_path):
    app, client = _desk(repo_tmp_path)
    other = app.state.cases.create(**application.opening(home.SAMPLE_APPLICATION))
    location = (app.state.cases.folder(other.id) / "case.json").relative_to(home.REPO_ROOT).as_posix()

    _refused(client, _case_locating(app, "bank_statements", location), "bank_statements")


def test_a_link_inside_applications_that_leads_out_is_not_served(repo_tmp_path, another_application):
    app, client = _desk(repo_tmp_path)
    secret = repo_tmp_path / "pretend-secret.env"
    secret.write_text("OPENROUTER_API_KEY=sk-pretend\n")
    (home.REPO_ROOT / another_application / "statements.csv").symlink_to(secret)
    location = (another_application / "statements.csv").as_posix()
    case = _case_locating(
        app, "bank_statements", location, application_location=(another_application / "application.json").as_posix()
    )

    _refused(client, case, "bank_statements")


def test_a_case_whose_record_fails_the_check_serves_none_of_its_documents(repo_tmp_path):
    """The check is the checker agent's: a record it would not work from is
    not served in part either."""
    app, client = _desk(repo_tmp_path)
    case = _case_locating(app, "bank_statements", "pyproject.toml")

    assert client.get(f"/cases/{case.id}/documents/application").json() == REFUSED
