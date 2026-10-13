"""The credit desk: what the lender's own app shows about each case.

Bruksvik's credit officer reads a case the way they read any application:
who is asking for how much, what the agents concluded, how they got there,
and the audit trail to hand over. Sanning shows only as the "Recorded" marks
and the export. This module turns the desk's own record of a case
(`credit_demo.common.cases`) into that view, as plain values; the templates
only print them, escaped.

**Every sentence here is built from what a record holds, never invented.**
A figure on a card comes off the signed message or file it describes: the
assessment the underwriter wrote, the challenge and the conclusion the
checker signed and the underwriter verified, the revision the underwriter
sent. Where a value is missing the card says less, never a default.

**Every "Recorded" time is the record's own.** It is the `signed_at` inside
the signed envelope, the time the agent declared when it signed. It is not
this service's clock and not a public-record time.

**A stopped case says where and why in plain words**, with the full reason
beside it as technical detail.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from ..common import application as application_file
from ..common import cases as case_states
from ..common import home
from ..common.cases import Case
from ..common.sanning import ASSESSMENT, CHALLENGE, CONCLUSION, REVISION

UNDERWRITER = "underwriter"
CHECKER = "checker"
AGENT_NAME = {UNDERWRITER: "underwriting agent", CHECKER: "checker agent"}

SIGNOFF = "signoff"

# The four turns of a case, in order, with who takes each and the file that
# holds what the turn produced.
TURNS = (
    {"key": "assessment", "number": 1, "side": UNDERWRITER, "title": "Assessment", "output": "assessment.json"},
    {"key": "challenge", "number": 2, "side": CHECKER, "title": "Challenge", "output": "review.json"},
    {"key": "revision", "number": 3, "side": UNDERWRITER, "title": "Revision", "output": "revision.json"},
    {"key": "conclusion", "number": 4, "side": CHECKER, "title": "Conclusion", "output": "conclusion.json"},
)

# Which hand-off follows which turn, who sends it and the phrase for it.
HANDOFFS = {
    ASSESSMENT: {"after": "assessment", "sender": UNDERWRITER, "receiver": CHECKER, "noun": "assessment"},
    CHALLENGE: {"after": "challenge", "sender": CHECKER, "receiver": UNDERWRITER, "noun": "challenge"},
    REVISION: {"after": "revision", "sender": UNDERWRITER, "receiver": CHECKER, "noun": "revision"},
    CONCLUSION: {"after": "conclusion", "sender": CHECKER, "receiver": UNDERWRITER, "noun": "conclusion"},
}

# What the case is doing now, by the last turn it reached. The words the
# officer reads on a running case.
WORKING = {
    1: "Underwriting agent is assessing the application",
    2: "Checker agent is reviewing the assessment",
    3: "Underwriting agent is revising its assessment",
    4: "Checker agent is concluding",
}

VERDICTS = {
    "approve": "Approve",
    "approve_with_conditions": "Approve with conditions",
    "decline": "Decline",
    "refer": "Refer",
}


# ── Small formatting ──────────────────────────────────────────────────────


def verdict_label(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return "Not stated"
    return VERDICTS.get(value, value.replace("_", " ").capitalize())


def money(amount: int) -> str:
    return f"SEK {amount:,}"


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def clock(value: Any) -> str | None:
    """HH:MM:SS, UTC, off a signed time."""
    moment = _when(value)
    return moment.strftime("%H:%M:%S") if moment else None


def day_time(value: Any) -> str | None:
    """25 Sep 2026, 08:59 UTC."""
    moment = _when(value)
    return f"{moment.day} {moment.strftime('%b %Y, %H:%M')} UTC" if moment else None


def short_day_time(value: Any) -> str | None:
    moment = _when(value)
    return f"{moment.day} {moment.strftime('%b %H:%M')}" if moment else None


def short_key(key: Any) -> str | None:
    if not isinstance(key, str) or len(key) < 12:
        return None
    return f"{key[:4]}…{key[-4:]}"


def _conditions(value: Any) -> list[str]:
    """A message's conditions as a list of text, whatever shape arrived."""
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


