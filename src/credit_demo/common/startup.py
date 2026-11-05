"""Start-up tests: the launcher tests each service's settings against the real
services before it starts either one.

A production service refuses to start on a setting that cannot work, and says
which one, rather than starting and failing its first user. So before
`credit-demo` starts the two services, it asks, for each side:

- **Settings**: every setting a run needs is set, and the seed is a seed.
- **Sanning**: the API key can read this agent's records back (a read, so
  nothing is recorded). `bundle` reads records with the same key to build a
  pack, so a key that cannot read would fail there too.
- **Enrollment**: each agent's signing key is enrolled under its name, the
  step its first record would otherwise take. Enrolling a key that is already
  enrolled changes nothing, so this is safe on every start. A name already
  enrolled under a different key stops here, instead of on every step of the
  first case.
- **Bucket**: with a bucket set, the bucket answers to these keys, and whether it has Object Lock,
  versioning and a default retention rule. Read only: a locked bucket keeps
  every object it is given, so a test that wrote one would leave it there.
  Missing protection is a warning, not a stop: the SDK itself warns and writes.
  The SDK's answer does not say whether the bucket refused the keys or did not
  answer, so a bucket test never waits: it stops, naming both.
- **Model**: the model answers one short prompt through its provider key.

Every failure is one of two kinds. A setting that is wrong (a refused key, a
bucket these keys cannot open, a model that does not exist) stops the launcher
at once, naming the variable to change: waiting does not fix it. A service that
did not answer (a timeout, a refused connection, a 5xx, a 429) is tried again
every `RETRY_EVERY_S` seconds until `WAIT_FOR_S` has passed, so a short outage
delays the start rather than failing it.

None of this prints a secret: a failure names the variable, never its value.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from sanning_anchor.enroll import enroll
from sanning_anchor.errors import (
    EnrollmentFailedError,
    EnrollmentThrottledError,
    IngestError,
    RotationUnauthorizedError,
)
from sanning_anchor.signer import LocalEd25519Signer
from sanning_anchor.records_index import ControlPlaneRecordsIndex
from sanning_anchor.s3 import S3ObjectStore

from .config import ServiceSettings
from .errors import describe, tell_the_operator
from .sanning import build_model

OK, WARN, FAIL = "ok", "warn", "fail"

WAIT_FOR_S = 120.0
RETRY_EVERY_S = 10.0


@dataclass(frozen=True)
class Finding:
    """One test's result for one service. `transient` marks a failure a retry
    could fix: the service did not answer, rather than refused."""

    service: str
    test: str
    state: str
    message: str
    transient: bool = False

    def line(self) -> str:
        mark = {OK: "ok  ", WARN: "warn", FAIL: "FAIL"}[self.state]
        return f"credit-demo: {mark} {self.service} {self.test}: {self.message}"


def test_settings(settings: ServiceSettings) -> Finding:
    v = settings.variable
    if settings.missing:
        message = "not set: " + ", ".join(settings.missing) + " (see .env.example)"
        if settings.api_key and not settings.sandbox and not settings.bucket:
            message = (
                "a Live API key needs the S3-compatible bucket for the content. " + message
                + ". Only a Sandbox API key (sanning_test_) can keep the content on local disk"
            )
        return Finding(settings.service, "settings", FAIL, message)
    seed = settings.signing_seed_hex.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", seed):
        return Finding(
            settings.service, "settings", FAIL,
            f"{v('SANNING_SIGNING_SEED')} is not a seed: it must be 64 hex "
            "characters (`credit-demo keys` prints a fresh one)",
        )
    return Finding(settings.service, "settings", OK, "all set")


def test_sanning(
    settings: ServiceSettings,
    *,
    make_index: Callable[..., Any] = ControlPlaneRecordsIndex,
) -> Finding:
    v = settings.variable
    until = datetime.now(timezone.utc)
    since = until - timedelta(minutes=1)
    try:
        index = make_index(api_key=settings.api_key, base_url=settings.base_url)
        index.list(
            since=since.isoformat().replace("+00:00", "Z"),
            until=until.isoformat().replace("+00:00", "Z"),
            agent=settings.agent_id,
        )
    except IngestError as exc:
        if exc.retryable:
            return Finding(
                settings.service, "Sanning", FAIL,
                f"{settings.base_url} did not answer ({describe(exc)})",
                transient=True,
            )
        if exc.status in (401, 403):
            return Finding(
                settings.service, "Sanning", FAIL,
                f"the API key was refused (HTTP {exc.status}). Change "
                f"{v('SANNING_API_KEY')}: it needs Anchor, and must belong to "
                f"the organization at {v('SANNING_BASE_URL')}",
            )
        return Finding(
            settings.service, "Sanning", FAIL,
            f"{settings.base_url} refused the request ({describe(exc)}). "
            f"Check {v('SANNING_BASE_URL')}",
        )
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        return Finding(
            settings.service, "Sanning", FAIL,
            f"{v('SANNING_BASE_URL')} could not be used ({describe(exc)})",
        )
    mode = "a Sandbox key: records are not evidence" if settings.sandbox else "a Live key"
    return Finding(
        settings.service, "Sanning", OK,
        f"the API key works at {settings.base_url} ({mode})",
    )


def test_enrollment(
    settings: ServiceSettings,
    *,
    enroll_agent: Callable[..., Any] = enroll,
) -> Finding:
    v = settings.variable
    try:
        enroll_agent(
            base_url=settings.base_url,
            api_key=settings.api_key,
            agent_id=settings.agent_id,
            signer=LocalEd25519Signer.from_seed_hex(settings.signing_seed_hex),
        )
    except RotationUnauthorizedError:
        return Finding(
            settings.service, "enrollment", FAIL,
            f"an agent named {settings.agent_id!r} is already enrolled in this "
            f"organization with a different signing key. Give this agent its own "
            f"name ({v('SANNING_AGENT_ID')}), or use the seed it was enrolled "
            f"with ({v('SANNING_SIGNING_SEED')})",
        )
    except EnrollmentThrottledError:
        return Finding(
            settings.service, "enrollment", FAIL,
            "Sanning is limiting enrollments for this API key",
            transient=True,
        )
    except IngestError as exc:
        if exc.retryable:
            return Finding(
                settings.service, "enrollment", FAIL,
                f"{settings.base_url} did not answer ({describe(exc)})",
                transient=True,
            )
        return Finding(settings.service, "enrollment", FAIL, f"refused ({describe(exc)})")
    except EnrollmentFailedError as exc:
        return Finding(settings.service, "enrollment", FAIL, f"refused ({describe(exc)})")
    return Finding(
        settings.service, "enrollment", OK,
        f"{settings.agent_id!r} is enrolled with this signing key",
    )


def test_bucket(
    settings: ServiceSettings,
    *,
    make_store: Callable[..., Any] = S3ObjectStore,
) -> Finding:
    v = settings.variable
    if not settings.bucket:
        # Only a Sandbox key gets here: settings refuse any other key
        # without a bucket. Local disk has no connection to test.
        return Finding(
            settings.service, "bucket", OK,
            "none set; the content stays on local disk (Sandbox only)",
        )
    try:
        store = make_store(
            bucket=settings.bucket,
            endpoint=settings.bucket_endpoint or None,
            region=settings.bucket_region,
            access_key_id=settings.bucket_access_key_id or None,
            secret_access_key=settings.bucket_secret_access_key or None,
            force_path_style=settings.bucket_path_style,
            # A test never creates a bucket.
            create_bucket=False,
        )
        verdict = store.check_protection()
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        return Finding(
            settings.service, "bucket", FAIL,
            f"could not open it ({describe(exc)}). Check {v('ENDPOINT')} "
            f"and {v('REGION')}",
        )
    if verdict.code == "store-unreachable":
        return Finding(
            settings.service, "bucket", FAIL,
            f"{v('ENDPOINT')} did not answer ({verdict.reason})",
            transient=True,
        )
    if verdict.code == "store-unknown":
        return Finding(
            settings.service, "bucket", FAIL,
            f"these keys cannot open {v('BUCKET')}. Check {v('BUCKET')}, "
            f"{v('ENDPOINT')}, {v('REGION')}, {v('ACCESS_KEY_ID')} and "
            f"{v('SECRET_ACCESS_KEY')}",
        )
    if verdict.code == "store-absent":
        return Finding(
            settings.service, "bucket", FAIL,
            f"no bucket by that name at {v('ENDPOINT')}. Create it with "
            f"Object Lock on, or change {v('BUCKET')}",
        )
    if not verdict.satisfied:
        return Finding(settings.service, "bucket", WARN, verdict.reason)
    return Finding(
        settings.service, "bucket", OK,
        "opens with these keys, with Object Lock, versioning and default retention on",
    )


def _model_failure_is_transient(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    name = type(exc).__name__
    return name in ("APIConnectionError", "APITimeoutError") or "timeout" in name.lower()


def test_model(
    settings: ServiceSettings,
    *,
    build: Callable[[ServiceSettings], Any] = build_model,
) -> Finding:
    v = settings.variable
    try:
        build(settings).invoke("Reply with the single word OK.")
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        if _model_failure_is_transient(exc):
            return Finding(
                settings.service, "model", FAIL,
                f"{v('OPENROUTER_BASE_URL')} did not answer ({describe(exc)})",
                transient=True,
            )
        status = getattr(exc, "status_code", None)
        if status == 401:
            why = f"the key was refused. Change {v('OPENROUTER_API_KEY')}"
        elif status == 402:
            why = f"the account behind {v('OPENROUTER_API_KEY')} has no credit left"
        elif status in (400, 404):
            why = f"no model {settings.model!r} for this key. Check {v('MODEL')}"
        else:
            why = f"the model call failed ({describe(exc)})"
        return Finding(settings.service, "model", FAIL, why)
    return Finding(settings.service, "model", OK, f"{settings.model} answers")


NETWORK_TESTS: tuple[Callable[[ServiceSettings], Finding], ...] = (
    # Enrollment first: an agent that is not enrolled yet has no records to
    # read, and the read below answers 404 for it.
    test_enrollment,
    test_sanning,
    test_bucket,
    test_model,
)


def run_startup_tests(
    services: Sequence[ServiceSettings],
    *,
    tests: Sequence[Callable[[ServiceSettings], Finding]] = NETWORK_TESTS,
    wait_for_s: float = WAIT_FOR_S,
    retry_every_s: float = RETRY_EVERY_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] = tell_the_operator,
) -> bool:
    """Test every service, print one line per test, and say whether to start.

    Settings come first: a service whose settings are incomplete gets no
    network test, since each one would only fail on the same missing value.
    """
    pending: list[tuple[ServiceSettings, Callable[[ServiceSettings], Finding]]] = []
    passed = True
    for settings in services:
        finding = test_settings(settings)
        log(finding.line())
        if finding.state == FAIL:
            passed = False
            continue
        pending += [(settings, test) for test in tests]
    if not passed:
        return False

    deadline = clock() + wait_for_s
    while pending:
        waiting: list[tuple[ServiceSettings, Callable[[ServiceSettings], Finding], Finding]] = []
        for settings, test in pending:
            finding = test(settings)
            if finding.state == FAIL and finding.transient:
                waiting.append((settings, test, finding))
                continue
            log(finding.line())
            if finding.state == FAIL:
                passed = False
        if not passed or not waiting:
            if not passed:
                for _settings, _test, finding in waiting:
                    log(finding.line())
            return passed
        if clock() + retry_every_s > deadline:
            for _settings, _test, finding in waiting:
                log(replace(finding, message=f"{finding.message}; gave up after "
                            f"{int(wait_for_s)} seconds").line())
            return False
        for _settings, _test, finding in waiting:
            log(f"credit-demo: wait {finding.service} {finding.test}: {finding.message}. "
                f"Trying again in {int(retry_every_s)} seconds")
        sleep(retry_every_s)
        pending = [(settings, test) for settings, test, _finding in waiting]
    return passed
