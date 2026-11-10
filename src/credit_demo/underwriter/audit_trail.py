"""The audit trail: one zip per case, for an auditor.

It is the case file the evidence builder builds (`credit_demo.evidence_builder`):
both agents' packs, sealed with the organization's hand-over key, each record
signed by its own agent. The kernel reads the whole file as one verdict:
`sanning-proof case audit-trail-<id>.zip`.

The credit desk stands in for the company's case system: when a case closes it
asks the evidence builder for the file, and the agents take no part. A file can
be built only once Sanning has stamped the case's records, which takes minutes
on a real run, so the desk asks in the background and asks again while the
records are not stamped yet, and when a call fails because the evidence builder
did not answer (it might be waking from a stop).
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

from ..common import cases as case_states
from ..common.cases import CaseStore
from ..common.errors import describe, tell_the_operator
from ..common.messages import AUDIT_TRAIL_FAILED, AUDIT_TRAIL_NOT_READY

# How long to keep trying while the records are not stamped yet, or the
# evidence builder does not answer.
TRY_EVERY_S = 20.0
TRIES = 45

# How long one call may stay open. The evidence builder does its whole build
# while the call is open.
CALL_TIMEOUT_S = 300

# What the evidence builder answers while the records are not stamped yet.
NOT_STAMPED_YET = 202

# The statuses a host or a proxy answers while the service behind it is not
# up: the call is made again, as for a connection that was refused.
SERVICE_NOT_UP = (502, 503, 504)


class NotStampedInTime(Exception):
    """The case file was still not ready when the tries ran out."""


def fail(cases: CaseStore, case_id: str, exc: BaseException) -> None:
    """Mark the audit trail failed. The page says it in plain words; the
    technical text goes to the log and the Technical detail."""
    detail = describe(exc)
    tell_the_operator(f"case {case_id}: the audit trail failed: {detail}")
    case = cases.get(case_id)
    case.audit_trail_state = case_states.FAILED
    case.audit_trail_error = AUDIT_TRAIL_NOT_READY if isinstance(exc, NotStampedInTime) else AUDIT_TRAIL_FAILED
    case.audit_trail_detail = detail
    cases.save(case)


def audit_trail_path(cases: CaseStore, case_id: str) -> Path:
    return cases.folder(case_id) / f"audit-trail-{case_id}.zip"


def prepare(
    *,
    cases: CaseStore,
    case_id: str,
    evidence_builder_url: str,
    try_every_s: float | None = None,
    tries: int | None = None,
) -> None:
    """Get the audit trail for a concluded case from the evidence builder,
    and say how it went on the case: ready, or failed with the reason."""
    case = cases.get(case_id)
    case.audit_trail_state = case_states.PREPARING
    case.audit_trail_error = None
    case.audit_trail_detail = None
    cases.save(case)
    try:
        body = _ask_for_the_case_file(
            evidence_builder_url,
            case_id,
            TRY_EVERY_S if try_every_s is None else try_every_s,
            TRIES if tries is None else tries,
        )
        audit_trail_path(cases, case_id).write_bytes(body)
        case = cases.get(case_id)
        case.audit_trail_state = case_states.READY
    except Exception as exc:
        fail(cases, case_id, exc)
        return
    cases.save(case)


def _ask_for_the_case_file(url: str, case_id: str, try_every_s: float, tries: int) -> bytes:
    """The case file, asked for until it comes. A 202 (the records are not
    stamped yet) and a call that got no answer (the connection was refused or
    timed out, or the service was not up) are asked again. Any other answer
    that is not the file ends the asking."""
    last = "the records were not stamped yet"
    for attempt in range(tries):
        if attempt:
            time.sleep(try_every_s)
        try:
            answer = httpx.post(f"{url}/cases/{case_id}/case-file", timeout=CALL_TIMEOUT_S)
        except httpx.TransportError as exc:
            last = f"the evidence builder did not answer ({describe(exc)})"
            continue
        if answer.status_code == NOT_STAMPED_YET:
            last = _reason(answer) or last
            continue
        if answer.status_code in SERVICE_NOT_UP:
            last = f"the evidence builder was not up (HTTP {answer.status_code})"
            continue
        if answer.status_code != 200:
            raise RuntimeError(
                f"the evidence builder could not build the case file (HTTP {answer.status_code}): "
                f"{_reason(answer) or answer.text}"
            )
        return answer.content
    raise NotStampedInTime(f"gave up waiting for the case file: {last}")


def _reason(answer: httpx.Response) -> str:
    try:
        return str(answer.json().get("reason") or "")
    except (ValueError, AttributeError):
        return ""