# ── Reading the case ──────────────────────────────────────────────────────


def _handoff_view(case: Case, step: str) -> dict[str, Any] | None:
    """One hand-off, once both sides recorded it: when it was sent and
    received, and the message both committed to. While the case runs, a
    turn this agent finished is on its way: the line shows as sent, not yet
    recorded by both, until the checker agent answers."""
    meta = HANDOFFS[step]
    label = f"{meta['noun'].capitalize()} sent to the {AGENT_NAME[meta['receiver']]}"
    handoff = case.handoff(step)
    if handoff is None:
        if case.status == case_states.RUNNING and case.turn(meta["sender"], step) is not None:
            return {"step": step, "label": label, "recorded": False}
        return None
    return {
        "step": step,
        "label": label,
        "recorded": bool(handoff.get("recorded_by_both")),
        "sent_at": clock(handoff.get("sent_at")),
        "received_at": clock(handoff.get("received_at")),
        "sender": AGENT_NAME[meta["sender"]],
        "receiver": AGENT_NAME[meta["receiver"]],
        "message_hash": short_key(handoff.get("message_hash")),
    }


def _document_location(case: Case, document: str) -> str | None:
    location = next(
        (d.get("location") for d in case.case_documents or [] if isinstance(d, Mapping) and d.get("document") == document),
        None,
    )
    return location if isinstance(location, str) else None


def _application(case: Case) -> Mapping[str, Any]:
    """The application the case was opened on, read where the case record
    says it is. A case with none says less, never a default."""
    location = _document_location(case, "application")
    if location is None:
        return {}
    try:
        return application_file.read(home.REPO_ROOT / location)
    except (OSError, ValueError):
        return {}


def received(value: Any) -> str | None:
    """30 Sep 2026, off the application's received date."""
    moment = _when(value)
    return f"{moment.day} {moment.strftime('%b %Y')}" if moment else None


def _text(mapping: Any, key: str) -> str:
    value = mapping.get(key) if isinstance(mapping, Mapping) else None
    return value if isinstance(value, str) else ""


# What the case documents panel prints beneath a document's title.
IN_FORCE = "In force when this case opened"


def _case_documents(case: Case, application: Mapping[str, Any]) -> list[dict[str, str]]:
    """The panel's rows, in the order the case record lists them. The
    application opens as a screen in the desk, the rest by file name."""
    rows = []
    for entry in case.case_documents or []:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("location"), str):
            continue
        kind = entry.get("document")
        row = {"title": str(entry.get("title") or ""), "note": "", "href": f"/cases/{case.id}/documents/{kind}"}
        if kind == "application":
            number = _text(application, "application_number") or (case.application_number or "")
            when = received(application.get("received"))
            row["title"] = "Application"
            row["note"] = " · ".join(part for part in (number, f"received {when}" if when else "") if part)
            row["href"] = f"/cases/{case.id}/application"
        elif kind == "credit_policy":
            row["note"] = IN_FORCE
        rows.append(row)
    return rows


def _applicant(application: Mapping[str, Any]) -> str:
    applicant = application.get("applicant")
    name = applicant.get("name") if isinstance(applicant, Mapping) else None
    return name if isinstance(name, str) else ""


def _facility(application: Mapping[str, Any], *, with_term: bool) -> str:
    requested = application.get("financing_requested")
    if not isinstance(requested, Mapping):
        return ""
    parts = [requested.get("product")]
    if isinstance(requested.get("amount_sek"), int):
        parts.append(money(requested["amount_sek"]))
    if with_term and isinstance(requested.get("term_months"), int):
        parts.append(f"{requested['term_months']} months")
    parts = [part for part in parts if isinstance(part, str) and part]
    return " · ".join(parts)


