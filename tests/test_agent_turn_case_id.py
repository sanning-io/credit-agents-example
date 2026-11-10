"""Every record an agent turn anchors must carry `context.case_id`, and it
must be the case's own id.

The `bundle` verb the pack builder calls groups records into a case by that
field and refuses a record without it. A turn whose `AnchorCallbackHandler`
has no `case_id` would complete, and then its pack request would fail on
both sides: on the underwriter's assessment and revision turns and on the
checker's review and conclusion turns.

The test drives one real turn through the real `AnchorCallbackHandler` and
a real `Anchorer`, over a capturing (fake) control plane and a real
`FsLogStore`, and reads each record back off the store by its own event id,
never off what this test process passed in: what a record SAYS is read from
the record.
"""

from __future__ import annotations

import json

from sanning_anchor import FsLogStore

from credit_demo.checker.agent import review_turn
from credit_demo.underwriter.agent import assessment_turn
from tests.turns import context, run
from tests.fakes import (
    checker_script,
    fake_anchorer,
    fake_checker_anchorer,
    underwriting_script,
    ScriptedChatModel,
)

CASE_ID = "20260101T000000000000Z-c4b3"

ASSESSMENT = {
    "applicant": "Nordvik Haulage AB",
    "dscr": 1.3257,
    "minimum_dscr": 1.25,
    "meets_minimum": True,
    "recommendation": "approve",
}


def _stored_record(store_dir, event_id):
    raw = FsLogStore(str(store_dir)).get_record(event_id)
    assert raw is not None, f"no record in the log store for {event_id!r}"
    return json.loads(raw)


def _case_id_of(store_dir, event_id):
    record = _stored_record(store_dir, event_id)
    context = record.get("context")
    return context.get("case_id") if isinstance(context, dict) else None


def test_every_record_of_an_underwriting_turn_carries_the_run_id_as_case_id(repo_tmp_path):
    """An assessment turn, run through the real handler, must anchor every
    one of its records under the case's own id, never `None`."""
    anchorer, _client = fake_anchorer(repo_tmp_path)
    model = ScriptedChatModel(responses=underwriting_script())

    outcome = run(assessment_turn(repo_tmp_path / "run", context()), anchorer=anchorer, model=model, case_id=CASE_ID)

    assert outcome.status == "complete", outcome.error
    assert outcome.events, "the turn anchored nothing, so this test proves nothing"

    store_dir = repo_tmp_path / "logstore"
    checked = 0
    for event in outcome.events:
        event_id = event.event_id
        if not event_id:
            # A step that never anchored has nothing to read back; the
            # assertion above already requires the turn to have completed.
            continue
        found = _case_id_of(store_dir, event_id)
        assert found == CASE_ID, (
            f"record {event_id} carries context.case_id={found!r}, "
            f"not this run's id {CASE_ID!r}"
        )
        checked += 1
    assert checked == len(outcome.events), "not every anchored step was read back"


def test_the_checkers_turn_on_the_same_run_carries_the_same_case_id(repo_tmp_path):
    """The checker never mints its own case id: it takes part in the
    underwriter's case, under the id the hand-off carried, so its own
    records must agree with the underwriter's."""
    anchorer, _plane = fake_checker_anchorer(repo_tmp_path)
    model = ScriptedChatModel(responses=checker_script())

    outcome = run(review_turn(repo_tmp_path / "case", context(), assessment=ASSESSMENT), anchorer=anchorer, model=model, case_id=CASE_ID)

    assert outcome.status == "complete", outcome.error
    assert outcome.events

    store_dir = repo_tmp_path / "checker-logstore"
    for event in outcome.events:
        event_id = event.event_id
        if not event_id:
            continue
        found = _case_id_of(store_dir, event_id)
        assert found == CASE_ID, (
            f"checker record {event_id} carries context.case_id={found!r}, "
            f"not the case id {CASE_ID!r} the underwriter minted"
        )


def test_the_closing_record_also_carries_the_run_id_as_case_id(repo_tmp_path):
    """The closing record is written when the recorder closes, outside the
    turn itself, so it is checked on its own."""
    anchorer, _client = fake_anchorer(repo_tmp_path)
    outcome = run(
        assessment_turn(repo_tmp_path / "run", context()),
        anchorer=anchorer,
        model=ScriptedChatModel(responses=underwriting_script()),
        case_id=CASE_ID,
    )

    closing = [e for e in outcome.events if e.event_type == "agent.run_closed"]
    assert closing, "the recorder wrote no closing record"
    for event in closing:
        assert _case_id_of(repo_tmp_path / "logstore", event.event_id) == CASE_ID
