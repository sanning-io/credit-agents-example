"""Test stand-ins for the three network seams: the model, Sanning, and the
case file builder's records index. Tests only; the example itself always runs
against a real model, real Sanning and a real bucket.

Everything else is real: each agent has its own Ed25519 key and its own log
store, the SDK signs and records every step, and the SDK's own `bundle` verb
builds the case file. Only what `bundle` would ask Sanning's records index for
is answered here: one stamp over every record the two agents submitted, with
a witness id of this process's own making, so no public record confirms it. The hand-over key routes are answered here
too, as Sanning rules them.
"""

from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sanning_anchor import Anchorer, FsLogStore
from sanning_anchor.cli import Io, run_bundle
from sanning_anchor.handover_key import Answer
from sanning_anchor.signer import LocalEd25519Signer
from sanning_proof import STAMP_BODY_VERSION, audit_path, jcs, leaf_hash, merkle_root

from credit_demo.common.sanning import Services
from credit_demo.evidence_builder import config as evidence_builder_config
from credit_demo.evidence_builder.case_file import BundleRun
from credit_demo.evidence_builder.config import EvidenceBuilderSettings
from credit_demo.evidence_builder.web import create_app as create_evidence_builder_app
from tests.scripted import ScriptedChatModel, checker_script, underwriter_script

UNDERWRITER_SEED = bytes(range(32)).hex()
CHECKER_SEED = bytes(range(32, 64)).hex()
SEEDS = {"underwriter": UNDERWRITER_SEED, "checker": CHECKER_SEED}
# The organization's hand-over key, which neither agent holds.
ORGANIZATION_SEED = bytes(range(64, 96)).hex()


def public_key_for(seed_hex: str) -> str:
    return LocalEd25519Signer.from_seed_hex(seed_hex).public_key().hex()


@dataclass
class Plane:
    """Sanning's ingest, shared by both agents, as one organization's is.
    Keeps every envelope, which agent submitted it, when the plane received
    it, and the case digest the SDK declared beside it."""

    submitted: list[dict] = field(default_factory=list)
    owner: dict[str, str] = field(default_factory=dict)
    received_at: dict[str, datetime] = field(default_factory=dict)
    case_id: dict[str, str] = field(default_factory=dict)
    # The organization's active hand-over key, hex, or None while none is
    # enrolled, and every hand-over route asked, in order.
    handover_key: str | None = None
    handover_routes: list[str] = field(default_factory=list)
    # Seconds each agent's records take to arrive, by agent ID: a slow log store.
    submit_delay_s: dict[str, float] = field(default_factory=dict)

    def handover_transport(self, method: str, url: str, body: Any, _headers: Any, _timeout: Any) -> Answer:
        """The three hand-over key routes, as Sanning rules them: a key
        enrolls while none is active, the same key again answers 200, and a
        different key is refused with 409."""
        route = f"{method} {url[url.index('/api/v1/handover-key'):]}"
        self.handover_routes.append(route)
        if route == "GET /api/v1/handover-key":
            return Answer(200, None, {"handover_key": self._held(self.handover_key)})
        if route == "POST /api/v1/handover-key/challenge":
            nonce = base64.urlsafe_b64encode(hashlib.sha256(b"challenge").digest()).decode().rstrip("=")
            return Answer(200, None, {"challenge": nonce, "expires_at": "2099-01-01T00:00:00.000Z"})
        if route == "POST /api/v1/handover-key/enroll":
            key = body["public_key"]
            if self.handover_key not in (None, key):
                error = {"code": "HANDOVER_KEY_ALREADY_ACTIVE", "message": "a hand-over key is active"}
                return Answer(409, None, {"error": error})
            registered = self.handover_key is None
            self.handover_key = key
            return Answer(201 if registered else 200, None, {"handover_key": self._held(key), "registered": registered})
        return Answer(404, None, {"error": {"code": "NOT_FOUND", "message": "no such route"}})

    @staticmethod
    def _held(key: str | None) -> dict | None:
        if key is None:
            return None
        return {"public_key": key, "status": "active", "algorithm": "ed25519", "created_at": "2026-01-01T00:00:00.000Z"}

    def client_for(self, agent_id: str) -> "PlaneClient":
        return PlaneClient(self, agent_id)


@dataclass
class PlaneClient:
    plane: Plane
    agent_id: str

    def submit(self, envelope: dict, *, index: dict | None = None, **_kwargs: Any) -> None:
        time.sleep(self.plane.submit_delay_s.get(self.agent_id, 0.0))
        event_id = envelope["event_id"]
        self.plane.submitted.append(dict(envelope))
        self.plane.owner[event_id] = self.agent_id
        self.plane.received_at[event_id] = datetime.now(timezone.utc)
        case_id = (index or {}).get("context", {}).get("case_id")
        if case_id:
            self.plane.case_id[event_id] = case_id


