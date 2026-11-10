"""Everything this example asks of Sanning, in one place.

Each agent needs four things, and the published SDK provides all four:

- an **anchorer**: the agent's own signing key, its own log store for the
  content (a bucket, or in the Sandbox a folder on local disk), and Sanning
  for the signed commitments (`Anchorer`);
- a **recorder** for its LangChain agent: every prompt, model call, tool call
  and file, recorded as it happens (`AnchorCallbackHandler`). One recorder
  per agent per case, closed once when the case ends, with how it ended;
- a **hand-off helper** for each message between the two agents: one call on
  each side, checked on receipt, confirmed back (`create_handoff`);
- the **`bundle` verb**, which builds this agent's pack for the case from
  Sanning's index and the agent's own log store.

`Services` holds the parts that reach the outside world, so a test can
replace them: the anchorer's network client, the model, and `bundle`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
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
from sanning_anchor.s3 import S3ObjectStore
from sanning_anchor.signer import LocalEd25519Signer

from starlette.concurrency import run_in_threadpool

from . import documents
from .config import ServiceSettings
from .errors import describe, tell_the_operator

PROTOCOL = "credit-demo/handoff/1"

# The four hand-offs of one case, in order. The conclusion goes back to the
# underwriter as a hand-off like the challenge, so the answer the case is for
# is recorded on both sides.
ASSESSMENT = "assessment"
CHALLENGE = "challenge"
REVISION = "revision"
CONCLUSION = "conclusion"

# A run closed as failed carries one of the SDK's reason codes, never free
# text (the SDK refuses it). The words for a person stay on `case.error`.
# `error` is the default; the others say what happened when it is known.
FAILED_REASON = "error"
TIMEOUT_REASON = "timeout"
# The other side refused a message, or a message did not pass a check this
# example makes on it.
POLICY_REASON = "policy"

# The version of this example, signed into every record as
# `gen_ai.agent.version`: the claim a reader sees beside what the agent did.
AGENT_VERSION = version("credit-demo")

# What each message is, stated on the sender's signed record.
CONTENT_TYPES = {
    ASSESSMENT: "application/vnd.credit-demo.assessment+json",
    CHALLENGE: "application/vnd.credit-demo.challenge+json",
    REVISION: "application/vnd.credit-demo.revision+json",
    CONCLUSION: "application/vnd.credit-demo.conclusion+json",
}
STEP_OF = {content_type: step for step, content_type in CONTENT_TYPES.items()}

# The most a model may write in one reply, tool-call arguments included. A
# real model writes its whole rationale as a tool argument; under a smaller
# limit the call is cut off mid-string, LangChain files it as an invalid tool
# call, and the turn ends with nothing written. 8,192 is too small: it cuts
# off a checker agent's review once it has a real adjustment to weigh.
MAX_OUTPUT_TOKENS = 16384


@dataclass
class BundleRun:
    """One run of the SDK's `bundle` verb: its exit code, and what it said."""

    code: int
    errors: list[str] = field(default_factory=list)


@dataclass
class Services:
    """One agent's outside world. `logs` is where its content lives, as
    `bundle --logs` takes it."""

    anchorer: Any
    model: Any
    logs: str
    bundle: Callable[[list[str]], BundleRun]


def require_cases_under_home(settings: ServiceSettings) -> None:
    """Every file an agent reads or writes must sit under one root for the
    recorder to record it: the documents, and each case's workspace. A cases
    folder elsewhere would leave every output file unrecorded while the
    audit trail still read Verified, so the service refuses to start."""
    runs = Path(settings.runs_dir).resolve()
    root = Path(documents.REPO_ROOT).resolve()
    if root not in (runs, *runs.parents):
        raise ValueError(
            f"{settings.variable('RUNS_DIR')} must be a folder inside {root}, where the "
            "documents are, so every file the agents write is recorded"
        )


class NotReady(Exception):
    """This service is missing settings it needs before it can record
    anything. The message names them."""


class ServicesWhenNeeded:
    """The services, built the first time they are needed. A service with
    settings missing still starts, and says what is missing on `/health`
    and when a case is run, rather than refusing to start at all.

    Calling it returns the services. `close` ends the anchorer, and only if
    one was built: a service that never recorded anything builds nothing at
    shutdown."""

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
        """Wait for the anchorer's in-flight submissions, once. A close that
        raises is reported to the operator and does not stop the shutdown."""
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
    """A lifespan for a FastAPI app: nothing at start-up, and at shutdown the
    anchorer is closed if it was built."""

    @asynccontextmanager
    async def lifespan(_app: Any) -> AsyncIterator[None]:
        yield
        await run_in_threadpool(get_services.close)

    return lifespan


