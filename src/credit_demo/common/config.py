"""Settings, read once from the environment for one agent, under the Sanning Quickstart's standard names.

Reading is lenient: a missing value is listed in `missing`, never raised. The
launcher is strict and refuses to start on a missing or wrong value
(`credit_demo.common.startup`). The one exception is `COUNTERPARTY_PUBLIC_KEY`:
a value that is set but is not an Ed25519 public key refuses to load, naming the
variable, so a typo shows at start-up and not as a key mismatch at the first
hand-off.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from nacl.signing import SigningKey
from sanning_anchor.client import DEFAULT_BASE_URL

from . import home

# An Ed25519 public key, as this example reads one: 32 bytes as 64 hex
# characters, in either case.
PUBLIC_KEY_HEX_LENGTH = 64
_HEX_DIGITS = frozenset("0123456789abcdef")


def looks_like_public_key(value: Any) -> bool:
    """Is this the shape of an Ed25519 public key? Shape only."""
    if not isinstance(value, str):
        return False
    candidate = value.strip().lower()
    return len(candidate) == PUBLIC_KEY_HEX_LENGTH and set(candidate) <= _HEX_DIGITS

# A relative RUNS_DIR resolves against this root, not the working directory.
REPO_ROOT = home.REPO_ROOT

# The local-disk log store, beside the agents' case folders in `runs/`.
LOG_DIR_NAME = "logstore"


# Loopback unless the operator says otherwise: no endpoint takes credentials,
# so a service bound to every interface serves anyone who can reach the machine.
HOST_VARIABLE = "CREDIT_DEMO_HOST"
DEFAULT_HOST = "127.0.0.1"


def bind_host(env: Mapping[str, str] | None = None) -> str:
    """The interface a service binds to: `CREDIT_DEMO_HOST`, or loopback."""
    value = (os.environ if env is None else env).get(HOST_VARIABLE, "").strip()
    return value or DEFAULT_HOST


def read_env_file(path: Path) -> dict[str, str]:
    """The KEY=VALUE lines of an env file.

    An empty value is skipped, so a later non-empty line for the same key wins
    (a copy of `.env.example` has empty settings, and `credit-demo keys` appends
    after them). A missing file reads as empty.
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            values[key.strip()] = value
    return values


def load_env_files(*paths: Path) -> None:
    """The first file named wins over later ones, and the environment wins over every file."""
    for path in paths:
        for key, value in read_env_file(path).items():
            os.environ.setdefault(key, value)


