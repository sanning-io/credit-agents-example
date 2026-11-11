"""The evidence builder's service: it builds a case's evidence file in one
`bundle --case` command, replies with the file or with "try again", and does
all of that while the call is open."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from credit_demo.evidence_builder import case_file as case_file_module
from credit_demo.evidence_builder.case_file import BundleRun
from credit_demo.evidence_builder.config import settings_from_env
from credit_demo.evidence_builder.web import create_app
from tests.rig import ORGANIZATION_SEED, evidence_builder_settings

CASE = "20261110T101500000000Z-ab12"
ZIP = b"PK\x03\x04 a case file"


class Bundle:
    """A `bundle` that records how it was asked, and answers `code`. With a
    zero or three, it writes `ZIP` where `--out` says."""

    def __init__(self, code: int = 0, errors: list[str] | None = None) -> None:
        self.code = code
        self.errors = errors or []
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> BundleRun:
        self.calls.append(args)
        out = Path(args[args.index("--out") + 1])
        if self.code in (0, 3):
            out.write_bytes(ZIP)
        return BundleRun(code=self.code, errors=self.errors)


def _client(tmp_path, bundle, **settings):
    return TestClient(create_app(evidence_builder_settings(tmp_path, **settings), run_bundle=bundle))


@pytest.mark.parametrize("code", [0, 3])
def test_it_replies_200_with_the_zip_when_the_case_file_is_built(tmp_path, code):
    bundle = Bundle(code)
    answer = _client(tmp_path, bundle).post(f"/cases/{CASE}/case-file")

    assert answer.status_code == 200
    assert answer.headers["content-type"] == "application/zip"
    assert answer.content == ZIP
    assert len(bundle.calls) == 1


def test_it_replies_202_while_the_records_are_not_stamped(tmp_path):
    bundle = Bundle(2, ["bundle: records are not stamped yet"])
    answer = _client(tmp_path, bundle).post(f"/cases/{CASE}/case-file")

    assert answer.status_code == 202
    assert answer.json() == {"ready": False, "reason": "bundle: records are not stamped yet"}
    assert len(bundle.calls) == 1


def test_it_replies_422_with_the_reason_when_the_case_file_cannot_be_built(tmp_path):
    answer = _client(tmp_path, Bundle(4, ["bundle: no records for this case"])).post(f"/cases/{CASE}/case-file")

    assert answer.status_code == 422
    assert answer.json() == {"ready": False, "reason": "bundle: no records for this case"}


def test_a_reason_carries_no_local_path(tmp_path):
    bundle = Bundle(4, ["bundle: cannot write /srv/builder/work/audit-trail-x.zip"])
    answer = _client(tmp_path, bundle).post(f"/cases/{CASE}/case-file")

    assert "/srv/builder" not in answer.json()["reason"]
    assert "audit-trail-x.zip" in answer.json()["reason"]


@pytest.mark.parametrize("case_id", ["x", "../etc", "20261110T101500000000Z-zzzz", "audit trail"])
def test_it_builds_nothing_for_a_case_id_that_is_not_one(tmp_path, case_id):
    bundle = Bundle(0)
    answer = _client(tmp_path, bundle).post(f"/cases/{case_id}/case-file")

    assert answer.status_code in (404, 405)
    assert bundle.calls == []


def test_one_bundle_command_asks_for_both_agents_in_order_with_a_logs_value_for_each(tmp_path):
    """One `--case`, one `--agent` and one `--logs` for each agent in the same
    order, built from the bucket and the two agent IDs the settings name."""
    bundle = Bundle(0)
    _client(
        tmp_path,
        bundle,
        SANNING_LOG_BUCKET="evidence",
        AWS_REGION="eu-north-1",
        AWS_ACCESS_KEY_ID="id",
        AWS_SECRET_ACCESS_KEY="secret",
        UNDERWRITER_AGENT_ID="underwriter-live",
        CHECKER_AGENT_ID="checker-live",
    ).post(f"/cases/{CASE}/case-file")

    (args,) = bundle.calls
    assert args[:2] == ["--case", CASE]
    assert [args[i + 1] for i, a in enumerate(args) if a == "--agent"] == ["underwriter-live", "checker-live"]
    assert [args[i + 1] for i, a in enumerate(args) if a == "--logs"] == [
        "s3://evidence/underwriter-live",
        "s3://evidence/checker-live",
    ]
    assert "--from" not in args and "--to" not in args
    assert Path(args[args.index("--out") + 1]).name == f"audit-trail-{CASE}.zip"
    # Nothing about the bucket or a key rides in argv.
    assert not any("secret" in a or a.startswith("SANNING_") for a in args)


def test_on_local_disk_the_logs_values_are_the_agents_folders(tmp_path):
    bundle = Bundle(0)
    settings = evidence_builder_settings(tmp_path)
    TestClient(create_app(settings, run_bundle=bundle)).post(f"/cases/{CASE}/case-file")

    (args,) = bundle.calls
    assert [args[i + 1] for i, a in enumerate(args) if a == "--logs"] == [
        str(settings.log_dir / "underwriter"),
        str(settings.log_dir / "checker"),
    ]


def test_a_live_key_with_no_bucket_is_refused_rather_than_read_from_disk(tmp_path):
    settings = evidence_builder_settings(tmp_path, SANNING_API_KEY="sanning_prod_key")
    assert "SANNING_LOG_BUCKET" in settings.missing
    answer = TestClient(create_app(settings, run_bundle=Bundle(0))).post(f"/cases/{CASE}/case-file")

    assert answer.status_code == 500
    assert "SANNING_LOG_BUCKET" in answer.json()["reason"]


def test_it_does_all_its_work_in_the_call_and_keeps_nothing_after_it(tmp_path):
    """Some hosts cut the CPU or stop the service once a reply has gone. The
    build is finished when the reply is, and the file is not left on disk."""
    bundle = Bundle(0)
    answer = _client(tmp_path, bundle).post(f"/cases/{CASE}/case-file")

    assert answer.content == ZIP
    out = Path(bundle.calls[0][bundle.calls[0].index("--out") + 1])
    assert not out.exists() and not out.parent.exists()


def test_the_service_starts_no_background_work():
    """The rule, as source: no thread, no background task, no task left to run
    after a reply."""
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path(case_file_module.__file__).parent.glob("*.py")
    )
    for forbidden in ("threading", "BackgroundTasks", "create_task", "ThreadPoolExecutor", "Timer(", "sched"):
        assert forbidden not in source, forbidden


def test_it_refuses_to_start_on_a_key_that_is_not_the_organizations(tmp_path):
    for kind in ("", "agent"):
        settings = evidence_builder_settings(tmp_path, SANNING_SIGNING_KEY_KIND=kind)
        with pytest.raises(ValueError, match="SANNING_SIGNING_KEY_KIND must be organization"):
            create_app(settings, run_bundle=Bundle(0))


def test_health_shows_presence_and_the_public_key_never_a_secret(tmp_path):
    body = _client(tmp_path, Bundle(0)).get("/health").json()

    assert body["service"] == "evidence-builder"
    assert body["signing_key_kind"] == "organization"
    assert body["agent_ids"] == ["underwriter", "checker"]
    assert body["signing_key_configured"] is True
    assert ORGANIZATION_SEED not in str(body)
    assert "sanning_test_key" not in str(body)


def test_it_holds_no_agents_seed_model_key_or_counterparty_key(tmp_path):
    """Whatever an agent's environment carries, the evidence builder's settings
    read none of it."""
    settings = settings_from_env(
        {
            "SANNING_API_KEY": "sanning_test_key",
            "SANNING_SIGNING_SEED": ORGANIZATION_SEED,
            "SANNING_SIGNING_KEY_KIND": "organization",
            "UNDERWRITER_AGENT_ID": "underwriter",
            "CHECKER_AGENT_ID": "checker",
            "OPENAI_API_KEY": "made-up-model-key",
            "COUNTERPARTY_PUBLIC_KEY": "cd" * 32,
            "RUNS_DIR": str(tmp_path / "runs"),
        }
    )
    held = " ".join(str(value) for value in vars(settings).values())
    assert "made-up-model-key" not in held
    assert "cd" * 32 not in held
    assert settings.signing_seed_hex == ORGANIZATION_SEED


class _Ran:
    returncode = 0
    stderr = ""


def _started_with(monkeypatch, tmp_path, **settings):
    started = {}

    def run(argv, **kwargs):
        started["argv"] = argv
        started["kwargs"] = kwargs
        Path(argv[argv.index("--out") + 1]).write_bytes(ZIP)
        return _Ran()

    monkeypatch.setattr(subprocess, "run", run)
    bundle = case_file_module.run_bundle_command
    TestClient(create_app(evidence_builder_settings(tmp_path, **settings), run_bundle=bundle)).post(
        f"/cases/{CASE}/case-file"
    )
    return started


def test_bundle_runs_in_the_services_own_environment(monkeypatch, tmp_path):
    """Nothing is copied or renamed for `bundle`: it starts with no
    environment of its own, so it inherits the service's, where the API key,
    the evidence seed, its kind and the bucket settings already have the names
    it reads."""
    started = _started_with(monkeypatch, tmp_path)

    assert started["argv"][1] == "bundle"
    assert "env" not in started["kwargs"]


def test_amazon_s3_with_no_endpoint_starts_bundle_the_same_way(monkeypatch, tmp_path):
    started = _started_with(
        monkeypatch,
        tmp_path,
        SANNING_API_KEY="sanning_prod_key",
        SANNING_LOG_BUCKET="evidence",
        AWS_REGION="eu-north-1",
        AWS_ACCESS_KEY_ID="id",
        AWS_SECRET_ACCESS_KEY="secret",
    )

    assert not any(part.startswith("SANNING_S3_") for part in started["argv"])
    assert "--endpoint" not in " ".join(started["argv"])
    assert "env" not in started["kwargs"]
