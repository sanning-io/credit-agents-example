"""The evidence key belongs to the evidence builder alone, and the evidence builder holds nothing
of the agents'. Start-up refuses the wrong kind of key on either side, the launcher and compose
never give the builder the shared settings file, and the agents build no pack."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from credit_demo import dev
from credit_demo.checker.config import settings_from_env as checker_settings
from credit_demo.checker.web import create_app as create_checker_app
from credit_demo.common import startup
from credit_demo.common.config import read_env_file, settings_from_env
from credit_demo.common.documents import REPO_ROOT
from credit_demo.common.startup import FAIL, OK
from credit_demo.evidence_builder.config import settings_from_env as builder_settings

AGENT_SEED = "11" * 32
HAND_OVER_SEED = "99" * 32
CASE = "20261110T101500000000Z-ab12"


def _agent(service, tmp_path, **extra):
    env = {
        "SANNING_API_KEY": "sanning_test_key",
        "SANNING_SIGNING_SEED": AGENT_SEED,
        "OPENAI_API_KEY": "made-up-model-key",
        "COUNTERPARTY_PUBLIC_KEY": "cd" * 32,
        "CHECKER_URL": "http://localhost:8002",
        "EVIDENCE_BUILDER_URL": "http://localhost:8003",
        "DESK_URL": "http://localhost:8001",
        "RUNS_DIR": str(tmp_path / "runs"),
        **extra,
    }
    return settings_from_env(service=service, default_agent_id=service, default_port=8001, env=env)


def _builder(tmp_path, **extra):
    env = {
        "SANNING_API_KEY": "sanning_test_key",
        "SANNING_SIGNING_SEED": HAND_OVER_SEED,
        "SANNING_SIGNING_KEY_KIND": "organization",
        "UNDERWRITER_AGENT_ID": "underwriter",
        "CHECKER_AGENT_ID": "checker",
        "RUNS_DIR": str(tmp_path / "runs"),
        **extra,
    }
    return builder_settings(env)


@pytest.mark.parametrize("service", ["underwriter", "checker"])
def test_an_agent_refuses_the_organization_kind(tmp_path, service):
    finding = startup.test_settings(_agent(service, tmp_path, SANNING_SIGNING_KEY_KIND="organization"))

    assert finding.state == FAIL
    assert "SANNING_SIGNING_KEY_KIND" in finding.message
    assert "evidence builder" in finding.message
    assert AGENT_SEED not in finding.message


@pytest.mark.parametrize("service", ["underwriter", "checker"])
@pytest.mark.parametrize("kind", ["", "agent", "AGENT"])
def test_an_agent_starts_on_its_own_key(tmp_path, service, kind):
    assert startup.test_settings(_agent(service, tmp_path, SANNING_SIGNING_KEY_KIND=kind)).state == OK


@pytest.mark.parametrize("kind", ["", "agent", "something-else"])
def test_the_evidence_builder_refuses_any_kind_but_the_organization(tmp_path, kind):
    finding = startup.test_settings(_builder(tmp_path, SANNING_SIGNING_KEY_KIND=kind))

    assert finding.state == FAIL
    assert "SANNING_SIGNING_KEY_KIND must be organization" in finding.message
    assert HAND_OVER_SEED not in finding.message


def test_the_evidence_builder_starts_on_the_organization_key(tmp_path):
    assert startup.test_settings(_builder(tmp_path)).state == OK


def test_the_evidence_builder_needs_both_agents_ids_and_they_differ(tmp_path):
    missing = startup.test_settings(_builder(tmp_path, UNDERWRITER_AGENT_ID="", CHECKER_AGENT_ID=""))
    assert missing.state == FAIL
    assert "UNDERWRITER_AGENT_ID" in missing.message and "CHECKER_AGENT_ID" in missing.message

    same = startup.test_settings(_builder(tmp_path, CHECKER_AGENT_ID="underwriter"))
    assert same.state == FAIL

    bad = startup.test_settings(_builder(tmp_path, CHECKER_AGENT_ID="../other"))
    assert bad.state == FAIL
    assert "CHECKER_AGENT_ID" in bad.message


def test_the_evidence_builder_is_tested_against_sanning_and_the_log_store_and_never_a_model(tmp_path):
    assert startup.EVIDENCE_BUILDER_TESTS == (startup.test_sanning, startup.test_log_store)
    assert startup.test_model not in startup.EVIDENCE_BUILDER_TESTS
    assert startup.test_log_store not in startup.NETWORK_TESTS


def test_the_log_store_test_opens_the_bucket_with_the_builders_own_keys(tmp_path):
    settings = _builder(
        tmp_path,
        SANNING_LOG_BUCKET="evidence",
        AWS_REGION="eu-north-1",
        AWS_ACCESS_KEY_ID="its-own-id",
        AWS_SECRET_ACCESS_KEY="its-own-secret",
    )
    opened = {}

    class Store:
        def __init__(self, **kwargs):
            opened.update(kwargs)

        def check_protection(self):
            return type("Verdict", (), {"code": "store-ok", "satisfied": False, "reason": "no lock"})()

    finding = startup.test_log_store(settings, make_store=Store)

    assert finding.state == OK, "protection is the agents' side"
    assert opened["access_key_id"] == "its-own-id"
    assert opened["create_bucket"] is False


@pytest.mark.parametrize(
    ("code", "state", "words"),
    [
        ("store-unknown", FAIL, "s3:ListBucket"),
        ("store-absent", FAIL, "never does"),
        ("store-unreachable", FAIL, "did not answer"),
    ],
)
def test_the_log_store_test_names_what_is_wrong(tmp_path, code, state, words):
    settings = _builder(tmp_path, SANNING_LOG_BUCKET="evidence", AWS_REGION="r", AWS_ACCESS_KEY_ID="i", AWS_SECRET_ACCESS_KEY="s")

    class Store:
        def __init__(self, **_kwargs):
            pass

        def check_protection(self):
            return type("Verdict", (), {"code": code, "satisfied": False, "reason": "boom"})()

    finding = startup.test_log_store(settings, make_store=Store)

    assert finding.state == state
    assert words in finding.message
    assert finding.transient is (code == "store-unreachable")


def _launch_alone(service, tmp_path, monkeypatch, capsys, files):
    """Start `service` alone the way a host does, with real start-up tests
    that need no network because the settings test fails first."""
    monkeypatch.chdir(tmp_path)
    for name in ("SANNING_API_KEY", "SANNING_SIGNING_SEED", "SANNING_SIGNING_KEY_KIND", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    executed = []
    code = dev.serve_one(service, execute=lambda *args: executed.append(args))
    return code, executed, capsys.readouterr().err


def test_the_evidence_builder_alone_will_not_start_without_the_organization_kind(tmp_path, monkeypatch, capsys):
    code, executed, said = _launch_alone(
        "evidence-builder", tmp_path, monkeypatch, capsys,
        {"evidence-builder.env": f"SANNING_API_KEY=sanning_test_key\nSANNING_SIGNING_SEED={HAND_OVER_SEED}\n"
                                 "UNDERWRITER_AGENT_ID=underwriter\nCHECKER_AGENT_ID=checker\n"},
    )

    assert code == 1 and executed == []
    assert "SANNING_SIGNING_KEY_KIND must be organization" in said
    assert HAND_OVER_SEED not in said


@pytest.mark.parametrize("service", ["underwriter", "checker"])
def test_an_agent_alone_will_not_start_with_the_organization_kind(tmp_path, monkeypatch, capsys, service):
    code, executed, said = _launch_alone(
        service, tmp_path, monkeypatch, capsys,
        {f"{service}.env": f"SANNING_API_KEY=sanning_test_key\nOPENAI_API_KEY=made-up-model-key\n"
                           f"SANNING_SIGNING_SEED={AGENT_SEED}\nCOUNTERPARTY_PUBLIC_KEY={'cd' * 32}\n"
                           "SANNING_SIGNING_KEY_KIND=organization\n"},
    )

    assert code == 1 and executed == []
    assert "SANNING_SIGNING_KEY_KIND" in said and "evidence builder alone" in said
    assert AGENT_SEED not in said


SHARED = (
    "SANNING_API_KEY=the-agents-key\nOPENAI_API_KEY=made-up-model-key\nMODEL=a-model\n"
    "SANNING_LOG_BUCKET=writers-bucket\nAWS_ACCESS_KEY_ID=writer-id\n"
    "AWS_SECRET_ACCESS_KEY=writer-secret\nPORT=9999\n"
)
OWN = "SANNING_API_KEY=the-builders-key\nAWS_ACCESS_KEY_ID=reader-id\nAWS_SECRET_ACCESS_KEY=reader-secret\n"


def test_the_launcher_gives_the_evidence_builder_its_own_file_and_never_the_shared_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(SHARED)
    (tmp_path / "evidence-builder.env").write_text(OWN)

    builder = dev.service_env("evidence-builder", {})
    underwriter = dev.service_env("underwriter", {})

    assert builder["SANNING_API_KEY"] == "the-builders-key"
    assert (builder["AWS_ACCESS_KEY_ID"], builder["AWS_SECRET_ACCESS_KEY"]) == ("reader-id", "reader-secret")
    for name in ("OPENAI_API_KEY", "MODEL", "SANNING_LOG_BUCKET"):
        assert name not in builder, name
    assert builder["PORT"] == "8003", "the shared PORT is the agents'"
    # The agents still read it.
    assert underwriter["SANNING_API_KEY"] == "the-agents-key"
    assert underwriter["OPENAI_API_KEY"] == "made-up-model-key"


def test_the_one_command_starts_the_evidence_builder_without_the_shared_file(tmp_path, monkeypatch):
    class Stop(Exception):
        pass

    seen = {}

    def popen(command, *args, env=None, **kwargs):
        seen[command[-1]] = env
        if len(seen) == 3:
            raise Stop
        return object()

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(SHARED)
    (tmp_path / "underwriter.env").write_text(f"SANNING_SIGNING_SEED={AGENT_SEED}\n")
    (tmp_path / "checker.env").write_text("SANNING_SIGNING_SEED=" + "22" * 32 + "\n")
    (tmp_path / "evidence-builder.env").write_text(OWN + "PORT=9003\n")
    for name in (
        "SANNING_API_KEY", "OPENAI_API_KEY", "MODEL", "SANNING_LOG_BUCKET", "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY", "COUNTERPARTY_PUBLIC_KEY", "CHECKER_URL", "DESK_URL",
        "EVIDENCE_BUILDER_URL", "CREDIT_DEMO_HOST", "PORT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(dev.subprocess, "Popen", popen)

    with pytest.raises(Stop):
        dev.main([], startup=lambda _services: True)

    builder = seen["credit_demo.evidence_builder"]
    assert builder["SANNING_API_KEY"] == "the-builders-key"
    assert builder["PORT"] == "9003"
    for name in ("OPENAI_API_KEY", "MODEL", "SANNING_LOG_BUCKET", "COUNTERPARTY_PUBLIC_KEY", "CHECKER_URL"):
        assert name not in builder, name
    assert "writer-secret" not in builder.values() and "the-agents-key" not in builder.values()
    # The credit desk is told where to ask for a case file.
    assert seen["credit_demo.underwriter"]["EVIDENCE_BUILDER_URL"] == "http://localhost:9003"


def test_one_service_exiting_leaves_the_underwriter_up_and_is_said_once():
    class Proc:
        def __init__(self, exits_after=None):
            self.polls, self.exits_after = 0, exits_after

        def poll(self):
            self.polls += 1
            return -9 if self.exits_after is not None and self.polls > self.exits_after else None

    procs = {"underwriter": Proc(), "checker": Proc(), "evidence-builder": Proc(exits_after=1)}
    stop, lines, ticks = {"requested": False}, [], {"n": 0}

    def sleep(_seconds):
        ticks["n"] += 1
        stop["requested"] = ticks["n"] >= 6

    assert dev.supervise(procs, stop, sleep=sleep, log=lines.append) == 0
    assert [line for line in lines if "evidence-builder exited" in line] == [
        "credit-demo: evidence-builder exited (-9); the underwriter stays up"
    ]


def _service_block(name: str) -> str:
    """One service's lines in compose.yaml: from its key to the next key at
    the same depth."""
    lines = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8").splitlines()
    start = lines.index(f"  {name}:")
    block = []
    for line in lines[start + 1 :]:
        if re.match(r"^\S", line) or re.match(r"^  \S", line) and not line.startswith("  #"):
            break
        block.append(line)
    return "\n".join(block)


def test_compose_gives_the_evidence_builder_its_own_file_only():
    block = _service_block("evidence-builder")

    assert "env_file: [evidence-builder.env]" in block
    assert not re.search(r"(^|[\[\s,])\.env\b", block)
    assert "credit-demo\", \"evidence-builder\"" in block


def test_compose_publishes_no_port_for_the_evidence_builder_or_the_checker():
    assert "ports:" not in _service_block("evidence-builder")
    assert "ports:" not in _service_block("checker")
    assert "127.0.0.1:8001:8001" in _service_block("underwriter")


def test_compose_points_the_underwriter_at_the_evidence_builder_by_name():
    block = _service_block("underwriter")

    assert "EVIDENCE_BUILDER_URL: http://evidence-builder:8003" in block
    assert "evidence-builder.env" not in block and "evidence-builder.env" not in _service_block("checker")


def test_the_keys_command_writes_the_evidence_builders_file(tmp_path):
    lines: list[str] = []
    (tmp_path / "underwriter.env").write_text("SANNING_AGENT_ID=desk-live\n")

    assert dev.write_keys(tmp_path, log=lines.append) == 0

    builder = read_env_file(tmp_path / "evidence-builder.env")
    underwriter = read_env_file(tmp_path / "underwriter.env")
    checker = read_env_file(tmp_path / "checker.env")
    assert builder["SANNING_SIGNING_KEY_KIND"] == "organization"
    assert builder["UNDERWRITER_AGENT_ID"] == "desk-live"
    assert builder["CHECKER_AGENT_ID"] == "checker-sandbox"
    assert re.fullmatch(r"[0-9a-f]{64}", builder["SANNING_SIGNING_SEED"])
    assert builder["SANNING_SIGNING_SEED"] not in (underwriter["SANNING_SIGNING_SEED"], checker["SANNING_SIGNING_SEED"])
    assert (tmp_path / "evidence-builder.env").stat().st_mode & 0o077 == 0, "a seed file is private"
    printed = "\n".join(lines)
    assert builder["SANNING_SIGNING_SEED"] not in printed
    assert "the evidence builder's public key is" in printed
    # None of the agents' own settings, and no API key for anyone to forget.
    assert "SANNING_API_KEY" not in builder and "COUNTERPARTY_PUBLIC_KEY" not in builder
    assert startup.test_settings(builder_settings({**builder, "SANNING_API_KEY": "sanning_test_x"})).state == OK


def test_the_keys_command_keeps_what_the_evidence_builders_file_has(tmp_path):
    (tmp_path / "evidence-builder.env").write_text(
        f"SANNING_API_KEY=its-own-key\nSANNING_SIGNING_SEED={HAND_OVER_SEED}\nCHECKER_AGENT_ID=mine\n"
    )

    dev.write_keys(tmp_path, log=lambda _line: None)
    dev.write_keys(tmp_path, log=lambda _line: None)

    builder = read_env_file(tmp_path / "evidence-builder.env")
    assert builder["SANNING_SIGNING_SEED"] == HAND_OVER_SEED
    assert builder["SANNING_API_KEY"] == "its-own-key"
    assert builder["CHECKER_AGENT_ID"] == "mine"
    assert (tmp_path / "evidence-builder.env").read_text().count("SANNING_SIGNING_KEY_KIND") == 1


def test_the_checkers_pack_route_is_gone(tmp_path):
    settings = checker_settings(service="checker", default_agent_id="checker", default_port=8002, env={})
    client = TestClient(create_checker_app(settings))

    assert client.post(f"/cases/{CASE}/pack").status_code in (404, 405)
    assert not [path for path in client.get("/openapi.json").json()["paths"] if "pack" in path]


def test_no_agent_code_builds_a_pack_or_runs_bundle():
    package = REPO_ROOT / "src" / "credit_demo"
    with pytest.raises(ModuleNotFoundError):
        __import__("credit_demo.common.packs")
    for folder in ("underwriter", "checker", "common"):
        for path in sorted((package / folder).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for forbidden in ("build_pack", "run_bundle", "subprocess", '"bundle"'):
                assert forbidden not in text, f"{path.name} mentions {forbidden}"


def test_an_agent_reads_no_hand_over_key_setting():
    """The agents' settings carry the key's kind only so start-up can refuse
    the organization's; nothing builds a signer from it."""
    text = Path(REPO_ROOT / "src/credit_demo/common/sanning.py").read_text(encoding="utf-8")
    assert "ORGANIZATION_KEY_KIND" not in text and "KEY_KIND_VARIABLE" not in text


def test_the_readme_and_the_example_settings_describe_the_third_service():
    readme = " ".join((REPO_ROOT / "README.md").read_text(encoding="utf-8").split())
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "choose **Build evidence**" in readme
    assert "holds the organization's evidence key and no agent's seed" in readme
    assert "https://docs.sanning.io/evidence/build#how-to-build-an-evidence-pack" in readme
    assert "evidence-builder.env" in readme and "evidence-builder.env" in example
    deploy = " ".join((REPO_ROOT / "DEPLOY.md").read_text(encoding="utf-8").split())
    assert "EVIDENCE_BUILDER_URL" in deploy and "EVIDENCE_BUILDER_URL" in example


def test_a_bug_while_opening_the_log_store_is_not_reported_as_a_failed_check(tmp_path):
    settings = _builder(
        tmp_path, SANNING_LOG_BUCKET="evidence", AWS_REGION="r", AWS_ACCESS_KEY_ID="i", AWS_SECRET_ACCESS_KEY="s"
    )

    def broken(**_kwargs):
        raise RuntimeError("a bug")

    with pytest.raises(RuntimeError, match="a bug"):
        startup.test_log_store(settings, make_store=broken)
