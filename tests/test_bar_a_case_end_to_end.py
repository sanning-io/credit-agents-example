"""What a customer does with this example, from outside: start both agents and the evidence builder
as three services, run the sample case over HTTP, export its audit trail, verify it with the open
kernel, change one byte in a signed record and verify again. The model, Sanning and its records
index are stand-ins (`tests.rig`). Everything else is the example's own code, the published SDK
and the kernel."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from credit_demo.checker.config import settings_from_env as checker_settings
from credit_demo.checker.web import create_app as create_checker_app
from credit_demo.evidence_builder.web import create_app as create_evidence_builder_app
from credit_demo.underwriter.config import settings_from_env as underwriter_settings
from credit_demo.underwriter.web import create_app as create_underwriter_app
from tests.rig import (
    CHECKER_SEED,
    ORGANIZATION_SEED,
    UNDERWRITER_SEED,
    Plane,
    case_file_bundle,
    evidence_builder_app,
    evidence_builder_settings,
    public_key_for,
    services_for,
)
from tests.servers import listening, serving, url_of

STEPS = ["assessment", "challenge", "revision", "conclusion"]


def _env(seed_hex: str, counterparty_seed_hex: str, cases_dir: Path) -> dict:
    """Every setting a real case needs. The stand-ins replace every seam
    that would use these values, so none of them reaches a network."""
    return {
        "SANNING_API_KEY": "sanning_test_key",
        "SANNING_SIGNING_SEED": seed_hex,
        "OPENAI_API_KEY": "test-model-key",
        "SANNING_LOG_BUCKET": "test-bucket",
        "AWS_ENDPOINT_URL_S3": "http://localhost:9000",
        "AWS_ACCESS_KEY_ID": "test-access",
        "AWS_SECRET_ACCESS_KEY": "test-secret",
        "COUNTERPARTY_PUBLIC_KEY": public_key_for(counterparty_seed_hex),
        "RUNS_DIR": str(cases_dir),
    }


def _wait(check, *, timeout: float = 180.0):
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the case")
        time.sleep(0.1)


@pytest.fixture(scope="module")
def a_plane():
    """Sanning's stand-in, with no hand-over key enrolled yet."""
    return Plane()


@pytest.fixture(scope="module")
def a_case(repo_tmp_module, a_plane):
    """One sample case, run once for every test in this file: the case as the
    credit desk holds it, and the audit trail it exported. The checker agent
    asks the desk itself for the case documents, so the desk's address is
    fixed before either is served."""
    root = repo_tmp_module
    plane = a_plane
    desk_socket = listening()
    checker = create_checker_app(
        checker_settings(
            service="checker",
            default_agent_id="checker",
            default_port=8002,
            env=_env(CHECKER_SEED, UNDERWRITER_SEED, root / "checker-cases"),
        ),
        services=services_for("checker", plane, root),
        desk_url=url_of(desk_socket),
    )
    with serving(checker) as checker_url, serving(evidence_builder_app(plane, root)) as builder_url:
        underwriter = create_underwriter_app(
            underwriter_settings(
                service="underwriter",
                default_agent_id="underwriter",
                default_port=8001,
                env=_env(UNDERWRITER_SEED, CHECKER_SEED, root / "underwriter-cases"),
            ),
            services=services_for("underwriter", plane, root),
            checker_url=checker_url,
            evidence_builder_url=builder_url,
        )
        with serving(underwriter, desk_socket) as url:
            started = httpx.post(f"{url}/cases", follow_redirects=False, timeout=30)
            assert started.status_code == 303, started.text
            case_id = started.headers["location"].rsplit("/", 1)[-1]
            cases = underwriter.state.cases
            _wait(lambda: cases.get(case_id).audit_trail_state in ("ready", "failed"))
            case = cases.get(case_id)
            exported = httpx.get(f"{url}/cases/{case_id}/audit-trail", timeout=60)
            pages = {
                "applications": httpx.get(f"{url}/", timeout=30).text,
                "case": httpx.get(f"{url}/cases/{case_id}", timeout=30).text,
            }
    return case, exported, root, pages


def _kernel_case(path: Path) -> tuple[int, dict]:
    ran = subprocess.run(
        [str(Path(sys.executable).parent / "sanning-proof"), "case", str(path), "--json"],
        capture_output=True,
        text=True,
    )
    return ran.returncode, json.loads(ran.stdout)


def test_a_case_runs_to_its_conclusion_over_http(a_case):
    case, _exported, _root, _pages = a_case

    assert case.status == "concluded", case.error
    assert [h["step"] for h in case.handoffs] == STEPS
    assert all(h["recorded_by_both"] for h in case.handoffs), case.handoffs
    assert case.conclusion["verdict"] == "approve_with_conditions"
    assert case.audit_trail_state == "ready", case.audit_trail_error


