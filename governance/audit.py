"""A small, append-only event log for governance decisions.

This is DELIBERATELY separate from src/monkey_brain/kernel/audit.py's
AuditLog, which is CognitiveOS's product security-audit trail for
security-critical operations (payments, orders, world mutations, ...).
Mixing this development-process log into that product trail would make
it look like CognitiveOS's runtime has a human-approval gate in front of
its own operations, which is not true — see governance/README.md.

Event types (Section 25 of the approval-artifact spec, plus
`approval_authorized` — added specifically so a SUCCESSFUL governance
decision has something real to audit too; see governance/README.md
"Audit failure precedence" for why this one addition was necessary
despite the general "don't invent event types" rule):
    approval_created, approval_rejected, approval_expired, approval_revoked,
    approval_superseded, approval_validation_failed, scope_change_requested,
    implementation_blocked_by_approval, approval_authorized

Never log secrets. Append failure raises (fail-closed) rather than
swallowing the error — an approval-governance event that silently failed
to record is exactly the kind of "pretend success" Section 20 forbids.

Not idempotent: no caller in this package retries a failed
record_governance_event() call (Section 12 — fail closed instead of an
unbounded/duplicate-risking retry loop), so duplicate-event risk from
retries does not arise today. Each event still carries a unique
`event_id` (Section 13) for forward-compatible correlation/dedup if a
future caller ever does retry.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_EVENT_LOG = Path(__file__).resolve().parent / "approvals" / "audit.jsonl"

EVENT_TYPES = frozenset(
    {
        "approval_created",
        "approval_rejected",
        "approval_expired",
        "approval_revoked",
        "approval_superseded",
        "approval_validation_failed",
        "scope_change_requested",
        "implementation_blocked_by_approval",
        "approval_authorized",
    }
)


class GovernanceAuditError(Exception):
    """Raised when a governance event cannot be durably recorded."""


def record_governance_event(
    event_type: str,
    *,
    details: dict[str, Any],
    path: Path | str = DEFAULT_EVENT_LOG,
) -> str:
    """Append one governance event. Returns the event's `event_id`.

    Raises GovernanceAuditError (never silently swallowed) if the event
    type is unrecognized or the write is not durable — see main() in
    governance/cli.py for how a caller must treat that failure.
    """
    if event_type not in EVENT_TYPES:
        raise GovernanceAuditError(f"unknown governance event_type {event_type!r}")
    event_id = uuid.uuid4().hex
    entry = {
        "event_id": event_id,
        "event_type": event_type,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "details": details,
    }
    log_path = Path(path)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
            f.flush()
    except OSError as exc:
        raise GovernanceAuditError(f"failed to durably record governance event {event_type!r}: {exc}") from exc
    return event_id


def read_governance_events(
    path: Path | str = DEFAULT_EVENT_LOG,
) -> list[dict[str, Any]]:
    log_path = Path(path)
    if not log_path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events
