"""One case, from the underwriting agent's side.

The underwriter assesses, hands the assessment to the checker, revises if the checker
challenges, and closes its record. Every hand-off uses the SDK's `send`, `receive` and
`confirm`; anything short of that stops the case, with the reason on the case.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Mapping

import httpx
from sanning_anchor import HandoffUnexpectedSenderError
from sanning_anchor.errors import AnchorError

from ..common import cases as case_states
from ..common.cases import CaseStore
from ..common.config import ServiceSettings
from ..common.context import CaseContext
from ..common.errors import describe, tell_the_operator, without_local_paths
from ..common.messages import CHECKER_DID_NOT_ANSWER, CHECKER_TOOK_TOO_LONG, OUT_OF_STEP, UNEXPECTED, reason_for
from ..common.sanning import (
    ASSESSMENT,
    CHALLENGE,
    CONCLUSION,
    CONTENT_TYPES,
    FAILED_REASON,
    POLICY_REASON,
    REVISION,
    RECORDING_FAULTS,
    TIMEOUT_REASON,
    Services,
    arriving_step,
    deviation,
    open_handoff,
    recorder,
    unwire,
    wire,
)
from ..common.turn import run_turn
from .agent import assessment_turn, revision_turn
from .tools import DECISION_TOOLS

# A hand-off's answer carries the checker agent's whole turn, and every step
# of it is a record written to the customer's log store, so a slow bucket
# makes the turn slow without making it wrong. The wait is therefore bounded
# by progress, not by the clock: the case fails when the checker agent has
# written no new record for HANDOFF_STALL_S, and, whatever it writes, after
# HANDOFF_CEILING_S. The underwriting agent asks the checker agent how far it
# has got every HANDOFF_POLL_S.
HANDOFF_STALL_S = 600.0
HANDOFF_CEILING_S = 7200.0
HANDOFF_POLL_S = 15.0
PROGRESS_TIMEOUT_S = 10.0

# A receipt is confirmed at once; it carries no model turn.
RECEIPT_TIMEOUT_S = 60.0


class _Stalled(httpx.ReadTimeout):
    """The checker agent has written no new record for `HANDOFF_STALL_S`."""


class Stop(Exception):
    """The case cannot go on. `reason` says why in plain words, for the page;
    `detail` is the technical text, for the log and the Technical detail;
    `code` is the SDK's reason code for the closing record."""

    def __init__(self, reason: str, code: str = FAILED_REASON, detail: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code
        self.detail = detail


def assessment_message(assessment: Mapping[str, Any], summary: str) -> dict[str, Any]:
    return {"kind": "assessment", "assessment": dict(assessment), "underwriter_summary": summary}


def revision_message(revision: Mapping[str, Any], challenge: Mapping[str, Any], summary: str) -> dict[str, Any]:
    return {
        "kind": "revision",
        "answers_challenge": challenge.get("about"),
        "response": revision.get("response"),
        "recommendation": revision.get("recommendation"),
        "deposit_percent": revision.get("deposit_percent"),
        "conditions": revision.get("conditions"),
        "adjustments": revision.get("adjustments"),
        "rationale": revision.get("rationale"),
        "summary": revision.get("summary"),
        "calculations": revision.get("calculations"),
        "underwriter_summary": summary,
    }


class CaseRun:
    """Runs one case to its end. Never raises: every way a case can end is a
    status and a reason on the case."""

    def __init__(
        self,
        *,
        settings: ServiceSettings,
        services: Services,
        cases: CaseStore,
        checker_url: str,
        case_id: str,
    ) -> None:
        self.settings = settings
        self.services = services
        self.cases = cases
        self.checker_url = checker_url
        self.case_id = case_id
        self.checker_key = settings.counterparty_public_key
        self._events: list[Any] = []
        self.recorder = recorder(services.anchorer, case_id, decisions=DECISION_TOOLS, on_event=self._events.append)
        self.handoff = open_handoff(services.anchorer, case_id)
        self.workspace = cases.workspace(case_id)

    def run(self) -> None:
        try:
            self._run()
        except Stop as stop:
            self._stop(stop.reason, stop.code, stop.detail)
            return
        except Exception as exc:
            self._stop(reason_for(exc), detail=f"the case raised: {without_local_paths(describe(exc))}")
            return
        # Closing records the conclusion; a failure to close is reported without undoing it.
        self._events.clear()
        try:
            self.recorder.close(outcome="completed")
        except RECORDING_FAULTS as exc:
            tell_the_operator(f"case {self.case_id}: closing this agent's record raised: {describe(exc)}")
            return
        closing = [
            e.envelope for e in self._events if e.event_type == "agent.run_closed" and getattr(e, "envelope", None)
        ]
        if closing:
            case = self.cases.get(self.case_id)
            case.sent_for_signoff_at = closing[-1].get("signed_at")
            self.cases.save(case)

    def _run(self) -> None:
        try:
            context = CaseContext.of(self.cases.get(self.case_id))
        except ValueError as exc:
            raise Stop(UNEXPECTED, detail=describe(exc)) from exc
        self._at(ASSESSMENT)
        assessed = self._turn(assessment_turn(self.workspace, context), ASSESSMENT, "its assessment")
        reply, sent_hash = self._hand_over(
            ASSESSMENT, assessment_message(assessed.output, assessed.summary), in_reply_to=None
        )
        step, message, received_hash = self._take(reply, expecting=(CHALLENGE, CONCLUSION), in_reply_to=sent_hash)

        if step == CHALLENGE:
            self._at(REVISION)
            revised = self._turn(
                revision_turn(self.workspace, context, assessment=assessed.output, challenge=message),
                REVISION,
                "its revision",
            )
            reply, sent_hash = self._hand_over(
                REVISION, revision_message(revised.output, message, revised.summary), in_reply_to=received_hash
            )
            step, message, _ = self._take(reply, expecting=(CONCLUSION,), in_reply_to=sent_hash)

        case = self.cases.get(self.case_id)
        case.status = case_states.CONCLUDED
        case.concluded_at = case_states.now()
        case.conclusion = dict(message)
        case.step = None
        self.cases.save(case)

    def _stop(self, reason: str, code: str = FAILED_REASON, detail: str | None = None) -> None:
        case = self.cases.get(self.case_id)
        case.status = case_states.STOPPED
        case.error = reason
        case.error_detail = detail
        case.stopped_at = case_states.now()
        self.cases.save(case)
        tell_the_operator(f"case {self.case_id}: stopped: {detail or reason}")
        # Without `raise_on_gaps`, a lost record comes back for the check below instead of an
        # exception.
        try:
            self.recorder.close(raise_on_gaps=False, outcome="failed", reason=code)
        except RECORDING_FAULTS as exc:
            tell_the_operator(f"case {self.case_id}: closing this agent's record raised: {describe(exc)}")
            return
        if not self.recorder.is_complete:
            tell_the_operator(f"case {self.case_id}: records of this agent are missing from the trail")

    def _at(self, step: str) -> None:
        case = self.cases.get(self.case_id)
        case.step = step
        self.cases.save(case)

    def _turn(self, turn: Any, step: str, owed: str) -> Any:
        self._events.clear()
        result = run_turn(turn, model=self.services.model, recorder=self.recorder)
        if result.status != "complete":
            raise Stop(
                result.message,
                TIMEOUT_REASON if result.timed_out else FAILED_REASON,
                f"the underwriting agent could not finish {owed}: {result.error}",
            )
        signed = [e.envelope for e in self._events if getattr(e, "envelope", None)]
        case = self.cases.get(self.case_id)
        case.turns.append(
            {
                "side": "underwriter",
                "step": step,
                "recorded_at": signed[-1].get("signed_at") if signed else None,
                "key": self.services.anchorer.public_key_hex(),
                "steps": len(signed),
                "output": result.output,
                "summary": result.summary,
            }
        )
        self.cases.save(case)
        return result

    def _post_and_watch(self, payload: dict[str, Any]) -> httpx.Response:
        """Post a hand-off and wait for the checker agent's answer for as long
        as it keeps writing records. Raises `_Stalled` when it writes none for
        `HANDOFF_STALL_S`, and `httpx.ReadTimeout` after `HANDOFF_CEILING_S`.
        A stalled call is left to end on its own; the case does not wait for it."""
        outcome: dict[str, Any] = {}

        def post() -> None:
            try:
                outcome["answer"] = httpx.post(
                    f"{self.checker_url}/cases/{self.case_id}/handoffs", json=payload, timeout=HANDOFF_CEILING_S
                )
            except Exception as exc:  # re-raised on the case's own thread
                outcome["error"] = exc

        call = threading.Thread(target=post, name=f"handoff-{self.case_id}", daemon=True)
        call.start()
        seen: Any = None
        moved_at = time.monotonic()
        while True:
            call.join(HANDOFF_POLL_S)
            if not call.is_alive():
                break
            progress = self._checker_progress()
            if progress is not None and progress != seen:
                seen, moved_at = progress, time.monotonic()
            elif time.monotonic() - moved_at >= HANDOFF_STALL_S:
                raise _Stalled(f"the checker agent wrote no record for {HANDOFF_STALL_S:g} seconds")
        if "error" in outcome:
            raise outcome["error"]
        return outcome["answer"]

    def _checker_progress(self) -> Any:
        """How many records the checker agent has written for this case, or
        None when it does not say. Either way the answer is only a reading: a
        dead or unreachable checker agent simply does not move."""
        try:
            answer = httpx.get(f"{self.checker_url}/cases/{self.case_id}/progress", timeout=PROGRESS_TIMEOUT_S)
            return answer.json().get("progress") if answer.status_code == 200 else None
        except (httpx.HTTPError, ValueError, AttributeError):
            return None

    def _hand_over(self, step: str, message: dict[str, Any], *, in_reply_to: str | None) -> tuple[dict[str, Any], str]:
        """Send one hand-off to the checker agent and confirm its received
        record. Returns the checker agent's answer and the sent message's
        hash."""
        sent = self.handoff.send(
            message=message, to=self.checker_key, content_type=CONTENT_TYPES[step], in_reply_to=in_reply_to
        )
        # The checker agent is working from here.
        self._at(CHALLENGE if step == ASSESSMENT else CONCLUSION)
        try:
            answer = self._post_and_watch(wire(sent))
        except httpx.HTTPError as exc:
            timed_out = isinstance(exc, httpx.TimeoutException)
            raise Stop(
                (CHECKER_DID_NOT_ANSWER if isinstance(exc, _Stalled) else CHECKER_TOOK_TOO_LONG) if timed_out else UNEXPECTED,
                TIMEOUT_REASON if timed_out else FAILED_REASON,
                f"the {step} did not reach the checker agent: {describe(exc)}",
            ) from exc
        body = answer.json() if answer.headers.get("content-type", "").startswith("application/json") else {}
        if answer.status_code != 200:
            raise Stop(
                body.get("message") or UNEXPECTED,
                POLICY_REASON,
                f"the checker agent refused the {step}: {body.get('refused') or answer.text}",
            )

        confirmation = self.handoff.confirm(sent=sent, received_record=body.get("received_record"))
        if not confirmation.confirmed:
            details = "; ".join(reason.detail for reason in confirmation.reasons)
            raise Stop(UNEXPECTED, POLICY_REASON, f"the checker agent did not confirm the {step}: {details}")
        case = self.cases.get(self.case_id)
        case.handoffs.append(
            {
                "step": step,
                "sender": "underwriter",
                "sent_event_id": sent.result.event_id,
                "sent_at": (sent.result.envelope or {}).get("signed_at"),
                "received_at": ((body.get("received_record") or {}).get("envelope") or {}).get("signed_at"),
                "message_hash": sent.message_hash,
                "recorded_by_both": True,
            }
        )
        self.cases.save(case)
        if body.get("error"):
            raise Stop(body["error"], detail=body.get("error_detail"))
        if not isinstance(body.get("reply"), dict):
            raise Stop(UNEXPECTED, detail=f"the checker agent recorded the {step} and sent nothing back")
        return body["reply"], sent.message_hash

    def _take(self, reply: dict[str, Any], *, expecting: tuple[str, ...], in_reply_to: str) -> tuple[str, dict[str, Any], str]:
        """Receive the checker agent's hand-off: decide from its signed
        record whether it is what this agent is waiting for, record it with
        that outcome, and send this side's received record back."""
        try:
            attachment, wire_bytes = unwire(reply)
        except ValueError as exc:
            raise Stop(UNEXPECTED, detail=f"the checker agent's answer was not a hand-off: {describe(exc)}") from exc
        step = arriving_step(attachment)
        if step not in expecting:
            reason = f"this agent was waiting for the {' or '.join(expecting)}, not {step or 'this message'}"
        else:
            reason = deviation(attachment, step=step, in_reply_to=in_reply_to)
        try:
            received = self.handoff.receive(
                attachment=attachment,
                wire_bytes=wire_bytes,
                outcome="rejected" if reason else "accepted",
                expected_sender=self.checker_key,
                # This side's own record says which message the reply
                # answers. Only for a reply it accepts: a refused one may
                # answer something else, and saying otherwise would be false.
                in_reply_to=None if reason else in_reply_to,
            )
        except HandoffUnexpectedSenderError as exc:
            raise Stop(
                UNEXPECTED, POLICY_REASON, f"the answer was signed by {exc.actual_sender}, not the checker agent"
            ) from exc
        except (AnchorError, ValueError) as exc:
            raise Stop(
                reason_for(exc),
                POLICY_REASON,
                f"the checker agent's answer was refused on receipt: {without_local_paths(describe(exc))}",
            ) from exc
        if reason:
            raise Stop(
                OUT_OF_STEP if step not in expecting else UNEXPECTED,
                POLICY_REASON,
                f"the checker agent's answer was refused: {reason}",
            )

        sender_envelope = attachment.get("envelope") if isinstance(attachment.get("envelope"), Mapping) else {}
        case = self.cases.get(self.case_id)
        case.turns.append(
            {
                "side": "checker",
                "step": step,
                "recorded_at": sender_envelope.get("signed_at"),
                "key": received.sender_public_key,
                "steps": None,
                "output": dict(received.message),
                "summary": "",
            }
        )
        case.handoffs.append(
            {
                "step": step,
                "sender": "checker",
                "sent_at": sender_envelope.get("signed_at"),
                "received_event_id": received.result.event_id,
                "received_at": (received.result.envelope or {}).get("signed_at"),
                "message_hash": received.message_hash,
                "recorded_by_both": True,
            }
        )
        self.cases.save(case)

        # The received record goes back so the checker agent can confirm it. A receipt that does
        # not land is noted, not fatal: this agent has already verified and recorded the message.
        try:
            answer = httpx.post(
                f"{self.checker_url}/cases/{self.case_id}/receipts",
                json={"step": step, "received_record": received.received_record},
                timeout=RECEIPT_TIMEOUT_S,
            )
            confirmed = answer.status_code == 200 and answer.json().get("confirmed") is True
        except httpx.HTTPError:
            confirmed = False
        case = self.cases.get(self.case_id)
        case.handoff(step)["confirmed_by_sender"] = confirmed
        self.cases.save(case)
        return step, dict(received.message), received.message_hash
