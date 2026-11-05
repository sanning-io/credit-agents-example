"""The checker agent's service, driven over HTTP the way the underwriting
agent drives it.

A turn is complete only on a file it wrote itself. A case carries one
assessment and one conclusion: a message this agent will not act on is
refused, and the record it signs says "rejected", decided before anything
reads the message. A stranger is refused with no record at all. A finished
case stays finished across a restart.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from credit_demo.checker.agent import review_turn
from credit_demo.checker.config import settings_from_env
from credit_demo.checker.tools import REVIEW_FILENAME
from credit_demo.checker.web import create_app
from credit_demo.common.sanning import CONTENT_TYPES, open_handoff, wire
from tests.fakes import ScriptedChatModel, fake_checker_anchorer, nonsense_script, review_script
from tests.desks import sample_desk
from tests.rig import CHECKER_SEED, UNDERWRITER_SEED, Plane, services_for
from tests.test_bar_a_case_end_to_end import _env
from tests.turns import context, run

CASE = "20260101T000000000000Z-0001"
ASSESSMENT = {"dscr": 1.3257, "minimum_dscr": 1.25, "recommendation": "approve"}


def _checker(root, plane, **services_kwargs):
    settings = settings_from_env(
        service="checker",
        prefix="CHECKER",
        default_agent_id="checker",
        default_port=8002,
        env=_env("CHECKER", CHECKER_SEED, UNDERWRITER_SEED, root / "checker-cases"),
    )
    return TestClient(
        create_app(settings, services=services_for("checker", plane, root, **services_kwargs), desk_url=sample_desk())
    )


def _underwriter(root, plane):
    services = services_for("underwriter", plane, root)
    return open_handoff(services.anchorer, CASE), services.anchorer


def _send(client, handoff, message, step, **kwargs):
    sent = handoff.send(
        message=message,
        to=_checker_key(),
        content_type=CONTENT_TYPES[step],
        **kwargs,
    )
    return sent, client.post(f"/cases/{CASE}/handoffs", json=json.loads(json.dumps(wire(sent))))


def _checker_key():
    from tests.rig import public_key_for

    return public_key_for(CHECKER_SEED)


def _received_outcomes(root):
    """The outcome of every signed `handoff.received` in the checker
    agent's own log store, read off the records themselves."""
    received = []
    for path in sorted((root / "checker-logs").rglob("*")):
        if not path.is_file():
            continue
        try:
            record = json.loads(path.read_bytes())
        except (ValueError, RecursionError):
            # Content objects too, the stored copy of an attack included.
            continue
        if isinstance(record, dict) and record.get("event_type") == "agent.handoff.received":
            received.append(record["event"])
    # In the order the hand-offs arrived, not the order the files list in.
    return [event["outcome"] for event in sorted(received, key=lambda event: event["sequence"])]


def _assessment():
    return {"kind": "assessment", "assessment": ASSESSMENT, "underwriter_summary": "s"}


def test_a_turn_does_not_finish_on_the_file_the_turn_before_it_wrote(repo_tmp_path):
    """The first review writes `review.json`. The second runs a model that
    calls nothing, and must fail rather than pass on the first one's file."""
    anchorer, _plane = fake_checker_anchorer(repo_tmp_path)
    workspace = repo_tmp_path / "one-case"
    first = run(
        review_turn(workspace, context(), assessment=ASSESSMENT),
        anchorer=anchorer,
        model=ScriptedChatModel(responses=review_script()),
    )
    assert first.status == "complete", first.error
    second = run(
        review_turn(workspace, context(), assessment=ASSESSMENT),
        anchorer=anchorer,
        model=ScriptedChatModel(responses=nonsense_script()),
    )
    assert second.status == "failed"
    assert "without writing a review" in (second.error or "")
    assert not (workspace / REVIEW_FILENAME).exists()


def test_an_assessment_is_recorded_reviewed_and_answered_with_a_challenge(repo_tmp_path):
    plane = Plane()
    client = _checker(repo_tmp_path, plane)
    handoff, _ = _underwriter(repo_tmp_path, plane)

    _sent, answer = _send(client, handoff, _assessment(), "assessment")

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["received_record"]["event_record"]["event"]["outcome"] == "accepted"
    assert json.loads(body["reply"]["message"])["kind"] == "challenge"
    assert _received_outcomes(repo_tmp_path) == ["accepted"]


def test_a_second_assessment_on_a_reviewed_case_is_signed_rejected(repo_tmp_path):
    plane = Plane()
    client = _checker(repo_tmp_path, plane)
    handoff, _ = _underwriter(repo_tmp_path, plane)
    _send(client, handoff, _assessment(), "assessment")

    _sent, answer = _send(client, handoff, {**_assessment(), "underwriter_summary": "again"}, "assessment")

    assert answer.status_code == 409
    assert answer.json()["recorded"] is True
    assert "waiting for the revision" in answer.json()["refused"]
    assert _received_outcomes(repo_tmp_path) == ["accepted", "rejected"]


