"""Each step records the file it read, and the calculator's file is the
application.

The calculator reads the financing requested (the amount, the term and the
rate) from the application, so its step carries a file record for the
application file, role `input`, with the fingerprint of the bytes it
calculated on, so the record fingerprints the terms a calculation used. It
does so on every turn of both agents, because both have the same calculator.
The credit
policy is recorded by the step that reads it, and no record carries
`metadata.rules`.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from credit_demo.checker.agent import conclusion_turn, review_turn
from credit_demo.checker.tools import build_conclusion_tools, build_review_tools
from credit_demo.common import home
from credit_demo.common.case_tools import APPLICATION_SOURCE
from credit_demo.common.context import CaseContext
from credit_demo.common.files import CREDIT_POLICY_SOURCE, input_file
from credit_demo.underwriter.agent import assessment_turn, revision_turn
from credit_demo.underwriter.tools import build_revision_tools, build_tools
from tests.fakes import (
    ScriptedChatModel,
    fake_anchorer,
    fake_checker_anchorer,
    fake_checker_model,
    fake_conclusion_model,
    revision_script,
    underwriting_script,
)
from tests.turns import CASE, context, run, runtime

ASSESSMENT = {"applicant": "Nordvik Haulage AB", "recommendation": "approve_with_conditions", "deposit_percent": 15}
CHALLENGE = {"about": "the adjustment to the prior year's earnings"}
REVISION = {"response": "maintained", "recommendation": "approve_with_conditions", "deposit_percent": 15}


def _records(log_store):
    records = []
    for path in log_store.rglob("*"):
        try:
            record = json.loads(path.read_bytes())
        except (ValueError, RecursionError, IsADirectoryError, UnicodeDecodeError):
            continue
        if isinstance(record, dict) and "event_type" in record:
            records.append(record)
    return records


def _underwriting(tmp, case):
    anchorer, _ = fake_anchorer(tmp)
    run(assessment_turn(tmp / "w", case), anchorer=anchorer, model=ScriptedChatModel(responses=underwriting_script()))
    return tmp / "logstore"


def _revising(tmp, case):
    anchorer, _ = fake_anchorer(tmp)
    run(revision_turn(tmp / "w", case, assessment={}, challenge=CHALLENGE), anchorer=anchorer,
        model=ScriptedChatModel(responses=revision_script()))
    return tmp / "logstore"


def _reviewing(tmp, case):
    anchorer, _ = fake_checker_anchorer(tmp)
    run(review_turn(tmp / "w", case, assessment=ASSESSMENT), anchorer=anchorer, model=fake_checker_model())
    return tmp / "checker-logstore"


def _concluding(tmp, case):
    anchorer, _ = fake_checker_anchorer(tmp)
    run(conclusion_turn(tmp / "w", case, revision=REVISION, challenge=CHALLENGE), anchorer=anchorer,
        model=fake_conclusion_model())
    return tmp / "checker-logstore"


TURNS = [
    pytest.param(_underwriting, id="assessment"),
    pytest.param(_revising, id="revision"),
    pytest.param(_reviewing, id="review"),
    pytest.param(_concluding, id="conclusion"),
]


def _files_by_tool(records) -> dict[str, list[tuple[dict, str]]]:
    """Each tool's file records: (the file record, its fingerprint)."""
    tool_of = {  # tool step (run id) -> tool name
        r["metadata"]["run"]["run_id"]: r["metadata"]["run"]["name"]
        for r in records
        if r["event_type"] == "agent.tool_call.started"
    }
    files: dict[str, list[tuple[dict, str]]] = {}
    for r in records:
        if r["event_type"] == "agent.file":
            name = tool_of[r["metadata"]["run"]["run_id"]]
            files.setdefault(name, []).append((r["metadata"]["artifact"], r["event"]["content_hash"]))
    return files


def _fingerprint(location: str) -> str:
    return hashlib.sha256((home.REPO_ROOT / location).read_bytes()).hexdigest()


def _elsewhere(root, *, amount_sek: int) -> CaseContext:
    """The sample case with its application copied elsewhere, asking for
    `amount_sek`: a file the calculator can only have used by reading it."""
    sample = context()
    copy = root / "store" / "request.json"
    copy.parent.mkdir(parents=True)
    content = json.loads(sample.path("application").read_text(encoding="utf-8"))
    content["financing_requested"]["amount_sek"] = amount_sek
    copy.write_text(json.dumps(content), encoding="utf-8")
    return CaseContext(
        case_id=CASE,
        application_number=sample.application_number,
        application=str(copy.relative_to(home.REPO_ROOT)),
        bank_statements=sample.bank_statements,
        annual_report=sample.annual_report,
        credit_policy=sample.credit_policy,
    )


