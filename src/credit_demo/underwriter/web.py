"""The underwriting agent's service and the credit desk: a credit officer runs the sample case and
follows it here.

No route takes credentials. Case documents are served only from where the case record names
them, and only from where case documents are kept.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..common import application
from ..common import cases as case_states
from ..common import case_locations, home
from ..common.cases import CaseStore
from ..common.config import ServiceSettings
from ..common.errors import (
    configure_logging,
    describe,
    tell_the_operator,
    tell_the_operator_a_request_raised,
    without_local_paths,
)
from ..common.messages import reason_for
from ..common.sanning import Services, closing_at_shutdown, require_cases_under_home, services_when_needed
from . import desk
from .case import CaseRun
from .config import load as load_settings
from .audit_trail import audit_trail_path, fail, prepare

configure_logging("uvicorn", "uvicorn.error", "uvicorn.access")

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def default_checker_url() -> str:
    url = os.environ.get("CHECKER_URL", "")
    if not url:
        raise ValueError("CHECKER_URL is not set: it is the checker's private address")
    return url.rstrip("/")


def default_evidence_builder_url() -> str:
    url = os.environ.get("EVIDENCE_BUILDER_URL", "")
    if not url:
        raise ValueError("EVIDENCE_BUILDER_URL is not set: it is the evidence builder's private address")
    return url.rstrip("/")


def create_app(
    settings: ServiceSettings | None = None,
    *,
    services: Services | None = None,
    checker_url: str | None = None,
    evidence_builder_url: str | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    require_cases_under_home(settings)
    get_services = services_when_needed(settings, services)
    checker_url = (checker_url or default_checker_url()).rstrip("/")
    evidence_builder_url = (evidence_builder_url or default_evidence_builder_url()).rstrip("/")
    cases = CaseStore(settings.runs_dir)
    # The example runs as one copy per cases folder.
    claimed = cases.claim()
    if claimed is None:
        raise ValueError(
            f"another copy of this service is already using the folder {settings.variable('RUNS_DIR')} names, "
            "and a second copy would pick up its half-built audit trails. Stop the other copy, "
            f"or set {settings.variable('RUNS_DIR')} to a different folder"
        )

    app = FastAPI(title="credit-demo credit desk", lifespan=closing_at_shutdown(get_services))
    app.state.cases = cases
    app.state.claimed = claimed

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        tell_the_operator_a_request_raised("underwriter", request.method, request.url.path, exc)
        return JSONResponse(status_code=500, content={"detail": without_local_paths(exc)})

    def in_background(target, *args) -> None:
        # Named for the work and the case, so a test can wait for a case's threads.
        threading.Thread(target=target, args=args, name=f"{target.__name__}:{args[0]}", daemon=True).start()

    def stop(case_id: str, reason: str, detail: str) -> None:
        case = cases.get(case_id)
        case.status = case_states.STOPPED
        case.error = reason
        case.error_detail = detail
        case.stopped_at = case_states.now()
        cases.save(case)

    def run_case(case_id: str) -> None:
        """Run one case in the background. Any failure stops the case with the reason."""
        try:
            CaseRun(
                settings=settings, services=get_services(), cases=cases, checker_url=checker_url, case_id=case_id
            ).run()
        except Exception as exc:
            tell_the_operator(f"case {case_id}: stopped: {describe(exc)}")
            stop(case_id, reason_for(exc), describe(exc))
            return
        if cases.get(case_id).status == case_states.CONCLUDED:
            if claim(case_id):
                prepare_file(case_id)

    # One audit-trail preparation per case at a time.
    preparing: set[str] = set()
    preparing_guard = threading.Lock()

    def claim(case_id: str) -> bool:
        with preparing_guard:
            if case_id in preparing:
                return False
            preparing.add(case_id)
            return True

    def prepare_file(case_id: str) -> None:
        """Prepare the audit trail for a case already claimed."""
        try:
            prepare(cases=cases, case_id=case_id, evidence_builder_url=evidence_builder_url)
        except Exception as exc:
            fail(cases, case_id, exc)
        finally:
            with preparing_guard:
                preparing.discard(case_id)

    def start_preparing(case_id: str) -> None:
        if claim(case_id):
            in_background(prepare_file, case_id)

    # An audit trail a restart left half prepared, or marked ready with its file gone, is prepared
    # again: the page never offers a missing download.
    for case in cases.all():
        if case.status != case_states.CONCLUDED:
            continue
        missing = case.audit_trail_state == case_states.READY and not audit_trail_path(cases, case.id).is_file()
        if case.audit_trail_state == case_states.PREPARING or missing:
            start_preparing(case.id)

    @app.get("/health")
    def health() -> dict:
        return settings.as_health()

    @app.get("/", response_class=HTMLResponse)
    def applications(request: Request) -> HTMLResponse:
        rows = [desk.application_row(case) for case in cases.all()]
        return TEMPLATES.TemplateResponse(
            request, "applications.html", {"rows": rows, "live": any(row["live"] for row in rows)}
        )

    @app.post("/cases")
    def run_the_sample_case() -> RedirectResponse:
        case = cases.create(**application.opening(home.SAMPLE_APPLICATION))
        in_background(run_case, case.id)
        return RedirectResponse(f"/cases/{case.id}", status_code=303)

    @app.get("/cases/{case_id}", response_class=HTMLResponse)
    def case_page(request: Request, case_id: str) -> HTMLResponse:
        case = cases.get(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="no such case")
        view = desk.case_view(case, sandbox=settings.sandbox)
        live = view["running"] or view["export"]["state"] == case_states.PREPARING
        return TEMPLATES.TemplateResponse(request, "case.html", {"case": view, "live": live})

    @app.get("/cases/{case_id}/application", response_class=HTMLResponse)
    def application_page(request: Request, case_id: str) -> HTMLResponse:
        case = cases.get(case_id)
        view = desk.application_view(case, cases.all()) if case is not None else None
        if view is None:
            raise HTTPException(status_code=404, detail="no such application")
        return TEMPLATES.TemplateResponse(request, "application.html", {"application": view, "live": False})

    @app.get("/cases/{case_id}/audit-trail")
    def audit_trail(case_id: str) -> FileResponse:
        case = cases.get(case_id)
        path = audit_trail_path(cases, case_id)
        if case is None or case.audit_trail_state != case_states.READY or not path.is_file():
            raise HTTPException(status_code=404, detail="the audit trail is not ready")
        return FileResponse(path, filename=path.name, media_type="application/zip")

    @app.get("/cases/{case_id}/case-documents")
    def case_documents(case_id: str) -> dict:
        """A case's application number and case documents, for the checker agent."""
        case = cases.get(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="no such case")
        return {
            "case_id": case.id,
            "application_number": case.application_number,
            "case_documents": case.case_documents,
        }

    @app.post("/cases/{case_id}/audit-trail")
    def prepare_again(case_id: str) -> RedirectResponse:
        case = cases.get(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="no such case")
        if case.status == case_states.CONCLUDED:
            start_preparing(case_id)
        return RedirectResponse(f"/cases/{case_id}", status_code=303)

    @app.get("/cases/{case_id}/documents/{document}")
    def document(case_id: str, document: str) -> FileResponse:
        """A case document, served only through the location check the checker agent also makes.
        Every refusal is the same 404."""
        case = cases.get(case_id)
        try:
            location = case_locations.checked(case.case_documents if case else None).get(document)
            path = case_locations.file_of(location) if location else None
        except (case_locations.NotKept, OSError, ValueError):
            path = None
        if path is None:
            raise HTTPException(status_code=404, detail="no such document")
        return FileResponse(path, filename=path.name)

    return app
