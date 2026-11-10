"""The evidence builder's service. It holds the organization's hand-over key and read
access to the log store, and no agent's seed. Each call runs `bundle` once, replies,
and leaves nothing running and nothing kept.

Routes:
- `GET /health`: its settings, never a secret.
- `POST /cases/{id}/case-file`: build the case file. Replies 200 with the zip, 202
  while the records are not stamped yet (ask again later), or 422 when the file
  cannot be built.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from ..common.cases import valid_case_id
from ..common.config import ORGANIZATION_KEY_KIND
from ..common.errors import (
    configure_logging,
    tell_the_operator,
    tell_the_operator_a_request_raised,
    without_local_paths,
)
from .case_file import BUILT, NOT_STAMPED_YET, RunBundle, build_case_file, run_bundle_command
from .config import EvidenceBuilderSettings, load as load_settings

configure_logging("uvicorn", "uvicorn.error", "uvicorn.access")


def create_app(
    settings: EvidenceBuilderSettings | None = None,
    *,
    run_bundle: RunBundle = run_bundle_command,
) -> FastAPI:
    settings = settings or load_settings()
    if settings.signing_key_kind != ORGANIZATION_KEY_KIND:
        raise ValueError(
            f"{settings.variable('SANNING_SIGNING_KEY_KIND')} must be {ORGANIZATION_KEY_KIND}: "
            "this service signs with the organization's hand-over key, never an agent's"
        )

    app = FastAPI(title="credit-demo evidence builder")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        tell_the_operator_a_request_raised("evidence-builder", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=500, content={"ready": False, "reason": without_local_paths(exc)}
        )

    @app.get("/health")
    def health() -> dict:
        return settings.as_health()

    @app.post("/cases/{case_id}/case-file")
    def case_file(case_id: str) -> Response:
        if not valid_case_id(case_id):
            return JSONResponse(status_code=404, content={"ready": False, "reason": "no such case"})
        if settings.missing:
            return JSONResponse(
                status_code=500,
                content={
                    "ready": False,
                    "reason": f"this service cannot build anything yet: {', '.join(settings.missing)} not set",
                },
            )
        with tempfile.TemporaryDirectory(prefix="case-file-") as folder:
            out = Path(folder) / f"audit-trail-{case_id}.zip"
            ran = build_case_file(run_bundle, settings, case_id=case_id, out=out)
            reason = without_local_paths(" ".join(ran.errors))
            if ran.code == NOT_STAMPED_YET:
                tell_the_operator(f"case {case_id}: the records are not stamped yet")
                return JSONResponse(status_code=202, content={"ready": False, "reason": reason})
            if ran.code not in BUILT:
                tell_the_operator(f"case {case_id}: the case file could not be built (bundle exit {ran.code})")
                return JSONResponse(
                    status_code=422,
                    content={"ready": False, "reason": reason or f"bundle exited {ran.code}"},
                )
            body = out.read_bytes()
        tell_the_operator(f"case {case_id}: the case file was built")
        return Response(content=body, media_type="application/zip")

    return app
