"""Everything this example asks of Sanning: an anchorer, a recorder and a hand-off helper for each agent.

`Services` holds the parts that reach the outside world (the anchorer's
network client and the model), so a test can replace them. An agent builds no
pack or case file: the evidence builder does.
"""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping, Sequence

from sanning_anchor import (
    AnchorCallbackHandler,
    Anchorer,
    DecisionTool,
    FsLogStore,
    MemoryStore,
    ObjectLogStore,
    create_handoff,
)
from sanning_anchor.errors import AnchorError
from sanning_anchor.s3 import S3ObjectStore
from sanning_anchor.signer import LocalEd25519Signer

from starlette.concurrency import run_in_threadpool

from . import documents
from .config import ServiceSettings
from .errors import describe, tell_the_operator

PROTOCOL = "credit-demo/handoff/1"

# The four hand-offs of one case, in order.
ASSESSMENT = "assessment"
CHALLENGE = "challenge"
REVISION = "revision"
CONCLUSION = "conclusion"

# A run closed as failed carries one of the SDK's reason codes, never free text.
FAILED_REASON = "error"
TIMEOUT_REASON = "timeout"
# The other side refused a message, or a message failed a check of this example.
POLICY_REASON = "policy"

# What recording a step or closing a run can fail with: the SDK's own errors,
# and the log store's disk or network. Anything else is a bug and must surface.
RECORDING_FAULTS = (AnchorError, OSError)

# Signed into every record as `gen_ai.agent.version`.
AGENT_VERSION = version("credit-demo")

# Each message's type, stated on the sender's signed record.
CONTENT_TYPES = {
    ASSESSMENT: "application/vnd.credit-demo.assessment+json",
    CHALLENGE: "application/vnd.credit-demo.challenge+json",
    REVISION: "application/vnd.credit-demo.revision+json",
    CONCLUSION: "application/vnd.credit-demo.conclusion+json",
}
STEP_OF = {content_type: step for step, content_type in CONTENT_TYPES.items()}

# The most a model may write in one reply, tool-call arguments included. A
# smaller limit cuts a long rationale off mid-string, and the turn ends with
# nothing written.
MAX_OUTPUT_TOKENS = 16384


@dataclass
class Services:
    anchorer: Any
    model: Any


def require_cases_under_home(settings: ServiceSettings) -> None:
    """Refuse a cases folder outside the repo root: its files would go unrecorded while the audit trail still read Verified."""
    runs = Path(settings.runs_dir).resolve()
    root = Path(documents.REPO_ROOT).resolve()
    if root not in (runs, *runs.parents):
        raise ValueError(
            f"{settings.variable('RUNS_DIR')} must be a folder inside {root}, where the "
            "documents are, so every file the agents write is recorded"
        )


class NotReady(Exception):
    """Settings are missing. The message names them."""


class ServicesWhenNeeded:
    """The services, built on first call, so a service with settings missing still starts and says what is missing."""

    def __init__(self, settings: ServiceSettings, services: Services | None = None) -> None:
        self._settings = settings
        self._built: list[Services] = [services] if services is not None else []
        self._closed = False

    def __call__(self) -> Services:
        if not self._built:
            missing = self._settings.ready_to_record()
            if missing:
                raise NotReady(
                    f"this service cannot record anything yet: {', '.join(missing)} "
                    "not set (see .env.example)"
                )
            self._built.append(real_services(self._settings))
        return self._built[0]

    def close(self) -> None:
        """Wait for in-flight submissions, once. A failure is reported and does not stop the shutdown."""
        if not self._built or self._closed:
            return
        self._closed = True
        try:
            self._built[0].anchorer.close()
        except Exception as exc:
            tell_the_operator(f"the Sanning anchorer did not close cleanly: {describe(exc)}")


def services_when_needed(settings: ServiceSettings, services: Services | None = None) -> ServicesWhenNeeded:
    return ServicesWhenNeeded(settings, services)


def closing_at_shutdown(get_services: ServicesWhenNeeded) -> Callable[[Any], AsyncIterator[None]]:
    """A lifespan that closes the anchorer at shutdown, if one was built."""

    @asynccontextmanager
    async def lifespan(_app: Any) -> AsyncIterator[None]:
        yield
        await run_in_threadpool(get_services.close)

    return lifespan


