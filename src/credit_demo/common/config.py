"""Settings, read once from the environment under one service's own prefix.

Two services share this parsing logic. Each reads its own `{prefix}_*`
variables first. For the settings two agents in one organization can share
(`SHARED`: the Sanning API key, the model key and the bucket), a service
falls back to one unprefixed variable when its own is unset or empty, so a
one-machine `.env` states each shared value once. A service's own variable
always wins, which is how two services on two machines, or two agents with
separate buckets, keep their values apart.

Reading settings is lenient: a missing value is listed in `missing`, never
raised. The `credit-demo` launcher is what is strict: it tests every service's
settings before it starts either one (`credit_demo.common.startup`), and
refuses to start on a missing or wrong one. A run still calls
`ready_to_record()` before it anchors anything, for a service started some
other way.

**One setting is strict at start-up, and on its shape rather than only its
presence.** `{prefix}_COUNTERPARTY_PUBLIC_KEY` is the other agent's Ed25519
public key, and a value that is present and is not a key is not a setting a
service can be lenient about: `"None"`, `"TODO"`, a key with the `0x` a
wallet tool prints, and a key with the literal quotes a Railway variable or a
`docker -e` argument carries are all truthy. Read leniently, the service would
come up healthy and fail every hand-off one request at a time, each time
saying the counterparty was not who it expected. No hand-off is accepted on
one of them either way: both comparisons are whole-value and fail closed. What
the check changes is the moment and the message: the operator learns at
start-up, and learns it as a typo in a named variable rather than as a key
mismatch at the first hand-off. So this refuses to load, naming the variable.
Unset starts the service and stops every run, like every other setting a run
needs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from nacl.signing import SigningKey

from . import home

# An Ed25519 public key, as this example reads one: 32 bytes as 64 hex
# characters, in either case.
PUBLIC_KEY_HEX_LENGTH = 64
_HEX_DIGITS = frozenset("0123456789abcdef")


def looks_like_public_key(value: Any) -> bool:
    """Is this the shape of an Ed25519 public key? Shape only, so a value
    configured as a counterparty key is refused at start-up rather than at
    the first hand-off: `"None"`, `"TODO"`, `"0x<key>"` and a key with the
    quotes a Railway variable carries are all truthy."""
    if not isinstance(value, str):
        return False
    candidate = value.strip().lower()
    return len(candidate) == PUBLIC_KEY_HEX_LENGTH and set(candidate) <= _HEX_DIGITS

# The example's home (`credit_demo.common.home`), so a relative RUNS_DIR (the
# default) resolves the same way regardless of the process's own working
# directory: `cd ~ && credit-demo` must not fail every run at its first
# artifact because "./runs/underwriter" resolved against the shell's cwd
# instead of the repo.
REPO_ROOT = home.REPO_ROOT


# Where both services listen. The loopback interface unless the operator says
# otherwise: `POST /cases`, the audit trail download and the checker's
# hand-off routes take no credentials, so a service bound to every
# interface serves them to anyone who can reach the machine. The container
# sets `CREDIT_DEMO_HOST=0.0.0.0` in its Dockerfile, where the platform's own
# network is what stands in front of it.
HOST_VARIABLE = "CREDIT_DEMO_HOST"
DEFAULT_HOST = "127.0.0.1"


def bind_host(service: str = "", env: Mapping[str, str] | None = None) -> str:
    """The interface a service binds to.

    `CREDIT_DEMO_HOST`, or loopback, for the underwriter, which is the one
    service a platform's network has to reach: it is the one port the
    Dockerfile exposes, and `CREDIT_DEMO_HOST=0.0.0.0` in the container is
    there for it. The checker is never reached that way: the underwriter
    calls it over `CHECKER_URL`, which is `http://localhost:8002` both on a
    laptop and inside the one container the Dockerfile builds, so the
    checker binds to loopback always, `CREDIT_DEMO_HOST` included. One
    variable for both services would put the checker's own unauthenticated
    endpoints (`POST /cases/{id}/handoffs`, `POST /cases/{id}/receipts`,
    `POST /cases/{id}/pack`) on the platform's private network for no reason:
    nothing ever reaches the checker except the underwriter, over loopback,
    in the same container.
    """
    if service == "checker":
        return DEFAULT_HOST
    value = (os.environ if env is None else env).get(HOST_VARIABLE, "").strip()
    return value or DEFAULT_HOST


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines into the environment without overriding what is set.

    A later NON-EMPTY line for a key wins over an earlier empty one.
    `.env.example` ships every `{prefix}_SANNING_SIGNING_SEED` empty so
    `cp .env.example .env` starts clean, and `credit-demo keys >> .env`
    appends the real values after them in the same file. Plain `setdefault`
    on every line, in file order, would let the first, empty line claim the
    key in `os.environ`, so the second, real line would be a no-op and every
    appended value would be lost. An empty value is not "set" for this
    purpose: it is skipped rather than written, so the key is still open for
    whichever line names it next.
    A value already in the environment before this file is read is still
    never overridden, empty or not: that is what `setdefault` is doing on the
    line that does write.
    """
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if not value:
            continue
        os.environ.setdefault(key.strip(), value)


