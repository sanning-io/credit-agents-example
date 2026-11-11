"""The launcher's start-up tests: a wrong setting stops at once and names the
variable; a service that did not answer is tried again, then given up on."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from openai import OpenAIError
from sanning_anchor.errors import IngestError

from credit_demo.common import startup
from credit_demo.common.config import settings_from_env
from credit_demo.common.startup import FAIL, OK, WARN, Finding, run_startup_tests


def _settings(tmp_path, **overrides):
    env = {
        "SANNING_API_KEY": "sk-secret-value",
        "SANNING_SIGNING_SEED": "ab" * 32,
        "OPENAI_API_KEY": "or-secret-value",
        "SANNING_LOG_BUCKET": "evidence",
        "AWS_ENDPOINT_URL_S3": "https://s3.example",
        "AWS_REGION": "eu-north-1",
        "AWS_ACCESS_KEY_ID": "id",
        "AWS_SECRET_ACCESS_KEY": "bucket-secret-value",
        "COUNTERPARTY_PUBLIC_KEY": "cd" * 32,
        "CHECKER_URL": "http://localhost:8002",
        "EVIDENCE_BUILDER_URL": "http://localhost:8003",
        "RUNS_DIR": str(tmp_path / "runs"),
    }
    env.update(overrides)
    return settings_from_env(
        service="underwriter",
        default_agent_id="underwriter", default_port=8001, env=env,
    )


SECRETS = ("sk-secret-value", "or-secret-value", "bucket-secret-value", "ab" * 32)


def _no_secret_in(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text


def test_missing_settings_are_named(tmp_path):
    finding = startup.test_settings(_settings(tmp_path, SANNING_LOG_BUCKET=""))
    assert finding.state == FAIL
    assert "SANNING_LOG_BUCKET" in finding.message


def test_a_seed_that_is_not_a_seed_is_named_and_not_printed(tmp_path):
    finding = startup.test_settings(_settings(tmp_path, SANNING_SIGNING_SEED="zz" * 32))
    assert finding.state == FAIL
    assert "SANNING_SIGNING_SEED" in finding.message
    assert "zz" * 32 not in finding.message


def test_an_agent_id_that_cannot_be_a_folder_name_stops_the_start(tmp_path):
    finding = startup.test_settings(_settings(tmp_path, SANNING_AGENT_ID="Credit Desk"))
    assert finding.state == FAIL
    assert "SANNING_AGENT_ID" in finding.message
    assert startup.test_settings(_settings(tmp_path, SANNING_AGENT_ID="credit-desk")).state == OK


def test_an_agent_started_alone_needs_the_addresses_of_the_services_it_calls(tmp_path):
    wanted = {
        "underwriter": {"CHECKER_URL": "http://peer:1", "EVIDENCE_BUILDER_URL": "http://peer:3"},
        "checker": {"DESK_URL": "http://peer:1"},
    }
    for service, addresses in wanted.items():
        env = {
            "SANNING_API_KEY": "sanning_test_x", "SANNING_SIGNING_SEED": "ab" * 32,
            "OPENAI_API_KEY": "or-x", "COUNTERPARTY_PUBLIC_KEY": "cd" * 32,
            "RUNS_DIR": str(tmp_path / "runs"),
        }

        def settings(extra, service=service, env=env):
            return settings_from_env(
                service=service, default_agent_id=service, default_port=8001,
                env={**env, **extra},
            )

        finding = startup.test_settings(settings({}))
        assert finding.state == FAIL
        for variable in addresses:
            assert variable in finding.message
        assert startup.test_settings(settings(addresses)).state == OK


class _Index:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self

    def list(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return []

    def sealed_through(self):
        self.calls.append({"agent": None})
        if self.error:
            raise self.error
        return None


class _PlaneIndex:
    """Answers like Sanning does: 404 for a read that names an agent it has
    not seen, and a stamp answer for a read that names none."""

    def __init__(self, known_agents=()):
        self.known_agents = set(known_agents)
        self.calls = []

    def __call__(self, **kwargs):
        return self

    def list(self, *, since, until, agent=None):
        self.calls.append({"agent": agent})
        if agent is not None and agent not in self.known_agents:
            raise IngestError("not found", status=404)
        return []

    def sealed_through(self):
        self.calls.append({"agent": None})
        return None


def test_sanning_passes_for_an_agent_it_has_not_seen_and_names_no_agent(tmp_path):
    index = _PlaneIndex(known_agents=())
    finding = startup.test_sanning(_settings(tmp_path), make_index=index)
    assert finding.state == OK
    assert index.calls and all(call["agent"] is None for call in index.calls)


def test_a_refused_api_key_stops_and_names_the_variable(tmp_path):
    finding = startup.test_sanning(
        _settings(tmp_path), make_index=_Index(IngestError("refused", status=401))
    )
    assert (finding.state, finding.transient) == (FAIL, False)
    assert "SANNING_API_KEY" in finding.message
    _no_secret_in(finding.line())


def test_sanning_not_answering_is_worth_waiting_for(tmp_path):
    for error in (IngestError("control plane unreachable: timed out"), IngestError("busy", status=503)):
        finding = startup.test_sanning(_settings(tmp_path), make_index=_Index(error))
        assert (finding.state, finding.transient) == (FAIL, True)


def _store(code: str, satisfied: bool = False, reason: str = "because"):
    verdict = SimpleNamespace(code=code, satisfied=satisfied, reason=reason)

    def make(**kwargs):
        assert kwargs["create_bucket"] is False, "a test must never create a bucket"
        return SimpleNamespace(check_protection=lambda: verdict)

    return make


def test_a_protected_bucket_passes(tmp_path):
    finding = startup.test_bucket(_settings(tmp_path), make_store=_store("ready", satisfied=True))
    assert finding.state == OK


def test_a_bucket_without_object_lock_is_a_warning_not_a_stop(tmp_path):
    finding = startup.test_bucket(
        _settings(tmp_path), make_store=_store("object-lock-off", reason="Object Lock is off.")
    )
    assert finding.state == WARN
    assert "Object Lock is off." in finding.message


def test_a_bucket_these_keys_cannot_open_stops_and_names_the_variables(tmp_path):
    settings = _settings(tmp_path)
    finding = startup.test_bucket(settings, make_store=_store("store-unknown"))
    assert (finding.state, finding.transient) == (FAIL, False)
    for name in ("SANNING_LOG_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_ENDPOINT_URL_S3", settings.variable("REGION")):
        assert name in finding.message
    _no_secret_in(finding.line())


def test_a_bucket_that_does_not_answer_is_tried_again(tmp_path):
    finding = startup.test_bucket(_settings(tmp_path), make_store=_store("store-unreachable"))
    assert (finding.state, finding.transient) == (FAIL, True)
    assert "AWS_ENDPOINT_URL_S3" in finding.message
    _no_secret_in(finding.line())


def test_a_bucket_that_does_not_exist_yet_passes_and_says_who_creates_it(tmp_path):
    finding = startup.test_bucket(_settings(tmp_path), make_store=_store("store-absent"))
    assert (finding.state, finding.transient) == (OK, False)
    assert "The SDK creates it, locked, in the Region from AWS_REGION" in finding.message


class _ModelError(OpenAIError):
    def __init__(self, status_code=None):
        super().__init__(f"model error {status_code}")
        self.status_code = status_code


def _model(error=None):
    def build(_settings):
        def invoke(_prompt):
            if error:
                raise error
            return "OK"

        return SimpleNamespace(invoke=invoke)

    return build


def test_a_model_that_answers_passes(tmp_path):
    assert startup.test_model(_settings(tmp_path), build=_model()).state == OK


def test_a_refused_model_key_stops_and_names_the_variable(tmp_path):
    finding = startup.test_model(_settings(tmp_path), build=_model(_ModelError(401)))
    assert (finding.state, finding.transient) == (FAIL, False)
    assert "OPENAI_API_KEY" in finding.message


def test_an_unknown_model_names_the_model_setting(tmp_path):
    finding = startup.test_model(_settings(tmp_path), build=_model(_ModelError(404)))
    assert "MODEL" in finding.message


def test_a_busy_model_provider_is_worth_waiting_for(tmp_path):
    for status in (429, 502):
        finding = startup.test_model(_settings(tmp_path), build=_model(_ModelError(status)))
        assert (finding.state, finding.transient) == (FAIL, True)


def test_a_model_fault_that_is_not_the_provider_s_is_not_reported_as_a_failed_check(tmp_path):
    with pytest.raises(RuntimeError, match="a bug"):
        startup.test_model(_settings(tmp_path), build=_model(RuntimeError("a bug")))


def test_a_bug_while_testing_sanning_is_not_reported_as_a_failed_check(tmp_path):
    def broken(**_kwargs):
        raise RuntimeError("a bug")

    with pytest.raises(RuntimeError, match="a bug"):
        startup.test_sanning(_settings(tmp_path), make_index=broken)


def test_a_bug_while_opening_the_bucket_is_not_reported_as_a_failed_check(tmp_path):
    def broken(**_kwargs):
        raise RuntimeError("a bug")

    with pytest.raises(RuntimeError, match="a bug"):
        startup.test_bucket(_settings(tmp_path), make_store=broken)


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def _run(tmp_path, tests, **kwargs):
    clock = _Clock()
    lines: list[str] = []
    passed = run_startup_tests(
        [_settings(tmp_path, **kwargs)], tests=tests,
        sleep=clock.sleep, clock=clock, log=lines.append,
        wait_for_s=60, retry_every_s=10,
    )
    return passed, lines, clock


def _fixed(state, transient=False, name="bucket"):
    return lambda s: Finding(s.service, name, state, "said so", transient)


def test_all_passing_starts(tmp_path):
    passed, lines, clock = _run(tmp_path, [_fixed(OK), _fixed(WARN, name="model")])
    assert passed is True
    assert clock.slept == []
    assert len(lines) == 3  # settings, bucket, model


def test_a_wrong_setting_stops_at_once(tmp_path):
    passed, _lines, clock = _run(tmp_path, [_fixed(FAIL)])
    assert passed is False
    assert clock.slept == []


def test_incomplete_settings_skip_every_network_test(tmp_path):
    called = []
    passed, _lines, _clock = _run(
        tmp_path, [lambda s: called.append(s) or Finding(s.service, "x", OK, "")],
        SANNING_LOG_BUCKET="",
    )
    assert passed is False
    assert called == []


def test_a_service_that_comes_back_lets_the_start_go_ahead(tmp_path):
    answers = iter([FAIL, FAIL, OK])

    def flaky(s):
        state = next(answers)
        return Finding(s.service, "Sanning", state, "down" if state == FAIL else "up", state == FAIL)

    passed, lines, clock = _run(tmp_path, [flaky])
    assert passed is True
    assert clock.slept == [10, 10]
    assert sum("Trying again" in line for line in lines) == 2


def test_a_service_that_stays_down_is_given_up_on(tmp_path):
    passed, lines, clock = _run(tmp_path, [_fixed(FAIL, transient=True, name="Sanning")])
    assert passed is False
    assert sum(clock.slept) <= 60
    assert "gave up after 60 seconds" in lines[-1]


def test_a_wrong_setting_does_not_wait_for_a_down_service(tmp_path):
    passed, _lines, clock = _run(
        tmp_path, [_fixed(FAIL, transient=True, name="Sanning"), _fixed(FAIL, name="bucket")]
    )
    assert passed is False
    assert clock.slept == []


def test_startup_makes_no_enrollment_call(tmp_path, monkeypatch):
    import importlib

    enroll_module = importlib.import_module("sanning_anchor.enroll")
    calls = []
    monkeypatch.setattr(enroll_module, "enroll", lambda **kw: calls.append(kw))
    assert not hasattr(startup, "test_enrollment")
    assert not [t for t in startup.NETWORK_TESTS if "enroll" in t.__name__]
    startup.test_sanning(_settings(tmp_path), make_index=_PlaneIndex())
    startup.test_bucket(_settings(tmp_path), make_store=_store("ready", satisfied=True))
    assert calls == []


def test_a_missing_setting_names_the_file_the_service_reads(tmp_path):
    from credit_demo.evidence_builder.config import load as load_evidence_builder

    agent = startup.test_settings(_settings(tmp_path, OPENAI_API_KEY=""))
    assert "`.env`" in agent.message and "`underwriter.env`" in agent.message

    builder = startup.test_settings(load_evidence_builder({}))
    assert "SANNING_API_KEY" in builder.message
    assert "`evidence-builder.env`" in builder.message
    assert "never reads `.env`" in builder.message
