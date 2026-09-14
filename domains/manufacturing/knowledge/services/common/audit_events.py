"""Auth audit event emitter — thin service-layer wrapper.

Audit framework lives in src/introspection/audit.py (introspection owns AuditFramework).
NATS transport lives in cerebellum.capabilities.security.nats_audit (INV-003).
This module delegates to both and remains the public API for callers.
"""

import secrets
from datetime import datetime, timezone
from typing import Any

# Pull trace_id from context (INTRO-INV-003: every operation propagates trace)
try:
    from services.common.trace_context import get_trace_id
except ImportError:

    def get_trace_id() -> str:
        return ""


def _build_event(
    action: str,
    outcome: str,
    principal: dict[str, Any] | None = None,
    *,
    policy_path: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    p = principal or {}
    return {
        "event_id": f"auth-audit-{secrets.token_hex(6)}",
        "event_type": "audit.auth",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trace_id": get_trace_id(),
        "principal_type": p.get("principal_type", "unknown"),
        "subject": p.get("sub", ""),
        "agent_type": p.get("agent_type", ""),
        "spiffe_id": p.get("spiffe_id", ""),
        "mtls_verified": bool(p.get("mtls_verified", False)),
        "action": action,
        "outcome": outcome,
        "policy_path": policy_path,
        "metadata": metadata or {},
    }


async def emit(
    action: str,
    outcome: str,
    principal: dict[str, Any] | None = None,
    *,
    policy_path: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Emit an auth audit event. Never raises — audit is best-effort."""
    event = _build_event(
        action, outcome, principal, policy_path=policy_path, metadata=metadata
    )

    # Route through introspection audit framework first
    try:
        from src.introspection.audit import record

        await record(event)
    except Exception:
        pass

    # Transport via cerebellum NATS capability
    try:
        from cerebellum.capabilities.security.nats_audit import publish

        await publish(event)
    except Exception:
        # Last-resort structured log
        import logging

        logging.getLogger(__name__).info(
            "AUTH_AUDIT action=%s outcome=%s subject=%s trace=%s",
            event["action"],
            event["outcome"],
            event["subject"],
            event["trace_id"],
            extra={"audit_event": event},
        )