def test_a_message_of_no_known_step_is_signed_rejected(repo_tmp_path):
    plane = Plane()
    client = _checker(repo_tmp_path, plane)
    handoff, _ = _underwriter(repo_tmp_path, plane)
    sent = handoff.send(message={"kind": "gossip"}, to=_checker_key(), content_type="application/json")

    answer = client.post(f"/cases/{CASE}/handoffs", json=json.loads(json.dumps(wire(sent))))

    assert answer.status_code == 409
    assert _received_outcomes(repo_tmp_path) == ["rejected"]


def test_a_stranger_is_refused_and_nothing_is_recorded(repo_tmp_path):
    plane = Plane()
    client = _checker(repo_tmp_path, plane)
    stranger = services_for("checker", plane, repo_tmp_path / "stranger").anchorer  # any key but the underwriter's
    sent = open_handoff(stranger, CASE).send(
        message=_assessment(), to=_checker_key(), content_type=CONTENT_TYPES["assessment"]
    )
    before = len(plane.submitted)

    answer = client.post(f"/cases/{CASE}/handoffs", json=json.loads(json.dumps(wire(sent))))

    assert answer.status_code == 403
    assert answer.json()["recorded"] is False
    assert len(plane.submitted) == before
    assert _received_outcomes(repo_tmp_path) == []


def test_a_message_altered_on_the_way_is_refused_and_recorded_as_refused(repo_tmp_path):
    plane = Plane()
    client = _checker(repo_tmp_path, plane)
    handoff, _ = _underwriter(repo_tmp_path, plane)
    sent = handoff.send(message=_assessment(), to=_checker_key(), content_type=CONTENT_TYPES["assessment"])
    body = json.loads(json.dumps(wire(sent)))
    message = json.loads(body["message"])
    message["assessment"]["dscr"] = 1.9
    body["message"] = json.dumps(message)

    answer = client.post(f"/cases/{CASE}/handoffs", json=body)

    assert answer.status_code == 409
    assert answer.json()["recorded"] is True
    assert _received_outcomes(repo_tmp_path) == ["rejected"]


def test_a_deeply_nested_tamper_is_still_refused_with_a_record(repo_tmp_path):
    """An attack on the parser: a message nested deep enough to crash a
    JSON parser. This agent never parses the message
    before `receive`, so the SDK refuses it and records the refusal."""
    plane = Plane()
    client = _checker(repo_tmp_path, plane)
    handoff, _ = _underwriter(repo_tmp_path, plane)
    sent = handoff.send(message=_assessment(), to=_checker_key(), content_type=CONTENT_TYPES["assessment"])
    body = json.loads(json.dumps(wire(sent)))
    body["message"] = "[" * 200000 + "]" * 200000

    answer = client.post(f"/cases/{CASE}/handoffs", json=body)

    assert answer.status_code == 409
    assert _received_outcomes(repo_tmp_path) == ["rejected"]


def test_a_finished_case_is_not_worked_again_after_a_restart(repo_tmp_path):
    plane = Plane()
    client = _checker(repo_tmp_path, plane)
    handoff, _ = _underwriter(repo_tmp_path, plane)
    case = client.app.state.cases.create(CASE)
    case.status = "concluded"
    client.app.state.cases.save(case)
    restarted = _checker(repo_tmp_path, plane)

    _sent, answer = _send(restarted, handoff, _assessment(), "assessment")

    assert answer.status_code == 409
    assert "has ended" in answer.json()["refused"]
    assert _received_outcomes(repo_tmp_path) == ["rejected"]


def test_a_review_that_fails_stops_the_case_and_says_so(repo_tmp_path):
    plane = Plane()
    client = _checker(repo_tmp_path, plane, model=ScriptedChatModel(responses=nonsense_script()))
    handoff, _ = _underwriter(repo_tmp_path, plane)

    _sent, answer = _send(client, handoff, _assessment(), "assessment")

    assert answer.status_code == 200
    assert "could not finish its review" in answer.json()["error"]
    assert "reply" not in answer.json()
    assert client.app.state.cases.get(CASE).status == "stopped"


def test_a_challenge_it_could_not_record_is_said_as_that(repo_tmp_path, monkeypatch):
    """The assessment crossed and was reviewed; what failed is this side's
    record of its answer. That is not a refusal and not a message that
    never arrived."""
    from sanning_anchor.handoff import Handoff

    def broken_send(self, **kwargs):
        raise RuntimeError("the (fake) store refused the write")

    plane = Plane()
    client = _checker(repo_tmp_path, plane)
    handoff, _ = _underwriter(repo_tmp_path, plane)
    sent = handoff.send(message=_assessment(), to=_checker_key(), content_type=CONTENT_TYPES["assessment"])
    monkeypatch.setattr(Handoff, "send", broken_send)

    answer = client.post(f"/cases/{CASE}/handoffs", json=json.loads(json.dumps(wire(sent))))

    assert answer.status_code == 200
    assert "could not record its challenge" in answer.json()["error"]
