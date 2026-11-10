"""The checker service's own app: its health, and how it starts on its settings."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from credit_demo.checker.config import settings_from_env
from credit_demo.checker.web import create_app


def _settings():
    return settings_from_env(
        service="checker", default_agent_id="checker", default_port=8002, env={}
    )


def test_health_reports_this_services_own_identity():
    client = TestClient(create_app(_settings()))
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "checker"
    assert body["agent_id"] == "checker"


def test_the_service_refuses_to_start_on_a_counterparty_key_that_is_not_one(monkeypatch):
    """A deploy carrying a placeholder fails at start-up, naming the variable, instead of coming up
    healthy and failing every hand-off."""
    monkeypatch.setenv("COUNTERPARTY_PUBLIC_KEY", "TODO")
    with pytest.raises(ValueError) as caught:
        create_app()
    assert "COUNTERPARTY_PUBLIC_KEY" in str(caught.value)


def test_the_service_still_starts_with_no_env_at_all(monkeypatch):
    """Unset stays lenient: a laptop with no `.env` gets both services up,
    and `/health` says what is missing."""
    monkeypatch.delenv("COUNTERPARTY_PUBLIC_KEY", raising=False)
    client = TestClient(create_app(_settings()))
    assert client.get("/health").json()["counterparty_key_configured"] is False
