"""Where an agent keeps its content is a setting, not code: a bucket when one
is set, local disk for a Sandbox key with no bucket, and no start otherwise."""

from __future__ import annotations

import pytest
from sanning_anchor import FsLogStore, MemoryStore, ObjectLogStore

from credit_demo.common import cases as case_states
from credit_demo.common import startup
from credit_demo.common.config import REPO_ROOT, settings_from_env
from credit_demo.common.sanning import bundle_env, real_services
from credit_demo.common.startup import FAIL, OK
from credit_demo.underwriter import desk

SANDBOX_KEY = "sanning_test_abc"
LIVE_KEY = "sanning_prod_abc"
BUCKET = {
    "UNDERWRITER_BUCKET": "evidence",
    "UNDERWRITER_ENDPOINT": "https://s3.example",
    "UNDERWRITER_ACCESS_KEY_ID": "id",
    "UNDERWRITER_SECRET_ACCESS_KEY": "secret",
}


def _settings(runs, api_key: str, **extra: str):
    return settings_from_env(
        service="underwriter",
        prefix="UNDERWRITER",
        default_agent_id="underwriter",
        default_port=8001,
        env={
            "UNDERWRITER_SANNING_API_KEY": api_key,
            "UNDERWRITER_SANNING_SIGNING_SEED": "ab" * 32,
            "UNDERWRITER_OPENROUTER_API_KEY": "or-key",
            "UNDERWRITER_COUNTERPARTY_PUBLIC_KEY": "cd" * 32,
            "UNDERWRITER_RUNS_DIR": str(runs),
            **extra,
        },
    )


def _must_not_open(**_kwargs):
    raise AssertionError("local disk has no bucket to open")


def test_a_sandbox_key_with_no_bucket_keeps_the_content_on_local_disk(repo_tmp_path):
    settings = _settings(repo_tmp_path, SANDBOX_KEY)
    assert settings.missing == ()
    services = real_services(settings)
    assert isinstance(services.anchorer._log_store, FsLogStore)
    assert services.logs == str(settings.log_dir)
    # Inside the root the recorder watches, and in a folder git ignores.
    assert REPO_ROOT in settings.log_dir.parents
    assert "runs" in settings.log_dir.relative_to(REPO_ROOT).parts
    assert "SANNING_S3_ENDPOINT" not in bundle_env(settings)
    assert startup.test_bucket(settings, make_store=_must_not_open).state == OK


def test_a_live_key_with_no_bucket_does_not_start_and_says_what_to_set(repo_tmp_path):
    settings = _settings(repo_tmp_path, LIVE_KEY)
    assert "SANNING_LOG_BUCKET" in settings.missing
    finding = startup.test_settings(settings)
    assert finding.state == FAIL
    for name in ("SANNING_LOG_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        assert name in finding.message
    assert "Sandbox" in finding.message
    with pytest.raises(ValueError, match="Live API key needs an S3-compatible bucket"):
        real_services(settings)


def test_a_bucket_that_is_set_is_used_whatever_the_key(repo_tmp_path):
    for key in (SANDBOX_KEY, LIVE_KEY):
        settings = _settings(repo_tmp_path, key, **BUCKET)
        assert settings.missing == ()
        services = real_services(settings)
        assert isinstance(services.anchorer._log_store, ObjectLogStore)
        assert services.logs == "s3://evidence"
        assert bundle_env(settings)["SANNING_S3_ENDPOINT"] == "https://s3.example"


def test_a_bucket_half_set_names_what_is_left_even_with_a_sandbox_key(repo_tmp_path):
    settings = _settings(repo_tmp_path, SANDBOX_KEY, UNDERWRITER_BUCKET="evidence")
    assert "AWS_ACCESS_KEY_ID" in settings.missing
    assert "AWS_SECRET_ACCESS_KEY" in settings.missing


def test_the_quickstarts_names_set_the_bucket():
    settings = settings_from_env(
        service="underwriter", prefix="UNDERWRITER", default_agent_id="a", default_port=1,
        env={
            "SANNING_LOG_BUCKET": "evidence",
            "AWS_REGION": "eu-north-1",
            "AWS_ENDPOINT_URL_S3": "https://s3.example",
            "AWS_ACCESS_KEY_ID": "id",
            "AWS_SECRET_ACCESS_KEY": "secret",
        },
    )
    assert settings.bucket == "evidence"
    assert settings.bucket_region == "eu-north-1"
    assert settings.bucket_endpoint == "https://s3.example"
    assert settings.bucket_access_key_id == "id"
    assert settings.bucket_secret_access_key == "secret"


def test_the_old_bucket_names_set_nothing():
    settings = settings_from_env(
        service="underwriter", prefix="UNDERWRITER", default_agent_id="a", default_port=1,
        env={"SANNING_S3_BUCKET": "evidence", "SANNING_S3_REGION": "eu-north-1"},
    )
    assert settings.bucket == ""
    assert settings.bucket_region == "auto"


def test_amazon_s3_needs_no_endpoint_and_bundle_gets_the_regions_address(repo_tmp_path):
    settings = _settings(
        repo_tmp_path, LIVE_KEY,
        UNDERWRITER_BUCKET="evidence", UNDERWRITER_REGION="eu-north-1",
        UNDERWRITER_ACCESS_KEY_ID="id", UNDERWRITER_SECRET_ACCESS_KEY="secret",
    )
    assert settings.missing == ()
    assert settings.bucket_endpoint == ""
    env = bundle_env(settings)
    assert env["SANNING_S3_ENDPOINT"] == "https://s3.eu-north-1.amazonaws.com"
    assert env["SANNING_S3_REGION"] == "eu-north-1"
    assert isinstance(real_services(settings).anchorer._log_store, ObjectLogStore)


def test_the_anchorer_keeps_no_chain_between_runs(repo_tmp_path):
    services = real_services(_settings(repo_tmp_path, SANDBOX_KEY))
    assert isinstance(services.anchorer._store, MemoryStore)


def test_the_case_page_says_sandbox_for_a_sandbox_key_only():
    case = case_states.Case(id="c1", status=case_states.CONCLUDED, audit_trail_state=case_states.READY)
    assert "Sandbox" in desk.export_state(case, sandbox=True)["note"]
    assert "Sandbox" not in desk.export_state(case)["note"]
    case.audit_trail_state = case_states.PREPARING
    assert "Sandbox" in desk.export_state(case, sandbox=True)["note"]
