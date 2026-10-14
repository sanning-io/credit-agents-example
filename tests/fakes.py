"""Network-free doubles for the underwriter's model and its Sanning
identity, shared by the agent and web tests.

`FakeControlPlaneClient` is the seam `sanning_anchor.Anchorer` itself
documents (`client=`): supplying a client hands the caller their own network
surface, so no `Anchorer` built with one ever calls Sanning, enrolls a key,
or needs a real API key. `ScriptedChatModel` is LangChain's own
`FakeMessagesListChatModel`, subclassed only to answer `bind_tools` (the
base class does not implement it), so `create_agent` can drive a fixed
script of tool calls with no network and no real model.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from sanning_anchor import Anchorer, FsLogStore
from sanning_anchor.signer import LocalEd25519Signer

from tests.scripted import (  # noqa: F401  (re-exported for the tests)
    checker_script,
    conclusion_script,
    review_script,
    revision_script,
    underwriting_script,
)


class FakeControlPlaneClient:
    """Records every envelope it is asked to submit, and never touches a
    socket. What a test inspects to prove no document content reached it."""

    def __init__(self) -> None:
        self.submitted: list[dict] = []
        self.indexes: list[dict | None] = []

    def submit(self, envelope, index=None):
        self.submitted.append(envelope)
        self.indexes.append(index)


class ScriptedChatModel(FakeMessagesListChatModel):
    """A fake chat model whose `bind_tools` is a no-op: LangChain's
    tool-calling agent loop calls it once per turn, but the scripted
    `responses` decide what comes back regardless of which tools were bound
    or what their schemas say."""

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


# The underwriter's fixed test seed, exported so a test can independently
# derive the public key `fake_anchorer` signs with and assert an envelope
# carries THAT key, not merely some key.
FAKE_SIGNING_SEED = bytes(range(32))

# The checker's, which must differ: a hand-off recorded on both sides under
# ONE key would prove nothing about two agents, so a test that claims two
# keys has to be given two.
CHECKER_SIGNING_SEED = bytes(range(32, 64))


def fake_anchorer(
    tmp_path: Path,
    *,
    agent_id: str = "underwriter",
    seed: bytes = FAKE_SIGNING_SEED,
    store_name: str = "logstore",
) -> tuple[Anchorer, FakeControlPlaneClient]:
    client = FakeControlPlaneClient()
    anchorer = Anchorer(
        api_key="test-key",
        subject={"type": "agent", "agent_id": agent_id},
        # Every signing seam in the SDK takes a `Signer`
        # (`sanning_anchor.signer`).
        signer=LocalEd25519Signer.from_seed_hex(seed.hex()),
        environment="dev",
        client=client,
        log_store=FsLogStore(str(tmp_path / store_name)),
    )
    return anchorer, client


def fake_checker_anchorer(tmp_path: Path) -> tuple[Anchorer, FakeControlPlaneClient]:
    """The checker's own identity and its own store: a different key, and a
    different place to keep its bytes, as two services have."""
    return fake_anchorer(
        tmp_path,
        agent_id="checker",
        seed=CHECKER_SIGNING_SEED,
        store_name="checker-logstore",
    )


def fake_checker_model(**kwargs) -> ScriptedChatModel:
    """A checker model that walks both scripted turns above."""
    return ScriptedChatModel(responses=checker_script(**kwargs))


def fake_conclusion_model(**kwargs) -> ScriptedChatModel:
    """A checker model for a conclusion turn run on its own, with no review
    before it."""
    return ScriptedChatModel(responses=conclusion_script(**kwargs))


class TimingOutChatModel(ScriptedChatModel):
    """A model that does not answer. Stands in for every way a model call can
    fail on the way out: a timeout, a refused key, a provider outage, a
    rate limit. The agent that called it has no answer and must say so."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise TimeoutError("the model did not answer in time")


def nonsense_script() -> list[AIMessage]:
    """A model that says something and calls nothing. It is the shape of a
    model that lost the plot: plausible prose, no tool call, and so no
    figures, no finding and nothing recorded."""
    return [AIMessage(content="I have considered the matter and it seems fine.")]
