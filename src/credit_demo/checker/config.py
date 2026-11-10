"""The checker's settings, read from the standard names (see
`credit_demo.common.config`)."""

from __future__ import annotations

from typing import Mapping

from ..common.config import ServiceSettings, settings_from_env

SERVICE = "checker"
DEFAULT_AGENT_ID = "checker"
DEFAULT_PORT = 8002


def load(env: Mapping[str, str] | None = None) -> ServiceSettings:
    return settings_from_env(
        service=SERVICE,
        default_agent_id=DEFAULT_AGENT_ID,
        default_port=DEFAULT_PORT,
        env=env,
    )
