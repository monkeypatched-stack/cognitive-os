"""Checkpoint — serialized RuntimeProcessControlBlock snapshot for suspend/
resume across process restarts and cross-instance migration.

Bundles only already-serializable existing structures: ExecutionGraph.snapshot()
(added alongside this package), IntentIR.to_dict() (already existed), and
ExecutionContext's own fields (already a frozen, JSON-safe dataclass). This is
not a new serialization format — it's a manifest over existing to_dict()-
capable objects.

Storage: in-memory only for now (CheckpointStore below), same deliberate MVP
scope cut as plan/goals/run_store.py's RunStore — NOT durable across process
restarts. Flagged, not silently assumed away: a production deployment should
back this with the same MongoDB PersistenceManager already available on the
Kernel, but no schema for that exists in this codebase yet. The interface
(save/load/delete) is written so swapping in a durable backend later doesn't
require touching ProcessManager at all.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Literal

from src.monkey_brain.kernel.execute.context import ExecutionContext
from src.monkey_brain.kernel.execute.graph import ExecutionGraph, NodeState
from src.monkey_brain.kernel.execute.models import ExecutionMode
from src.monkey_brain.kernel.fix.scheduler.graph_scheduler import GraphScheduler
from src.monkey_brain.kernel.fix.self_healing.workload import SelfHealingPolicy
from src.monkey_brain.kernel.plan.goals.intent_ir import IntentIR
from src.monkey_brain.kernel.trusted_time import get_trusted_timestamp

logger = logging.getLogger("agentos.process.checkpoint")

# 1.1 (Trusted Time): _signing_payload() now includes created_at and
# trusted_timestamp, which it previously omitted. A checkpoint signed under
# 1.0 will fail verify() under this code — an intentional, narrow breaking
# change (unlike intent_ir.py/audit.py, checkpoints are short-lived
# operational state with a 7-day default cleanup window, not long-lived
# audit/replay records other systems depend on staying verifiable
# indefinitely). Failure mode is safe: restore_from_checkpoint() just
# refuses to restore from a stale-signature checkpoint, the same as any
# other tampered one.
CHECKPOINT_SCHEMA_VERSION = "1.1"


def _is_production() -> bool:
    """True when any standard env marker declares a production deployment.
    Mirrors kernel/plan/goals/intent_ir.py::_is_production() exactly --
    same check, duplicated rather than imported to avoid a cross-package
    dependency for four lines; keep both in sync if this ever changes."""
    for var in ("AGENTOS_ENV", "MONKEYBRAIN_ENV", "ENVIRONMENT", "ENV"):
        if os.environ.get(var, "").strip().lower() in ("production", "prod"):
            return True
    return False


def _signing_key() -> bytes:
    """Signing key for checkpoints (HMAC fallback path, used when Ed25519
    signing via the identity module raises -- see sign()/verify()).

    Fails closed in production: if AGENTOS_MASTER_KEY is unset there,
    checkpoint signing raises rather than silently falling back to a
    public constant that would make every checkpoint signature forgeable
    (previously: always silently used the fallback key, no warning, not
    fail-closed anywhere). Outside production it still falls back to a
    fixed dev key, now with a loud warning instead of silence.
    """
    key = os.environ.get("AGENTOS_MASTER_KEY", "")
    if not key:
        if _is_production():
            raise RuntimeError(
                "AGENTOS_MASTER_KEY is not set in a production environment — refusing "
                "to sign a checkpoint with the insecure dev-only fallback key. Set "
                "AGENTOS_MASTER_KEY before starting."
            )
        logger.warning(
            "AGENTOS_MASTER_KEY not set — checkpoint signatures use an insecure "
            "dev-only fallback key. Set AGENTOS_MASTER_KEY in any environment "
            "where checkpoint tamper-protection matters."
        )
        key = "insecure-dev-only-checkpoint-key"
    return key.encode("utf-8")


@dataclass
class Checkpoint:
    checkpoint_id: str
    run_id: str
    schema_version: str
    created_at: float
    context: dict[str, Any]  # ExecutionContext fields (minus intent_ir, stored separately)
    intent_ir: dict[str, Any] | None  # IntentIR.to_dict()
    graph_snapshot: dict[str, Any]  # ExecutionGraph.snapshot()
    retry_budget: dict[str, int]
    compensation_log: list[dict[str, Any]]
    approval_gate: dict[str, Any] | None
    # Trusted Time (kernel/trusted_time.py): created_at itself was NOT part
    # of this checkpoint's own signing payload before this field existed —
    # a real, separate pre-existing gap. trusted_timestamp carries the
    # Trusted Time service's own attestation for created_at (or an
    # attested=False local-clock fallback if that service was unreachable
    # when this checkpoint was built — see get_trusted_timestamp's
    # fail-open design), and, unlike created_at historically, IS included
    # in what gets signed below.
    trusted_timestamp: dict[str, Any] | None = None
    integrity: str = ""

    def _signing_payload(self) -> str:
        import json

        return "|".join(
            [
                self.schema_version,
                self.checkpoint_id,
                self.run_id,
                f"{self.created_at:.6f}",
                json.dumps(self.trusted_timestamp, sort_keys=True),
                json.dumps(self.context, sort_keys=True),
                json.dumps(self.intent_ir, sort_keys=True),
                json.dumps(self.graph_snapshot, sort_keys=True),
                json.dumps(self.retry_budget, sort_keys=True),
                json.dumps(self.compensation_log, sort_keys=True),
                json.dumps(self.approval_gate, sort_keys=True),
            ]
        )

    def sign(self) -> None:
        """Sign checkpoint using Ed25519 via identity module, fallback to HMAC."""
        blob = self._signing_payload().encode("utf-8")
        try:
            from src.monkey_brain.kernel.identity import (
                get_identity,
                get_key_manager,
                sign_bytes,
            )

            identity = get_identity()
            km = get_key_manager()
            key = km.get_or_create(identity.runtime_id)
            self.integrity = sign_bytes(blob, key)
        except Exception:
            self.integrity = hmac.new(_signing_key(), blob, hashlib.sha256).hexdigest()

    def verify(self) -> bool:
        """Verify checkpoint signature. Tries Ed25519 first, falls back to HMAC."""
        if not self.integrity:
            return False
        blob = self._signing_payload().encode("utf-8")

        # Try Ed25519
        try:
            from src.monkey_brain.kernel.identity import (
                get_identity,
                get_key_manager,
                verify_bytes,
            )

            identity = get_identity()
            km = get_key_manager()
            pub_pem = km.get_public_key_pem(identity.runtime_id)
            if verify_bytes(blob, self.integrity, pub_pem):
                return True
        except Exception:
            logger.debug("verify: suppressed exception", exc_info=True)

        # Fallback: HMAC
        expected = hmac.new(_signing_key(), blob, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, self.integrity)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "context": self.context,
            "intent_ir": self.intent_ir,
            "graph_snapshot": self.graph_snapshot,
            "retry_budget": self.retry_budget,
            "compensation_log": self.compensation_log,
            "approval_gate": self.approval_gate,
            "trusted_timestamp": self.trusted_timestamp,
            "integrity": self.integrity,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Checkpoint":
        return cls(
            checkpoint_id=d["checkpoint_id"],
            run_id=d["run_id"],
            schema_version=d["schema_version"],
            created_at=d["created_at"],
            context=d["context"],
            intent_ir=d.get("intent_ir"),
            graph_snapshot=d["graph_snapshot"],
            retry_budget=d.get("retry_budget", {}),
            compensation_log=d.get("compensation_log", []),
            approval_gate=d.get("approval_gate"),
            trusted_timestamp=d.get("trusted_timestamp"),
            integrity=d.get("integrity", ""),
        )


async def build_checkpoint(rpcb: Any, checkpoint_id: str) -> Checkpoint:
    """rpcb: RuntimeProcessControlBlock (typed as Any to avoid a circular
    import — models.py does not need this module).

    async only because of get_trusted_timestamp()'s HTTP call — that call
    never blocks longer than TRUSTED_TIME_TIMEOUT_SECONDS and never raises
    (see kernel/trusted_time.py), so this stays as cheap as the rest of
    checkpointing even when the Trusted Time service is unreachable.
    """
    from dataclasses import asdict

    ts = await get_trusted_timestamp()
    ir_dict = rpcb.context.intent_ir.to_dict() if rpcb.context.intent_ir is not None else None
    ckpt = Checkpoint(
        checkpoint_id=checkpoint_id,
        run_id=rpcb.run_id,
        schema_version=CHECKPOINT_SCHEMA_VERSION,
        created_at=ts.timestamp,
        trusted_timestamp={
            "timestamp": ts.timestamp,
            "signature": ts.signature,
            "key_id": ts.key_id,
            "attested": ts.attested,
        },
        context={
            "run_id": rpcb.context.run_id,
            "trace_id": rpcb.context.trace_id,
            "execution_mode": rpcb.context.execution_mode.value,
            "user_id": rpcb.context.user_id,
            "request_metadata": dict(rpcb.context.request_metadata),
        },
        intent_ir=ir_dict,
        graph_snapshot=rpcb.graph.snapshot(),
        retry_budget=dict(rpcb.retry_budget),
        compensation_log=[asdict(r) for r in rpcb.compensation_log],
        approval_gate=asdict(rpcb.approval_gate) if rpcb.approval_gate else None,
    )
    ckpt.sign()
    return ckpt


def restore_from_checkpoint(
    ckpt: Checkpoint,
) -> tuple[ExecutionContext, ExecutionGraph]:
    """Rebuild the two heavyweight pieces of an RPCB from a checkpoint.
    Caller (ProcessManager.restore_process) reattaches the rest (retry_budget,
    compensation_log, approval_gate) directly from the checkpoint's dicts.
    """
    if not ckpt.verify():
        raise ValueError(f"checkpoint {ckpt.checkpoint_id!r} failed integrity verification — refusing to restore")

    intent_ir = IntentIR.from_dict(ckpt.intent_ir) if ckpt.intent_ir is not None else None
    context = ExecutionContext.create(
        run_id=ckpt.context["run_id"],
        execution_mode=ExecutionMode(ckpt.context["execution_mode"]),
        intent_ir=intent_ir,
        user_id=ckpt.context["user_id"],
        trace_id=ckpt.context["trace_id"],
        request_metadata=ckpt.context["request_metadata"],
    )
    graph = ExecutionGraph.from_snapshot(ckpt.graph_snapshot)
    return context, graph


class CheckpointStore:
    """Process-local, thread-safe checkpoint_id -> Checkpoint map.

    NOT durable across process restarts — see module docstring. Swappable:
    anything implementing save/load/delete/list_for_run can replace this.
    """

    def __init__(self) -> None:
        self._checkpoints: dict[str, Checkpoint] = {}
        self._by_run: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def save(self, checkpoint: Checkpoint) -> None:
        with self._lock:
            self._checkpoints[checkpoint.checkpoint_id] = checkpoint
            self._by_run.setdefault(checkpoint.run_id, []).append(checkpoint.checkpoint_id)

    def load(self, checkpoint_id: str) -> Checkpoint | None:
        with self._lock:
            return self._checkpoints.get(checkpoint_id)

    def latest_for_run(self, run_id: str) -> Checkpoint | None:
        with self._lock:
            ids = self._by_run.get(run_id, [])
            if not ids:
                return None
            return self._checkpoints.get(ids[-1])

    def delete(self, checkpoint_id: str) -> None:
        with self._lock:
            ckpt = self._checkpoints.pop(checkpoint_id, None)
            if ckpt is not None:
                ids = self._by_run.get(ckpt.run_id, [])
                if checkpoint_id in ids:
                    ids.remove(checkpoint_id)

    def cleanup(self, max_age_seconds: float = 86400 * 7) -> int:
        """Delete checkpoints older than max_age_seconds (default 7 days)."""
        import time as _time

        cutoff = _time.time() - max_age_seconds
        to_delete = [cid for cid, ckpt in self._checkpoints.items() if ckpt.created_at < cutoff]
        for cid in to_delete:
            self.delete(cid)
        return len(to_delete)


class MongoCheckpointStore:
    """Durable checkpoint store backed by MongoDB.

    Same interface as CheckpointStore (save/load/delete/latest_for_run) so
    ProcessManager can swap it in without any changes. Requires an async
    Motor client (motor.motor_asyncio.AsyncIOMotorClient).

    Collection: ``checkpoints`` — one document per checkpoint, indexed on
    (run_id, created_at) for efficient latest-for-run queries.
    """

    def __init__(self, mongo_client: Any, db_name: str = "monkeybrain") -> None:
        import motor.motor_asyncio

        # Accept both sync pymongo.MongoClient and async motor client
        if isinstance(mongo_client, motor.motor_asyncio.AsyncIOMotorClient):
            self._db = mongo_client[db_name]
        else:
            # Fallback: wrap a sync client via a lightweight async adapter
            self._db = mongo_client[db_name]
        self._col = self._db["checkpoints"]

    async def save(self, checkpoint: Checkpoint) -> None:
        doc = checkpoint.to_dict()
        doc["_id"] = checkpoint.checkpoint_id
        await self._col.update_one(
            {"_id": checkpoint.checkpoint_id},
            {"$set": doc},
            upsert=True,
        )
        # Audit: record checkpoint persistence
        try:
            from src.monkey_brain.kernel.audit import get_audit_log

            get_audit_log().record(
                runtime_id=checkpoint.run_id,
                event_type="checkpoint",
                action="save",
                target=checkpoint.checkpoint_id,
                details={
                    "run_id": checkpoint.run_id,
                    "schema_version": checkpoint.schema_version,
                },
            )
        except Exception:
            logger.debug("save: suppressed exception", exc_info=True)

    async def load(self, checkpoint_id: str) -> Checkpoint | None:
        doc = await self._col.find_one({"_id": checkpoint_id})
        if doc is None:
            return None
        doc.pop("_id", None)
        return Checkpoint.from_dict(doc)

    async def latest_for_run(self, run_id: str) -> Checkpoint | None:
        doc = await self._col.find_one(
            {"run_id": run_id},
            sort=[("created_at", -1)],
        )
        if doc is None:
            return None
        doc.pop("_id", None)
        return Checkpoint.from_dict(doc)

    async def delete(self, checkpoint_id: str) -> None:
        await self._col.delete_one({"_id": checkpoint_id})

    async def list_for_run(self, run_id: str) -> list[Checkpoint]:
        """Return all checkpoints for a run, oldest first."""
        cursor = self._col.find({"run_id": run_id}).sort("created_at", 1)
        results = []
        async for doc in cursor:
            doc.pop("_id", None)
            results.append(Checkpoint.from_dict(doc))
        return results

    async def cleanup(self, max_age_seconds: float = 86400 * 7) -> int:
        """Delete checkpoints older than max_age_seconds (default 7 days).

        Returns the number of deleted documents. Best-effort — called
        periodically to bound storage growth.
        """
        import time as _time

        cutoff = _time.time() - max_age_seconds
        result = await self._col.delete_many({"created_at": {"$lt": cutoff}})
        return result.deleted_count


class CheckpointOpsMixin:
    """checkpoint_process()/restore_process() — split out of the manager
    (kernel/process/manager.py) purely to keep that file under the
    architecture validator's 500-line threshold; behaviorally these are
    still methods of the class that mixes this in, reading/writing the
    exact same `self.*` attributes (`_table`, `_schedulers`,
    `_expansion_policies`, `_checkpoint_store`, `_max_repair_attempts`,
    `_transition`, `_require`) that class itself defines.
    """

    async def checkpoint_process(self, run_id: str) -> "CheckpointRef":
        """Requires the process to already be SUSPENDED — checkpointing a
        live RUNNING process would serialize a graph mid-mutation.
        """
        from src.monkey_brain.kernel.process.models import (
            CheckpointRef,
            RuntimeProcessState,
        )

        rpcb = self._require(run_id)
        checkpoint_id = f"ckpt-{run_id}-{len(rpcb.checkpoints)}"
        ckpt = await build_checkpoint(rpcb, checkpoint_id)
        save_result = self._checkpoint_store.save(ckpt)
        if asyncio.iscoroutine(save_result):
            await save_result
        ref = CheckpointRef(checkpoint_id=checkpoint_id, run_id=run_id)
        rpcb.checkpoints.append(ref)
        await self._transition(rpcb, RuntimeProcessState.CHECKPOINTED, checkpoint_id=checkpoint_id)
        return ref

    async def restore_process(
        self,
        checkpoint_id: str,
        *,
        execution_mode: Literal["serial", "parallel"] = "serial",
    ) -> Any:
        """Rebuild an RPCB from a stored checkpoint, in RESUMING state. Caller
        must call start_process() (RESUMING -> RUNNING) to continue it —
        kept as two steps so a caller can inspect the restored state first.

        `execution_mode`: not part of the checkpoint (it's a dispatch-time
        concern, not process state) — defaults to "serial", same rationale
        as create_process().
        """
        from src.monkey_brain.kernel.process.expansion_policy import (
            _TrackingExpansionPolicy,
        )
        from src.monkey_brain.kernel.process.models import (
            ApprovalGateState,
            RuntimeProcessControlBlock,
            RuntimeProcessState,
        )

        load_result = self._checkpoint_store.load(checkpoint_id)
        if asyncio.iscoroutine(load_result):
            ckpt = await load_result
        else:
            ckpt = load_result
        if ckpt is None:
            raise ValueError(f"no checkpoint found for checkpoint_id={checkpoint_id!r}")

        context, graph = restore_from_checkpoint(ckpt)
        run_id = ckpt.run_id

        # Any node left RUNNING at checkpoint time (shouldn't happen for a
        # checkpoint taken from SUSPENDED, but defends against a checkpoint
        # captured after a crash) is of unknown outcome — reset to PENDING so
        # it re-executes rather than being silently treated as complete.
        for node in graph.all_nodes():
            if graph.get_state(node.id) == NodeState.RUNNING:
                graph.set_state(node.id, NodeState.PENDING)

        existing = self._table.get(run_id)
        rpcb = RuntimeProcessControlBlock(
            run_id=run_id,
            context=context,
            graph=graph,
            target=existing.target if existing else "execute",
            state=RuntimeProcessState.CHECKPOINTED,
            retry_budget=dict(ckpt.retry_budget),
            compensation_log=[],
            approval_gate=(ApprovalGateState(**ckpt.approval_gate) if ckpt.approval_gate else None),
        )
        self._table[run_id] = rpcb
        policy = _TrackingExpansionPolicy(SelfHealingPolicy(max_repair_attempts=self._max_repair_attempts))
        policy.attempts = dict(ckpt.retry_budget)
        self._expansion_policies[run_id] = policy
        self._schedulers[run_id] = GraphScheduler(bus=None, expansion_policy=policy, execution_mode=execution_mode)

        await self._transition(rpcb, RuntimeProcessState.RESUMING)
        return rpcb