def test_the_case_was_opened_on_the_application_and_worked_from_it(a_case):
    """The desk noted the application and the four case documents when it
    opened the case, and the underwriting agent read each of them there,
    the application included."""
    case, _exported, root, _pages = a_case
    assert case.application_number == "APP-2026-0412"
    noted = {d["document"]: d["location"] for d in case.case_documents}
    assert set(noted) == {"application", "bank_statements", "annual_report", "credit_policy"}
    read = set()
    for path in (root / "underwriter-logs").rglob("*"):
        try:
            record = json.loads(path.read_bytes())
        except (ValueError, IsADirectoryError, UnicodeDecodeError):
            continue
        if isinstance(record, dict) and record.get("event_type") == "agent.file":
            artifact = record["metadata"]["artifact"]
            if artifact.get("role") == "input":
                read.add(artifact["path"])
    assert read == set(noted.values())


def test_the_checker_agent_worked_from_the_desks_record_and_its_pack_holds_the_application(a_case):
    """The checker agent read the four case documents where the desk's
    record of the case locates them, the application included, and its own
    pack in the exported audit trail carries the file record for the
    application it read."""
    case, exported, root, _pages = a_case
    noted = {d["document"]: d["location"] for d in case.case_documents}
    read = set()
    for path in (root / "checker-logs").rglob("*"):
        try:
            record = json.loads(path.read_bytes())
        except (ValueError, IsADirectoryError, UnicodeDecodeError):
            continue
        if isinstance(record, dict) and record.get("event_type") == "agent.file":
            artifact = record["metadata"]["artifact"]
            if artifact.get("role") == "input":
                read.add(artifact["path"])
    assert read == set(noted.values())

    # The checker agent's pack carries each signed record's bytes.
    trail = zipfile.ZipFile(io.BytesIO(exported.content))
    pack = json.loads(trail.read("packs/checker/bundle.json"))
    in_pack = []
    for event in pack["body"]["events"]:
        record = json.loads(bytes.fromhex(event["event_bytes"]))
        if record.get("event_type") == "agent.file":
            in_pack.append(record["metadata"]["artifact"])
    applications = [a for a in in_pack if a["path"] == noted["application"]]
    # In each of the review turn and the conclusion turn: one read, and one
    # calculation on the financing it requests.
    assert len(applications) == 4, in_pack
    assert all(a["role"] == "input" for a in applications)


def test_its_audit_trail_reads_verified_with_the_kernel(a_case):
    _case, exported, root, _pages = a_case
    assert exported.status_code == 200, exported.text
    path = root / "audit-trail.zip"
    path.write_bytes(exported.content)

    code, reading = _kernel_case(path)

    # Exit 3 is verified in the sandbox: the rig signs `dev` records.
    assert code == 3, reading
    assert reading["verdict"] == {"status": "verified"}
    assert reading["environment_marker"] == "dev"
    assert len(reading["packs"]) == 2
    assert reading["handoff_records"] == 2 * len(STEPS)
    assert reading["exceptions"] == []
    names = zipfile.ZipFile(io.BytesIO(exported.content)).namelist()
    # Only the SDK's own case file: the packs, the index and the how-to.
    assert not any(name.startswith("report/") for name in names), names


def test_the_hand_over_key_seals_both_packs_and_each_record_stays_its_agents(a_case):
    """The evidence builder holds neither agent's seed: the organization's
    key seals each pack and the pack names it as issuer, while every record in
    the pack is still signed by the agent that made it."""
    _case, exported, _root, _pages = a_case
    trail = zipfile.ZipFile(io.BytesIO(exported.content))
    agents = {"underwriter": UNDERWRITER_SEED, "checker": CHECKER_SEED}
    for ref, seed in agents.items():
        pack = json.loads(trail.read(f"packs/{ref}/bundle.json"))
        assert pack["issuer"] == {"kind": "organization"}, ref
        assert pack["public_key"] == public_key_for(ORGANIZATION_SEED), ref
        assert {event["envelope"]["public_key"] for event in pack["body"]["events"]} == {public_key_for(seed)}, ref


def test_the_first_case_file_enrolls_the_hand_over_key(a_case, a_plane):
    """No key was active, so the evidence builder's first case file enrolled
    the key, before any declaration went out, and asked once."""
    assert a_plane.handover_key == public_key_for(ORGANIZATION_SEED)
    assert a_plane.handover_routes == [
        "GET /api/v1/handover-key",
        "POST /api/v1/handover-key/challenge",
        "POST /api/v1/handover-key/enroll",
    ]


