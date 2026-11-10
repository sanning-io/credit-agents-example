"""Where case documents are kept, and the check every location in a case record
passes before the file it names is read or served. A location must resolve, after
every link, to a file in an application's folder or in the policy folder under the
example's home. `NotKept` says which document failed and never repeats the
location.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import home
from .context import DOCUMENTS


class NotKept(Exception):
    """A case record's locations do not pass. `document` is the case
    document that failed, and `why` one of the reasons below."""

    # The record does not locate the document.
    MISSING = "missing"
    # The operating system cannot resolve the location.
    UNREADABLE = "unreadable"
    # The location, its links followed, is outside the example's home.
    OUTSIDE_HOME = "outside home"
    # Inside the home, but not where case documents are kept.
    ELSEWHERE = "elsewhere"
    # Where case documents are kept, with no file there.
    NOT_ON_DISK = "not on disk"

    def __init__(self, document: str, why: str) -> None:
        super().__init__(f"{document}: {why}")
        self.document = document
        self.why = why


def file_of(location: str) -> Path | None:
    """The file a location names once every link is followed, or `None` for
    one outside the example's home. Raises `OSError` or `ValueError` for a
    location the operating system cannot resolve."""
    root = Path(home.REPO_ROOT).resolve()
    found = (root / location).resolve()
    try:
        found.relative_to(root)
    except ValueError:
        return None
    return found


def _folder(name: Path) -> Path:
    """A folder where case documents are kept, in the example's home. The
    names come from `common/home.py`, where the sample case's live."""
    return (Path(home.REPO_ROOT).resolve() / name).resolve()


def checked(case_documents: Any) -> dict[str, str]:
    """Each case document's location from a case record's `case_documents`, once all
    pass. Raises `NotKept` for the first that does not. An entry that is not a
    document name and a location, both text, is ignored.
    """
    located = {
        entry["document"]: entry["location"]
        for entry in (case_documents if isinstance(case_documents, list) else [])
        if isinstance(entry, dict) and isinstance(entry.get("document"), str) and isinstance(entry.get("location"), str)
    }
    locations: dict[str, str] = {}
    # The application's own folder: the applicant's documents must sit in it.
    application_folder: Path | None = None
    for document in DOCUMENTS:
        location = located.get(document)
        if not location:
            raise NotKept(document, NotKept.MISSING)
        try:
            found = file_of(location)
            on_disk = found is not None and (home.REPO_ROOT / location).is_file()
        except (OSError, ValueError):
            raise NotKept(document, NotKept.UNREADABLE) from None
        if found is None:
            raise NotKept(document, NotKept.OUTSIDE_HOME)
        if document == "application":
            kept = found.parent.parent == _folder(home.SAMPLE_APPLICATION.parent.parent)
            application_folder = found.parent if kept else None
        elif document == "credit_policy":
            kept = found.parent == _folder(home.CREDIT_POLICY.parent)
        else:
            kept = application_folder is not None and found.parent == application_folder
        if not kept:
            raise NotKept(document, NotKept.ELSEWHERE)
        if not on_disk:
            raise NotKept(document, NotKept.NOT_ON_DISK)
        locations[document] = location
    return locations