def _output(case: Case, side: str, step: str) -> Mapping[str, Any]:
    turn = case.turn(side, step)
    output = turn.get("output") if turn else None
    return output if isinstance(output, Mapping) else {}


# ── The turns ─────────────────────────────────────────────────────────────


def _summary(output: Mapping[str, Any]) -> list[str]:
    """The agent's own one-sentence summary of its turn, as the card's line.
    A turn with no summary has no line."""
    own = output.get("summary")
    return [own.strip()] if isinstance(own, str) and own.strip() else []


def _assessment_card(assessment: Mapping[str, Any]) -> dict[str, Any] | None:
    if not assessment:
        return None
    return {
        "lines": _summary(assessment),
        "result": f"Recommends: {verdict_label(assessment.get('recommendation')).lower()}",
    }


def _challenge_card(challenge: Mapping[str, Any]) -> dict[str, Any] | None:
    if not challenge:
        return None
    about = challenge.get("about")
    return {
        "lines": _summary(challenge),
        "result": f"Challenges: {about if isinstance(about, str) and about else 'the assessment'}",
    }


def _revision_card(revision: Mapping[str, Any]) -> dict[str, Any] | None:
    if not revision:
        return None
    how = "Maintains its assessment" if revision.get("response") == "maintained" else "Revises its assessment"
    return {"lines": _summary(revision), "result": f"{how}: {verdict_label(revision.get('recommendation')).lower()}"}


def _conclusion_card(conclusion: Mapping[str, Any]) -> dict[str, Any] | None:
    if not conclusion.get("verdict"):
        return None
    result = (
        "Refers the case to a senior credit officer"
        if conclusion.get("verdict") == "refer"
        else f"Concludes: {verdict_label(conclusion.get('verdict')).lower()}"
    )
    return {"lines": _summary(conclusion), "result": result, "conditions": _conditions(conclusion.get("conditions"))}


def _agreed(case: Case) -> bool:
    """The checker agent concluded on the assessment, with no challenge."""
    return case.handoff(CONCLUSION) is not None and case.handoff(CHALLENGE) is None


def _turns(case: Case) -> list[dict[str, Any]]:
    """The turns of the case that have started, in order: done, or in
    progress. A turn that has not started is not shown: what comes next is
    the agents' call, since the checker agent may challenge or agree. A case
    the checker agent agreed with has no challenge or revision."""
    outputs = {turn["key"]: _output(case, turn["side"], turn["key"]) for turn in TURNS}
    cards = {
        "assessment": _assessment_card(outputs["assessment"]),
        "challenge": _challenge_card(outputs["challenge"]),
        "revision": _revision_card(outputs["revision"]),
        "conclusion": _conclusion_card(outputs["conclusion"]),
    }
    skipped = {"challenge", "revision"} if _agreed(case) else set()
    turns = []
    current_set = False
    for turn in TURNS:
        key = turn["key"]
        if key in skipped:
            continue
        recorded = case.turn(turn["side"], key)
        view = {
            "number": turn["number"],
            "key": key,
            "agent": AGENT_NAME[turn["side"]].capitalize(),
            "title": turn["title"],
            "card": cards[key],
            "recorded_at": clock(recorded.get("recorded_at")) if recorded else None,
            "signer_key": short_key(recorded.get("key")) if recorded else None,
            "steps": recorded.get("steps") if recorded else None,
            "handoff": _handoff_view(case, key),
        }
        if recorded is not None and cards[key] is not None:
            view["state"] = "done"
        elif case.status == case_states.RUNNING and not current_set:
            view["state"] = "current"
            current_set = True
            # Until the checker agent answers, nobody knows whether its
            # answer is a challenge or agreement.
            if key == "challenge":
                view["title"] = "Review"
        else:
            continue
        turns.append(view)
    if case.status == case_states.CONCLUDED and case.sent_for_signoff_at:
        turns.append(_signoff_step(case))
    return turns


def _referred(case: Case) -> bool:
    return (case.conclusion or {}).get("verdict") == "refer"


