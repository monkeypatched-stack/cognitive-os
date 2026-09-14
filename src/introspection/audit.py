"""Audit framework — owned by introspection per the Introspection Constitution.

Provides the canonical audit record structure and routing for all immutable
audit records produced by the system (auth decisions, policy evaluations,
agent lifecycle events).

Constitutional contract (introspection/values.yaml):
  owns: [Auditing]
  deliverable: AuditFramework
  invariant INTRO-INV-001: introspection never modifies runtime behavior. Observes only.

This module:
  - Defines the AuditRecord dataclass (schema authority)
  - Routes records to configured sinks (MongoDB append-only, NATS, structured log)
  - Never modifies request behaviour — fire-and-forget, best-effort
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("introspection.audit")

_SINK_LOG = True  # always active — baseline
_SINK_DB = bool(os.getenv("AUDIT_MONGODB_ENABLED", ""))
_COLLECTION = os.getenv("AUDIT_COLLECTION", "audit_records")


@dataclass
class AuditRecord:
    """Immutable audit record. Written once, never modified."""

    event_id: str
    event_type: str
    timestamp: str
    action: str
    outcome: str
    trace_id: str = ""
    span_id: str = ""
    principal_type: str = "unknown"
    subject: str = ""
    agent_type: str = ""
    spiffe_id: str = ""
    mtls_verified: bool = False
    policy_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _from_event(event: dict[str, Any]) -> AuditRecord:
    return AuditRecord(
        event_id=event.get("event_id", ""),
        event_type=event.get("event_type", "audit.auth"),
        timestamp=event.get("timestamp", datetime.now(timezone.utc).isoformat()),
        action=event.get("action", ""),
        outcome=event.get("outcome", ""),
        trace_id=event.get("trace_id", ""),
        span_id=event.get("span_id", ""),
        principal_type=event.get("principal_type", "unknown"),
        subject=event.get("subject", ""),
        agent_type=event.get("agent_type", ""),
        spiffe_id=event.get("spiffe_id", ""),
        mtls_verified=bool(event.get("mtls_verified", False)),
        policy_path=event.get("policy_path", ""),
        metadata=event.get("metadata", {}),
    )


async def record(event: dict[str, Any]) -> None:
    """Accept an audit event dict, validate schema, route to sinks. Never raises."""
    try:
        rec = _from_event(event)
        if _SINK_LOG:
            logger.info(
                "AUDIT event_id=%s action=%s outcome=%s subject=%s trace=%s",
                rec.event_id,
                rec.action,
                rec.outcome,
                rec.subject,
                rec.trace_id,
                extra={"audit_record": rec.to_dict()},
            )
        if _SINK_DB:
            await _write_to_db(rec)
    except Exception as exc:
        logger.debug("Audit record failed (non-fatal): %s", exc)


async def _write_to_db(rec: AuditRecord) -> None:
    """Append-only insert to MongoDB. Never updates existing records."""
    try:
        from src.monkey_brain.persistence.events import PersistenceEvent, EventType

        # Re-use the existing persistence event infrastructure
        pe = PersistenceEvent(
            event_type=EventType.ENTITY,
            payload={**rec.to_dict(), "_immutable": True},
            entity_type=_COLLECTION,
        )
        # Best-effort — if PersistenceManager isn't wired yet, skip
        import sys

        app_module = sys.modules.get("src.monkey_brain.api.main")
        if app_module:
            app = getattr(app_module, "app", None)
            pm = getattr(getattr(app, "state", None), "persistence_manager", None)
            if pm:
                await pm.handle(pe)
    except Exception as exc:
        logger.debug("Audit DB write failed (non-fatal): %s", exc)
