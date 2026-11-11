"""Where an agent keeps its content is a setting, not code: a bucket when one
is set, local disk for a Sandbox key with no bucket, and no start otherwise."""

from __future__ import annotations

import pytest
from sanning_anchor import FsLogStore, MemoryStore, ObjectLogStore

from credit_demo.common import cases as case_states
from credit_demo.common import startup
from credit_demo.common.config import REPO_ROOT, settings_from_env
from credit_demo.common.sanning import log_location, real_services
from credit_demo.common.startup import FAIL, OK
from credit_demo.underwriter import desk

SANDBOX_KEY = "sanning_test_abc"
LIVE_KEY = "sanning_prod_abc"
BUCKET = {
    "SANNING_LOG_BUCKET": "evidence",
    "AWS_ENDPOINT_URL_S3": "https://s3.example",
    "AWS_REGION": "eu-north-1",
    "AWS_ACCESS_KEY_ID": "id",
    "AWS_SECRET_ACCESS_KEY": "secret",
}


def _settings(runs, api_key: str, **extra: str):
    return settings_from_env(
        service="underwriter",
        default_agent_id="underwriter",
        default_port=8001,
        env={
            "SANNING_API_KEY": api_key,
            "SANNING_SIGNING_SEED": "ab" * 32,
            "OPENAI_API_KEY": "or-key",
            "COUNTERPARTY_PUBLIC_KEY": "cd" * 32,
            "CHECKER_URL": "http://localhost:8002",
            "EVIDENCE_BUILDER_URL": "http://localhost:8003",
            "RUNS_DIR": str(runs),
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
    assert log_location(settings, settings.agent_id) == str(settings.log_dir / settings.agent_id)
    # Inside the root the recorder watches, and in a folder git ignores.
    assert REPO_ROOT in settings.log_dir.parents
    assert "runs" in settings.log_dir.relative_to(REPO_ROOT).parts
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
        assert log_location(settings, settings.agent_id) == f"s3://evidence/{settings.agent_id}"


def test_a_bucket_half_set_names_what_is_left_even_with_a_sandbox_key(repo_tmp_path):
    settings = _settings(repo_tmp_path, SANDBOX_KEY, SANNING_LOG_BUCKET="evidence")
    assert "AWS_ACCESS_KEY_ID" in settings.missing
    assert "AWS_SECRET_ACCESS_KEY" in settings.missing


def test_the_quickstarts_names_set_the_bucket():
    settings = settings_from_env(
        service="underwriter", default_agent_id="a", default_port=1,
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
        service="underwriter", default_agent_id="a", default_port=1,
        env={"SANNING_S3_BUCKET": "evidence", "SANNING_S3_REGION": "eu-north-1"},
    )
    assert settings.bucket == ""
    assert settings.bucket_region == ""


def test_a_bucket_with_no_region_is_a_missing_setting_with_no_default(repo_tmp_path):
    settings = _settings(
        repo_tmp_path, SANDBOX_KEY,
        SANNING_LOG_BUCKET="evidence",
        AWS_ACCESS_KEY_ID="id", AWS_SECRET_ACCESS_KEY="secret",
    )
    assert settings.bucket_region == ""
    assert settings.missing == ("AWS_REGION",)
    finding = startup.test_settings(settings)
    assert finding.state == FAIL
    assert "AWS_REGION" in finding.message


def test_amazon_s3_needs_no_endpoint(repo_tmp_path):
    settings = _settings(
        repo_tmp_path, LIVE_KEY,
        SANNING_LOG_BUCKET="evidence", AWS_REGION="eu-north-1",
        AWS_ACCESS_KEY_ID="id", AWS_SECRET_ACCESS_KEY="secret",
    )
    assert settings.missing == ()
    assert settings.bucket_endpoint == ""
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


def test_each_agent_writes_under_a_folder_named_after_its_agent_id(repo_tmp_path):
    """Both agents share one bucket. An agent's store puts every key under its
    agent ID, and `bundle --logs` points at that folder."""
    for agent in ("underwriter-local", "checker-local"):
        settings = _settings(repo_tmp_path, SANDBOX_KEY, SANNING_AGENT_ID=agent, **BUCKET)
        services = real_services(settings)
        assert services.anchorer._log_store._objects._prefix == f"{agent}/"
        assert log_location(settings, agent) == f"s3://evidence/{agent}"


def test_on_local_disk_the_agents_share_one_log_store_with_a_subfolder_each(repo_tmp_path):
    """The Sandbox lays its content out as the bucket does: one log store
    folder, and a subfolder for each agent."""
    stores = {}
    for service, agent in (("underwriter", "underwriter-local"), ("checker", "checker-local")):
        settings = settings_from_env(
            service=service, default_agent_id=agent, default_port=1,
            env={
                "SANNING_API_KEY": SANDBOX_KEY,
                "SANNING_AGENT_ID": agent,
                "SANNING_SIGNING_SEED": "ab" * 32,
                "OPENAI_API_KEY": "or-key",
                "RUNS_DIR": str(repo_tmp_path / "runs" / service),
            },
        )
        stores[agent] = log_location(settings, agent)
    assert stores == {
        "underwriter-local": str(repo_tmp_path / "runs" / "logstore" / "underwriter-local"),
        "checker-local": str(repo_tmp_path / "runs" / "logstore" / "checker-local"),
    }


@pytest.mark.parametrize("agent_id", ["../other", "a/b", ".", "..", "a..b", "-x", "a b"])
def test_an_agent_id_that_is_not_a_folder_name_is_refused(repo_tmp_path, agent_id):
    """The agent ID names the agent's folder, so one that could point outside
    it or into another agent's folder never reaches the store."""
    for extra in ({}, BUCKET):
        settings = _settings(repo_tmp_path, SANDBOX_KEY, SANNING_AGENT_ID=agent_id, **extra)
        with pytest.raises(ValueError, match="SANNING_AGENT_ID"):
            real_services(settings)