def folder_name(agent_id: str) -> str:
    """The agent's folder in the shared log store. The agent ID names it, so
    an ID that could point outside it, or at another agent's folder, is
    refused."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", agent_id) or ".." in agent_id:
        raise ValueError(
            "SANNING_AGENT_ID names this agent's folder in the log store, so it must "
            "be letters, digits, dots, hyphens and underscores, and start with a letter or digit"
        )
    return agent_id


def log_store_for(settings: ServiceSettings) -> tuple[Any, str]:
    """Where this agent's content lives, and the `bundle --logs` value that
    reads it back. Both agents share one log store, and each writes only under
    a folder named after its agent ID, so `bundle --logs` for an agent points
    at its own folder. The settings decide, and the agent's code is the same
    either way:

    - a bucket is set: the S3-compatible bucket, for a Sandbox key or a Live
      key alike;
    - no bucket and a Sandbox key: a folder on local disk, which only the
      Sandbox may use;
    - no bucket and any other key: refused. The SDK accepts a local folder
      with a Live key and records without complaint, and a folder is lost with
      the machine, so this is the check that stops it.
    """
    folder = folder_name(settings.agent_id)
    if settings.bucket:
        objects = S3ObjectStore(
            bucket=settings.bucket,
            endpoint=settings.bucket_endpoint or None,
            region=settings.bucket_region,
            access_key_id=settings.bucket_access_key_id or None,
            secret_access_key=settings.bucket_secret_access_key or None,
            force_path_style=settings.bucket_path_style,
            prefix=folder,
        )
        return ObjectLogStore(objects), f"s3://{settings.bucket}/{folder}"
    if settings.sandbox:
        log_dir = settings.log_dir / folder
        return FsLogStore(str(log_dir)), str(log_dir)
    raise ValueError(
        "a Live API key needs an S3-compatible bucket for the content: set "
        f"{settings.variable('BUCKET')}, "
        f"{settings.variable('ACCESS_KEY_ID')} and {settings.variable('SECRET_ACCESS_KEY')}. "
        "Only a Sandbox API key (it starts with sanning_test_) can keep the "
        "content on local disk"
    )


def real_services(settings: ServiceSettings) -> Services:
    """The real thing: Sanning, the agent's own log store, and the model
    through OpenRouter, all from this service's own settings."""
    log_store, logs = log_store_for(settings)
    anchorer = Anchorer(
        api_key=settings.api_key,
        subject={"type": "agent", "agent_id": settings.agent_id},
        signer=LocalEd25519Signer.from_seed_hex(settings.signing_seed_hex),
        environment=settings.environment,
        base_url=settings.base_url,
        log_store=log_store,
        # Each case has its own chain, so the process keeps no chain between runs.
        store=MemoryStore(),
    )
    return Services(
        anchorer=anchorer,
        model=build_model(settings),
        logs=logs,
        bundle=_run_bundle_command,
    )


def build_model(settings: ServiceSettings, *, http_client: Any | None = None) -> Any:
    """The agent's model, through OpenRouter's OpenAI-compatible API. The
    model name says which provider, so changing either is a setting, not
    code. `http_client` lets a test answer with no network."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        max_tokens=MAX_OUTPUT_TOKENS,
        default_headers={"HTTP-Referer": "https://sanning.io", "X-Title": "Sanning credit demo"},
        http_client=http_client,
    )


def _run_bundle_command(args: list[str]) -> BundleRun:
    """`sanning-anchor bundle`, the SDK's own command, as a subprocess. It
    inherits this service's environment: it reads the API key, the signing
    seed and the base URL from the names the service reads, and the bucket
    from the standard AWS names that recording reads. Nothing is copied or
    renamed on the way."""
    command = Path(sys.executable).parent / "sanning-anchor"
    ran = subprocess.run(
        [str(command), "bundle", *args],
        capture_output=True,
        text=True,
    )
    return BundleRun(code=ran.returncode, errors=[line for line in ran.stderr.splitlines() if line])


def recorder(
    anchorer: Any,
    case_id: str,
    *,
    decisions: Sequence[DecisionTool],
    on_event: Callable[[Any], None] | None = None,
) -> AnchorCallbackHandler:
    """The recorder for one agent on one case. `decisions` names the tools
    of this agent whose completion is a decision, each with the decision's
    name: the agent's own, never another agent's.

    `artifacts` records each document an agent reads and each file it
    writes as a file record; its root must contain both the documents and
    the case's workspace, so it is the repo root. The policy a run used is
    shown by the file record on the step that reads it. Each calculation
    records the application file it took the financing requested from, with
    the same fingerprint as the step that read the application unless the
    file changed between the two.

    What goes where: a file record carries the document's name, its path
    under the root and its fingerprint, and stays in the operator's own log
    store. Sanning receives the envelope, which carries hashes and no
    metadata, so a document's bytes, name and path never reach the plane.
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
    """This agent's hand-off helper for one case. It carries the agent's own
    message count and the messages it has already answered, so it lives as
    long as the case does."""
    return create_handoff(anchorer=anchorer, case_id=case_id, protocol=PROTOCOL)


def wire(sent: Any) -> dict[str, Any]:
    """What crosses the wire for one hand-off: the message as the exact text
    that was signed, and the sender's signed record."""
    return {"message": sent.wire_bytes.decode("utf-8"), "sanning_handoff": sent.attachment}


def unwire(body: Mapping[str, Any]) -> tuple[Mapping[str, Any], bytes]:
    """The sender's record and the exact bytes that arrived. Refuses a body
    that is not a hand-off at all; judging the message is `receive`'s job."""
    attachment = body.get("sanning_handoff") if isinstance(body, Mapping) else None
    message = body.get("message") if isinstance(body, Mapping) else None
    if not isinstance(attachment, Mapping) or not isinstance(message, str):
        raise ValueError("not a hand-off: expected `message` text and `sanning_handoff`")
    return attachment, message.encode("utf-8")


def arriving_step(attachment: Mapping[str, Any]) -> str | None:
    """Which of the four hand-offs the sender's own record says this is.

    Read off the sender's signed record, never out of the message, so a
    receiver decides what to do before anything reads the message itself.
    `receive` then verifies that record; a forged one is refused whatever
    this returns."""
    record = attachment.get("event_record")
    event = record.get("event") if isinstance(record, Mapping) else None
    content_type = event.get("content_type") if isinstance(event, Mapping) else None
    return STEP_OF.get(content_type) if isinstance(content_type, str) else None


def deviation(
    attachment: Mapping[str, Any], *, step: str, in_reply_to: str | None
) -> str | None:
    """Why the arriving record is not the message this side is waiting for,
    or `None` when it is.

    `receive` proves the record verifies, names this case and comes from the
    expected sender. It does not check that it belongs to this exchange, is
    the step this side is waiting for, or answers the message this side
    sent, so this example does."""
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