def _unresolved(case: Case) -> bool:
    """The agents still disagreed at the end: the underwriting agent
    maintained its assessment against the challenge, and the checker agent
    referred the case rather than accept it. A referral the agents agreed on
    is a referral, not a disagreement."""
    revision = _output(case, UNDERWRITER, "revision")
    return _referred(case) and revision.get("response") == "maintained"


def _signoff_step(case: Case) -> dict[str, Any]:
    """The last step of a concluded case: the underwriting agent closed its
    record, stating it sent the case to a credit officer for sign-off, or
    referred it to a senior credit officer when the checker agent referred
    it."""
    underwriter = next((t for t in case.turns if t.get("side") == UNDERWRITER), {})
    referred = _referred(case)
    unresolved = _unresolved(case)
    return {
        "number": 5,
        "key": SIGNOFF,
        "agent": AGENT_NAME[UNDERWRITER].capitalize(),
        "title": "Referred to a senior credit officer" if referred else "Sent for sign-off",
        "card": {
            "lines": [
                "Handed to a senior credit officer, with both agents' views."
                if unresolved
                else "Handed to a senior credit officer to decide."
                if referred
                else "Handed to a credit officer for sign-off."
            ],
            "result": "Waiting for a senior credit officer" if referred else "Waiting for sign-off",
        },
        "recorded_at": clock(case.sent_for_signoff_at),
        "signer_key": short_key(underwriter.get("key")),
        "steps": None,
        "handoff": None,
        "state": "done",
    }


# ── Stopped cases ─────────────────────────────────────────────────────────


def stopped(case: Case) -> dict[str, str] | None:
    """Where and why a case stopped. The reason's first clause is the title;
    the whole reason is the technical detail."""
    if case.status != case_states.STOPPED:
        return None
    detail = case.error or "The case stopped and gave no reason."
    title = detail.split(":", 1)[0].strip()
    title = title[0].upper() + title[1:] if title else "The case stopped"
    number = next((t["number"] for t in TURNS if t["key"] == case.step), None)
    where = f"Stopped at step {number}" if number else "Stopped"
    return {
        "where": where,
        "title": title,
        "line": "The reason is in the technical detail.",
        "detail": detail,
        "status": f"{where} · {title[0].lower()}{title[1:]}",
    }


# ── The audit trail ───────────────────────────────────────────────────────


def export_state(case: Case) -> dict[str, str]:
    """The export button, in its four states: working, preparing, ready,
    could not prepare."""
    if case.status == case_states.RUNNING:
        return {"state": "working", "note": "Available when the agents finish"}
    if case.status != case_states.CONCLUDED:
        return {"state": "none"}
    if case.audit_trail_state == case_states.READY:
        return {"state": "ready", "note": "Both agents' signed records"}
    if case.audit_trail_state == case_states.FAILED:
        return {"state": "failed", "note": case.audit_trail_error or "The audit trail could not be prepared."}
    return {"state": "preparing", "note": "The records are being sealed. This takes a few minutes."}


# ── The two pages ─────────────────────────────────────────────────────────


def _status(case: Case, turns: Sequence[Mapping[str, Any]]) -> str:
    stop = stopped(case)
    if stop:
        return stop["status"]
    if case.status == case_states.RUNNING:
        current = next((t for t in turns if t["state"] == "current"), None)
        at = turns.index(current) + 1 if current else len(turns)
        return f"Agents working · step {at}"
    export = export_state(case)
    waiting = "Referred to a senior credit officer" if _referred(case) else "Waiting for sign-off"
    return {
        "ready": f"{waiting} · audit trail ready",
        "failed": f"{waiting} · audit trail could not be prepared",
    }.get(export["state"], f"{waiting} · preparing audit trail")


