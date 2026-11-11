"""`settings_from_env` reads the Quickstart's standard names for one agent,
and reports what is missing rather than refusing to start."""

from __future__ import annotations

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
    """One agent per process: only the standard names are read, so an agent-name form of a setting
    is ignored and the standard name is reported missing."""
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
    assert "OPENAI_API_KEY" in settings.missing
    assert "SANNING_SIGNING_SEED" in settings.missing
    assert "COUNTERPARTY_PUBLIC_KEY" in settings.missing
    # Construction itself never raises: a service must start with no .env.
    # `ready_to_record()` is the same list, under the name a run checks.
    assert settings.ready_to_record() == settings.missing


def test_the_model_defaults_and_the_base_url_has_none_of_its_own():
    settings = _underwriter({})
    assert settings.model == "openai/gpt-5-mini"
    assert settings.openai_base_url == ""

    assert _underwriter({"MODEL": "openai/gpt-5"}).model == "openai/gpt-5"
    assert _underwriter({"OPENAI_BASE_URL": "https://models.example/v1/"}).openai_base_url == "https://models.example/v1"


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
    assert health["model_key_configured"] is False
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
