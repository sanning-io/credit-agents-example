"""A stand-in credit desk, for a checker agent tested without the real one.

The checker agent asks the credit desk for a case's documents at the start
of each turn (`credit_demo.checker.case_documents`). A test that drives the
checker on its own points it here: a desk that answers every case with the
sample application's case documents, as the real desk notes them when it
opens a case, or with whatever answer the test gives it.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from credit_demo.common import application, home
from tests.servers import serving


def sample_answer(case_id: str) -> dict[str, Any]:
    """What the real desk answers for a case opened on the sample
    application."""
    return {"case_id": case_id, **application.opening(home.SAMPLE_APPLICATION)}


def stand_in_desk(
    answer: Callable[[str], Any] = sample_answer, *, status: int = 200, asked: list[str] | None = None
) -> FastAPI:
    """A desk answering `GET /cases/{id}/case-documents` with `answer(id)`
    and `status`. Each case id it is asked for goes on `asked`."""
    desk = FastAPI()

    @desk.get("/cases/{case_id}/case-documents")
    def case_documents(case_id: str) -> JSONResponse:
        if asked is not None:
            asked.append(case_id)
        return JSONResponse(status_code=status, content=answer(case_id))

    return desk


_sample: dict[str, str] = {}
_sample_guard = threading.Lock()


def sample_desk() -> str:
    """The URL of one stand-in desk for this whole test run, answering every
    case with the sample case documents."""
    with _sample_guard:
        if "url" not in _sample:
            served = serving(stand_in_desk())
            _sample["url"] = served.__enter__()
            # Kept for the run; the server thread is a daemon.
            _sample["served"] = served  # type: ignore[assignment]
        return _sample["url"]
