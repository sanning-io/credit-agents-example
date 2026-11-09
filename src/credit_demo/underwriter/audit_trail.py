"""The audit trail: one zip per case, for an auditor.

It holds both agents' packs, each built by the SDK's `bundle` verb and
signed by that agent's own key, put together by the SDK's `build_case_file`.
The kernel reads the whole file as one verdict:
`sanning-proof case audit-trail-<id>.zip`.

A pack can be built only once Sanning has stamped the case's records, which
takes minutes on a real run, so the file is prepared in the background and
tried again while either pack is not stamped yet.
"""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import httpx
from sanning_anchor import CaseFilePack, ZipEntry, build_case_file, zip_archive

from ..common import cases as case_states
from ..common.cases import CaseStore
from ..common.config import ServiceSettings
from ..common.errors import describe, tell_the_operator
from ..common.messages import AUDIT_TRAIL_FAILED, AUDIT_TRAIL_NOT_READY
from ..common.packs import BUILT, NOT_STAMPED_YET, build_pack
from ..common.sanning import Services

# How long to keep trying while the records are not stamped yet.
TRY_EVERY_S = 20.0
TRIES = 45


class NotStampedInTime(Exception):
    """The records were still not stamped when the tries ran out."""


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


def _entries(zip_bytes: bytes) -> list[ZipEntry]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        return [ZipEntry(name, archive.read(name)) for name in archive.namelist() if not name.endswith("/")]


def prepare(
    *,
    settings: ServiceSettings,
    services: Services,
    cases: CaseStore,
    case_id: str,
    checker_url: str,
    try_every_s: float = TRY_EVERY_S,
    tries: int = TRIES,
) -> None:
    """Build the audit trail for a concluded case, and say how it went on
    the case: ready, or failed with the reason."""
    case = cases.get(case_id)
    case.audit_trail_state = case_states.PREPARING
    case.audit_trail_error = None
    case.audit_trail_detail = None
    cases.save(case)
    try:
        own, theirs = _both_packs(settings, services, cases, case, checker_url, try_every_s, tries)
        built = build_case_file(
            case_id=case_id,
            name=f"audit-trail-{case_id}",
            packs=[
                CaseFilePack(ref="underwriter", files=_entries(own)),
                CaseFilePack(ref="checker", files=_entries(theirs)),
            ],
        )
        audit_trail_path(cases, case_id).write_bytes(zip_archive(built.entries))
        case = cases.get(case_id)
        case.audit_trail_state = case_states.READY
    except Exception as exc:
        fail(cases, case_id, exc)
        return
    cases.save(case)


def _both_packs(settings, services, cases, case, checker_url, try_every_s, tries) -> tuple[bytes, bytes]:
    out = cases.folder(case.id) / f"pack-{settings.agent_id}.zip"
    own: bytes | None = None
    last = "the records were not stamped yet"
    for attempt in range(tries):
        if attempt:
            time.sleep(try_every_s)
        if own is None:
            ran = build_pack(services, settings, case_id=case.id, out=out)
            if ran.code == NOT_STAMPED_YET:
                last = " ".join(ran.errors) or last
                continue
            if ran.code not in BUILT:
                raise RuntimeError(f"this agent's pack could not be built: {' '.join(ran.errors)}")
            own = out.read_bytes()
        answer = httpx.post(f"{checker_url}/cases/{case.id}/pack", timeout=300)
        if answer.status_code == 202:
            last = answer.json().get("reason") or last
            continue
        if answer.status_code != 200:
            raise RuntimeError(f"the checker agent's pack could not be built: {answer.text}")
        return own, answer.content
    raise NotStampedInTime(f"gave up waiting for the records to be stamped: {last}")
