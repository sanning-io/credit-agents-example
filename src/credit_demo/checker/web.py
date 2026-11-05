"""The checker agent's service.

The underwriting agent sends each hand-off here. For each one the checker
agent decides whether it will act on it, records it with the SDK's
`receive` (checked on receipt, with that decision as its outcome), runs its
turn, and sends its answer back as the next hand-off. It never reads a
message before `receive` has verified it: what a message claims to be is
read off the sender's own signed record.

Each turn starts by asking the credit desk, over `DESK_URL`, for the case's
documents (`credit_demo.checker.case_documents`). A case whose documents it
cannot get stops there, with the reason, and no turn runs.

Routes:
- `POST /cases/{id}/handoffs`: a hand-off arrives; the answer carries this
  side's received record and, when there is one, the next hand-off.
- `POST /cases/{id}/receipts`: the underwriting agent's received record for
  a hand-off this side sent, which this side confirms.
- `POST /cases/{id}/pack`: this agent's pack for the case, as a zip.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sanning_anchor import HandoffUnexpectedSenderError
from sanning_anchor.errors import AnchorError, HandoffAlteredError
from starlette.concurrency import run_in_threadpool

from ..common import cases as case_states
from ..common.cases import CaseStore, valid_case_id
from ..common.config import ServiceSettings
from ..common.context import CaseContext
from ..common.errors import (
    configure_logging,
    describe,
    tell_the_operator,
    tell_the_operator_a_request_raised,
    without_local_paths,
)
from ..common.packs import BUILT, NOT_STAMPED_YET, build_pack
from ..common.sanning import (
    ASSESSMENT,
    CHALLENGE,
    CONCLUSION,
    CONTENT_TYPES,
    FAILED_REASON,
    TIMEOUT_REASON,
    REVISION,
    NotReady,
    Services,
    arriving_step,
    deviation,
    open_handoff,
    recorder,
    require_cases_under_home,
    services_when_needed,
    unwire,
    wire,
)
from ..common.turn import run_turn
from .agent import conclusion_turn, review_turn
from .case_documents import CaseDocumentsUnavailable, case_context, default_desk_url
from .config import load as load_settings
from .review import challenge_for, conclusion_message

configure_logging("uvicorn", "uvicorn.error", "uvicorn.access")

NOTHING = "nothing"


@dataclass
class Live:
    """What this agent holds for a case while it runs: its hand-off helper
    and recorder (both live as long as the case), what it is waiting for
    next, and what it sent."""

    handoff: Any
    recorder: Any
    expecting: str = ASSESSMENT
    last_sent_hash: str | None = None
    assessment: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    challenge: dict[str, Any] | None = None
    sent: dict[str, Any] = field(default_factory=dict)
    closed: bool = False


def create_app(
    settings: ServiceSettings | None = None,
    *,
    services: Services | None = None,
    desk_url: str | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    require_cases_under_home(settings)
    get_services = services_when_needed(settings, services)
    desk_url = (desk_url or default_desk_url()).rstrip("/")
    cases = CaseStore(settings.runs_dir)
    underwriter_key = settings.counterparty_public_key
    live: dict[str, Live] = {}
    # One lock per case: a case's turn and its receipts take turns, and two
    # cases never wait on each other.
    locks: dict[str, threading.Lock] = {}
    locks_guard = threading.Lock()

    def lock_for(case_id: str) -> threading.Lock:
        with locks_guard:
            return locks.setdefault(case_id, threading.Lock())

    app = FastAPI(title="credit-demo checker agent")
    app.state.cases = cases

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        tell_the_operator_a_request_raised("checker", request.method, request.url.path, exc)
        return JSONResponse(status_code=500, content={"detail": without_local_paths(exc)})

    @app.get("/health")
    def health() -> dict:
        return settings.as_health()

    def live_for(case_id: str) -> Live:
        if case_id not in live:
            anchorer = get_services().anchorer
            live[case_id] = Live(
                handoff=open_handoff(anchorer, case_id),
                recorder=recorder(anchorer, case_id),
            )
        return live[case_id]

    def forget_if_new(case_id: str, case: case_states.Case | None) -> None:
        """Nothing is kept for a case whose first message was refused
        before anything was recorded."""
        if case is None:
            live.pop(case_id, None)

    def close(case: case_states.Case, state: Live, *, outcome: str, code: str | None = None) -> None:
        """End this agent's record of the case, once, with how it ended. A
        failed close is reported, never dropped."""
        if state.closed:
            return
        state.closed = True
        try:
            # A failed case closes without `raise_on_gaps`, so a lost record
            # comes back for the check below instead of hiding the close.
            state.recorder.close(raise_on_gaps=outcome != "failed", outcome=outcome, reason=code)
        except Exception as exc:
            tell_the_operator(f"checker: case {case.id}: closing its record raised: {describe(exc)}")
            return
        if not state.recorder.is_complete:
            tell_the_operator(f"checker: case {case.id}: records of this agent are missing from the trail")

    def stop(case: case_states.Case, state: Live, reason: str, code: str = FAILED_REASON) -> dict[str, Any]:
        case.status = case_states.STOPPED
        case.error = reason
        case.stopped_at = case_states.now()
        cases.save(case)
        close(case, state, outcome="failed", code=code)
        return {"error": reason}

    def documents_for(case_id: str) -> tuple[CaseContext | None, str | None]:
        """The context for one turn, from the credit desk; or no context and
        the reason the case stops."""
        try:
            return case_context(desk_url, case_id), None
        except CaseDocumentsUnavailable as exc:
            return None, f"the checker agent could not get the case documents from the credit desk: {exc.reason}"

    def conclude(state: Live, workspace, case_id: str, *, revision) -> tuple[str, dict[str, Any] | None, str]:
        """The conclusion turn: the step and message to send, or the reason
        it could not conclude and no message. The last part is the close
        reason code for a case that stops here."""
        context, why = documents_for(case_id)
        if context is None:
            return why, None, FAILED_REASON
        result = run_turn(
            conclusion_turn(
                workspace,
                context,
                assessment=state.assessment,
                review=state.review,
                revision=revision,
                challenge=state.challenge,
            ),
            model=get_services().model,
            recorder=state.recorder,
        )
        if result.status != "complete":
            return (
                f"the checker agent could not conclude the case: {result.error}",
                None,
                TIMEOUT_REASON if result.timed_out else FAILED_REASON,
            )
        return CONCLUSION, conclusion_message(result.output), FAILED_REASON

    def take(case_id: str, body: Any) -> tuple[int, dict[str, Any]]:
        if not valid_case_id(case_id):
            return 404, {"refused": "no such case", "recorded": False}
        try:
            attachment, wire_bytes = unwire(body)
        except ValueError as exc:
            return 400, {"refused": describe(exc), "recorded": False}
        try:
            get_services()
        except NotReady as exc:
            return 503, {"refused": describe(exc), "recorded": False}

        with lock_for(case_id):
            case = cases.get(case_id)
            state = live_for(case_id)
            step = arriving_step(attachment)

            # Decide first, from the sender's own record and what this agent
            # has done so far; `receive` then records that decision.
            if case is not None and case.status != case_states.RUNNING:
                reason = "this case has ended here; it takes no further hand-off"
            elif step != state.expecting:
                reason = f"this agent is waiting for the {state.expecting}, not {step or 'this message'}"
            else:
                reason = deviation(attachment, step=step, in_reply_to=state.last_sent_hash)

            try:
                received = state.handoff.receive(
                    attachment=attachment,
                    wire_bytes=wire_bytes,
                    outcome="rejected" if reason else "accepted",
                    expected_sender=underwriter_key,
                    # This side's own record says which of its messages a
                    # reply answers, only for a reply it accepts. The
                    # assessment answers nothing.
                    in_reply_to=None if reason else state.last_sent_hash,
                )
            except HandoffUnexpectedSenderError as exc:
                forget_if_new(case_id, case)
                return 403, {
                    "refused": f"signed by {exc.actual_sender}, not the underwriting agent this agent deals with",
                    "recorded": False,
                }
            except HandoffAlteredError as exc:
                return 409, {"refused": "the message is not the one its sender signed", "recorded": exc.record_written}
            except (AnchorError, ValueError) as exc:
                forget_if_new(case_id, case)
                return 409, {"refused": without_local_paths(describe(exc)), "recorded": False}

            if reason:
                return 409, {"refused": reason, "recorded": True, "received_record": received.received_record}

            # A case exists here once a message on it verified and was taken.
            if case is None:
                case = cases.create(case_id)

            case.step = step
            case.handoffs.append(
                {
                    "step": step,
                    "sender": "underwriter",
                    "received_event_id": received.result.event_id,
                    "received_at": (received.result.envelope or {}).get("signed_at"),
                    "message_hash": received.message_hash,
                    "recorded_by_both": True,
                }
            )
            cases.save(case)
            answer: dict[str, Any] = {"received_record": received.received_record}

            workspace = cases.workspace(case_id)
            if step == ASSESSMENT:
                context, why = documents_for(case_id)
                if context is None:
                    answer.update(stop(case, state, why))
                    return 200, answer
                state.assessment = dict(received.message.get("assessment") or {})
                review = run_turn(
                    review_turn(workspace, context, assessment=state.assessment),
                    model=get_services().model,
                    recorder=state.recorder,
                )
                if review.status != "complete":
                    answer.update(
                        stop(
                            case,
                            state,
                            f"the checker agent could not finish its review: {review.error}",
                            TIMEOUT_REASON if review.timed_out else FAILED_REASON,
                        )
                    )
                    return 200, answer
                state.review = dict(review.output)
                if review.output.get("finding") == CHALLENGE:
                    state.challenge = challenge_for(review.output)
                    reply_step, message, code = CHALLENGE, state.challenge, FAILED_REASON
                else:
                    reply_step, message, code = conclude(state, workspace, case_id, revision=None)
            else:
                reply_step, message, code = conclude(state, workspace, case_id, revision=dict(received.message))

            if message is None:
                answer.update(stop(case, state, reply_step, code))
                return 200, answer

            try:
                sent = state.handoff.send(
                    message=message,
                    to=underwriter_key,
                    content_type=CONTENT_TYPES[reply_step],
                    in_reply_to=received.message_hash,
                )
            except Exception as exc:
                # The message arrived and was worked; what failed is this
                # side's record of its answer. Said as that, not as a refusal.
                answer.update(
                    stop(case, state, f"the checker agent could not record its {reply_step}: {without_local_paths(describe(exc))}")
                )
                return 200, answer
            state.sent[reply_step] = sent
            state.last_sent_hash = sent.message_hash
            state.expecting = REVISION if reply_step == CHALLENGE else NOTHING
            if reply_step == CONCLUSION:
                case.status = case_states.CONCLUDED
                case.concluded_at = case_states.now()
                case.conclusion = message
            cases.save(case)
            if reply_step == CONCLUSION:
                # This agent's part of the case is done once its conclusion
                # is recorded and sent.
                close(case, state, outcome="completed")
            answer["reply"] = wire(sent)
            return 200, answer

    @app.post("/cases/{case_id}/handoffs")
    async def handoffs(case_id: str, request: Request) -> JSONResponse:
        body = await request.json()
        status, answer = await run_in_threadpool(take, case_id, body)
        return JSONResponse(status_code=status, content=answer)

    @app.post("/cases/{case_id}/receipts")
    async def receipts(case_id: str, request: Request) -> JSONResponse:
        body = await request.json()

        def confirm() -> tuple[int, dict[str, Any]]:
            if not valid_case_id(case_id):
                return 404, {"confirmed": False, "reasons": ["no such case"]}
            with lock_for(case_id):
                state = live.get(case_id)
                case = cases.get(case_id)
                step = body.get("step") if isinstance(body, dict) else None
                if state is None or case is None or step not in state.sent:
                    return 404, {"confirmed": False, "reasons": ["no such hand-off from this agent"]}
                confirmation = state.handoff.confirm(
                    sent=state.sent[step], received_record=body.get("received_record")
                )
                return 200, {
                    "confirmed": confirmation.confirmed,
                    "reasons": [reason.detail for reason in confirmation.reasons],
                }

        status, answer = await run_in_threadpool(confirm)
        return JSONResponse(status_code=status, content=answer)

    @app.post("/cases/{case_id}/pack")
    async def pack(case_id: str) -> Response:
        case = cases.get(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="no such case")
        if case.status == case_states.RUNNING:
            raise HTTPException(status_code=409, detail="the case has not ended here yet")
        out = cases.folder(case_id) / f"pack-{settings.agent_id}.zip"
        ran = await run_in_threadpool(lambda: build_pack(get_services(), settings, case_id=case_id, out=out))
        if ran.code == NOT_STAMPED_YET:
            return JSONResponse(status_code=202, content={"ready": False, "reason": " ".join(ran.errors)})
        if ran.code not in BUILT:
            return JSONResponse(status_code=502, content={"ready": False, "reason": " ".join(ran.errors)})
        return Response(content=out.read_bytes(), media_type="application/zip")

    return app
