"""A case opens from an application, and the underwriting agent works from
the case.

The credit desk opens a case on the sample application and notes the four
case documents with their locations in its own case record. The underwriting
agent's run gets that case as LangChain's runtime context, its tools find
every case document through it, and the terms come from the application
file. Neither underwriting prompt, nor its task, names the applicant or the
amount: the agent learns them by reading the application.
"""

from __future__ import annotations

import ast
import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from credit_demo.common import application, home
from credit_demo.common import cases as case_states
from credit_demo.common.cases import Case, CaseStore
from credit_demo.common.context import CaseContext
from credit_demo.common.calculator import facility_annual_debt_service
from credit_demo.underwriter import desk
from credit_demo.underwriter.agent import assessment_turn, revision_turn
from credit_demo.underwriter.config import settings_from_env as underwriter_settings
from credit_demo.underwriter.plan import REVISION_SYSTEM_PROMPT, SYSTEM_PROMPT, revision_task, task
from credit_demo.underwriter.tools import ASSESSMENT_TOOLS, REVISION_TOOLS
from credit_demo.underwriter.web import create_app as create_underwriter_app
from tests import dockerfile, stand_ins
from tests.fakes import ScriptedChatModel, fake_anchorer, revision_script, underwriting_script
from tests.turns import CASE, context, run

FOLDER = home.SAMPLE_APPLICATION.parent
SAMPLE = json.loads((home.REPO_ROOT / home.SAMPLE_APPLICATION).read_text(encoding="utf-8"))
CHALLENGE = {"kind": "challenge", "about": "the adjustment to the prior year's earnings", "reason": "x"}


def _records(logstore: Path) -> list[dict]:
    found = []
    for path in logstore.rglob("*"):
        try:
            record = json.loads(path.read_bytes())
        except (ValueError, IsADirectoryError, UnicodeDecodeError):
            continue
        if isinstance(record, dict) and "event_type" in record:
            found.append(record)
    return found


def _file_records(logstore: Path) -> list[dict]:
    return [
        r["metadata"]["artifact"]
        for r in _records(logstore)
        if r["event_type"] == "agent.file"
    ]


# ── Storage ───────────────────────────────────────────────────────────────


def test_the_application_and_the_applicants_documents_share_one_folder():
    assert str(FOLDER) == "applications/APP-2026-0412"
    assert (home.REPO_ROOT / home.SAMPLE_APPLICATION).is_file()
    for listed in SAMPLE["applicants_documents"]:
        assert (home.REPO_ROOT / FOLDER / listed["file"]).is_file(), listed
    assert (home.REPO_ROOT / home.CREDIT_POLICY).is_file()
    assert str(home.CREDIT_POLICY.parent) == "policy"


def test_the_application_holds_what_the_desk_shows():
    assert SAMPLE["application_number"] == "APP-2026-0412"
    assert SAMPLE["received"] == "2026-09-30"
    assert SAMPLE["applicant"] == {"name": "Nordvik Haulage AB", "registration_number": "556-XXXX (fictional)"}
    assert SAMPLE["financing_requested"] == {
        "product": "Truck financing",
        "amount_sek": 4_200_000,
        "term_months": 60,
        "nominal_annual_rate_percent": 6.0,
    }
    assert [(d["title"], d["file"]) for d in SAMPLE["applicants_documents"]] == [
        ("Bank statements, 12 months", "bank-statements.csv"),
        ("Annual report, 2024/2025", "annual-report.pdf"),
    ]
    # The rate is stored so the monthly rate comes out exact.
    assert application.terms(SAMPLE).monthly_rate == 0.005


def test_the_image_ships_the_application_folder_and_the_credit_policy():
    copied = dockerfile.copies()
    assert (["applications"], "./applications") in copied
    assert (["policy"], "./policy") in copied
    assert home.SAMPLE_APPLICATION in home.CASE_DOCUMENTS


# ── Opening a case ────────────────────────────────────────────────────────


EXPECTED_CASE_DOCUMENTS = [
    {"document": "application", "title": "Application APP-2026-0412",
     "location": "applications/APP-2026-0412/application.json"},
    {"document": "bank_statements", "title": "Bank statements, 12 months",
     "location": "applications/APP-2026-0412/bank-statements.csv"},
    {"document": "annual_report", "title": "Annual report, 2024/2025",
     "location": "applications/APP-2026-0412/annual-report.pdf"},
    {"document": "credit_policy", "title": "Credit policy",
     "location": "policy/credit-policy.pdf"},
]