@pytest.mark.parametrize("turn", TURNS)
def test_the_calculator_records_the_application_it_calculated_on(repo_tmp_path, turn):
    case = _elsewhere(repo_tmp_path, amount_sek=3_000_000)
    files = _files_by_tool(_records(turn(repo_tmp_path, case)))

    assert len(files["calculate_cover"]) == 1, files
    artifact, fingerprint = files["calculate_cover"][0]
    assert artifact["path"] == case.application
    assert artifact["role"] == "input"
    assert artifact["source"] == APPLICATION_SOURCE
    # The bytes it calculated on, which are the bytes the reading step read.
    assert fingerprint == _fingerprint(case.application)
    assert [h for _a, h in files["read_application"]] == [fingerprint]


@pytest.mark.parametrize("turn", TURNS)
def test_the_policy_is_recorded_by_the_step_that_reads_it(repo_tmp_path, turn):
    files = _files_by_tool(_records(turn(repo_tmp_path, context())))

    policy = [name for name, steps in files.items() for a, _h in steps if a["path"] == home.CREDIT_POLICY.as_posix()]
    assert policy == ["read_credit_policy"], files
    artifact, fingerprint = files["read_credit_policy"][0]
    assert artifact["source"] == CREDIT_POLICY_SOURCE
    assert fingerprint == _fingerprint(home.CREDIT_POLICY.as_posix())


@pytest.mark.parametrize("turn", TURNS)
def test_no_record_carries_metadata_rules(repo_tmp_path, turn):
    records = _records(turn(repo_tmp_path, context()))
    assert records
    assert all("rules" not in r["metadata"] for r in records)
    assert all("rules" not in r.get("extras", {}) for r in records)


def test_the_calculator_returns_its_json_and_the_application_file_on_every_turn(repo_tmp_path):
    """Called as a tool call, the calculator returns its JSON text as the
    content and the application file as the artifact, and the amount it used
    is the one in that file; called with `.invoke({...})` alone it still
    returns the JSON text. Every turn of both agents has it."""
    case = _elsewhere(repo_tmp_path, amount_sek=3_000_000)
    turns = {
        "assessment": build_tools(repo_tmp_path),
        "revision": build_revision_tools(repo_tmp_path, challenge=CHALLENGE),
        "review": build_review_tools(repo_tmp_path, assessment=ASSESSMENT),
        "conclusion": build_conclusion_tools(repo_tmp_path, revision=REVISION, challenge=CHALLENGE),
    }
    args = {
        "earnings_by_year": [{"year": "a", "ebitda_sek": 2_000_000}],
        "existing_annual_debt_service_sek": 1_100_000,
        "runtime": runtime(case),
    }
    expected = input_file(case.path("application"), source=APPLICATION_SOURCE)
    for name, tools in turns.items():
        calculator = {t.name: t for t in tools}["calculate_cover"]
        message = calculator.invoke({"name": calculator.name, "args": args, "id": "c1", "type": "tool_call"})
        assert message.artifact == expected, name
        assert json.loads(message.content)["amount_requested_sek"] == 3_000_000
        assert calculator.invoke(args) == message.content


def test_an_application_changed_after_it_was_read_is_what_the_calculation_uses_and_names(repo_tmp_path):
    """The calculation reads the application again, so the amount it used
    and the file its record names are the application as it now stands."""
    case = _elsewhere(repo_tmp_path, amount_sek=4_200_000)
    tools = {t.name: t for t in build_tools(repo_tmp_path)}

    def call(name, **args):
        return tools[name].invoke(
            {"name": name, "args": {**args, "runtime": runtime(case)}, "id": "c", "type": "tool_call"}
        )

    read = call("read_application")
    changed = json.loads(case.path("application").read_text(encoding="utf-8"))
    changed["financing_requested"]["amount_sek"] = 3_000_000
    case.path("application").write_text(json.dumps(changed), encoding="utf-8")
    calculated = call(
        "calculate_cover",
        earnings_by_year=[{"year": "a", "ebitda_sek": 2_750_000}],
        existing_annual_debt_service_sek=1_100_000,
    )

    assert json.loads(read.content)["financing_requested"]["amount_sek"] == 4_200_000
    assert calculated.artifact == read.artifact
    assert json.loads(calculated.content)["amount_requested_sek"] == 3_000_000
