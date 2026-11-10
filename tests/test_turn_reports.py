"""What a turn says when it fails: how many records it lost, and why the
model stopped."""

from __future__ import annotations

from langchain_core.messages import AIMessage
from sanning_anchor import AnchorCallbackHandler

from credit_demo.common import documents
from credit_demo.common.turn import run_turn, why_it_stopped
from credit_demo.underwriter.agent import assessment_turn
from tests.fakes import ScriptedChatModel, fake_anchorer, underwriting_script
from tests.turns import CASE, context


class _LosesOneRecord:
    """A control plane that refuses exactly one submission."""

    def __init__(self, nth: int) -> None:
        self.nth = nth
        self.calls = 0

    def submit(self, envelope, index=None):
        self.calls += 1
        if self.calls == self.nth:
            raise RuntimeError("the (fake) control plane refused this envelope")


def test_one_lost_record_is_counted_once(repo_tmp_path):
    """One delivery failure is on the handler's `gaps` and on its
    `chain_gaps`, under one event id. The turn reports it as one step."""
    anchorer, _ = fake_anchorer(repo_tmp_path)
    anchorer._client = _LosesOneRecord(nth=3)
    # Not raising on a gap, so the turn runs to its end and the count is
    # what the turn reports.
    recorder = AnchorCallbackHandler(
        anchorer, case_id=CASE, raise_on_gaps=False, artifacts={"root": str(documents.REPO_ROOT)}
    )

    result = run_turn(
        assessment_turn(repo_tmp_path / "run", context()),
        model=ScriptedChatModel(responses=underwriting_script()),
        recorder=recorder,
    )

    assert len(recorder.gaps) == 1 and len(recorder.chain_gaps) == 1, "the premise: one loss, in both lists"
    assert result.status == "failed"
    assert result.error == "1 step(s) of this turn could not be recorded"


def _empty_reply(finish_reason: str) -> list[AIMessage]:
    return [AIMessage(content="", response_metadata={"finish_reason": finish_reason})]


def test_an_empty_reply_the_provider_failed_says_so_with_its_finish_reason():
    for reason in ("error", "other", "content_filter"):
        said = why_it_stopped(_empty_reply(reason))
        assert "model service returned no answer" in said
        assert reason in said
        assert "called no tool" not in said


def test_an_empty_normal_stop_is_still_a_reply_that_said_nothing():
    assert "called no tool and said nothing" in why_it_stopped(_empty_reply("stop"))
    assert "called no tool and said nothing" in why_it_stopped([AIMessage(content="")])
