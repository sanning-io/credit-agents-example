"""Start-up tests: the launcher tests each service's settings against the real
services before it starts either one.

A production service refuses to start on a setting that cannot work, and says
which one, rather than starting and failing its first user. So before
`credit-demo` starts its services, it asks, for each one:

- **Settings**: every setting a run needs is set, and the seed is a seed.
  Whose key the seed is gets checked too: an agent refuses the organization's
  hand-over key, and the evidence builder refuses anything but that key.
- **Sanning**: the API key is accepted and can read the organization's records.
  The read asks how far Sanning has stamped them and names no agent, so it
  records nothing and an agent that has recorded nothing yet passes.
  `bundle` reads records with the evidence builder's key to build a case
  file, so a key that cannot read would fail there too.
- **Bucket**: with a bucket set, the bucket answers to these keys, and whether
  it has Object Lock, versioning and a default retention rule. Read only: a
  locked bucket keeps every object it is given, so a test that wrote one would
  leave it there. A bucket that does not exist yet passes: the SDK creates it,
  locked, in the Region from `AWS_REGION`, on the agent's first record. Missing
  protection on a bucket that exists is a warning, not a stop: the SDK itself
  warns and writes.
  The SDK's answer does not say whether the bucket refused the keys or did not
  answer, so a bucket test never waits: it stops, naming both.
- **Log store**, for the evidence builder: the bucket opens with its own
  keys. It never creates the bucket, and says nothing of the bucket's
  protection, which is the agents' side.
- **Model**: the model answers one short prompt through its provider key.
  The evidence builder has no model.

Every failure is one of two kinds. A setting that is wrong (a refused key, a
bucket these keys cannot open, a model that does not exist) stops the launcher
at once, naming the variable to change: waiting does not fix it. A service that
did not answer (a timeout, a refused connection, a 5xx, a 429) is tried again
every `RETRY_EVERY_S` seconds until `WAIT_FOR_S` has passed, so a short outage
delays the start rather than failing it.

No test enrolls a signing key or creates a bucket: the SDK enrolls each
agent's key on its first record, and creates a missing bucket then. The
evidence builder's first case file enrolls the hand-over key.

None of this prints a secret: a failure names the variable, never its value.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Sequence

from sanning_anchor.errors import IngestError
from sanning_anchor.records_index import ControlPlaneRecordsIndex
from sanning_anchor.s3 import S3ObjectStore

from ..evidence_builder.config import EvidenceBuilderSettings
from .config import AGENT_KEY_KIND, ORGANIZATION_KEY_KIND, ServiceSettings
from .errors import describe, tell_the_operator
from .sanning import build_model, folder_name

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


def test_settings(settings: ServiceSettings | EvidenceBuilderSettings) -> Finding:
    if isinstance(settings, EvidenceBuilderSettings):
        return test_evidence_builder_settings(settings)
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
    if settings.signing_key_kind not in ("", AGENT_KEY_KIND):
        return Finding(
            settings.service, "settings", FAIL,
            f"{v('SANNING_SIGNING_KEY_KIND')} is {settings.signing_key_kind!r}: an agent "
            "signs with its own seed, and the organization's hand-over key belongs to the "
            "evidence builder alone. Unset it, or set it to agent",
        )
    try:
        folder_name(settings.agent_id)
    except ValueError as exc:
        return Finding(settings.service, "settings", FAIL, describe(exc))
    return Finding(settings.service, "settings", OK, "all set")


def test_evidence_builder_settings(settings: EvidenceBuilderSettings) -> Finding:
    v = settings.variable
    if settings.missing:
        return Finding(
            settings.service, "settings", FAIL,
            "not set: " + ", ".join(settings.missing) + " (see .env.example)",
        )
    if settings.signing_key_kind != ORGANIZATION_KEY_KIND:
        return Finding(
            settings.service, "settings", FAIL,
            f"{v('SANNING_SIGNING_KEY_KIND')} must be {ORGANIZATION_KEY_KIND}: this service "
            "signs with the organization's hand-over key, never an agent's",
        )
    seed = settings.signing_seed_hex.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", seed):
        return Finding(
            settings.service, "settings", FAIL,
            f"{v('SANNING_SIGNING_SEED')} is not a seed: it must be 64 hex "
            "characters (`credit-demo keys` writes a fresh one)",
        )
    for agent_id, variable in (
        (settings.underwriter_agent_id, "UNDERWRITER_AGENT_ID"),
        (settings.checker_agent_id, "CHECKER_AGENT_ID"),
    ):
        try:
            folder_name(agent_id, variable)
        except ValueError as exc:
            return Finding(settings.service, "settings", FAIL, describe(exc))
    if settings.underwriter_agent_id == settings.checker_agent_id:
        return Finding(
            settings.service, "settings", FAIL,
            "UNDERWRITER_AGENT_ID and CHECKER_AGENT_ID name the same agent: a case file "
            "holds one pack for each of two agents",
        )
    return Finding(settings.service, "settings", OK, "all set")


def test_sanning(
    settings: ServiceSettings,
    *,
    make_index: Callable[..., Any] = ControlPlaneRecordsIndex,
) -> Finding:
    v = settings.variable
    try:
        index = make_index(api_key=settings.api_key, base_url=settings.base_url)
        # Names no agent, so an agent Sanning has not seen yet passes.
        index.sealed_through()
    except IngestError as exc:
        if exc.retryable:
            return Finding(
                settings.service, "Sanning", FAIL,
                f"{settings.base_url} did not answer ({describe(exc)})",
                transient=True,
            )
        if exc.status in (401, 403):
            needs = "Hand-over" if isinstance(settings, EvidenceBuilderSettings) else "Anchor"
            return Finding(
                settings.service, "Sanning", FAIL,
                f"the API key was refused (HTTP {exc.status}). Change "
                f"{v('SANNING_API_KEY')}: it needs {needs}, and must belong to "
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
            # A test never creates a bucket: the SDK does, on the first record.
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
            settings.service, "bucket", OK,
            f"no bucket named {settings.bucket!r} yet. The SDK creates it, "
            "locked, in the Region from "
            f"{v('REGION')}, on the agent's first record",
        )
    if not verdict.satisfied:
        return Finding(settings.service, "bucket", WARN, verdict.reason)
    return Finding(
        settings.service, "bucket", OK,
        "opens with these keys, with Object Lock, versioning and default retention on",
    )


def test_log_store(
    settings: EvidenceBuilderSettings,
    *,
    make_store: Callable[..., Any] = S3ObjectStore,
) -> Finding:
    """The bucket the agents write to opens with the evidence builder's own
    keys. It reads the bucket and makes no claim about its protection, which
    is the agents' side of it, and it never creates one."""
    v = settings.variable
    if not settings.bucket:
        return Finding(
            settings.service, "log store", OK,
            "no bucket set; it reads the agents' folders on local disk (Sandbox only)",
        )
    try:
        verdict = make_store(
            bucket=settings.bucket,
            endpoint=settings.bucket_endpoint or None,
            region=settings.bucket_region,
            access_key_id=settings.bucket_access_key_id or None,
            secret_access_key=settings.bucket_secret_access_key or None,
            force_path_style=settings.bucket_path_style,
            create_bucket=False,
        ).check_protection()
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        return Finding(
            settings.service, "log store", FAIL,
            f"could not open it ({describe(exc)}). Check {v('ENDPOINT')} and {v('REGION')}",
        )
    if verdict.code == "store-unreachable":
        return Finding(
            settings.service, "log store", FAIL,
            f"{v('ENDPOINT')} did not answer ({verdict.reason})",
            transient=True,
        )
    if verdict.code == "store-unknown":
        return Finding(
            settings.service, "log store", FAIL,
            f"these keys cannot open {v('BUCKET')}. Check {v('BUCKET')}, "
            f"{v('ENDPOINT')}, {v('REGION')}, {v('ACCESS_KEY_ID')} and "
            f"{v('SECRET_ACCESS_KEY')}: they need s3:ListBucket on the bucket "
            "and s3:GetObject and s3:PutObject on its objects",
        )
    if verdict.code == "store-absent":
        return Finding(
            settings.service, "log store", FAIL,
            f"no bucket named {settings.bucket!r}. The agents create it on their first "
            "record, and this service never does",
        )
    return Finding(settings.service, "log store", OK, "opens with these keys")


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


NETWORK_TESTS: tuple[Callable[[Any], Finding], ...] = (
    test_sanning,
    test_bucket,
    test_model,
)

# The evidence builder calls no model and writes no record: it reads Sanning's
# index and the bucket.
EVIDENCE_BUILDER_TESTS: tuple[Callable[[Any], Finding], ...] = (
    test_sanning,
    test_log_store,
)


def run_startup_tests(
    services: Sequence[ServiceSettings | EvidenceBuilderSettings],
    *,
    tests: Sequence[Callable[[Any], Finding]] | None = None,
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
    pending: list[tuple[Any, Callable[[Any], Finding]]] = []
    passed = True
    for settings in services:
        finding = test_settings(settings)
        log(finding.line())
        if finding.state == FAIL:
            passed = False
            continue
        own = EVIDENCE_BUILDER_TESTS if isinstance(settings, EvidenceBuilderSettings) else NETWORK_TESTS
        pending += [(settings, test) for test in (own if tests is None else tests)]
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