class _NoReport:
    """The opener `bundle` hands a finished report to: a test opens no page."""

    def open(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class RecordsIndex:
    """What `bundle` reads from Sanning's records index: which records an
    agent submitted, which of them declared a case, each envelope, and each
    record's inclusion proof under one stamp over everything submitted so
    far."""

    def __init__(self, plane: Plane) -> None:
        envelopes = list(plane.submitted)
        self._owner = dict(plane.owner)
        self._received_at = dict(plane.received_at)
        self._case_id = dict(plane.case_id)
        self._leaves = [leaf_hash(jcs(e)) for e in envelopes]
        self._by_id = {e["event_id"]: (i, e) for i, e in enumerate(envelopes)}
        root = merkle_root(self._leaves).hex()
        body = jcs(
            {
                "stamp_version": STAMP_BODY_VERSION,
                "org_ref": "test-org",
                "interval_start": "2020-01-01T00:00:00.000Z",
                "interval_end": "2099-12-31T00:00:00.000Z",
                "merkle_root": root,
                "leaf_count": len(self._leaves),
                "subtrees": {
                    "test-org": {"first_leaf": 0, "leaf_count": len(self._leaves), "root": root}
                },
            }
        ).decode()
        witness_id = base64.urlsafe_b64encode(
            hashlib.sha256(b"local test witness").digest()
        ).decode().rstrip("=")
        self._stamp = {"body": body, "witness": {"kind": "arweave", "id": witness_id}, "state": "confirmed"}

    # A row carries what the real index sends: the plane's `received_at`.
    def list(self, *, since: Any = None, until: Any = None, agent: str | None = None, **_: Any):
        return [
            {"id": event_id, "received_at": self._received_at[event_id].isoformat()}
            for event_id, (_i, _e) in self._by_id.items()
            if agent is None or self._owner.get(event_id) == agent
        ]

    def list_case(
        self, *, case_id: str, agent: str | None = None
    ) -> list[dict]:
        # The rows alone, newest first, as the plane answers.
        rows = [row for row in self.list(agent=agent) if self._case_id.get(row["id"]) == case_id]
        return sorted(rows, key=lambda row: self._received_at[row["id"]], reverse=True)

    def envelope(self, record_id: str) -> dict:
        return self._by_id[record_id][1]

    def inclusion(self, record_id: str) -> dict:
        i = self._by_id[record_id][0]
        return {
            "leaf_hash": self._leaves[i].hex(),
            "leaf_index": i,
            "leaf_count": len(self._leaves),
            "audit_path": [h.hex() for h in audit_path(i, self._leaves)],
            "stamp": self._stamp,
        }


def services_for(agent_id: str, plane: Plane, root: Path, *, model: Any = None) -> Services:
    """One agent's outside world: its own key and log store, the shared
    plane and a scripted model."""
    logs = root / f"{agent_id}-logs"
    anchorer = Anchorer(
        api_key="test-key",
        subject={"type": "agent", "agent_id": agent_id},
        signer=LocalEd25519Signer.from_seed_hex(SEEDS[agent_id]),
        environment="dev",
        client=plane.client_for(agent_id),
        log_store=FsLogStore(str(logs)),
        auto_enroll=False,
    )

    script = checker_script() if agent_id == "checker" else underwriter_script()
    return Services(anchorer=anchorer, model=model or ScriptedChatModel(responses=script))


def evidence_builder_settings(root: Path, **overrides: str) -> EvidenceBuilderSettings:
    """The evidence builder's settings: the organization's hand-over key, and
    the two test agents' IDs. It holds neither agent's seed."""
    env = {
        "SANNING_API_KEY": "sanning_test_key",
        "SANNING_SIGNING_SEED": ORGANIZATION_SEED,
        "SANNING_SIGNING_KEY_KIND": "organization",
        "UNDERWRITER_AGENT_ID": "underwriter",
        "CHECKER_AGENT_ID": "checker",
        "RUNS_DIR": str(root / "evidence-builder-runs"),
        **overrides,
    }
    return evidence_builder_config.settings_from_env(env)


def case_file_bundle(plane: Plane, root: Path):
    """The SDK's own `bundle` verb for a case file, sealed with the
    organization's key, reading the records index above. The test agents keep
    their content in `<root>/<agent>-logs`, so each `--logs` is pointed there."""

    def bundle(args: list[str]) -> BundleRun:
        agents = [args[i + 1] for i, arg in enumerate(args) if arg == "--agent"]
        logs = iter(str(root / f"{agent}-logs") for agent in agents)
        args = [next(logs) if args[i - 1] == "--logs" else arg for i, arg in enumerate(args)]
        errors: list[str] = []
        env = {
            "SANNING_API_KEY": "sanning_test_key",
            "SANNING_SIGNING_SEED": ORGANIZATION_SEED,
            "SANNING_SIGNING_KEY_KIND": "organization",
        }
        code = run_bundle(
            args,
            Io(out=lambda _line: None, err=errors.append, env=env),
            make_index=lambda **_kw: RecordsIndex(plane),
            declaration_client=plane.client_for("evidence-builder"),
            handover_transport=plane.handover_transport,
            handover_sleep=lambda _seconds: None,            report_opener=_NoReport(),
        )
        return BundleRun(code=code, errors=errors)

    return bundle


def evidence_builder_app(plane: Plane, root: Path, *, run_bundle: Any = None):
    """The evidence builder, over the test plane, unless a test gives it a
    `bundle` of its own."""
    return create_evidence_builder_app(
        evidence_builder_settings(root), run_bundle=run_bundle or case_file_bundle(plane, root)
    )
