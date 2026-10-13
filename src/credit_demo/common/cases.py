"""The credit desk's own record of each case: plain app state, not evidence.

What the pages show about a case (its turns, its hand-offs, where it
stopped, whether its audit trail is ready) is kept here, one JSON file per
case. It is filled from what the SDK hands back as the case runs: the
recorder's `on_event` outcomes, and each hand-off's send, receive and
confirm results. The evidence itself is the signed records, in Sanning and
in each agent's own bucket; nothing here is part of it, and nothing here is
read to verify anything.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUNNING = "running"
CONCLUDED = "concluded"
STOPPED = "stopped"

# The audit trail's states.
NOT_STARTED = "not_started"
PREPARING = "preparing"
READY = "ready"
FAILED = "failed"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# What `new_case_id` makes: when the case opened, then four hex digits.
CASE_ID = re.compile(r"\d{8}T\d{12}Z-[0-9a-f]{4}")


def valid_case_id(value: object) -> bool:
    return isinstance(value, str) and CASE_ID.fullmatch(value) is not None


def new_case_id() -> str:
    """Sortable by when the case opened, unique by its suffix."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}-{secrets.token_hex(2)}"


@dataclass
class Case:
    id: str
    opened_at: str = field(default_factory=now)
    status: str = RUNNING
    step: str | None = None
    error: str | None = None
    stopped_at: str | None = None
    concluded_at: str | None = None
    # One entry per agent turn: side, step, recorded_at, key, steps, output.
    turns: list[dict[str, Any]] = field(default_factory=list)
    # One entry per hand-off: step, sender, sent/received event ids and
    # times, message hash, and whether both sides recorded it.
    handoffs: list[dict[str, Any]] = field(default_factory=list)
    conclusion: dict[str, Any] | None = None
    # When the underwriting agent's closing record, which says the case went
    # to a credit officer for sign-off, was signed.
    sent_for_signoff_at: str | None = None
    audit_trail_state: str = NOT_STARTED
    audit_trail_error: str | None = None
    # Noted when the case opens: the application it was opened on, and the
    # four case documents with their locations relative to the home
    # (`credit_demo.common.application.opening`). Both agents' runtime
    # context is built from these: the checker agent's from the desk's answer
    # to `GET /cases/{id}/case-documents`.
    application_number: str | None = None
    case_documents: list[dict[str, str]] = field(default_factory=list)

    def turn(self, side: str, step: str) -> dict[str, Any] | None:
        return next((t for t in self.turns if t["side"] == side and t["step"] == step), None)

    def handoff(self, step: str) -> dict[str, Any] | None:
        return next((h for h in self.handoffs if h["step"] == step), None)


class CaseStore:
    """Every case this service has, one JSON file each under `root`."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def folder(self, case_id: str) -> Path:
        if not valid_case_id(case_id):
            raise ValueError("not a case id")
        return self.root / case_id

    def workspace(self, case_id: str) -> Path:
        path = self.folder(case_id) / "work"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create(
        self,
        case_id: str | None = None,
        *,
        application_number: str | None = None,
        case_documents: list[dict[str, str]] | None = None,
    ) -> Case:
        case = Case(
            id=case_id or new_case_id(),
            application_number=application_number,
            case_documents=list(case_documents or []),
        )
        self.save(case)
        return case

    def get(self, case_id: str) -> Case | None:
        if not valid_case_id(case_id):
            return None
        path = self.folder(case_id) / "case.json"
        if not path.is_file():
            return None
        return Case(**json.loads(path.read_text()))

    def save(self, case: Case) -> None:
        folder = self.folder(case.id)
        folder.mkdir(parents=True, exist_ok=True)
        with self._lock:
            temp = folder / "case.json.tmp"
            temp.write_text(json.dumps(asdict(case), indent=2))
            temp.replace(folder / "case.json")

    def all(self) -> list[Case]:
        cases = []
        for path in sorted(self.root.glob("*/case.json"), reverse=True):
            try:
                cases.append(Case(**json.loads(path.read_text())))
            except (ValueError, TypeError):
                continue
        return cases
