"""Each agent's outside world, built from its own settings: its own key, its
own bucket, its own model. Built without touching the network, since a
service must start before it can be told what is wrong."""

from __future__ import annotations

import subprocess

from credit_demo.common.config import settings_from_env
from credit_demo.common.sanning import BundleRun, real_services
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
    assert services.logs == f"s3://test-bucket/{settings.agent_id}"
    assert services.model.model_name == settings.model


def test_each_agent_signs_with_its_own_configured_key(tmp_path):
    """"Signed by the underwriting agent's key", not by some key."""
    underwriter = real_services(_settings(tmp_path, "underwriter", "11" * 32))
    checker = real_services(_settings(tmp_path, "checker", "22" * 32))
    assert underwriter.anchorer.public_key_hex() == public_key_for("11" * 32)
    assert checker.anchorer.public_key_hex() == public_key_for("22" * 32)


def test_each_pack_is_built_from_its_own_agents_folder_in_the_shared_bucket(tmp_path):
    """Both agents share one bucket. `bundle` for an agent reads the folder
    named after that agent, so a pack never holds the other agent's content."""
    from credit_demo.common.packs import build_pack

    seen = []
    for service in ("underwriter", "checker"):
        settings = _settings(tmp_path, service, "11" * 32)
        services = real_services(settings)
        services.bundle = lambda args: seen.append(args) or BundleRun(code=0)
        build_pack(services, settings, case_id="c1", out=tmp_path / f"{service}.zip")

    assert [args[args.index("--logs") + 1] for args in seen] == [
        "s3://test-bucket/underwriter",
        "s3://test-bucket/checker",
    ]


def test_a_hand_off_waits_long_enough_for_a_real_model_turn():
    """A hand-off's answer carries the checker agent's whole turn, which
    takes minutes with a real model."""
    assert HANDOFF_TIMEOUT_S >= 300


class _Ran:
    returncode = 0
    stderr = ""


def _pack_built_with(monkeypatch, tmp_path, **extra):
    """Build a pack through the real `bundle` command, with the subprocess
    stubbed, and return the keyword arguments it was started with."""
    from credit_demo.common.packs import build_pack

    started = {}

    def run(argv, **kwargs):
        started["argv"] = argv
        started["kwargs"] = kwargs
        return _Ran()

    monkeypatch.setattr(subprocess, "run", run)
    settings = _settings(tmp_path, "checker", "22" * 32)
    services = real_services(settings)
    build_pack(services, settings, case_id="c1", out=tmp_path / "pack.zip")
    return started


def test_bundle_runs_in_the_services_own_environment(monkeypatch, tmp_path):
    """Nothing is copied or renamed for `bundle`: it starts with no
    environment of its own, so it inherits the service's, where the API key,
    the signing seed and the bucket settings already have the names it reads."""
    started = _pack_built_with(monkeypatch, tmp_path)
    assert started["argv"][1] == "bundle"
    assert "env" not in started["kwargs"]


def test_bundle_is_told_nothing_about_the_bucket_but_where_the_content_is(monkeypatch, tmp_path):
    started = _pack_built_with(monkeypatch, tmp_path)
    assert not any(part.startswith("SANNING_S3_") for part in started["argv"])
    assert started["argv"][started["argv"].index("--logs") + 1] == "s3://test-bucket/checker"


def test_amazon_s3_with_no_endpoint_starts_bundle_the_same_way(monkeypatch, tmp_path):
    """On Amazon S3 no endpoint is set, and none is made up for `bundle`."""
    from credit_demo.common.packs import build_pack

    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: started.update(kwargs) or _Ran())
    started = {}
    settings = settings_from_env(
        service="checker",
        default_agent_id="checker",
        default_port=8002,
        env={
            "SANNING_API_KEY": "sanning_prod_key",
            "SANNING_SIGNING_SEED": "22" * 32,
            "OPENROUTER_API_KEY": "test-openrouter-key",
            "SANNING_LOG_BUCKET": "evidence",
            "AWS_REGION": "eu-north-1",
            "AWS_ACCESS_KEY_ID": "id",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "RUNS_DIR": str(tmp_path / "runs"),
        },
    )
    assert settings.bucket_endpoint == ""
    build_pack(real_services(settings), settings, case_id="c1", out=tmp_path / "pack.zip")
    assert "env" not in started


def test_a_sandbox_agent_with_no_bucket_builds_its_pack_from_local_disk(monkeypatch, tmp_path):
    from credit_demo.common.packs import build_pack

    started = {}
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: started.update(argv=argv, **kwargs) or _Ran())
    settings = settings_from_env(
        service="checker",
        default_agent_id="checker",
        default_port=8002,
        env={
            "SANNING_API_KEY": "sanning_test_key",
            "SANNING_SIGNING_SEED": "22" * 32,
            "OPENROUTER_API_KEY": "test-openrouter-key",
            "RUNS_DIR": str(tmp_path / "runs"),
        },
    )
    services = real_services(settings)
    build_pack(services, settings, case_id="c1", out=tmp_path / "pack.zip")
    assert not services.logs.startswith("s3://")
    assert started["argv"][started["argv"].index("--logs") + 1] == services.logs
    assert "env" not in started


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