def _conclusion_box(case: Case, turns: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    conclusion = case.conclusion or {}
    if case.status != case_states.CONCLUDED or not conclusion.get("verdict"):
        return None
    handoff = case.handoff(CONCLUSION) or {}
    conditions = _conditions(conclusion.get("conditions"))
    referred = _referred(case)
    unresolved = _unresolved(case)
    return {
        "label": (
            "The agents did not agree · referred"
            if unresolved
            else "Agents' recommendation · referred"
            if referred
            else "Agents' recommendation · waiting for sign-off"
        ),
        "verdict": "Referred to a senior credit officer" if referred else verdict_label(conclusion.get("verdict")),
        "conditions": (
            "Both agents' views are on the record"
            if unresolved
            else "A senior credit officer decides"
            if referred
            else (" · ".join(conditions) if conditions else "No conditions")
        ),
        "reached": day_time(handoff.get("sent_at")),
        "challenges": (
            "no challenge"
            if _agreed(case)
            else ("1 challenge, not resolved" if unresolved else "1 challenge, resolved")
        ),
        "recorded": bool(handoff.get("recorded_by_both")),
    }


def case_view(case: Case) -> dict[str, Any]:
    turns = _turns(case)
    current = next((t for t in turns if t["state"] == "current"), None)
    running = case.status == case_states.RUNNING
    application = _application(case)
    return {
        "id": case.id,
        "applicant": _applicant(application) or f"Case {case.id[-4:]}",
        "facility": _facility(application, with_term=True),
        "documents": _case_documents(case, application),
        "running": running,
        "working": {
            "step": (turns.index(current) + 1) if current else None,
            "words": WORKING.get(current["number"]) if current else None,
            "started": day_time(case.opened_at),
        } if running else None,
        "conclusion": _conclusion_box(case, turns),
        "stopped": stopped(case),
        "turns": turns,
        "agreed": _agreed(case),
        "export": export_state(case),
        "opened": clock(case.opened_at),
        "concluded": clock((case.handoff(CONCLUSION) or {}).get("sent_at")),
    }


def application_row(case: Case) -> dict[str, Any]:
    turns = _turns(case)
    box = _conclusion_box(case, turns)
    application = _application(case)
    return {
        "id": case.id,
        "applicant": _applicant(application),
        "application_number": case.application_number or _text(application, "application_number"),
        "facility": _facility(application, with_term=False),
        "conclusion": box["verdict"] if box else None,
        "status": _status(case, turns),
        "opened": short_day_time(case.opened_at),
        "live": case.status == case_states.RUNNING or export_state(case)["state"] == "preparing",
    }


def application_view(case: Case, all_cases: Sequence[Case]) -> dict[str, Any] | None:
    """The application screen, opened from `case`: the application's own
    fields, the applicant's documents it lists, and every case whose record
    names this application. None for a case with no application."""
    application = _application(case)
    location = _document_location(case, "application")
    if not application or location is None:
        return None
    applicant = application.get("applicant")
    requested = application.get("financing_requested")
    requested = requested if isinstance(requested, Mapping) else {}
    amount, term, rate = requested.get("amount_sek"), requested.get("term_months"), requested.get("nominal_annual_rate_percent")
    number = _text(application, "application_number")
    documents = [
        {"title": _text(d, "title"), "href": f"/cases/{case.id}/documents/{_text(d, 'document')}"}
        for d in application.get("applicants_documents") or []
        if isinstance(d, Mapping) and _text(d, "file") and _text(d, "document")
    ]
    return {
        "number": number,
        "received": received(application.get("received")),
        "applicant": _applicant(application),
        "registration_number": _text(applicant, "registration_number"),
        "product": _text(requested, "product"),
        "amount": money(amount) if isinstance(amount, int) else "",
        "term": f"{term} months" if isinstance(term, int) else "",
        "rate": f"{float(rate)}% a year, nominal" if isinstance(rate, (int, float)) and not isinstance(rate, bool) else "",
        "documents": documents,
        "from_case": case.id,
        "cases": [
            {"id": other.id, "short": other.id[-4:]}
            for other in all_cases
            if number and other.application_number == number
        ],
    }
