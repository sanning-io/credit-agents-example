"""Scripted models for the two agents. Tests only; the example itself always
runs against a real model.

`ScriptedChatModel` walks a fixed script of tool calls instead of reasoning,
so `create_agent` drives the agents' real tools with no network. The scripts
follow each agent's own prompt: read the documents, calculate cover, write
the file the turn owes, reply.
"""

from __future__ import annotations

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage


class ScriptedChatModel(FakeMessagesListChatModel):
    """A fake chat model whose `bind_tools` is a no-op: the tool-calling
    agent loop calls it once per turn, but the scripted `responses` decide
    what comes back regardless of which tools were bound or what their
    schemas say."""

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


# The two years of earnings every scripted turn enters into the calculator,
# as the documents state them, and the adjustment the scripted underwriting
# agent argues for.
YEARS = [
    {"year": "Oct 2025 to Sep 2026 (bank statements)", "ebitda_sek": 2_750_000},
    {"year": "Oct 2024 to Sep 2025 (annual report)", "ebitda_sek": 1_600_000},
]
ADJUSTMENT = {
    "year": "Oct 2024 to Sep 2025 (annual report)",
    "amount_sek": 600_000,
    "reason": "A sector-wide freight rate downturn that has ended.",
}


def _reads(prefix: str) -> list[AIMessage]:
    reading = ["read_application", "read_bank_statements", "read_annual_report", "read_credit_policy"]
    return [
        AIMessage(content="", tool_calls=[{"name": name, "args": {}, "id": f"{prefix}-{i}"}])
        for i, name in enumerate(reading, start=1)
    ]


def _calculation(prefix: str, *, adjusted: bool) -> AIMessage:
    args = {"earnings_by_year": YEARS, "existing_annual_debt_service_sek": 1_100_000, "deposit_percent": 15}
    if adjusted:
        args["adjustments"] = [ADJUSTMENT]
    return AIMessage(content="", tool_calls=[{"name": "calculate_cover", "args": args, "id": f"{prefix}-calc"}])


def _call(prefix: str, name: str, args: dict) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"{prefix}-write"}])


def underwriting_script() -> list[AIMessage]:
    """The underwriter's assessment turn: read the four case documents, calculate
    cover with an adjustment, write the assessment, then reply."""
    return [
        *_reads("call"),
        _calculation("call", adjusted=True),
        _call(
            "call",
            "write_assessment",
            {
                "recommendation": "approve_with_conditions",
                "deposit_percent": 15,
                "conditions": ["Quarterly DSCR covenant"],
                "adjustments": [{**ADJUSTMENT, "evidence": "The annual report's administration report."}],
                "rationale": "Unadjusted cover is 1.13x; adjusted for the downturn it is 1.28x with a 15% deposit.",
                "summary": "Adjusted for a downturn that has ended, cover is 1.28x with a 15% deposit; I recommend approval with conditions.",
            },
        ),
        AIMessage(content="Read the documents, calculated cover and wrote the assessment."),
    ]


def revision_script(*, response: str = "revised") -> list[AIMessage]:
    """The underwriter's revision turn: read the four case documents, calculate
    cover, write the answer to the challenge, then reply."""
    return [
        *_reads("rev"),
        _calculation("rev", adjusted=response == "maintained"),
        _call(
            "rev",
            "write_revision",
            {
                "response": response,
                "recommendation": "refer" if response == "revised" else "approve_with_conditions",
                "deposit_percent": 15,
                "conditions": [] if response == "revised" else ["Quarterly DSCR covenant"],
                "rationale": "Answered the checker's challenge with the calculator's figures.",
                "summary": "Unadjusted cover is 1.13x after a 15% deposit, below the minimum, so I now refer the case.",
            },
        ),
        AIMessage(content="Answered the challenge and wrote the revision."),
    ]


def underwriter_script() -> list[AIMessage]:
    """Both of the underwriter's turns, back to back: the model instance the
    underwriting agent gets walks this list in order, from the assessment
    through to the revision."""
    return underwriting_script() + revision_script()


def review_script(*, finding: str = "challenge") -> list[AIMessage]:
    """The checker's review turn on its own."""
    args = {
        "finding": finding,
        "note": (
            "The adjustment rests on a downturn the documents do not show has ended for good."
            if finding == "challenge"
            else "The assessment stands against the policy."
        ),
        "summary": (
            "Unadjusted cover is 1.13x after a 15% deposit; I do not accept the adjustment."
            if finding == "challenge"
            else "The assessment stands against the policy."
        ),
    }
    if finding == "challenge":
        args["about"] = "the adjustment to the prior year's earnings"
    return [
        *_reads("review"),
        _calculation("review", adjusted=False),
        _call("review", "record_review", args),
        AIMessage(content="Read the documents, recalculated cover and recorded the review."),
    ]


def conclusion_script(*, verdict: str = "approve_with_conditions") -> list[AIMessage]:
    """The checker's conclusion turn on its own."""
    return [
        *_reads("conclusion"),
        _calculation("conclusion", adjusted=False),
        _call(
            "conclusion",
            "record_conclusion",
            {
                "verdict": verdict,
                "deposit_percent": 15,
                "note": "Recalculated cover from the documents and concluded under the policy.",
                "summary": "Cover is 1.13x after a 15% deposit; I conclude under the policy.",
                "conditions": ["Quarterly DSCR covenant"] if verdict == "approve_with_conditions" else [],
            },
        ),
        AIMessage(content="Read the documents, recalculated cover and recorded the conclusion."),
    ]


def checker_script(*, finding: str = "challenge", verdict: str = "approve_with_conditions") -> list[AIMessage]:
    """The checker's two turns, back to back: the model instance the
    checker's agent gets walks this list in order, from the review through
    to the conclusion."""
    return review_script(finding=finding) + conclusion_script(verdict=verdict)


class SharedScriptedModel(FakeMessagesListChatModel):
    """A scripted model safe to share between cases running at once. It
    picks the script from the turn's system prompt and the step from how many
    replies the conversation already holds. `delay` stands in for a slow
    real model."""

    responses: list = []
    delay: float = 0.0
    finding: str = "challenge"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        import time

        from langchain_core.messages import SystemMessage
        from langchain_core.outputs import ChatGeneration, ChatResult

        from credit_demo.checker.plan import CONCLUSION_SYSTEM_PROMPT, REVIEW_SYSTEM_PROMPT
        from credit_demo.underwriter.plan import REVISION_SYSTEM_PROMPT, SYSTEM_PROMPT

        system = next((m.content for m in messages if isinstance(m, SystemMessage)), "")
        script = {
            SYSTEM_PROMPT: underwriting_script,
            REVISION_SYSTEM_PROMPT: revision_script,
            REVIEW_SYSTEM_PROMPT: lambda: review_script(finding=self.finding),
            CONCLUSION_SYSTEM_PROMPT: conclusion_script,
        }[system]()
        position = sum(1 for m in messages if getattr(m, "type", None) == "ai")
        if self.delay:
            time.sleep(self.delay)
        return ChatResult(generations=[ChatGeneration(message=script[position])])
