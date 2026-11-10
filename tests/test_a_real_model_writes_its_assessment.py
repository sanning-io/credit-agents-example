"""The underwriting turn, driven through the model the example really builds (`build_model`),
answered by a fake provider that speaks the OpenAI-compatible chat completions API over HTTP.
Scripted models skip the request `ChatOpenAI` sends, the output budget it carries and how it
parses a reply back into tool calls, so a real model can fail a turn where a scripted one passes.
A reply cut off at the output limit leaves a trail of its own, which
`test_a_cut_off_reply_leaves_every_step_before_the_cut_on_the_trail` checks."""

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


# A stray `",` where the next key belongs, as a model writes it when it loses
# its place in a call's arguments.
UNREADABLE_ARGUMENTS = '{"recommendation": "approve", "deposit_percent": 15, "rationale": "Cover clears.","," summary": "x"}'


def _settings(tmp_path):
    return settings_from_env(
        service="underwriter",
        default_agent_id="underwriter",
        default_port=8001,
        env={
            "SANNING_API_KEY": "sanning_test_key",
            "SANNING_SIGNING_SEED": "ab" * 32,
            "OPENROUTER_API_KEY": "test-openrouter-key",
            "SANNING_LOG_BUCKET": "test-bucket",
            "AWS_ENDPOINT_URL_S3": "http://localhost:9000",
            "AWS_ACCESS_KEY_ID": "test-access",
            "AWS_SECRET_ACCESS_KEY": "test-secret",
            "RUNS_DIR": str(tmp_path / "runs"),
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
    """An OpenAI-compatible chat completions endpoint that acts like a tool-calling model: it reads
    the four case documents in parallel, calculates cover, then calls `write_assessment` with a
    rationale of `rationale_tokens`. It is stateless and honours the request's output budget: a
    longer reply comes back cut off, with `finish_reason: "length"`. `last_reply` replaces the
    reply to the `write_assessment` step and every step after it. `unreadable_first` is how many
    times that step is answered with arguments that are not valid JSON."""

    def __init__(
        self,
        *,
        rationale_tokens: int = REALISTIC_RATIONALE_TOKENS,
        last_reply: dict[str, Any] | None = None,
        unreadable_first: int = 0,
    ) -> None:
        self.rationale_tokens = rationale_tokens
        self.last_reply = last_reply
        self.unreadable_first = unreadable_first
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
        if step >= 2 and self.last_reply is not None:
            return self.last_reply
        if 2 <= step < 2 + self.unreadable_first:
            return _completion(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [_call(f"call_bad_{step}", "write_assessment", UNREADABLE_ARGUMENTS)],
                },
                "tool_calls",
            )
        if step == 2 + self.unreadable_first:
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
    """A budget of 8,192 tokens cuts off a checker agent's review once it has an adjustment to
    weigh."""
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
    """The trail a cut-off reply leaves, step for step: five model calls (two that read and
    calculate, the cut-off reply and its two retries), one read per case document with its file,
    the cover calculation with the application file it read, the closing record, and an empty
    final message. The cut-off calls are not tool runs."""
    outcome = _run(repo_tmp_path, FakeProvider(last_reply=_cut_off_reply()))

    assert outcome.status == "failed"
    assert outcome.summary == ""
    kinds = [e.event_type.removeprefix("agent.") for e in outcome.events]
    assert len(kinds) == 59
    assert kinds.count("model_call.started") == 5
    assert kinds.count("tool_call.started") == 5
    assert kinds.count("file") == 5
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


def _every_tool_call_is_answered(request: dict[str, Any]) -> bool:
    """Whether each tool call in the request's history has a tool message
    with its id, the rule the provider enforces with a 400."""
    asked = {c["id"] for m in request["messages"] if m["role"] == "assistant" for c in m.get("tool_calls") or []}
    answered = {m["tool_call_id"] for m in request["messages"] if m["role"] == "tool"}
    return asked == answered


def test_an_unreadable_call_goes_back_to_the_model_and_the_turn_completes(repo_tmp_path):
    provider = FakeProvider(unreadable_first=1)
    outcome = _run(repo_tmp_path, provider)

    assert outcome.status == "complete", outcome.error
    assert outcome.output["recommendation"] == "approve"
    # Read, calculate, the unreadable write, the write again, the closing reply.
    assert len(provider.requests) == 5
    told = [m for m in provider.requests[3]["messages"] if m["role"] == "tool" and m["tool_call_id"] == "call_bad_2"]
    assert len(told) == 1
    assert "write_assessment" in told[0]["content"]
    assert "could not be read" in told[0]["content"]
    assert "JSONDecodeError" in told[0]["content"]
    # The arguments are not quoted back.
    assert "Cover clears." not in told[0]["content"]
    assert all(_every_tool_call_is_answered(request) for request in provider.requests[1:])
    # The retry left no gap on the trail.
    assert outcome.events[-1].event_type == "agent.run_closed"


def test_two_unreadable_calls_in_a_row_are_still_retried(repo_tmp_path):
    provider = FakeProvider(unreadable_first=2)
    outcome = _run(repo_tmp_path, provider)

    assert outcome.status == "complete", outcome.error
    assert len(provider.requests) == 6
    assert all(_every_tool_call_is_answered(request) for request in provider.requests[1:])


def test_three_unreadable_calls_in_a_row_stop_the_turn_with_the_usual_reason(repo_tmp_path):
    provider = FakeProvider(unreadable_first=3)
    outcome = _run(repo_tmp_path, provider)

    assert outcome.status == "failed"
    assert outcome.error == (
        "the agent finished without writing an assessment: the model's last "
        "reply called write_assessment with arguments that could not be read"
    )
    # Two retries, not three: the third unreadable reply is the last request.
    assert len(provider.requests) == 5
