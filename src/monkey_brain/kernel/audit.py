"""Immutable Audit Log — append-only, tamper-evident audit trail.

Every operation records: runtime, proposal, signature, policy decision,
trust decision, merge decision, execution graph, world revision.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

logger = logging.getLogger("agentos.audit")

SECURITY_CRITICAL_EVENT_TYPES = frozenset(
    {
        "execute",
        "governance",
        "security",
        "auth",
        "world_mutation",
        "plan",
        "authorization",
        "policy",
        "login",
        "token",
        "delegation",
        "bias_audit",
    }
)


class AuditPersistenceError(RuntimeError):
    """Raised when a security-critical audit record cannot be durably stored."""


class MemoryDurableAuditStore:
    """Process-local append-only store used when Mongo is unavailable in insecure-dev.

    Not Redis. Survives AuditLog reconstruction in the same process; tests can
    share the backing dict across 'restarts'.
    """

    def __init__(self, backing: dict[str, dict[str, Any]] | None = None) -> None:
        self._docs: dict[str, dict[str, Any]] = backing if backing is not None else {}
        self._lock = threading.Lock()

    def append(self, tenant_id: str, event_type: str, payload: dict[str, Any]) -> None:
        entry_id = str(payload.get("entry_id") or uuid4())
        doc = {**payload, "tenant_id": tenant_id, "_immutable": True}
        with self._lock:
            if entry_id in self._docs:
                raise AuditPersistenceError("audit records are append-only; refuse overwrite")
            self._docs[entry_id] = doc

    def find(
        self,
        runtime_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._docs.values())
        if runtime_id:
            rows = [r for r in rows if r.get("runtime_id") == runtime_id]
        if event_type:
            rows = [r for r in rows if r.get("event_type") in (event_type, f"audit.{event_type}")]
        return rows[-limit:]


class MongoAuditStore:
    """Append-only Mongo collection — the production source of truth for audit."""

    def __init__(self, collection: Any = None) -> None:
        self._collection = collection

    def _col(self) -> Any:
        if self._collection is not None:
            return self._collection
        from src.monkey_brain.persistence.db_pool import get_db_pool
        import os

        pool = get_db_pool()
        name = os.getenv("AUDIT_COLLECTION", "audit_records")
        return pool.get_collection(name)

    def ping(self) -> None:
        col = self._col()
        col.database.client.admin.command("ping")

    def append(self, tenant_id: str, event_type: str, payload: dict[str, Any]) -> None:
        entry_id = str(payload.get("entry_id") or uuid4())
        col = self._col()
        if col.find_one({"entry_id": entry_id}):
            raise AuditPersistenceError("audit records are append-only; refuse overwrite")
        col.insert_one(
            {
                **payload,
                "tenant_id": tenant_id,
                "_immutable": True,
            }
        )

    def find(
        self,
        runtime_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        if runtime_id:
            query["runtime_id"] = runtime_id
        if event_type:
            query["event_type"] = {"$in": [event_type, f"audit.{event_type}"]}
        cursor = self._col().find(query).sort("timestamp", 1).limit(limit)
        return list(cursor)


@dataclass
class AuditEntry:
    """A single immutable audit record."""

    entry_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: float = field(default_factory=time.time)
    runtime_id: str = ""
    event_type: str = ""  # proposal | trust | merge | execute | governance | security
    action: str = ""
    actor: str = ""  # who performed the action
    target: str = ""  # what was affected
    outcome: str = ""  # success | failure | denied
    details: dict[str, Any] = field(default_factory=dict)
    proposal_id: str = ""
    signature: str = ""
    world_revision: int = 0
    prev_hash: str = ""  # hash of previous entry (chain integrity)
    entry_hash: str = ""  # hash of this entry

    def compute_hash(self) -> str:
        """Compute SHA-256 hash of this entry's content."""
        blob = json.dumps(
            {
                "entry_id": self.entry_id,
                "timestamp": self.timestamp,
                "runtime_id": self.runtime_id,
                "event_type": self.event_type,
                "action": self.action,
                "actor": self.actor,
                "target": self.target,
                "outcome": self.outcome,
                "details": self.details,
                "proposal_id": self.proposal_id,
                "world_revision": self.world_revision,
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
            default=str,
        ).encode()
        return hashlib.sha256(blob).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp,
            "runtime_id": self.runtime_id,
            "event_type": self.event_type,
            "action": self.action,
            "actor": self.actor,
            "target": self.target,
            "outcome": self.outcome,
            "details": self.details,
            "proposal_id": self.proposal_id,
            "signature": self.signature,
            "world_revision": self.world_revision,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AuditEntry":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


class AuditLog:
    """Append-only, tamper-evident audit log.

    Each entry chains to the previous via prev_hash, making the log
    a hash chain.  Any tampering breaks the chain.

    Optionally persists to an AppendOnlyLog for durability beyond
    process lifetime.
    """

    def __init__(self, max_entries: int = 1_000_000) -> None:
        self._entries: list[AuditEntry] = []
        self._last_hash: str = ""
        self._lock = threading.Lock()
        self._max = max_entries
        self._store: Any = None  # AppendOnlyLog for durability (optional)

    def set_store(self, store: Any) -> None:
        """Attach an AppendOnlyLog for durable persistence."""
        self._store = store

    def record(
        self,
        runtime_id: str,
        event_type: str,
        action: str,
        actor: str = "",
        target: str = "",
        outcome: str = "success",
        details: dict[str, Any] | None = None,
        proposal_id: str = "",
        signature: str = "",
        world_revision: int = 0,
        *,
        critical: bool | None = None,
        principal: str = "",
        correlation_id: str = "",
        policy_decision: str = "",
    ) -> AuditEntry:
        """Append an audit entry. Security-critical types fail closed on persist error."""
        is_critical = event_type in SECURITY_CRITICAL_EVENT_TYPES if critical is None else critical
        details = dict(details or {})
        if principal:
            details.setdefault("principal", principal)
        if correlation_id:
            details.setdefault("correlation_id", correlation_id)
        if policy_decision:
            details.setdefault("policy_decision", policy_decision)

        with self._lock:
            if len(self._entries) >= self._max:
                logger.warning("Audit log in-memory cache full — rotating cache only; durable store is source of truth")
                self._entries = self._entries[-self._max // 2 :]

            entry = AuditEntry(
                runtime_id=runtime_id,
                event_type=event_type,
                action=action,
                actor=actor,
                target=target,
                outcome=outcome,
                details=details,
                proposal_id=proposal_id,
                signature=signature,
                world_revision=world_revision,
                prev_hash=self._last_hash,
            )
            entry.entry_hash = entry.compute_hash()
            self._last_hash = entry.entry_hash
            self._entries.append(entry)

        try:
            store = self._ensure_store(required=is_critical)
            store.append(
                tenant_id=runtime_id or "audit",
                event_type=f"audit.{event_type}",
                payload=entry.to_dict(),
            )
        except AuditPersistenceError:
            raise
        except Exception as exc:
            if is_critical:
                raise AuditPersistenceError(
                    f"failed to durably persist security-critical audit event {event_type}/{action}"
                ) from exc
            logger.error(
                "audit persist failed for non-critical event_type=%s: %s",
                event_type,
                exc,
            )

        return entry

    def _ensure_store(self, *, required: bool) -> Any:
        if self._store is not None:
            return self._store
        try:
            mongo = MongoAuditStore()
            mongo.ping()
            self._store = mongo
            return mongo
        except Exception as exc:
            from src.monkey_brain.kernel.production_gates import insecure_dev_mode

            if required and not insecure_dev_mode():
                raise AuditPersistenceError("durable audit store (MongoDB) is unavailable") from exc
            logger.warning(
                "Mongo audit store unavailable (%s); using process-local durable cache",
                exc,
            )
            self._store = MemoryDurableAuditStore()
            return self._store

    def verify_chain(self) -> tuple[bool, int]:
        """Verify the hash chain integrity.  Returns (valid, broken_at_index)."""
        with self._lock:
            prev = ""
            for i, entry in enumerate(self._entries):
                if entry.prev_hash != prev:
                    return False, i
                expected = entry.compute_hash()
                if expected != entry.entry_hash:
                    return False, i
                prev = entry.entry_hash
        return True, -1

    def verify_and_load(self, path: str) -> int:
        """Load entries from a JSONL file and verify hash chain integrity.

        Returns the number of valid entries loaded.  Entries that break
        the chain are discarded with a warning.
        """
        import json
        from pathlib import Path

        if not Path(path).exists():
            return 0

        loaded = 0
        prev_hash = ""
        with self._lock:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        entry = AuditEntry.from_dict(data)

                        # Verify chain link
                        if entry.prev_hash != prev_hash:
                            logger.warning(
                                "[audit] chain break at entry %s — discarding",
                                entry.entry_id,
                            )
                            continue

                        # Verify hash
                        expected = entry.compute_hash()
                        if expected != entry.entry_hash:
                            logger.warning(
                                "[audit] hash mismatch at entry %s — discarding",
                                entry.entry_id,
                            )
                            continue

                        self._entries.append(entry)
                        self._last_hash = entry.entry_hash
                        prev_hash = entry.entry_hash
                        loaded += 1
                    except Exception as exc:
                        logger.warning("[audit] failed to parse entry: %s", exc)

        logger.info("[audit] loaded %d entries from %s", loaded, path)
        return loaded

    def query(
        self,
        runtime_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Query audit entries with optional filters. Durable store is source of truth."""
        if self._store is not None and hasattr(self._store, "find"):
            try:
                rows = self._store.find(runtime_id=runtime_id, event_type=event_type, limit=limit)
                loaded: list[AuditEntry] = []
                for row in rows:
                    payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
                    try:
                        loaded.append(AuditEntry.from_dict(payload))
                    except Exception:
                        continue
                if loaded:
                    return loaded[-limit:]
            except Exception as exc:
                logger.error("durable audit query failed, falling back to cache: %s", exc)
        result = self._entries
        if runtime_id:
            result = [e for e in result if e.runtime_id == runtime_id]
        if event_type:
            result = [e for e in result if e.event_type == event_type]
        return result[-limit:]

    def record_policy_decision(self, runtime_id: str, action: str, decision: dict[str, Any]) -> AuditEntry:
        """Record a governance policy decision to the durable audit log.

        Policy decisions are security-critical and fail closed on persist error.

        Args:
            runtime_id: Runtime/principal ID making the request
            action: Action being evaluated (e.g., "agent.propose_operation")
            decision: Policy decision dict with keys: allowed, reason, approval_mode,
                     risk_level, policy_rule, violations, etc.

        Returns:
            The AuditEntry that was recorded
        """
        return self.record(
            runtime_id=runtime_id,
            event_type="policy_decision",
            action=action,
            outcome="allow" if decision.get("allowed") else "deny",
            details={
                "approval_mode": decision.get("approval_mode", "AUTO_APPROVE"),
                "approval_source": decision.get("approval_source", "POLICY_AUTOMATIC"),
                "risk_level": decision.get("risk_level", "LOW"),
                "policy_rule": decision.get("policy_rule", ""),
                "requires_hitl": decision.get("requires_hitl", False),
                "violations": decision.get("violations", []),
                "reason": decision.get("reason", ""),
            },
            critical=True,  # Policy decisions are security-critical
        )

    def count(self) -> int:
        return len(self._entries)

    def last_n(self, n: int) -> list[AuditEntry]:
        return self._entries[-n:]


_default_audit: AuditLog | None = None


def get_audit_log() -> AuditLog:
    global _default_audit
    if _default_audit is None:
        _default_audit = AuditLog()
    return _default_audit
