"""The evidence builder's settings, read from the standard names. It holds the
organization's evidence key and nothing of either agent: no agent seed, model key
or counterparty key. A missing value is listed in `missing`, never raised; the
start-up tests refuse to start.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from sanning_anchor.client import DEFAULT_BASE_URL

from ..common.config import (
    BUCKET_KEYS,
    KEY_KIND_VARIABLE,
    LOG_DIR_NAME,
    VARIABLES,
    flag,
    is_sandbox_key,
    public_key_of,
    resolve_under_repo_root,
)

SERVICE = "evidence-builder"
DEFAULT_PORT = 8003

# The two agents whose records go into each case file, by the IDs they sign
# with. The order here is the order of `--agent` and of `--logs`.
UNDERWRITER_AGENT_VARIABLE = "UNDERWRITER_AGENT_ID"
CHECKER_AGENT_VARIABLE = "CHECKER_AGENT_ID"

# When true, the start-up tests refuse an evidence builder with no bucket. `compose.yaml` sets it,
# because the evidence builder's container cannot see the agents' volumes.
REQUIRE_BUCKET_VARIABLE = "CREDIT_DEMO_REQUIRE_BUCKET"

REQUIRED = (
    "SANNING_API_KEY",
    "SANNING_SIGNING_SEED",
    UNDERWRITER_AGENT_VARIABLE,
    CHECKER_AGENT_VARIABLE,
)


@dataclass(frozen=True, repr=False)
class EvidenceBuilderSettings:
    service: str
    api_key: str
    base_url: str
    # The organization's evidence seed. `signing_key_kind` says whose key it
    # is, and the start-up tests refuse any kind but the organization's.
    signing_seed_hex: str
    signing_key_kind: str
    underwriter_agent_id: str
    checker_agent_id: str
    bucket: str
    bucket_endpoint: str
    bucket_region: str
    bucket_access_key_id: str
    bucket_secret_access_key: str
    bucket_path_style: bool
    runs_dir: Path
    port: int
    missing: tuple[str, ...] = ()
    # Set where the evidence builder cannot read the agents' local disk (a container of its own).
    # It then needs the bucket even with a Sandbox key.
    bucket_required: bool = False

    @property
    def agent_ids(self) -> tuple[str, str]:
        return (self.underwriter_agent_id, self.checker_agent_id)

    @property
    def sandbox(self) -> bool:
        return is_sandbox_key(self.api_key)

    @property
    def log_dir(self) -> Path:
        """The log store on local disk, for a Sandbox key with no bucket: the
        folder the agents write to, beside their case folders."""
        return self.runs_dir.parent / LOG_DIR_NAME

    def variable(self, name: str) -> str:
        return VARIABLES.get(name, name)

    def __repr__(self) -> str:  # never print a secret
        return (
            f"EvidenceBuilderSettings(service={self.service!r}, agent_ids={self.agent_ids!r}, "
            f"base_url={self.base_url!r}, bucket={self.bucket!r}, port={self.port})"
        )

    def as_health(self) -> dict[str, Any]:
        """What `/health` shows: presence, never the value, for anything
        secret. The public key is public, so it is shown in full."""
        return {
            "service": self.service,
            "agent_ids": list(self.agent_ids),
            "base_url": self.base_url,
            "signing_key_kind": self.signing_key_kind,
            "signing_key_configured": bool(self.signing_seed_hex),
            "bucket_configured": bool(self.bucket),
            "log_store": "bucket" if self.bucket else "local disk",
            "sandbox": self.sandbox,
            "public_key": public_key_of(self.signing_seed_hex or ""),
            "missing": list(self.missing),
        }


def settings_from_env(env: Mapping[str, str] | None = None) -> EvidenceBuilderSettings:
    e = dict(os.environ if env is None else env)

    def get(name: str, default: str = "") -> str:
        return e.get(VARIABLES.get(name, name)) or default

    api_key = get("SANNING_API_KEY")
    # The bucket's settings are required when the content is in a bucket: a
    # bucket is set, or the key is not a Sandbox key.
    bucket_required = flag(get(REQUIRE_BUCKET_VARIABLE), False)
    needs_bucket = (
        bool(get("BUCKET")) or bucket_required or (bool(api_key) and not is_sandbox_key(api_key))
    )
    required = REQUIRED + (BUCKET_KEYS if needs_bucket else ())
    missing = tuple(VARIABLES.get(name, name) for name in required if not get(name))
    return EvidenceBuilderSettings(
        service=SERVICE,
        api_key=api_key,
        base_url=get("SANNING_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        signing_seed_hex=get("SANNING_SIGNING_SEED"),
        signing_key_kind=get(KEY_KIND_VARIABLE).strip().lower(),
        underwriter_agent_id=get(UNDERWRITER_AGENT_VARIABLE),
        checker_agent_id=get(CHECKER_AGENT_VARIABLE),
        bucket=get("BUCKET"),
        bucket_endpoint=get("ENDPOINT"),
        bucket_region=get("REGION"),
        bucket_access_key_id=get("ACCESS_KEY_ID"),
        bucket_secret_access_key=get("SECRET_ACCESS_KEY"),
        bucket_path_style=flag(get("BUCKET_PATH_STYLE"), False),
        runs_dir=resolve_under_repo_root(get("RUNS_DIR", f"./runs/{SERVICE}")),
        port=int(get("PORT", str(DEFAULT_PORT))),
        missing=missing,
        bucket_required=bucket_required,
    )


def load(env: Mapping[str, str] | None = None) -> EvidenceBuilderSettings:
    return settings_from_env(env)

