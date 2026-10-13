"""The tools name the files they read.

A tool's artifact goes into the signed record, so a name there is a claim
about a file. A case whose documents carry other names than the sample's
reads the same figures and names the files it read, never the sample's names.
The calculator is arithmetic on the figures an agent enters, on the terms
the application requests, and its step names the application file it read
those terms from.
"""

from __future__ import annotations

import json
import shutil

from credit_demo.common import application
from credit_demo.common.case_tools import CaseFile
from credit_demo.common.context import CaseContext
from credit_demo.common.files import CREDIT_POLICY_SOURCE
from tests.turns import CASE, context, runtime


def _copy_under_other_names(root) -> CaseContext:
    sample = context()
    names = {
        "application": "request-0412.json",
        "bank_statements": "ledger-2025.csv",
        "annual_report": "accounts-2024.pdf",
        "credit_policy": "lending-rules-v3.pdf",
    }
    located = {}
    for document, name in names.items():
        shutil.copy(sample.path(document), root / name)
        located[document] = str((root / name).relative_to(sample.path("application").parents[2]))
    return CaseContext(case_id=CASE, application_number="APP-COPY", **located)


def test_each_tool_names_the_file_it_read(repo_tmp_path):
    case = _copy_under_other_names(repo_tmp_path)
    tools = {t.name: t for t in CaseFile().tools()}
    call = {"runtime": runtime(case)}

    def artifact(name, args=None):
        return tools[name].invoke({"name": name, "args": {**(args or {}), **call}, "id": "c", "type": "tool_call"}).artifact

    application_file = artifact("read_application")
    assert application_file["path"].endswith("request-0412.json")
    assert artifact("read_bank_statements")["path"].endswith("ledger-2025.csv")
    assert artifact("read_annual_report")["path"].endswith("accounts-2024.pdf")
    policy = artifact("read_credit_policy")
    assert policy["path"].endswith("lending-rules-v3.pdf")
    assert policy["source"] == CREDIT_POLICY_SOURCE

    calculated = tools["calculate_cover"].invoke(
        {
            "name": "calculate_cover",
            "args": {
                "earnings_by_year": [{"year": "a", "ebitda_sek": 2_000_000}],
                "existing_annual_debt_service_sek": 1_100_000,
                **call,
            },
            "id": "c",
            "type": "tool_call",
        }
    )
    assert calculated.artifact == application_file
    requested = application.terms(application.read(case.path("application"))).amount_sek
    assert json.loads(calculated.content)["amount_requested_sek"] == requested