def test_opening_a_case_notes_the_four_case_documents_in_the_desks_record(repo_tmp_path):
    """`POST /cases` opens a case on the sample application. The desk's own
    record of it names the application and each case document's location,
    relative to the home, before any agent works on it."""
    settings = underwriter_settings(
        service="underwriter", default_agent_id="underwriter", default_port=8001,
        env={"RUNS_DIR": str(repo_tmp_path / "cases")},
    )
    app = create_underwriter_app(settings, checker_url="http://127.0.0.1:9")
    client = TestClient(app)

    case_id = client.post("/cases", follow_redirects=False).headers["location"].rsplit("/", 1)[-1]

    deadline = time.monotonic() + 10
    while app.state.cases.get(case_id).status == case_states.RUNNING:
        assert time.monotonic() < deadline, "the case never left running"
        time.sleep(0.05)
    case = app.state.cases.get(case_id)
    assert case.application_number == "APP-2026-0412"
    assert case.case_documents == EXPECTED_CASE_DOCUMENTS
    for noted in case.case_documents:
        assert (home.REPO_ROOT / noted["location"]).is_file(), noted
    # Read back from the record on disk, it builds the agent's context.
    built = CaseContext.of(CaseStore(repo_tmp_path / "cases").get(case_id))
    assert built.case_id == case_id
    assert built.application_number == "APP-2026-0412"
    assert {name: str(built.path(name).relative_to(home.REPO_ROOT)) for name in
            ("application", "bank_statements", "annual_report", "credit_policy")} == {
        d["document"]: d["location"] for d in EXPECTED_CASE_DOCUMENTS
    }


def test_a_case_opened_without_its_case_documents_has_no_context():
    with pytest.raises(ValueError, match="without its case documents"):
        CaseContext.of(Case(id=CASE))


# ── The tools find every document through the context ────────────────────


def _a_store_elsewhere(root: Path, *, amount_sek: int) -> CaseContext:
    """A copy of the sample application's folder and the credit policy in a
    folder of their own, the application asking for `amount_sek`."""
    store = root / "store"
    shutil.copytree(home.REPO_ROOT / FOLDER, store / "APP-COPY")
    (store / "policy").mkdir()
    shutil.copy2(home.REPO_ROOT / home.CREDIT_POLICY, store / "policy" / "credit-policy.pdf")
    copied = json.loads((store / "APP-COPY" / "application.json").read_text())
    copied["financing_requested"]["amount_sek"] = amount_sek
    (store / "APP-COPY" / "application.json").write_text(json.dumps(copied))
    where = store.relative_to(home.REPO_ROOT)
    return CaseContext(
        case_id=CASE,
        application_number="APP-COPY",
        application=f"{where}/APP-COPY/application.json",
        bank_statements=f"{where}/APP-COPY/bank-statements.csv",
        annual_report=f"{where}/APP-COPY/annual-report.pdf",
        credit_policy=f"{where}/policy/credit-policy.pdf",
    )


def test_the_underwriting_agent_works_from_the_case_context_alone(repo_tmp_path):
    """The case's documents are a copy somewhere else, asking for a
    different amount. Both turns still complete, every file they read is the
    copy, and the figures are on the copy's terms."""
    case = _a_store_elsewhere(repo_tmp_path, amount_sek=3_000_000)
    anchorer, _client = fake_anchorer(repo_tmp_path)

    assessed = run(assessment_turn(repo_tmp_path / "work", case), anchorer=anchorer,
                   model=ScriptedChatModel(responses=underwriting_script()))
    revised = run(revision_turn(repo_tmp_path / "work", case, assessment=assessed.output or {}, challenge=CHALLENGE),
                  anchorer=anchorer, model=ScriptedChatModel(responses=revision_script()))

    assert assessed.status == "complete", assessed.error
    assert revised.status == "complete", revised.error
    read = {f["path"] for f in _file_records(repo_tmp_path / "logstore") if f.get("role") == "input"}
    assert read == {case.application, case.bank_statements, case.annual_report, case.credit_policy}

    # The scripted turns ask the calculator for a 15% deposit: the cover is on
    # the copy's terms, not the sample application's.
    terms = application.Terms(amount_sek=3_000_000, term_months=60, nominal_annual_rate_percent=6.0)
    for output in (assessed.output, revised.output):
        assert output["amount_requested_sek"] == 3_000_000
        calculated = output["calculations"][-1]
        assert calculated["amount_requested_sek"] == 3_000_000
        assert calculated["financed_amount_sek"] == 2_550_000
        assert calculated["facility_annual_debt_service_sek"] == facility_annual_debt_service(terms, 2_550_000)


@pytest.mark.parametrize("module", ["underwriter/tools.py", "checker/tools.py", "common/case_tools.py"])
def test_no_agent_tool_names_a_document_path(module):
    source = (Path(__file__).resolve().parents[1] / "src/credit_demo" / module).read_text()
    tree = ast.parse(source)
    named = sorted(
        {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr.endswith("_PATH")}
        | {node.attr for node in ast.walk(tree)
           if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "home"}
    )
    assert named == []
    strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    for marker in (".pdf", ".csv", "application.json", "applications/", "policy/"):
        assert not any(marker in s for s in strings), marker


