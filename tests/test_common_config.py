"""`settings_from_env` reads the Quickstart's standard names for one agent,
and reports what is missing rather than refusing to start."""

from __future__ import annotations

import os

from credit_demo.common.config import settings_from_env


def _underwriter(env):
    return settings_from_env(service="underwriter", default_agent_id="underwriter", default_port=8001, env=env)


def _checker(env):
    return settings_from_env(service="checker", default_agent_id="checker", default_port=8002, env=env)


def test_reads_the_standard_names():
    settings = _underwriter({
        "SANNING_API_KEY": "key",
        "SANNING_BASE_URL": "https://sanning.example/",
        "SANNING_AGENT_ID": "underwriter-local",
        "SANNING_SIGNING_SEED": "ab" * 32,
        "SANNING_LOG_BUCKET": "bucket",
        "AWS_ENDPOINT_URL_S3": "https://s3.example",
        "AWS_REGION": "eu-north-1",
        "AWS_ACCESS_KEY_ID": "id",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "SANNING_S3_PATH_STYLE": "true",
        "PORT": "9000",
    })

    assert settings.api_key == "key"
    assert settings.base_url == "https://sanning.example"
    assert settings.agent_id == "underwriter-local"
    assert settings.signing_seed_hex == "ab" * 32
    assert settings.bucket == "bucket"
    assert settings.bucket_endpoint == "https://s3.example"
    assert settings.bucket_region == "eu-north-1"
    assert (settings.bucket_access_key_id, settings.bucket_secret_access_key) == ("id", "secret")
    assert settings.bucket_path_style is True
    assert settings.port == 9000


def test_an_agent_name_form_is_not_read():
    """One agent per process: the agent-name forms of the settings are gone,
    so a leftover one is ignored and the standard name is reported missing."""
    settings = _underwriter({
        "UNDERWRITER_SANNING_API_KEY": "key",
        "UNDERWRITER_SANNING_SIGNING_SEED": "ab" * 32,
        "UNDERWRITER_BUCKET": "bucket",
    })

    assert settings.api_key == ""
    assert settings.signing_seed_hex == ""
    assert settings.bucket == ""
    assert {"SANNING_API_KEY", "SANNING_SIGNING_SEED"} <= set(settings.missing)


def test_missing_settings_are_reported_not_fatal():
    settings = _underwriter({})
    assert "SANNING_API_KEY" in settings.missing
    # The bucket is named once the key says it is needed (a Sandbox key can
    # keep the content on local disk): see `test_log_store_choice.py`.
    assert "SANNING_LOG_BUCKET" not in settings.missing
    assert "OPENROUTER_API_KEY" in settings.missing
    assert "SANNING_SIGNING_SEED" in settings.missing
    assert "COUNTERPARTY_PUBLIC_KEY" in settings.missing
    # Construction itself never raises: a service must start with no .env.
    # `ready_to_record()` is the same list, under the name a run checks.
    assert settings.ready_to_record() == settings.missing


def test_openrouter_defaults_to_the_shared_house_model():
    settings = _underwriter({})
    assert settings.model == "anthropic/claude-sonnet-5"
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"

    assert _underwriter({"MODEL": "openai/gpt-5"}).model == "openai/gpt-5"


def test_health_never_includes_secret_values():
    settings = _underwriter({
        "SANNING_API_KEY": "super-secret",
        "SANNING_SIGNING_SEED": "ab" * 32,
    })
    health = settings.as_health()
    dumped = repr(health)
    assert "super-secret" not in dumped
    assert "ab" * 32 not in dumped
    assert health["signing_key_configured"] is True
    assert health["openrouter_key_configured"] is False
    # The public key is public: shown in full, so the other side can copy it.
    from nacl.signing import SigningKey

    assert health["public_key"] == SigningKey(bytes.fromhex("ab" * 32)).verify_key.encode().hex()


def test_defaults_differ_by_service():
    underwriter = _underwriter({})
    checker = _checker({})
    assert underwriter.port == 8001
    assert checker.port == 8002
    assert underwriter.agent_id == "underwriter"
    assert checker.agent_id == "checker"


def test_the_environment_is_a_setting():
    """`SANNING_ENVIRONMENT` marks the agent's records, and unset lets the
    API key decide."""
    assert _checker({"SANNING_ENVIRONMENT": "dev"}).environment == "dev"
    assert _checker({}).environment is None


def test_a_message_names_the_variable_the_operator_sets():
    settings = _underwriter({})
    assert settings.variable("BUCKET") == "SANNING_LOG_BUCKET"
    assert settings.variable("REGION") == "AWS_REGION"
    assert settings.variable("RUNS_DIR") == "RUNS_DIR"


def test_relative_runs_dir_resolves_against_the_repo_root_not_the_cwd():
    """The default `RUNS_DIR` is relative (`./runs/underwriter`). It must
    resolve the same way regardless of the process's own working directory,
    or `cd ~ && credit-demo` starts cleanly and then fails every run at the
    first artifact (the underwriter's workspace would sit outside the repo
    root `artifact_root` requires)."""
    from credit_demo.common.config import REPO_ROOT

    original_cwd = os.getcwd()
    os.chdir(os.path.expanduser("~"))
    try:
        settings = _underwriter({})
    finally:
        os.chdir(original_cwd)

    assert REPO_ROOT in (settings.runs_dir, *settings.runs_dir.parents)
