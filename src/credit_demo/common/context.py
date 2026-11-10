"""The case an agent works, as LangChain's runtime context: the case id, each case
document's location relative to the example's home, the folder the turn writes to
and the challenge a turn answers. It carries no value a decision uses, and
LangChain keeps it out of the tool's schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import home

DOCUMENTS = ("application", "bank_statements", "annual_report", "credit_policy")


@dataclass(frozen=True)
class CaseContext:
    case_id: str
    application_number: str
    application: str
    bank_statements: str
    annual_report: str
    credit_policy: str
    workspace: Path | None = None
    challenge: Mapping[str, Any] | None = None

    def path(self, document: str) -> Path:
        """Where one case document is on this machine."""
        if document not in DOCUMENTS:
            raise ValueError(f"not a case document: {document!r}")
        return home.REPO_ROOT / getattr(self, document)

    def output(self, filename: str) -> Path:
        """Where the turn writes one file it owes."""
        if self.workspace is None:
            raise ValueError("this turn has no workspace to write to")
        return self.workspace / filename

    @classmethod
    def of(cls, case: Any) -> "CaseContext":
        """The context for a case, from the credit desk's record of it
        (`credit_demo.common.cases.Case`). A case opened without its case
        documents raises: there is nothing to work it from."""
        located = {
            entry.get("document"): entry.get("location")
            for entry in (case.case_documents or [])
            if isinstance(entry, dict)
        }
        missing = [name for name in DOCUMENTS if not isinstance(located.get(name), str)]
        if missing or not case.application_number:
            raise ValueError(
                "the case was opened without its case documents: "
                + ", ".join(missing or ["the application number"])
            )
        return cls(
            case_id=case.id,
            application_number=case.application_number,
            **{name: located[name] for name in DOCUMENTS},
        )
