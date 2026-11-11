"""The audit trail: one zip per case, built by the evidence builder when the case closes.

A file can be built only once Sanning has stamped the case's records, so the desk asks in the
background and asks again while the records are not stamped or the evidence builder does not
answer.
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
CALL_TIMEOUT_S = 900

# What the evidence builder answers while the records are not stamped yet.
NOT_STAMPED_YET = 202

# The statuses a host or a proxy answers while the service behind it is not
# up: the call is made again, as for a connection that was refused.
SERVICE_NOT_UP = (502, 503, 504)


class CaseFileRefused(Exception):
    """The evidence builder answered, and the answer was no."""


class NotStampedInTime(Exception):
    """The case file was still not ready when the tries ran out."""


def fail(cases: CaseStore, case_id: str, exc: BaseException) -> None:
    """Mark the audit trail failed, with a plain reason for the page and the technical text for the
    log."""
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
    except (NotStampedInTime, CaseFileRefused, httpx.HTTPError, OSError) as exc:
        fail(cases, case_id, exc)
        return
    cases.save(case)


def _ask_for_the_case_file(url: str, case_id: str, try_every_s: float, tries: int) -> bytes:
    """Ask until the file comes. A 202 (not stamped yet) and a call with no answer are asked again;
    any other answer ends the asking."""
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
            raise CaseFileRefused(
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
