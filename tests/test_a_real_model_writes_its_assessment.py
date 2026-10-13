"""The underwriting turn, driven through the model this example really builds
(`build_model`, `langchain-openai`'s `ChatOpenAI`), answered by a fake
provider speaking the OpenAI-compatible chat completions API over HTTP.

Every other agent test drives a scripted LangChain model that hands the agent
loop finished `AIMessage` objects. That skips the part a real model goes
through: the request `ChatOpenAI` sends, the output budget it carries, and how
`langchain-openai` parses the provider's reply back into tool calls. A real
model can end a turn with "the agent finished without writing an assessment"
where scripted mode passes, and this file is where that difference is under
test.

A reply to `write_assessment` cut off at the output limit leaves a trail of
its own
(`test_a_cut_off_reply_leaves_every_step_before_the_cut_on_the_trail`):
three model calls, the four case documents read in parallel and the cover
calculated, each with its file, and an empty final message. The provider
returns the call with its arguments truncated mid-string and
`finish_reason: "length"`, `langchain-openai` files a call whose arguments are
not valid JSON under `invalid_tool_calls`, and the agent loop, seeing no tool
to run, ends the turn.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from credit_demo.common.config import settings_from_env
from credit_demo.common.sanning import MAX_OUTPUT_TOKENS, build_model
from credit_demo.underwriter.agent import assessment_turn
from tests.turns import context, run
from tests.fakes import fake_anchorer

# About four characters to a token, the usual rule of thumb for English prose.
CHARS_PER_TOKEN = 4

# The rationale a real model writes into `write_assessment`, in tokens. The
# system prompt asks it to state the figures, the parts of the policy it
# applied and why it decided as it did, after reading the annual report and
# the credit policy in full, and a model that reasons before answering spends
# part of the same budget on that.
# This is an estimate of a thorough reply, not a measurement: the records
# hold hashes, never the model's words. A reply of this size does not fit a
# budget of 1,024 tokens.
REALISTIC_RATIONALE_TOKENS = 1500


def _settings(tmp_path):
    prefix = "UNDERWRITER"
    return settings_from_env(
        service="underwriter",
        prefix=prefix,
        default_agent_id="underwriter",
        default_port=8001,
        env={
            f"{prefix}_SANNING_API_KEY": "test-key",
            f"{prefix}_SANNING_SIGNING_SEED": "ab" * 32,
            f"{prefix}_OPENROUTER_API_KEY": "test-openrouter-key",
            f"{prefix}_BUCKET": "test-bucket",
            f"{prefix}_ENDPOINT": "http://localhost:9000",
            f"{prefix}_ACCESS_KEY_ID": "test-access",
            f"{prefix}_SECRET_ACCESS_KEY": "test-secret",
            f"{prefix}_RUNS_DIR": str(tmp_path / "runs"),
        },
    )


def _call(call_id: str, name: str, arguments: str) -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def _completion(message: dict[str, Any], finish_reason: str) -> dict[str, Any]:
    return {
        "id": "gen-fake",
        "object": "chat.completion",
        "created": 0,
        "model": "anthropic/claude-sonnet-5",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class FakeProvider:
    """An OpenAI-compatible chat completions endpoint that underwrites the way
    a tool-calling model does: the four case documents read in parallel, then the
    ratio, then `write_assessment` with a rationale of `rationale_tokens`,
    then one short paragraph.

    Like a real provider it is stateless, deciding what to reply from the
    conversation it is sent, and it honours the output budget the request
    carries: a reply longer than the budget comes back cut off at it, with its
    arguments truncated and `finish_reason: "length"`.

    `last_reply` replaces the reply to the `write_assessment` step, for the
    cases where the model does something other than call it.
    """

    def __init__(
        self,
        *,
        rationale_tokens: int = REALISTIC_RATIONALE_TOKENS,
        last_reply: dict[str, Any] | None = None,
    ) -> None:
        self.rationale_tokens = rationale_tokens
        self.last_reply = last_reply
        self.requests: list[dict[str, Any]] = []

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        replies_so_far = sum(1 for m in body["messages"] if m["role"] == "assistant")
        budget = body.get("max_completion_tokens") or body.get("max_tokens")
        return httpx.Response(200, json=self._reply(replies_so_far, budget))

    def _reply(self, step: int, budget: int | None) -> dict[str, Any]:
        if step == 0:
            return _completion(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _call("call_0", "read_application", "{}"),
                        _call("call_1", "read_bank_statements", "{}"),
                        _call("call_2", "read_annual_report", "{}"),
                        _call("call_3", "read_credit_policy", "{}"),
                    ],
                },
                "tool_calls",
            )
        if step == 1:
            return _completion(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _call(
                            "call_4",
                            "calculate_cover",
                            json.dumps(
                                {
                                    "earnings_by_year": [
                                        {"year": "Oct 2025 to Sep 2026", "ebitda_sek": 2750000},
                                        {"year": "Oct 2024 to Sep 2025", "ebitda_sek": 1600000},
                                    ],
                                    "existing_annual_debt_service_sek": 1100000,
                                    "deposit_percent": 15,
                                }
                            ),
                        )
                    ],
                },
                "tool_calls",
            )
        if step == 2:
            if self.last_reply is not None:
                return self.last_reply
            words = "The ratio clears the policy minimum on the twelve months. "
            rationale = (words * (self.rationale_tokens * CHARS_PER_TOKEN // len(words) + 1))[
                : self.rationale_tokens * CHARS_PER_TOKEN
            ]
            arguments = json.dumps(
                {
                    "recommendation": "approve",
                    "deposit_percent": 15,
                    "rationale": rationale,
                    "summary": "Cover over 24 months is 1.13x with a 15% deposit.",
                }
            )
            if budget is not None and len(arguments) > budget * CHARS_PER_TOKEN:
                return _completion(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            _call(
                                "call_5",
                                "write_assessment",
                                arguments[: budget * CHARS_PER_TOKEN],
                            )
                        ],
                    },
                    "length",
                )
            return _completion(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [_call("call_5", "write_assessment", arguments)],
                },
                "tool_calls",
            )
        return _completion(
            {
                "role": "assistant",
                "content": "Read the four case documents, computed the ratio and wrote the assessment.",
            },
            "stop",
        )


def _run(repo_tmp_path, provider: FakeProvider):
    model = build_model(_settings(repo_tmp_path), http_client=provider.client())
    anchorer, _client = fake_anchorer(repo_tmp_path)
    return run(assessment_turn(repo_tmp_path / "run", context()), anchorer=anchorer, model=model)


def test_a_real_models_assessment_fits_the_output_budget(repo_tmp_path):
    """A thorough reply calling `write_assessment` fits the budget, and the
    turn completes. Under a budget of 1,024 tokens the same reply comes back
    cut off, and the turn fails."""
    provider = FakeProvider()
    outcome = _run(repo_tmp_path, provider)

    assert outcome.status == "complete", outcome.error
    assert outcome.output is not None
    assert outcome.output["recommendation"] == "approve"
    assert len(outcome.output["rationale"]) == REALISTIC_RATIONALE_TOKENS * CHARS_PER_TOKEN
    # The budget went out on the wire, and it is the one this example names.
    sent = provider.requests[0].get("max_completion_tokens") or provider.requests[0].get(
        "max_tokens"
    )
    assert sent == MAX_OUTPUT_TOKENS


def test_the_output_budget_leaves_room_well_beyond_a_thorough_rationale():
    """The budget is a ceiling, billed only for what a reply uses, so it
    carries headroom over the rationale above rather than sitting on it."""
    assert MAX_OUTPUT_TOKENS >= 4 * REALISTIC_RATIONALE_TOKENS


def test_the_output_budget_is_sixteen_thousand_tokens():
    """8,192 is too small: it cuts off a checker agent's review once it has a
    real adjustment to weigh and writes its reasoning as the tool's
    argument."""
    assert MAX_OUTPUT_TOKENS == 16_384


def _cut_off_reply() -> dict[str, Any]:
    arguments = json.dumps({"recommendation": "approve", "deposit_percent": 15, "rationale": "The ratio " * 50})
    return _completion(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [_call("call_5", "write_assessment", arguments[:120])],
        },
        "length",
    )


def test_a_cut_off_reply_leaves_every_step_before_the_cut_on_the_trail(repo_tmp_path):
    """The trail a cut-off reply leaves, step for step: three model calls,
    the reads (one per case document) each with its file, the cover
    calculation (which returns the application file it read the terms from,
    so it has a file too), the closing record, and an empty final
    message."""
    outcome = _run(repo_tmp_path, FakeProvider(last_reply=_cut_off_reply()))

    assert outcome.status == "failed"
    assert outcome.summary == ""
    kinds = [e.event_type.removeprefix("langchain.") for e in outcome.events]
    assert len(kinds) == 40
    assert kinds.count("chat_model_start") == 3
    assert kinds.count("tool_start") == 5
    assert kinds.count("artifact") == 5
    assert kinds[-1] == "run_closed"


def test_a_cut_off_reply_is_named_as_one(repo_tmp_path):
    """"Finished without writing an assessment" alone would point at the
    tools and the SDK; the reply itself says it was cut off, so the error
    says so too."""
    outcome = _run(repo_tmp_path, FakeProvider(last_reply=_cut_off_reply()))

    assert outcome.status == "failed"
    assert outcome.error == (
        "the agent finished without writing an assessment: the model's last "
        "reply was cut off at its output limit, partway through its call to "
        "write_assessment"
    )


def test_a_call_whose_arguments_do_not_parse_is_named(repo_tmp_path):
    reply = _completion(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [_call("call_5", "write_assessment", "{not json")],
        },
        "tool_calls",
    )
    outcome = _run(repo_tmp_path, FakeProvider(last_reply=reply))

    assert outcome.status == "failed"
    assert outcome.error == (
        "the agent finished without writing an assessment: the model's last "
        "reply called write_assessment with arguments that could not be read"
    )


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("", "the model's last reply called no tool and said nothing"),
        (
            "I recommend approval.",
            "the model's last reply answered in text and called no tool",
        ),
    ],
)
def test_a_reply_that_calls_no_tool_is_named(repo_tmp_path, content, reason):
    reply = _completion({"role": "assistant", "content": content}, "stop")
    outcome = _run(repo_tmp_path, FakeProvider(last_reply=reply))

    assert outcome.status == "failed"
    assert outcome.error == f"the agent finished without writing an assessment: {reason}"
