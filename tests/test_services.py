"""Each agent's outside world, built from its own settings: its own key, its
own bucket, its own model. Built without touching the network, since a
service must start before it can be told what is wrong."""

from __future__ import annotations

from credit_demo.common.config import settings_from_env
from credit_demo.common.sanning import BundleRun, bundle_env, real_services
from credit_demo.underwriter.case import HANDOFF_TIMEOUT_S
from tests.rig import public_key_for


def _settings(tmp_path, service="underwriter", seed="ab" * 32):
    return settings_from_env(
        service=service,
        default_agent_id=service,
        default_port=8001,
        env={
            "SANNING_API_KEY": "sanning_test_key",
            "SANNING_SIGNING_SEED": seed,
            "OPENROUTER_API_KEY": "test-openrouter-key",
            "SANNING_LOG_BUCKET": "test-bucket",
            "AWS_ENDPOINT_URL_S3": "http://localhost:9000",
            "AWS_ACCESS_KEY_ID": "test-access",
            "AWS_SECRET_ACCESS_KEY": "test-secret",
            "RUNS_DIR": str(tmp_path / "runs"),
        },
    )


def test_services_build_without_touching_the_network(tmp_path):
    settings = _settings(tmp_path)
    services = real_services(settings)
    assert services.logs == f"s3://test-bucket/{settings.agent_id}"
    assert services.model.model_name == settings.model


def test_each_agent_signs_with_its_own_configured_key(tmp_path):
    """"Signed by the underwriting agent's key", not by some key."""
    underwriter = real_services(_settings(tmp_path, "underwriter", "11" * 32))
    checker = real_services(_settings(tmp_path, "checker", "22" * 32))
    assert underwriter.anchorer.public_key_hex() == public_key_for("11" * 32)
    assert checker.anchorer.public_key_hex() == public_key_for("22" * 32)


def test_bundle_is_given_this_agents_own_seed_and_bucket(tmp_path):
    """Each pack's declaration is signed by the agent whose pack it is."""
    env = bundle_env(_settings(tmp_path, "checker", "22" * 32))
    assert env["SANNING_SIGNING_SEED"] == "22" * 32
    assert env["SANNING_S3_ACCESS_KEY_ID"] == "test-access"


def test_each_pack_is_built_from_its_own_agents_folder_in_the_shared_bucket(tmp_path):
    """Both agents share one bucket. `bundle` for an agent reads the folder
    named after that agent, so a pack never holds the other agent's content."""
    from credit_demo.common.packs import build_pack

    seen = []
    for service in ("underwriter", "checker"):
        settings = _settings(tmp_path, service, "11" * 32)
        services = real_services(settings)
        services.bundle = lambda args, env: seen.append(args) or BundleRun(code=0)
        build_pack(services, settings, case_id="c1", out=tmp_path / f"{service}.zip")

    assert [args[args.index("--logs") + 1] for args in seen] == [
        "s3://test-bucket/underwriter",
        "s3://test-bucket/checker",
    ]


def test_a_hand_off_waits_long_enough_for_a_real_model_turn():
    """A hand-off's answer carries the checker agent's whole turn, which
    takes minutes with a real model."""
    assert HANDOFF_TIMEOUT_S >= 300