def flag(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_under_repo_root(value: str) -> Path:
    """An absolute value resolves as given; a relative one resolves against
    the repo root, never against the process's own current directory."""
    path = Path(value)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


# The settings a run needs before it starts. Absence is reported, never fatal
# at start-up, and it stops a run before anything is anchored.
#
# The counterparty's public key is one of them, on both sides. Without it an
# underwriter addresses whatever key answers as the checker, so a run with a
# checker key nobody configured could reach `complete`; a checker without it
# takes a hand-off from anyone who can sign a record. So neither
# side starts a run, or takes one, without knowing who it deals with, and
# the message names the variable. The one-command launcher derives both
# public keys from the two signing seeds in the same `.env`, so a laptop run
# still needs nothing more than the seeds (`credit_demo.dev`).
REQUIRED_SUFFIXES = (
    "SANNING_API_KEY",
    "SANNING_SIGNING_SEED",
    "BUCKET",
    "ENDPOINT",
    "ACCESS_KEY_ID",
    "SECRET_ACCESS_KEY",
    "OPENROUTER_API_KEY",
    "COUNTERPARTY_PUBLIC_KEY",
)

# The settings both agents can share, each mapped to the one unprefixed
# variable that supplies it when `{prefix}_<suffix>` is unset or empty. The
# bucket's names are the ones the SDK's own `bundle` verb reads. A signing
# seed is never shared: each agent signs with its own key.
SHARED: dict[str, str] = {
    "SANNING_API_KEY": "SANNING_API_KEY",
    "SANNING_BASE_URL": "SANNING_BASE_URL",
    "SANNING_ENVIRONMENT": "SANNING_ENVIRONMENT",
    "OPENROUTER_API_KEY": "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL": "OPENROUTER_BASE_URL",
    "MODEL": "MODEL",
    "BUCKET": "SANNING_S3_BUCKET",
    "ENDPOINT": "SANNING_S3_ENDPOINT",
    "REGION": "SANNING_S3_REGION",
    "ACCESS_KEY_ID": "SANNING_S3_ACCESS_KEY_ID",
    "SECRET_ACCESS_KEY": "SANNING_S3_SECRET_ACCESS_KEY",
    "BUCKET_PATH_STYLE": "SANNING_S3_PATH_STYLE",
}


@dataclass(frozen=True, repr=False)
class ServiceSettings:
    """One service's own Sanning identity, bucket and case store."""

    service: str
    api_key: str
    base_url: str
    agent_id: str
    signing_seed_hex: str
    environment: str
    # The model, through OpenRouter's OpenAI-compatible API: the base URL
    # is fixed to OpenRouter's endpoint, and the
    # model is a configurable slug, so pointing this service at a different
    # model, or a customer's own OpenRouter account, is an env change.
    openrouter_api_key: str
    openrouter_base_url: str
    model: str
    bucket: str
    bucket_endpoint: str
    bucket_region: str
    bucket_access_key_id: str
    bucket_secret_access_key: str
    bucket_path_style: bool
    runs_dir: Path
    # The other agent's Ed25519 public key, as this operator configured it.
    # Identity CONTEXT, never a condition of a record verifying: it
    # decides who this service is willing to DEAL with, which the SDK leaves
    # to the integrator. Empty means unset.
    counterparty_public_key: str
    port: int
    missing: tuple[str, ...] = field(default=())
    # Which variable each setting came from, by suffix: `{prefix}_<suffix>`,
    # or the shared name when that supplied it or neither is set. A message
    # about a setting names this variable, so the operator edits the line
    # that is actually in their `.env`.
    sources: Mapping[str, str] = field(default_factory=dict)

    def variable(self, suffix: str) -> str:
        """The variable to name for `suffix` in a message."""
        return self.sources.get(suffix) or f"{self.service.upper()}_{suffix}"

    def __repr__(self) -> str:  # never print a secret
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
            "environment": self.environment,
            "model": self.model,
            "signing_key_configured": bool(self.signing_seed_hex),
            "openrouter_key_configured": bool(self.openrouter_api_key),
            "bucket_configured": bool(self.bucket),
            "counterparty_key_configured": bool(self.counterparty_public_key),
            # Public, so shown in full: the key this agent signs with, which
            # the other side sets as its counterparty key.
            "public_key": public_key_of(self.signing_seed_hex or ""),
            "missing": list(self.missing),
        }

    def ready_to_record(self) -> tuple[str, ...]:
        """The settings a run needs before it can anchor anything for real:
        a subset of `missing` that matters the moment a run starts, rather
        than at service start-up (see module docstring). Starting the two
        services stays lenient; starting a run does not."""
        return self.missing


