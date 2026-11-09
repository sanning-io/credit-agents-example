"""Each service closes its Sanning anchorer when the process shuts down, so a
send still in flight is not lost. A service that never built its services
builds nothing at shutdown."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from credit_demo.checker.config import settings_from_env as checker_settings
from credit_demo.checker.web import create_app as create_checker_app
from credit_demo.common import sanning
from credit_demo.underwriter.config import settings_from_env as underwriter_settings
from credit_demo.underwriter.web import create_app as create_underwriter_app
from tests.rig import CHECKER_SEED, UNDERWRITER_SEED, Plane, services_for
from tests.test_bar_a_case_end_to_end import _env


class _Anchorer:
    def __init__(self, raises: Exception | None = None):
        self.closed = 0
        self._raises = raises

    def close(self, **_kwargs):
        self.closed += 1
        if self._raises is not None:
            raise self._raises


def _app(service: str, root, services=None):
    if service == "underwriter":
        settings = underwriter_settings(
            service="underwriter",
            default_agent_id="underwriter",
            default_port=8001,
            env=_env(UNDERWRITER_SEED, CHECKER_SEED, root / "underwriter-cases"),
        )
        return create_underwriter_app(settings, services=services)
    settings = checker_settings(
        service="checker",
        default_agent_id="checker",
        default_port=8002,
        env=_env(CHECKER_SEED, UNDERWRITER_SEED, root / "checker-cases"),
    )
    return create_checker_app(settings, services=services)


def _services(service: str, root, anchorer):
    services = services_for(service, Plane(), root)
    services.anchorer = anchorer
    return services


@pytest.mark.parametrize("service", ["underwriter", "checker"])
def test_shutdown_closes_the_anchorer_once_when_services_were_built(service, repo_tmp_path):
    anchorer = _Anchorer()
    app = _app(service, repo_tmp_path, _services(service, repo_tmp_path, anchorer))

    with TestClient(app):
        assert anchorer.closed == 0

    assert anchorer.closed == 1


@pytest.mark.parametrize("service", ["underwriter", "checker"])
def test_shutdown_builds_nothing_when_services_were_never_built(service, repo_tmp_path, monkeypatch):
    def built(_settings):
        raise AssertionError("shutdown built the services")

    monkeypatch.setattr(sanning, "real_services", built)

    with TestClient(_app(service, repo_tmp_path)):
        pass


@pytest.mark.parametrize("service", ["underwriter", "checker"])
def test_a_close_that_raises_is_logged_and_the_shutdown_completes(service, repo_tmp_path, capsys):
    anchorer = _Anchorer(raises=RuntimeError("the network went away"))
    app = _app(service, repo_tmp_path, _services(service, repo_tmp_path, anchorer))

    with TestClient(app):
        pass

    assert anchorer.closed == 1
    logged = capsys.readouterr().err
    assert "did not close cleanly" in logged
    assert "the network went away" in logged
