"""The case an agent works, as LangChain's runtime context.

A turn is run with `create_agent(context_schema=CaseContext)` and
`invoke(..., context=...)`, and a tool reads it through `ToolRuntime`. It
says which case this is and where each case document is, relative to the
example's home (the same form a file record uses), and it carries what else
is fixed for the turn: the folder the turn writes to and, for a turn that
answers one, the challenge. It never carries a value the decision uses: every
figure still comes from a file a tool reads, and the record fingerprints that
read. The challenge's `about` is copied into the file the turn writes, and
the model reads the challenge itself in its task.

LangChain keeps the runtime out of the tool's schema, so the model never
sees it, and out of the input the recorder records for a tool call
(`tests/test_a_case_opens_from_an_application.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import home

# The case documents a context locates, by the names the case record uses.
DOCUMENTS = ("application", "bank_statements", "annual_report", "credit_policy")


@dataclass(frozen=True)
class CaseContext:
    case_id: str
    application_number: str
    # Each case document's location, relative to the example's home.
    application: str
    bank_statements: str
    annual_report: str
    credit_policy: str
    # Where the turn writes the file it owes. Set when the turn is built.
    workspace: Path | None = None
    # The challenge a turn answers, as the checker agent sent it.
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
