"""Two services must never share a setting. `settings_from_env` reads only
the variables under its own prefix, and reports what is missing rather than
refusing to start."""

from __future__ import annotations

import os

from credit_demo.common.config import settings_from_env


def test_reads_only_its_own_prefix():
    env = {
        "UNDERWRITER_BUCKET": "underwriter-bucket",
        "UNDERWRITER_SANNING_API_KEY": "uw-key",
        "CHECKER_BUCKET": "checker-bucket",
        "CHECKER_SANNING_API_KEY": "ck-key",
    }
    underwriter = settings_from_env(
        service="underwriter", prefix="UNDERWRITER", default_agent_id="underwriter", default_port=8001, env=env
    )
    checker = settings_from_env(
        service="checker", prefix="CHECKER", default_agent_id="checker", default_port=8002, env=env
    )

    assert underwriter.bucket == "underwriter-bucket"
    assert underwriter.api_key == "uw-key"
    assert checker.bucket == "checker-bucket"
    assert checker.api_key == "ck-key"
    # Neither settings object can see the other service's values.
    assert underwriter.bucket != checker.bucket
    assert underwriter.api_key != checker.api_key


def test_missing_settings_are_reported_not_fatal():
    settings = settings_from_env(
        service="underwriter", prefix="UNDERWRITER", default_agent_id="underwriter", default_port=8001, env={}
    )
    # A shareable setting that nothing supplies is named by its shared
    # variable, the one line a one-machine .env needs; a seed never is.
    assert "SANNING_API_KEY" in settings.missing
    assert "SANNING_S3_BUCKET" in settings.missing
    assert "OPENROUTER_API_KEY" in settings.missing
    assert "UNDERWRITER_SANNING_SIGNING_SEED" in settings.missing
    # Construction itself never raises: a service must start with no .env.
    # `ready_to_record()` is the same list, under the name a run checks.
    assert settings.ready_to_record() == settings.missing


def test_openrouter_defaults_to_the_shared_house_model():
    settings = settings_from_env(
        service="underwriter", prefix="UNDERWRITER", default_agent_id="underwriter", default_port=8001, env={}
    )
    assert settings.model == "anthropic/claude-sonnet-5"
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"

    overridden = settings_from_env(
        service="underwriter",
        prefix="UNDERWRITER",
        default_agent_id="underwriter",
        default_port=8001,
        env={"UNDERWRITER_MODEL": "openai/gpt-5"},
    )
    assert overridden.model == "openai/gpt-5"


def test_health_never_includes_secret_values():
    settings = settings_from_env(
        service="underwriter",
        prefix="UNDERWRITER",
        default_agent_id="underwriter",
        default_port=8001,
        env={
            "UNDERWRITER_SANNING_API_KEY": "super-secret",
            "UNDERWRITER_SANNING_SIGNING_SEED": "ab" * 32,
        },
    )
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
    underwriter = settings_from_env(
        service="underwriter", prefix="UNDERWRITER", default_agent_id="underwriter", default_port=8001, env={}
    )
    checker = settings_from_env(
        service="checker", prefix="CHECKER", default_agent_id="checker", default_port=8002, env={}
    )
    assert underwriter.port == 8001
    assert checker.port == 8002
    assert underwriter.agent_id == "underwriter"
    assert checker.agent_id == "checker"


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
        settings = settings_from_env(
            service="underwriter", prefix="UNDERWRITER", default_agent_id="underwriter", default_port=8001, env={}
        )
    finally:
        os.chdir(original_cwd)

    assert REPO_ROOT in (settings.runs_dir, *settings.runs_dir.parents)




def _both(env):
    common = dict(default_port=8001)
    return (
        settings_from_env(service="underwriter", prefix="UNDERWRITER", default_agent_id="underwriter", env=env, **common),
        settings_from_env(service="checker", prefix="CHECKER", default_agent_id="checker", env=env, **common),
    )


def test_a_shared_value_reaches_both_agents():
    underwriter, checker = _both({
        "SANNING_API_KEY": "one-key",
        "OPENROUTER_API_KEY": "one-model-key",
        "SANNING_S3_BUCKET": "one-bucket",
        "SANNING_S3_ENDPOINT": "https://s3.example",
        "SANNING_S3_REGION": "eu-north-1",
        "SANNING_S3_ACCESS_KEY_ID": "id",
        "SANNING_S3_SECRET_ACCESS_KEY": "secret",
    })
    for settings in (underwriter, checker):
        assert settings.api_key == "one-key"
        assert settings.openrouter_api_key == "one-model-key"
        assert (settings.bucket, settings.bucket_region) == ("one-bucket", "eu-north-1")
        assert settings.variable("BUCKET") == "SANNING_S3_BUCKET"


def test_an_agents_own_value_wins_and_an_empty_one_does_not():
    underwriter, checker = _both({
        "SANNING_S3_BUCKET": "shared-bucket",
        "UNDERWRITER_BUCKET": "underwriter-bucket",
        "CHECKER_BUCKET": "",
    })
    assert underwriter.bucket == "underwriter-bucket"
    assert underwriter.variable("BUCKET") == "UNDERWRITER_BUCKET"
    assert checker.bucket == "shared-bucket"


def test_a_signing_seed_is_never_shared():
    underwriter, _checker = _both({"SANNING_SIGNING_SEED": "ab" * 32})
    assert underwriter.signing_seed_hex == ""
    assert "UNDERWRITER_SANNING_SIGNING_SEED" in underwriter.missing


def test_the_environment_is_one_shared_setting():
    """`SANNING_ENVIRONMENT` reaches both agents, so a test run marks both
    agents' records `dev` at once; an agent's own value still wins."""
    from credit_demo.common.config import settings_from_env

    def load(service, env):
        return settings_from_env(
            service=service, prefix=service.upper(), default_agent_id=service, default_port=8001, env=env
        )

    shared = {"SANNING_ENVIRONMENT": "dev"}
    assert load("underwriter", shared).environment == "dev"
    assert load("checker", shared).environment == "dev"
    assert load("checker", {**shared, "CHECKER_SANNING_ENVIRONMENT": "production"}).environment == "production"
    assert load("checker", {}).environment is None
