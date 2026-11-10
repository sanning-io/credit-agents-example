"""Each agent's outside world, built from its own settings: its own key, its
own bucket, its own model. Built without touching the network, since a
service must start before it can be told what is wrong."""

from __future__ import annotations

from credit_demo.common.config import settings_from_env
from credit_demo.common.sanning import log_location, real_services
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
            "AWS_REGION": "eu-north-1",
            "AWS_ACCESS_KEY_ID": "test-access",
            "AWS_SECRET_ACCESS_KEY": "test-secret",
            "RUNS_DIR": str(tmp_path / "runs"),
        },
    )


def test_services_build_without_touching_the_network(tmp_path):
    settings = _settings(tmp_path)
    services = real_services(settings)
    assert log_location(settings, settings.agent_id) == f"s3://test-bucket/{settings.agent_id}"
    assert services.model.model_name == settings.model


def test_each_agent_signs_with_its_own_configured_key(tmp_path):
    """"Signed by the underwriting agent's key", not by some key."""
    underwriter = real_services(_settings(tmp_path, "underwriter", "11" * 32))
    checker = real_services(_settings(tmp_path, "checker", "22" * 32))
    assert underwriter.anchorer.public_key_hex() == public_key_for("11" * 32)
    assert checker.anchorer.public_key_hex() == public_key_for("22" * 32)


def test_an_agents_services_hold_no_bundle_and_no_logs_value(tmp_path):
    """An agent records and nothing else: it builds no pack, so its services
    carry no `bundle` and no `--logs` value."""
    services = real_services(_settings(tmp_path))
    assert not hasattr(services, "bundle")
    assert not hasattr(services, "logs")


def test_a_hand_off_waits_long_enough_for_a_real_model_turn():
    """A hand-off's answer carries the checker agent's whole turn, which
    takes minutes with a real model."""
    assert HANDOFF_TIMEOUT_S >= 300


def test_the_examples_bucket_names_are_none_the_sdk_has_retired():
    """`bundle` exits 4 when it finds one of its retired names set with no
    replacement, so no setting this example reads may be one of them."""
    from credit_demo.common.config import VARIABLES

    retired = {
        "SANNING_S3_ENDPOINT",
        "SANNING_S3_ACCESS_KEY_ID",
        "SANNING_S3_SECRET_ACCESS_KEY",
        "SANNING_S3_REGION",
    }
    assert retired.isdisjoint(VARIABLES.values())


def test_bundle_refuses_an_old_bucket_name_left_set_in_a_container(tmp_path):
    """A container that still sets an old name, with no standard name beside
    it, stops `bundle` with exit 4 and a message naming the replacement."""
    from sanning_anchor.cli import Io, run_bundle

    said = []
    code = run_bundle(
        ["--agent", "checker", "--case", "c1", "--logs", "s3://evidence/checker",
         "--out", str(tmp_path / "pack.zip")],
        Io(out=lambda _line: None, err=said.append, env={
            "SANNING_API_KEY": "sanning_test_key",
            "SANNING_S3_ENDPOINT": "http://localhost:9000",
        }),
    )
    assert code == 4
    assert any("AWS_ENDPOINT_URL_S3" in line for line in said)