def test_a_different_active_hand_over_key_stops_the_case_file_with_a_clear_reason(a_case, a_plane):
    """The organization already has another hand-over key: the evidence
    builder builds nothing, and says why."""
    case, _exported, root, _pages = a_case
    other = Plane(
        submitted=list(a_plane.submitted),
        owner=dict(a_plane.owner),
        received_at=dict(a_plane.received_at),
        case_id=dict(a_plane.case_id),
        handover_key=public_key_for(UNDERWRITER_SEED),
    )
    app = create_evidence_builder_app(evidence_builder_settings(root), run_bundle=case_file_bundle(other, root))
    answer = TestClient(app).post(f"/cases/{case.id}/case-file")

    assert answer.status_code == 422, answer.text
    assert "already has a different hand-over key" in answer.json()["reason"], answer.json()
    assert "No declaration was sent" in answer.json()["reason"], answer.json()
    assert other.handover_key == public_key_for(UNDERWRITER_SEED)
    assert other.handover_routes == ["GET /api/v1/handover-key"]


def test_one_changed_byte_in_a_signed_record_reads_failed(a_case):
    _case, exported, root, _pages = a_case
    source = zipfile.ZipFile(io.BytesIO(exported.content))
    target = root / "audit-trail-changed.zip"
    changed = False
    with zipfile.ZipFile(target, "w") as out:
        for name in source.namelist():
            data = source.read(name)
            if not changed and name.endswith("packs/underwriter/bundle.json"):
                # The first letter or digit from the middle on, so the change
                # lands inside a signed value and the file stays JSON: a flip
                # that broke the JSON would test the parser, not the record.
                middle = len(data) // 2
                while not chr(data[middle]).isalnum():
                    middle += 1
                data = data[:middle] + bytes([data[middle] ^ 0x01]) + data[middle + 1 :]
                changed = True
            out.writestr(name, data)
    assert changed

    code, reading = _kernel_case(target)

    assert code == 1, reading
    assert reading["verdict"]["status"] == "failed"
    # The reason is that the underwriting agent's records do not verify, not
    # that the file stopped parsing.
    failed = [e for e in reading["exceptions"] if e["kind"] == "pack_does_not_verify"]
    assert [e["pack_ref"] for e in failed] == ["audit-trail-changed/packs/underwriter"], reading["exceptions"]
    assert "does not verify" in failed[0]["rendered"]


def test_the_credit_desk_shows_the_finished_case(a_case):
    case, _exported, _root, pages = a_case

    assert "Approve with conditions" in pages["applications"]
    assert "Waiting for sign-off · audit trail ready" in pages["applications"]
    page = pages["case"]
    assert "Approve with conditions" in page
    assert "Agents&#39; recommendation" in page
    assert page.count("✓ Recorded by both agents") == len(STEPS)
    for title in ("Assessment", "Challenge", "Revision", "Conclusion", "Sent for sign-off"):
        assert f"· {title}</span>" in page, title
    # Each card shows the agent's own one-sentence summary of its turn.
    for summary in (
        "Adjusted for a downturn that has ended, cover is 1.28x with a 15% deposit; I recommend approval with conditions.",
        "Unadjusted cover is 1.13x after a 15% deposit; I do not accept the adjustment.",
        "Unadjusted cover is 1.13x after a 15% deposit, below the minimum, so I now refer the case.",
        "Cover is 1.13x after a 15% deposit; I conclude under the policy.",
    ):
        assert summary in page, summary
    assert f'href="/cases/{case.id}/audit-trail"' in page


def test_the_underwriting_agent_closes_its_record_as_completed_and_the_desk_notes_when(a_case):
    import json

    case, _exported, root, _pages = a_case
    assert case.sent_for_signoff_at, "no time for the sign-off step"
    stated = []
    for path in (root / "underwriter-logs").rglob("*"):
        try:
            record = json.loads(path.read_bytes())
        except (ValueError, IsADirectoryError):
            continue
        if isinstance(record, dict) and record.get("event_type") == "agent.run_closed":
            stated.append(record["metadata"]["closing"]["outcome"]["stated"])
    assert stated and all(s == {"status": "completed"} for s in stated), stated


def test_every_received_reply_names_the_message_it_answers(a_case):
    """Each side's own record of a reply says which message it answers, and
    it agrees with the sender's record. The first message, the assessment,
    answers nothing."""
    import json

    _case, _exported, root, _pages = a_case
    records = []
    for side in ("underwriter", "checker"):
        for path in (root / f"{side}-logs").rglob("*"):
            try:
                record = json.loads(path.read_bytes())
            except (ValueError, RecursionError, IsADirectoryError, UnicodeDecodeError):
                continue
            if isinstance(record, dict) and str(record.get("event_type", "")).startswith("agent.handoff."):
                records.append(record)
    sent = {r["event"]["message_hash"]: r for r in records if r["event_type"] == "agent.handoff.sent"}
    received = [r for r in records if r["event_type"] == "agent.handoff.received"]
    assert len(received) == len(STEPS)
    for record in received:
        answers = sent[record["event"]["message_hash"]]["context"].get("in_reply_to")
        assert record["context"].get("in_reply_to") == answers, record["event"]["content_type"]
    assert sum(1 for r in received if r["context"].get("in_reply_to")) == len(STEPS) - 1
