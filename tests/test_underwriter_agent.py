"""The underwriting agent, run end to end against a fake model and a
network-free anchorer: every prompt, model call, tool call and the written
assessment are anchored, the assessment recommends approval on the designed
data, and no document content reaches the (fake) control plane."""

from __future__ import annotations

import json

from nacl.signing import SigningKey
from sanning_proof import public_key_hex

from credit_demo.underwriter.agent import assessment_turn
from tests.fakes import FAKE_SIGNING_SEED, ScriptedChatModel, fake_anchorer, underwriting_script
from tests.turns import CASE as TEST_CASE_ID
from tests.turns import context, run


def _run(workspace, model=None):
    anchorer, client = fake_anchorer(workspace)
    outcome = run(
        assessment_turn(workspace / "run", context()),
        anchorer=anchorer,
        model=model or ScriptedChatModel(responses=underwriting_script()),
    )
    return outcome, client


def test_run_completes_and_records_the_models_recommendation(repo_tmp_path):
    outcome, _client = _run(repo_tmp_path)

    assert outcome.status == "complete", outcome.error
    assert outcome.output is not None
    assert outcome.output["recommendation"] == "approve_with_conditions"


def test_the_figures_are_the_calculators_regardless_of_model_wording(repo_tmp_path):
    """The cover figures are the calculator's own numbers, never the model's:
    this must hold exactly, even though the model's prose was scripted
    independently of the arithmetic."""
    outcome, _client = _run(repo_tmp_path)

    assessment = outcome.output
    [calculation] = assessment["calculations"]
    assert calculation["cover"] == 1.128
    assert calculation["adjusted_cover"] == 1.2836
    assert not {"dscr", "minimum_dscr", "meets_minimum", "policy_check"} & set(assessment)


def test_every_step_is_anchored_and_signed(repo_tmp_path):
    outcome, client = _run(repo_tmp_path)

    event_types = [e.event_type for e in outcome.events]
    # Prompts and model calls.
    assert "langchain.chat_model_start" in event_types
    assert "langchain.llm_end" in event_types
    # Each of the six tool calls.
    assert event_types.count("langchain.tool_start") == 6
    assert event_types.count("langchain.tool_end") == 6
    # The four case documents read, the application file the calculator read,
    # and the written assessment: six artifacts.
    assert event_types.count("langchain.artifact") == 6
    # Nothing anchored is a gap, and every envelope reached the (fake) plane.
    assert all(not e.is_gap for e in outcome.events)
    assert len(client.submitted) == len(outcome.events)


def test_the_documents_read_are_the_real_files(repo_tmp_path):
    """The artifact events name the applicant's and policy's own files, not
    a copy or a description of them."""
    outcome, _client = _run(repo_tmp_path)

    artifact_events = [e for e in outcome.events if e.event_type == "langchain.artifact"]
    # Six: the four case documents, the application again under the calculator,
    # and the written assessment.
    assert len(artifact_events) == 6
    # Every artifact event carries a content hash: the commitment is to the
    # file's own bytes, computed on disk, never to a copy the agent invented.
    assert all(e.content_hash for e in artifact_events)


SECRETS_IN_THE_DOCUMENTS = [
    "Nordvik Haulage",  # the applicant's name, from the annual report
    "customer_receipts_sek",  # a bank statement column header
    "2795833",  # October 2025's customer receipts, from the CSV
    "1,600,000",  # the annual report's stated prior-year EBITDA
    "hire purchase financing",  # prose from the credit policy
]


def test_content_never_reaches_the_control_plane(repo_tmp_path):
    """Sanning receives hashes, never content: this fails if a single
    figure or word from the applicant's documents appears anywhere in what
    was submitted to the (fake) control plane, the ENVELOPE half of what the
    SDK is handed."""
    outcome, client = _run(repo_tmp_path)
    assert outcome.status == "complete"

    submitted_blob = json.dumps(client.submitted, default=str)

    for secret in SECRETS_IN_THE_DOCUMENTS:
        assert secret not in submitted_blob, f"{secret!r} reached the control plane"


def test_content_reaches_the_log_store_the_control_plane_never_sees(repo_tmp_path):
    """The other half of the same claim, and the half that makes the test
    above non-vacuous: the envelope schema is hash-only for ANY input, so a
    test that only greps envelopes would still pass if this repo's tools
    had handed the SDK nothing at all, or a placeholder, instead of the
    documents' real content. This asserts the opposite side directly: the
    RECORD bytes this run committed to its own log store (the customer's own
    store, not Sanning's) do carry the documents' real content, so a change
    here that stopped handing the SDK real content would turn this one red,
    and a change that leaked content into the envelope would turn the test
    above red."""
    outcome, _client = _run(repo_tmp_path)
    assert outcome.status == "complete"

    content_dir = repo_tmp_path / "logstore" / "content"
    assert content_dir.exists(), "the run's log store has no content directory"
    stored_blob = b"".join(p.read_bytes() for p in content_dir.glob("*"))

    found = [secret for secret in SECRETS_IN_THE_DOCUMENTS if secret.encode() in stored_blob]
    assert found, "none of the documents' own content reached the log store"


