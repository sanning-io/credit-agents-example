"""The underwriter's own settings: its own key, its own bucket, read from
`UNDERWRITER_*` variables only."""

from __future__ import annotations

from typing import Mapping

from ..common.config import ServiceSettings, settings_from_env

SERVICE = "underwriter"
PREFIX = "UNDERWRITER"
DEFAULT_AGENT_ID = "underwriter"
DEFAULT_PORT = 8001


def load(env: Mapping[str, str] | None = None) -> ServiceSettings:
    return settings_from_env(
        service=SERVICE,
        prefix=PREFIX,
        default_agent_id=DEFAULT_AGENT_ID,
        default_port=DEFAULT_PORT,
        env=env,
    )
