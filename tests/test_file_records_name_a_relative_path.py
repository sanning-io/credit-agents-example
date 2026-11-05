"""A file record names the file the way the case does, not the machine.

The recorder resolves each file under one root folder and records the path
the tool hands it. Handed a path relative to that root, the record says
`applications/APP-2026-0412/bank-statements.csv`, the same on every machine. Handed a full
path, it would record this machine's folders and its user's name.
"""

from __future__ import annotations

import json

from credit_demo.common import home
from credit_demo.underwriter.agent import assessment_turn
from tests.fakes import ScriptedChatModel, fake_anchorer, underwriting_script
from tests.turns import context, run


def _artifact_paths(log_store):
    paths = []
    for path in log_store.rglob("*"):
        try:
            record = json.loads(path.read_bytes())
        except (ValueError, RecursionError, IsADirectoryError, UnicodeDecodeError):
            continue
        if isinstance(record, dict) and record.get("event_type", "").startswith("agent.file"):
            paths.append(record["metadata"]["artifact"].get("path"))
    return paths


def test_every_file_record_names_a_path_relative_to_the_root(repo_tmp_path):
    anchorer, _client = fake_anchorer(repo_tmp_path)
    outcome = run(
        assessment_turn(repo_tmp_path / "work", context()),
        anchorer=anchorer,
        model=ScriptedChatModel(responses=underwriting_script()),
    )
    assert outcome.status == "complete", outcome.error

    paths = _artifact_paths(repo_tmp_path / "logstore")

    # Six: the four case documents, the application again under the cover
    # calculation, and the written assessment.
    assert len(paths) == 6, paths
    assert not any(p.startswith("/") for p in paths), paths
    assert not any(str(home.REPO_ROOT) in p for p in paths), paths
    assert {
        "applications/APP-2026-0412/application.json",
        "applications/APP-2026-0412/bank-statements.csv",
        "applications/APP-2026-0412/annual-report.pdf",
        "policy/credit-policy.pdf",
    } <= set(paths)
    workspace = (repo_tmp_path / "work").resolve().relative_to(home.REPO_ROOT.resolve())
    assert f"{workspace}/assessment.json" in paths
