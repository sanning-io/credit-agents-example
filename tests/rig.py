"""Test stand-ins for the three network seams: the model, Sanning, and the
pack builder's records index. Tests only; the example itself always runs
against a real model, real Sanning and a real bucket.

Everything else is real: each agent has its own Ed25519 key and its own log
store, the SDK signs and records every step, and the SDK's own `bundle` verb
builds each pack. Only what `bundle` would ask Sanning's records index for
is answered here: one stamp over every record the two agents submitted, with
a witness id of this process's own making, so no public record confirms it.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sanning_anchor import Anchorer, CaseReadResult, FsLogStore
from sanning_anchor.cli import Io, run_bundle
from sanning_anchor.signer import LocalEd25519Signer
from sanning_proof import STAMP_BODY_VERSION, audit_path, jcs, leaf_hash, merkle_root

from credit_demo.common.sanning import BundleRun, Services
from tests.scripted import ScriptedChatModel, checker_script, underwriter_script

UNDERWRITER_SEED = bytes(range(32)).hex()
CHECKER_SEED = bytes(range(32, 64)).hex()
SEEDS = {"underwriter": UNDERWRITER_SEED, "checker": CHECKER_SEED}


def public_key_for(seed_hex: str) -> str:
    return LocalEd25519Signer.from_seed_hex(seed_hex).public_key().hex()


@dataclass
class Plane:
    """Sanning's ingest, shared by both agents, as one organization's is.
    Keeps every envelope, which agent submitted it, when the plane witnessed
    it, and the case digest the SDK declared beside it."""

    submitted: list[dict] = field(default_factory=list)
    owner: dict[str, str] = field(default_factory=dict)
    witnessed_at: dict[str, datetime] = field(default_factory=dict)
    case_id_hash: dict[str, str] = field(default_factory=dict)

    def client_for(self, agent_id: str) -> "PlaneClient":
        return PlaneClient(self, agent_id)


@dataclass
class PlaneClient:
    plane: Plane
    agent_id: str

    def submit(self, envelope: dict, *, index: dict | None = None, **_kwargs: Any) -> None:
        event_id = envelope["event_id"]
        self.plane.submitted.append(dict(envelope))
        self.plane.owner[event_id] = self.agent_id
        self.plane.witnessed_at[event_id] = datetime.now(timezone.utc)
        if index and index.get("case_id_hash"):
            self.plane.case_id_hash[event_id] = index["case_id_hash"]


class RecordsIndex:
    """What `bundle` reads from Sanning's records index: which records an
    agent submitted, which of them declared a case, each envelope, and each
    record's inclusion proof under one stamp over everything submitted so
    far."""

    def __init__(self, plane: Plane) -> None:
        envelopes = list(plane.submitted)
        self._owner = dict(plane.owner)
        self._witnessed_at = dict(plane.witnessed_at)
        self._case_id_hash = dict(plane.case_id_hash)
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

    # A row carries what the real index sends: the plane's `witnessed_at`,
    # and no `received_at`.
    def list(self, *, since: Any = None, until: Any = None, agent: str | None = None, **_: Any):
        return [
            {"id": event_id, "witnessed_at": self._witnessed_at[event_id].isoformat()}
            for event_id, (_i, _e) in self._by_id.items()
            if agent is None or self._owner.get(event_id) == agent
        ]

    def list_case(
        self, *, case_id_hash: str, since: Any = None, until: Any = None, agent: str | None = None
    ) -> CaseReadResult:
        rows = [row for row in self.list(agent=agent) if self._case_id_hash.get(row["id"]) == case_id_hash]
        times = [self._witnessed_at[row["id"]] for row in rows] or [datetime.now(timezone.utc)]
        return CaseReadResult(rows, since or min(times), until or max(times))

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
    plane, a scripted model, and the SDK's own `bundle` verb reading the
    records index above."""
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

    def bundle(args: list[str], env: dict[str, str]) -> BundleRun:
        errors: list[str] = []
        code = run_bundle(
            args,
            Io(out=lambda _line: None, err=errors.append, env=env),
            make_index=lambda **_kw: RecordsIndex(plane),
            declaration_client=plane.client_for(agent_id),
            report_opener=lambda *_a, **_k: None,
        )
        return BundleRun(code=code, errors=errors)

    script = checker_script() if agent_id == "checker" else underwriter_script()
    return Services(
        anchorer=anchorer,
        model=model or ScriptedChatModel(responses=script),
        logs=str(logs),
        bundle=bundle,
    )
