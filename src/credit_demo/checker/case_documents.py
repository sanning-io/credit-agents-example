"""Where the checker agent learns a case's documents: it asks the credit desk over
`DESK_URL` at the start of each turn, and never takes a location from a hand-off.
A location arrives over the network, so it passes
`credit_demo.common.case_locations` before anything reads it. A case whose
documents cannot be got is not worked, and the reason never repeats a refused
location.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from ..common.case_locations import NotKept, checked
from ..common.context import CaseContext
from ..common.errors import describe

DESK_URL_VARIABLE = "DESK_URL"
# The desk answers from its own case record, with no model turn behind it.
DESK_TIMEOUT_S = 30.0


# What the checker agent accepts as an application number, because the number
# goes into its task: 1 to 32 letters, digits and hyphens, and nothing else.
APPLICATION_NUMBER = re.compile(r"[A-Za-z0-9-]{1,32}")


def default_desk_url() -> str:
    url = os.environ.get(DESK_URL_VARIABLE, "")
    if not url:
        raise ValueError(f"{DESK_URL_VARIABLE} is not set: it is the underwriter's private address")
    return url.rstrip("/")


class CaseDocumentsUnavailable(Exception):
    """The checker agent could not get a case's documents. `reason` says
    why, for the case."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# Why the checker agent will not work from the desk's record, for each way a
# location can fail the check. None repeats the location.
_REASONS = {
    NotKept.MISSING: "the credit desk's record of the case does not locate the {document}",
    NotKept.UNREADABLE: "the credit desk located the {document} somewhere the checker agent cannot read",
    NotKept.OUTSIDE_HOME: (
        "the credit desk located the {document} outside the example's home, and the checker agent "
        "reads nothing there"
    ),
    NotKept.ELSEWHERE: (
        "the credit desk located the {document} somewhere other than where case documents are kept, "
        "and the checker agent reads nothing there"
    ),
    NotKept.NOT_ON_DISK: "the {document} is not where the credit desk says it is",
}


def case_context(desk_url: str, case_id: str) -> CaseContext:
    """The context for one turn on `case_id`, from the credit desk's answer.
    Raises `CaseDocumentsUnavailable` for an answer it cannot work from."""
    try:
        answer = httpx.get(f"{desk_url.rstrip('/')}/cases/{case_id}/case-documents", timeout=DESK_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise CaseDocumentsUnavailable(f"the credit desk did not answer: {describe(exc)}") from exc
    if answer.status_code != 200:
        raise CaseDocumentsUnavailable(f"the credit desk answered {answer.status_code}")
    try:
        body: Any = answer.json()
    except ValueError as exc:
        raise CaseDocumentsUnavailable("the credit desk's answer was not JSON") from exc
    body = body if isinstance(body, dict) else {}

    number = body.get("application_number")
    if not isinstance(number, str) or not number:
        raise CaseDocumentsUnavailable("the credit desk's record of the case names no application")
    if not APPLICATION_NUMBER.fullmatch(number):
        raise CaseDocumentsUnavailable(
            "the credit desk's application number is not 1 to 32 letters, digits and hyphens"
        )
    try:
        locations = checked(body.get("case_documents"))
    except NotKept as refused:
        raise CaseDocumentsUnavailable(_REASONS[refused.why].format(document=refused.document)) from None
    return CaseContext(case_id=case_id, application_number=number, **locations)