def test_missing_assessment_is_a_failed_run_not_a_silent_gap(repo_tmp_path):
    """If the model never calls `write_assessment`, the run fails loudly
    rather than reporting complete with nothing to show."""
    from langchain_core.messages import AIMessage

    anchorer, _client = fake_anchorer(repo_tmp_path)
    # A model that reads one document, then stops without ever writing an
    # assessment: the shape a real model could produce if it gave up early.
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "read_bank_statements", "args": {}, "id": "call-1"}],
            ),
            AIMessage(content="I could not complete the assessment."),
        ]
    )
    outcome = run(assessment_turn(repo_tmp_path / "run", context()), anchorer=anchorer, model=model)

    assert outcome.status == "failed"
    assert "assessment" in outcome.error


def test_every_envelope_is_signed_by_the_underwriters_own_key(repo_tmp_path):
    """"Signed by the underwriter's own key" means that key, not merely
    "signed by SOME key": this fails if the envelope's
    `public_key` is not the one this seed derives, so a mutant that swaps in
    an all-zero (or any other) key cannot pass silently."""
    outcome, client = _run(repo_tmp_path)
    assert outcome.status == "complete"
    assert client.submitted, "no envelope reached the (fake) control plane"

    expected_public_key = public_key_hex(SigningKey(FAKE_SIGNING_SEED))
    for envelope in client.submitted:
        assert envelope["public_key"] == expected_public_key


def test_a_gap_the_client_refuses_ends_the_run_failed_not_silently_complete(repo_tmp_path):
    """`raise_on_gaps=True` is what makes a step that could not be anchored
    stop the run rather than disappear into a clean-looking record: this
    fails if that flag is flipped (or defaulted away), because a client
    that always refuses to submit would otherwise still report `complete`."""

    class _RefusingClient:
        def submit(self, envelope):
            raise RuntimeError("the (fake) control plane refused this envelope")

    anchorer, _client = fake_anchorer(repo_tmp_path)
    anchorer._client = _RefusingClient()  # force every submission to fail
    model = ScriptedChatModel(responses=underwriting_script())

    outcome = run(assessment_turn(repo_tmp_path / "run", context()), anchorer=anchorer, model=model)

    assert outcome.status == "failed"
    assert outcome.error
    assert any(e.is_gap for e in outcome.events), "the gap must be visible in the turn's own events"


def test_the_recorder_is_configured_to_raise_on_a_gap(repo_tmp_path, monkeypatch):
    """Pins `sanning.recorder`'s own choice at the construction site itself:
    `AnchorCallbackHandler(..., raise_on_gaps=True, ...)`. Calling that
    handler through a live `agent.invoke()` cannot tell `True` apart from
    `False` (LangChain's callback manager always swallows a callback's own
    exception, since `AnchorCallbackHandler.raise_error` is a fixed
    `False`), which is why `test_a_gap_the_client_refuses_ends_the_run_failed_...`
    passes either way and cannot close this mutation by itself. A spy on
    the constructor, recording the keyword `recorder` actually passed, is
    what a mutant flipping that one word to `False` cannot survive."""
    from credit_demo.common import sanning

    captured = {}
    real_handler_cls = sanning.AnchorCallbackHandler

    class _SpyHandler(real_handler_cls):
        def __init__(self, *args, **kwargs):
            captured["raise_on_gaps"] = kwargs.get("raise_on_gaps")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(sanning, "AnchorCallbackHandler", _SpyHandler)

    outcome, _client = _run(repo_tmp_path)

    assert outcome.status == "complete", outcome.error
    assert captured["raise_on_gaps"] is True


def test_every_record_of_a_turn_carries_the_case_id(repo_tmp_path):
    """`bundle --case` selects a case's records by their signed
    `context.case_id`. A turn whose handler is not given the case id writes
    records with none, and on a real control plane every pack request for the
    case then fails: "records carry no context.case_id". Every record a turn
    anchors names the case it belongs to."""
    from sanning_anchor.logstore_read import FsLogStoreReader

    outcome, client = _run(repo_tmp_path)
    assert client.submitted, "no envelope reached the (fake) control plane"
    reader = FsLogStoreReader(str(repo_tmp_path / "logstore"))
    for envelope in client.submitted:
        record = json.loads(reader.record(envelope["payload_hash"]).bytes_)
        assert record.get("context", {}).get("case_id") == TEST_CASE_ID, record.get("event", {}).get("event_type")
