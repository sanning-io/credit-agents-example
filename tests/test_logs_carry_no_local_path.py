"""What the services log carries no local path, whatever shape the path came
in, and a 404 still logs one ordinary access line.

Each service runs in a process of its own, started the way `credit-demo`
starts it (`tests/log_every_shape.py`): uvicorn's own logging set-up, then
the service's web module and the scrub filter it installs. Cleaning only a
record's text and string args would let a path in a `Path` arg, an
exception, a list, a mapping, an exception logged as the message, or a path
split over two args reach the log whole. Cleaning the whole message instead,
and dropping the args, would break uvicorn's access line, which reads five
args, one of them the status code as a number.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

from credit_demo.common import home
from tests.log_every_shape import HOME, LOGGERS, SHAPES, mark, shapes

SERVICES = ("underwriter", "checker")


@pytest.fixture(scope="module", params=SERVICES)
def logged(request, repo_tmp_module) -> str:
    """Everything one service logged, standard output and standard error."""
    env = {
        **os.environ,
        "RUNS_DIR": str(repo_tmp_module / "underwriter"),
        "RUNS_DIR": str(repo_tmp_module / "checker"),
        "CHECKER_URL": "http://127.0.0.1:9",
        "DESK_URL": "http://127.0.0.1:9",
    }
    ran = subprocess.run(
        [sys.executable, "-m", "tests.log_every_shape", request.param],
        cwd=home.SOURCE_ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert ran.returncode == 0, ran.stderr[-3000:]
    return ran.stdout + ran.stderr


def _lines_with(logged: str, text: str) -> list[str]:
    return [line for line in logged.splitlines() if text in line]


@pytest.mark.parametrize("logger", LOGGERS)
@pytest.mark.parametrize("shape", SHAPES)
def test_each_shape_of_a_local_path_is_cut_to_its_name(logged, logger, shape):
    marker = mark(logger, shape)
    _message, _args, expected = shapes(marker)[shape]

    lines = _lines_with(logged, marker)

    assert len(lines) == 1, lines
    assert lines[0].endswith(expected), lines[0]
    assert HOME not in lines[0]


@pytest.mark.parametrize("logger", LOGGERS)
def test_a_traceback_is_dropped_and_its_message_kept(logged, logger):
    lines = _lines_with(logged, mark(logger, "traceback"))

    assert len(lines) == 1, lines
    assert lines[0].endswith(f"{mark(logger, 'traceback')} reading failed")
    assert "Traceback" not in logged


def test_no_line_carries_the_local_path_or_a_logging_error(logged):
    assert HOME not in logged
    assert "Logging error" not in logged


def test_a_404_logs_one_ordinary_access_line(logged):
    access = re.compile(r'^INFO: +127\.0\.0\.1:\d+ - "GET /favicon\.ico HTTP/1\.1" 404 Not Found$')

    assert len([line for line in logged.splitlines() if access.match(line)]) == 1, logged


def test_the_operator_line_for_a_request_that_raised_names_its_route_whole(repo_tmp_path, monkeypatch, capsys):
    """A case document's route is four segments deep, as deep as a local path
    the scrub cuts to its last part, so a scrubbed line would read
    `underwriter: GET bank_statements raised`. The route stays whole, and the
    exception still loses its path."""
    from fastapi.testclient import TestClient

    from credit_demo.underwriter.config import settings_from_env as underwriter_settings
    from credit_demo.underwriter.web import create_app

    settings = underwriter_settings(
        service="underwriter", default_agent_id="underwriter", default_port=8001,
        env={"RUNS_DIR": str(repo_tmp_path / "cases")},
    )
    app = create_app(settings, checker_url="http://127.0.0.1:9")
    case_id = "20260101T000000000000Z-1a2b"

    def unreadable(_case_id):
        raise FileNotFoundError(2, "No such file or directory", f"{HOME}/work/case.json")

    monkeypatch.setattr(app.state.cases, "get", unreadable)
    answer = TestClient(app, raise_server_exceptions=False).get(f"/cases/{case_id}/documents/bank_statements")

    assert answer.status_code == 500
    line = capsys.readouterr().err.strip()
    assert line == (
        f"underwriter: GET /cases/{case_id}/documents/bank_statements raised: "
        "FileNotFoundError: [Errno 2] No such file or directory: 'case.json'"
    )
