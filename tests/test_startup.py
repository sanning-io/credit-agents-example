"""The launcher's start-up tests: a wrong setting stops at once and names the
variable; a service that did not answer is tried again, then given up on."""

from __future__ import annotations

from types import SimpleNamespace

from sanning_anchor.errors import IngestError

from credit_demo.common import startup
from credit_demo.common.config import settings_from_env
from credit_demo.common.startup import FAIL, OK, WARN, Finding, run_startup_tests


def _settings(tmp_path, **overrides):
    env = {
        "UNDERWRITER_SANNING_API_KEY": "sk-secret-value",
        "UNDERWRITER_SANNING_SIGNING_SEED": "ab" * 32,
        "UNDERWRITER_OPENROUTER_API_KEY": "or-secret-value",
        "UNDERWRITER_BUCKET": "evidence",
        "UNDERWRITER_ENDPOINT": "https://s3.example",
        "UNDERWRITER_ACCESS_KEY_ID": "id",
        "UNDERWRITER_SECRET_ACCESS_KEY": "bucket-secret-value",
        "UNDERWRITER_COUNTERPARTY_PUBLIC_KEY": "cd" * 32,
        "UNDERWRITER_RUNS_DIR": str(tmp_path / "runs"),
    }
    env.update(overrides)
    return settings_from_env(
        service="underwriter", prefix="UNDERWRITER",
        default_agent_id="underwriter", default_port=8001, env=env,
    )


SECRETS = ("sk-secret-value", "or-secret-value", "bucket-secret-value", "ab" * 32)


def _no_secret_in(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text


# ── settings ──────────────────────────────────────────────────────────────


def test_missing_settings_are_named(tmp_path):
    finding = startup.test_settings(_settings(tmp_path, UNDERWRITER_BUCKET=""))
    assert finding.state == FAIL
    assert "SANNING_S3_BUCKET" in finding.message


def test_a_seed_that_is_not_a_seed_is_named_and_not_printed(tmp_path):
    finding = startup.test_settings(_settings(tmp_path, UNDERWRITER_SANNING_SIGNING_SEED="zz" * 32))
    assert finding.state == FAIL
    assert "UNDERWRITER_SANNING_SIGNING_SEED" in finding.message
    assert "zz" * 32 not in finding.message


# ── Sanning ───────────────────────────────────────────────────────────────


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


def test_sanning_is_a_read_of_this_agents_records(tmp_path):
    index = _Index()
    finding = startup.test_sanning(_settings(tmp_path), make_index=index)
    assert finding.state == OK
    assert index.calls[0]["agent"] == "underwriter"


def test_a_refused_api_key_stops_and_names_the_variable(tmp_path):
    finding = startup.test_sanning(
        _settings(tmp_path), make_index=_Index(IngestError("refused", status=401))
    )
    assert (finding.state, finding.transient) == (FAIL, False)
    assert "UNDERWRITER_SANNING_API_KEY" in finding.message
    _no_secret_in(finding.line())


def test_sanning_not_answering_is_worth_waiting_for(tmp_path):
    for error in (IngestError("control plane unreachable: timed out"), IngestError("busy", status=503)):
        finding = startup.test_sanning(_settings(tmp_path), make_index=_Index(error))
        assert (finding.state, finding.transient) == (FAIL, True)


# ── bucket ────────────────────────────────────────────────────────────────


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
    for name in ("UNDERWRITER_BUCKET", "UNDERWRITER_ACCESS_KEY_ID", "UNDERWRITER_ENDPOINT", settings.variable("REGION")):
        assert name in finding.message
    _no_secret_in(finding.line())


def test_a_bucket_that_does_not_answer_is_tried_again(tmp_path):
    finding = startup.test_bucket(_settings(tmp_path), make_store=_store("store-unreachable"))
    assert (finding.state, finding.transient) == (FAIL, True)
    assert "UNDERWRITER_ENDPOINT" in finding.message
    _no_secret_in(finding.line())


def test_a_bucket_that_does_not_exist_stops(tmp_path):
    finding = startup.test_bucket(_settings(tmp_path), make_store=_store("store-absent"))
    assert finding.state == FAIL
    assert "UNDERWRITER_BUCKET" in finding.message


# ── model ─────────────────────────────────────────────────────────────────


class _ModelError(Exception):
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
    assert "UNDERWRITER_OPENROUTER_API_KEY" in finding.message


def test_an_unknown_model_names_the_model_setting(tmp_path):
    finding = startup.test_model(_settings(tmp_path), build=_model(_ModelError(404)))
    assert "MODEL" in finding.message


def test_a_busy_model_provider_is_worth_waiting_for(tmp_path):
    for status in (429, 502):
        finding = startup.test_model(_settings(tmp_path), build=_model(_ModelError(status)))
        assert (finding.state, finding.transient) == (FAIL, True)


# ── the loop ──────────────────────────────────────────────────────────────


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
        UNDERWRITER_BUCKET="",
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


# ── enrollment ────────────────────────────────────────────────────────────


def _enroll(error=None):
    calls = []

    def enroll_agent(**kwargs):
        calls.append(kwargs)
        if error:
            raise error

    enroll_agent.calls = calls
    return enroll_agent


def test_enrollment_enrolls_this_agent_under_its_own_name(tmp_path):
    enroll_agent = _enroll()
    finding = startup.test_enrollment(_settings(tmp_path), enroll_agent=enroll_agent)
    assert finding.state == OK
    assert enroll_agent.calls[0]["agent_id"] == "underwriter"


def test_a_name_held_by_another_key_stops_and_says_what_to_change(tmp_path):
    from sanning_anchor.errors import RotationUnauthorizedError

    finding = startup.test_enrollment(
        _settings(tmp_path), enroll_agent=_enroll(RotationUnauthorizedError("underwriter"))
    )
    assert (finding.state, finding.transient) == (FAIL, False)
    assert "UNDERWRITER_SANNING_AGENT_ID" in finding.message
    _no_secret_in(finding.line())


def test_a_throttled_enrollment_is_worth_waiting_for(tmp_path):
    from sanning_anchor.errors import EnrollmentThrottledError

    finding = startup.test_enrollment(
        _settings(tmp_path), enroll_agent=_enroll(EnrollmentThrottledError("underwriter", "limit"))
    )
    assert (finding.state, finding.transient) == (FAIL, True)