def folder_name(agent_id: str, variable: str = "SANNING_AGENT_ID") -> str:
    """The agent's folder in the shared log store. An ID that could point outside it is refused."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", agent_id) or ".." in agent_id:
        raise ValueError(
            f"{variable} names an agent's folder in the log store, so it must "
            "be letters, digits, dots, hyphens and underscores, and start with a letter or digit"
        )
    return agent_id


def log_location(settings: Any, agent_id: str, *, variable: str = "SANNING_AGENT_ID") -> str:
    """Where one agent's content lives, as `bundle --logs` takes it.

    A bucket is used when set. With no bucket, only a Sandbox key may use a
    local folder: the SDK accepts one with a Live key, and the content would be
    lost with the machine, so this refuses it.
    """
    folder = folder_name(agent_id, variable)
    if settings.bucket:
        return f"s3://{settings.bucket}/{folder}"
    if settings.sandbox:
        return str(settings.log_dir / folder)
    raise ValueError(
        "a Live API key needs an S3-compatible bucket for the content: set "
        f"{settings.variable('BUCKET')}, "
        f"{settings.variable('ACCESS_KEY_ID')} and {settings.variable('SECRET_ACCESS_KEY')}. "
        "Only a Sandbox API key (it starts with sanning_test_) can keep the "
        "content on local disk"
    )


def log_store_for(settings: ServiceSettings) -> Any:
    location = log_location(settings, settings.agent_id)
    if settings.bucket:
        objects = S3ObjectStore(
            bucket=settings.bucket,
            endpoint=settings.bucket_endpoint or None,
            region=settings.bucket_region,
            access_key_id=settings.bucket_access_key_id or None,
            secret_access_key=settings.bucket_secret_access_key or None,
            force_path_style=settings.bucket_path_style,
            prefix=folder_name(settings.agent_id),
        )
        return ObjectLogStore(objects)
    return FsLogStore(location)


def real_services(settings: ServiceSettings) -> Services:
    anchorer = Anchorer(
        api_key=settings.api_key,
        subject={"type": "agent", "agent_id": settings.agent_id},
        signer=LocalEd25519Signer.from_seed_hex(settings.signing_seed_hex),
        base_url=settings.base_url,
        log_store=log_store_for(settings),
        # Each case has its own chain, so the process keeps no chain between runs.
        store=MemoryStore(),
    )
    return Services(anchorer=anchorer, model=build_model(settings))


def build_model(settings: ServiceSettings, *, http_client: Any | None = None) -> Any:
    """The agent's model, through any OpenAI-compatible service. `http_client` lets a test answer with no network."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
        max_tokens=MAX_OUTPUT_TOKENS,
        default_headers={"HTTP-Referer": "https://sanning.io", "X-Title": "Sanning credit demo"},
        http_client=http_client,
    )


def recorder(
    anchorer: Any,
    case_id: str,
    *,
    decisions: Sequence[DecisionTool],
    on_event: Callable[[Any], None] | None = None,
) -> AnchorCallbackHandler:
    """The recorder for one agent on one case.

    `decisions` names this agent's own decision tools. The artifacts root is the
    repo root, so it holds both the documents and each case's workspace. A file
    record (name, path, fingerprint) stays in the operator's log store; Sanning
    receives only hashes, so a document's bytes, name and path never reach it.
    """
    return AnchorCallbackHandler(
        anchorer,
        case_id=case_id,
        raise_on_gaps=True,
        agent_version=AGENT_VERSION,
        artifacts={"root": str(documents.REPO_ROOT)},
        decisions=list(decisions),
        on_event=on_event,
    )


def open_handoff(anchorer: Any, case_id: str) -> Any:
    """This agent's hand-off helper for one case. It keeps state, so it lives as long as the case."""
    return create_handoff(anchorer=anchorer, case_id=case_id, protocol=PROTOCOL)


def wire(sent: Any) -> dict[str, Any]:
    """The message as the exact text that was signed, and the sender's signed record."""
    return {"message": sent.wire_bytes.decode("utf-8"), "sanning_handoff": sent.attachment}


def unwire(body: Mapping[str, Any]) -> tuple[Mapping[str, Any], bytes]:
    """The sender's record and the exact bytes that arrived."""
    attachment = body.get("sanning_handoff") if isinstance(body, Mapping) else None
    message = body.get("message") if isinstance(body, Mapping) else None
    if not isinstance(attachment, Mapping) or not isinstance(message, str):
        raise ValueError("not a hand-off: expected `message` text and `sanning_handoff`")
    return attachment, message.encode("utf-8")


def arriving_step(attachment: Mapping[str, Any]) -> str | None:
    """Which of the four hand-offs the sender's record says this is.

    Read from the record, never the message. `receive` verifies the record, so a
    forged one is refused whatever this returns.
    """
    record = attachment.get("event_record")
    event = record.get("event") if isinstance(record, Mapping) else None
    content_type = event.get("content_type") if isinstance(event, Mapping) else None
    return STEP_OF.get(content_type) if isinstance(content_type, str) else None


def deviation(
    attachment: Mapping[str, Any], *, step: str, in_reply_to: str | None
) -> str | None:
    """Why the record is not the message this side is waiting for, or `None` when it is.

    `receive` does not check the exchange, the step or what the message answers.
    """
    record = attachment.get("event_record")
    record = record if isinstance(record, Mapping) else {}
    context = record.get("context") if isinstance(record.get("context"), Mapping) else {}
    event = record.get("event") if isinstance(record.get("event"), Mapping) else {}
    if context.get("protocol") != PROTOCOL:
        return f"it belongs to another exchange (protocol {context.get('protocol')!r})"
    if event.get("content_type") != CONTENT_TYPES[step]:
        return f"it is not the {step} this side is waiting for"
    if in_reply_to is not None:
        answers = context.get("in_reply_to")
        if not isinstance(answers, str) or answers.lower() != in_reply_to.lower():
            return "it does not answer the message this side sent"
    return None
