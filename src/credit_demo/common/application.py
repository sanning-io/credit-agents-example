"""The application: an applicant's request for financing, as one JSON file.

It holds who is asking (the applicant's name and registration number), what
they ask for (the product, the amount, the term and the nominal rate), and
the applicant's documents, each by its file name in the application's own
folder. The folder stands in for the lender's document store: the agents
read the files where they are, and nothing is copied when a case opens.

The terms of the financing requested are read from here, so every figure the
agents compute rests on a file the record fingerprints.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from . import home
from .errors import describe


@dataclass(frozen=True)
class Terms:
    """The financing the application requests, as cover arithmetic takes it."""

    amount_sek: int
    term_months: int
    nominal_annual_rate_percent: float

    @property
    def monthly_rate(self) -> float:
        return self.nominal_annual_rate_percent / 100 / 12


def read(path: Path | str) -> dict[str, Any]:
    """The application file's content."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def terms(application: Mapping[str, Any]) -> Terms:
    """The terms the application requests. A missing or malformed term raises
    rather than falling back to a number nobody can see."""
    requested = application.get("financing_requested")
    if not isinstance(requested, Mapping):
        raise ValueError("application: no financing requested")
    try:
        return Terms(
            amount_sek=int(requested["amount_sek"]),
            term_months=int(requested["term_months"]),
            nominal_annual_rate_percent=float(requested["nominal_annual_rate_percent"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"application: the financing requested is incomplete ({describe(exc)})") from exc


def case_documents(location: Path | str, *, credit_policy: Path | str = home.CREDIT_POLICY) -> list[dict[str, str]]:
    """The four case documents of a case opened on the application at
    `location` (relative to the home): the application, the applicant's
    documents it lists, and the credit policy in force. Each is named, titled
    and located relative to the home, the same form file records use."""
    location = PurePosixPath(Path(location).as_posix())
    application = read(home.REPO_ROOT / location)
    documents = [
        {
            "document": "application",
            "title": f"Application {application['application_number']}",
            "location": str(location),
        }
    ]
    for listed in application.get("applicants_documents") or []:
        documents.append(
            {
                "document": listed["document"],
                "title": listed["title"],
                "location": str(location.parent / listed["file"]),
            }
        )
    documents.append(
        {
            "document": "credit_policy",
            "title": "Credit policy",
            "location": PurePosixPath(Path(credit_policy).as_posix()).as_posix(),
        }
    )
    return documents


def opening(location: Path | str) -> dict[str, Any]:
    """What the credit desk notes when it opens a case on the application at
    `location`: the application number and the four case documents. Desk
    state, not evidence: nothing is signed for it."""
    application = read(home.REPO_ROOT / Path(location))
    return {
        "application_number": application["application_number"],
        "case_documents": case_documents(location),
    }