# ── The agent reads the application ──────────────────────────────────────


def test_the_agent_reads_the_application_and_the_cover_figures_use_its_terms(repo_tmp_path):
    anchorer, _client = fake_anchorer(repo_tmp_path)
    outcome = run(assessment_turn(repo_tmp_path / "work", context()), anchorer=anchorer,
                  model=ScriptedChatModel(responses=underwriting_script()))

    assert outcome.status == "complete", outcome.error
    files = _file_records(repo_tmp_path / "logstore")
    application_files = [f for f in files if f["path"] == str(home.SAMPLE_APPLICATION)]
    # One for reading the application, and one for the calculation on its terms.
    assert len(application_files) == 2
    assert all(f["role"] == "input" for f in application_files)
    requested = SAMPLE["financing_requested"]
    assert outcome.output["applicant"] == SAMPLE["applicant"]["name"]
    assert outcome.output["amount_requested_sek"] == requested["amount_sek"]
    assert outcome.output["term_months"] == requested["term_months"]
    assert outcome.output["calculations"][0]["amount_requested_sek"] == requested["amount_sek"]


def test_a_tool_that_takes_the_runtime_records_only_the_models_arguments(repo_tmp_path):
    """LangChain keeps the injected runtime out of a tool call's recorded
    input: no state, no messages, no context, only what the model passed."""
    case = context()
    anchorer, _client = fake_anchorer(repo_tmp_path)
    outcome = run(assessment_turn(repo_tmp_path / "work", case), anchorer=anchorer,
                  model=ScriptedChatModel(responses=underwriting_script()))
    assert outcome.status == "complete", outcome.error

    logstore = repo_tmp_path / "logstore"
    starts = [r for r in _records(logstore) if r["event_type"] == "agent.tool_call.started"]
    assert len(starts) == 6
    for record in starts:
        content = (logstore / "content" / record["event"]["content_hash"]).read_text()
        # The input is recorded as the text of the arguments the model passed.
        inputs = ast.literal_eval(json.loads(content))
        name = record["metadata"]["gen_ai"]["tool"]["name"]
        name = record["metadata"]["gen_ai"]["tool"]["name"]
        # Only what the scripted model passed, never the injected runtime.
        passed = next(c for m in underwriting_script() for c in m.tool_calls if c["name"] == name)
        assert set(inputs) == set(passed["args"]), name
        for leaked in ("runtime", '"messages"', '"state"', task(case), case.application,
                       case.bank_statements, case.credit_policy):
            assert leaked not in content, (name, leaked)


# ── The prompts ───────────────────────────────────────────────────────────


def test_neither_underwriting_prompt_nor_its_task_names_the_applicant_or_the_amount(repo_tmp_path):
    """The stand-ins carry the applicant and the amount, as the real ones do
    (`tests/stand_ins.py`). The revision task embeds the case so far, this
    agent's own assessment and the checker agent's challenge, each as the
    agents wrote it after reading the application and not prompt text, so the
    words are looked for in the rest of the task."""
    case = context()
    descriptions = [t.description for t in ASSESSMENT_TOOLS + REVISION_TOOLS]
    revision = revision_turn(repo_tmp_path, case, assessment=stand_ins.ASSESSMENT, challenge=stand_ins.CHALLENGE).task
    embedded, own_text = stand_ins.embedded(revision)
    assert embedded == [stand_ins.ASSESSMENT, stand_ins.CHALLENGE]
    texts = [SYSTEM_PROMPT, REVISION_SYSTEM_PROMPT, assessment_turn(repo_tmp_path, case).task, own_text, *descriptions]
    for text in texts:
        for word in stand_ins.forbidden():
            assert word not in text, (word, text[:80])


def test_the_task_names_the_case():
    case = context()
    for text in (task(case), revision_task(stand_ins.ASSESSMENT, CHALLENGE, case)):
        assert case.case_id in text
        assert case.application_number in text


# ── The desk ──────────────────────────────────────────────────────────────


def test_the_desk_reads_the_applicant_and_the_financing_from_the_application(repo_tmp_path):
    store = CaseStore(repo_tmp_path / "cases")
    opened = store.create(**application.opening(home.SAMPLE_APPLICATION))
    without = store.create()

    view = desk.case_view(opened)
    assert view["applicant"] == SAMPLE["applicant"]["name"]
    assert view["facility"] == "Truck financing · SEK 4,200,000 · 60 months"
    assert desk.application_row(opened)["facility"] == "Truck financing · SEK 4,200,000"
    # A case with no application says less, never a default: the case page
    # is headed by the case, and the list's cells are left empty.
    assert desk.case_view(without)["applicant"] == f"Case {without.id[-4:]}"
    assert desk.application_row(without)["applicant"] == ""
    assert desk.case_view(without)["facility"] == ""