def settings_from_env(
    *,
    service: str,
    prefix: str,
    default_agent_id: str,
    default_port: int,
    env: Mapping[str, str] | None = None,
) -> ServiceSettings:
    """Read one service's settings from its own `{prefix}_*` variables.

    Raises `ValueError` when `{prefix}_COUNTERPARTY_PUBLIC_KEY` is set to
    something that is not an Ed25519 public key (see the module docstring).
    """
    e = dict(os.environ if env is None else env)

    sources: dict[str, str] = {}

    def get(suffix: str, default: str = "") -> str:
        own = f"{prefix}_{suffix}"
        shared = SHARED.get(suffix)
        if e.get(own):
            sources[suffix] = own
            return e[own]
        if shared:
            sources[suffix] = shared
            if e.get(shared):
                return e[shared]
        else:
            sources[suffix] = own
        return e.get(own) or default

    missing = tuple(
        (sources.get(suffix) or f"{prefix}_{suffix}")
        for suffix in REQUIRED_SUFFIXES
        if not get(suffix)
    )
    if home.missing_documents(REPO_ROOT):
        # A run reads the case documents, so a home without them is a
        # setting a run is missing, named like the others.
        missing += (home.HOME_VARIABLE,)
    counterparty = get("COUNTERPARTY_PUBLIC_KEY").strip()
    if counterparty and not looks_like_public_key(counterparty):
        # The value is deliberately not echoed. An operator who pasted a
        # signing seed into the wrong variable must not have it printed into
        # a start-up log, and the shape is the whole of what is wrong.
        non_hex = set(counterparty.lower()) - set("0123456789abcdef")
        raise ValueError(
            f"{prefix}_COUNTERPARTY_PUBLIC_KEY is set to something that is not "
            f"an Ed25519 public key: it must be {PUBLIC_KEY_HEX_LENGTH} hex "
            f"characters, and this value's length is {len(counterparty)}"
            + (" and it carries characters outside 0-9a-f" if non_hex else "")
            + ". It is the OTHER agent's public key, which that agent shows as "
            "public_key on /health: no 0x, no quotes, either case. The value itself is not "
            "shown here in case a secret was pasted into it."
        )
    # Absolute, and a relative value resolves against the REPO root, not the
    # process's current directory: a service started from a different
    # working directory (the `credit-demo` launcher, a test, a container, or
    # a shell sitting in `~`) must still resolve every path the same way.
    runs_dir = _resolve_under_repo_root(get("RUNS_DIR", f"./runs/{service}"))
    return ServiceSettings(
        service=service,
        api_key=get("SANNING_API_KEY"),
        base_url=get("SANNING_BASE_URL", "https://console.sanning.io").rstrip("/"),
        agent_id=get("SANNING_AGENT_ID", default_agent_id),
        signing_seed_hex=get("SANNING_SIGNING_SEED"),
        environment=get("SANNING_ENVIRONMENT", "production"),
        openrouter_api_key=get("OPENROUTER_API_KEY"),
        openrouter_base_url=get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
        model=get("MODEL", "anthropic/claude-sonnet-5"),
        bucket=get("BUCKET"),
        bucket_endpoint=get("ENDPOINT"),
        bucket_region=get("REGION", "auto"),
        bucket_access_key_id=get("ACCESS_KEY_ID"),
        bucket_secret_access_key=get("SECRET_ACCESS_KEY"),
        bucket_path_style=flag(get("BUCKET_PATH_STYLE"), False),
        runs_dir=runs_dir,
        counterparty_public_key=counterparty.lower(),
        port=int(get("PORT", str(default_port))),
        missing=missing,
        sources=dict(sources),
    )


def public_key_of(seed_hex: str) -> str | None:
    """The public key a seed signs with, or `None` for a value that is not a
    seed. Never raises: a bad seed is reported by the service that holds it."""
    try:
        seed = bytes.fromhex(seed_hex.strip())
    except ValueError:
        return None
    if len(seed) != 32:
        return None
    return SigningKey(seed).verify_key.encode().hex()