def flag(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def resolve_under_repo_root(value: str) -> Path:
    """A relative value resolves against the repo root, never the current directory."""
    path = Path(value)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


# The settings a run needs. A missing one stops the run before anything is
# anchored. The counterparty key is one of them: without it an underwriter
# would accept whatever key answers as the checker, and a checker would take a
# hand-off from anyone who can sign a record.
REQUIRED = (
    "SANNING_API_KEY",
    "SANNING_SIGNING_SEED",
    "OPENAI_API_KEY",
    "COUNTERPARTY_PUBLIC_KEY",
)

# The services each agent calls. They have no default.
PEER_URL_VARIABLES = {
    "underwriter": ("CHECKER_URL", "EVIDENCE_BUILDER_URL"),
    "checker": ("DESK_URL",),
}

# Whose key `SANNING_SIGNING_SEED` is. The organization's hand-over key belongs
# to the evidence builder alone.
KEY_KIND_VARIABLE = "SANNING_SIGNING_KEY_KIND"
AGENT_KEY_KIND = "agent"
ORGANIZATION_KEY_KIND = "organization"

# Required when the content goes to a bucket: `BUCKET` is set, or the key is not
# a Sandbox key (only a Sandbox key may keep content on local disk). `ENDPOINT`
# is not required: it is unset on Amazon S3.
BUCKET_KEYS = ("BUCKET", "REGION", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY")

# The SDK reads the same prefix to set the mode.
SANDBOX_KEY_PREFIX = "sanning_test_"


def is_sandbox_key(api_key: str) -> bool:
    return api_key.startswith(SANDBOX_KEY_PREFIX)

# The variable each short name is read from. Any other setting reads under its own name.
VARIABLES: dict[str, str] = {
    "BUCKET": "SANNING_LOG_BUCKET",
    "ENDPOINT": "AWS_ENDPOINT_URL_S3",
    "REGION": "AWS_REGION",
    "ACCESS_KEY_ID": "AWS_ACCESS_KEY_ID",
    "SECRET_ACCESS_KEY": "AWS_SECRET_ACCESS_KEY",
    "BUCKET_PATH_STYLE": "SANNING_S3_PATH_STYLE",
}


@dataclass(frozen=True, repr=False)
class ServiceSettings:
    service: str
    api_key: str
    base_url: str
    agent_id: str
    signing_seed_hex: str
    # Lowercase; empty means the agent's own key.
    signing_key_kind: str
    openai_api_key: str
    # Empty means the OpenAI library's own default address.
    openai_base_url: str
    model: str
    bucket: str
    bucket_endpoint: str
    bucket_region: str
    bucket_access_key_id: str
    bucket_secret_access_key: str
    bucket_path_style: bool
    runs_dir: Path
    # The other agent's public key. It decides who this service deals with,
    # never whether a record verifies. Empty means unset.
    counterparty_public_key: str
    port: int
    missing: tuple[str, ...] = field(default=())

    @property
    def sandbox(self) -> bool:
        """Does the API key record to the Sandbox?"""
        return is_sandbox_key(self.api_key)

    @property
    def log_dir(self) -> Path:
        """The local log store, for a Sandbox key with no bucket. Each agent writes to a subfolder named after its agent ID."""
        return self.runs_dir.parent / LOG_DIR_NAME

    def variable(self, name: str) -> str:
        return VARIABLES.get(name, name)

    def __repr__(self) -> str:  # omits every secret
        return (
            f"ServiceSettings(service={self.service!r}, agent_id={self.agent_id!r}, "
            f"base_url={self.base_url!r}, model={self.model!r}, bucket={self.bucket!r}, "
            f"port={self.port})"
        )

    def as_health(self) -> dict[str, Any]:
        """What `/health` shows: presence, never the value, for anything secret."""
        return {
            "service": self.service,
            "agent_id": self.agent_id,
            "base_url": self.base_url,
            "model": self.model,
            "signing_key_configured": bool(self.signing_seed_hex),
            "model_key_configured": bool(self.openai_api_key),
            "bucket_configured": bool(self.bucket),
            "log_store": "bucket" if self.bucket else "local disk",
            "sandbox": self.sandbox,
            "counterparty_key_configured": bool(self.counterparty_public_key),
            # Public, so shown in full: the other side sets it as its counterparty key.
            "public_key": public_key_of(self.signing_seed_hex or ""),
            "missing": list(self.missing),
        }

    def ready_to_record(self) -> tuple[str, ...]:
        """The settings a run needs before it can anchor anything."""
        return self.missing


def settings_from_env(
    *,
    service: str,
    default_agent_id: str,
    default_port: int,
    env: Mapping[str, str] | None = None,
) -> ServiceSettings:
    """Raises `ValueError` when `COUNTERPARTY_PUBLIC_KEY` is set but is not a public key."""
    e = dict(os.environ if env is None else env)

    def get(name: str, default: str = "") -> str:
        return e.get(VARIABLES.get(name, name)) or default

    api_key = get("SANNING_API_KEY")
    # With no key at all, the key is the setting to name first.
    needs_bucket = bool(get("BUCKET")) or (bool(api_key) and not is_sandbox_key(api_key))
    required = REQUIRED + (BUCKET_KEYS if needs_bucket else ())
    if service in PEER_URL_VARIABLES:
        required += PEER_URL_VARIABLES[service]
    missing = tuple(VARIABLES.get(name, name) for name in required if not get(name))
    if home.missing_documents(REPO_ROOT):
        # A run reads the case documents.
        missing += (home.HOME_VARIABLE,)
    counterparty = get("COUNTERPARTY_PUBLIC_KEY").strip()
    if counterparty and not looks_like_public_key(counterparty):
        # Not echoed: the value might be a signing seed pasted into the wrong variable.
        non_hex = set(counterparty.lower()) - set("0123456789abcdef")
        raise ValueError(
            f"COUNTERPARTY_PUBLIC_KEY is set to something that is not "
            f"an Ed25519 public key: it must be {PUBLIC_KEY_HEX_LENGTH} hex "
            f"characters, and this value's length is {len(counterparty)}"
            + (" and it carries characters outside 0-9a-f" if non_hex else "")
            + ". It is the OTHER agent's public key, which that agent shows as "
            "public_key on /health: no 0x, no quotes, either case. The value itself is not "
            "shown here in case a secret was pasted into it."
        )
    runs_dir = resolve_under_repo_root(get("RUNS_DIR", f"./runs/{service}"))
    return ServiceSettings(
        service=service,
        api_key=api_key,
        base_url=get("SANNING_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        agent_id=get("SANNING_AGENT_ID", default_agent_id),
        signing_seed_hex=get("SANNING_SIGNING_SEED"),
        signing_key_kind=get(KEY_KIND_VARIABLE).strip().lower(),
        openai_api_key=get("OPENAI_API_KEY"),
        openai_base_url=get("OPENAI_BASE_URL").rstrip("/"),
        model=get("MODEL", "openai/gpt-5-mini"),
        bucket=get("BUCKET"),
        bucket_endpoint=get("ENDPOINT"),
        bucket_region=get("REGION"),
        bucket_access_key_id=get("ACCESS_KEY_ID"),
        bucket_secret_access_key=get("SECRET_ACCESS_KEY"),
        bucket_path_style=flag(get("BUCKET_PATH_STYLE"), False),
        runs_dir=runs_dir,
        counterparty_public_key=counterparty.lower(),
        port=int(get("PORT", str(default_port))),
        missing=missing,
    )


def public_key_of(seed_hex: str) -> str | None:
    """The public key a seed signs with, or `None` for a value that is not a seed."""
    try:
        seed = bytes.fromhex(seed_hex.strip())
    except ValueError:
        return None
    if len(seed) != 32:
        return None
    return SigningKey(seed).verify_key.encode().hex()
