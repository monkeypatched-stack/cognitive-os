"""Dynamic authorization policy context.

Spec: "Authorization is not static but adaptive to runtime conditions."

This module assembles a DynamicPolicyContext from three signal sources:
  1. Risk classification      — action + resource + time → risk_level
  2. Reliability scoring      — agent's historical reward from BellmanPolicy Q-table
  3. Audit flag detection     — is this principal flagged in recent audit events?

The context is passed into OPA as extra input, and used by AuthPolicyAgent to
derive obligations and adjust access (e.g. read-only for low-reliability agents).

All signals are best-effort — failures return safe defaults so dynamic context
never blocks the request path.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---- risk classification rules -----------------------------------------------
# Actions and resource patterns that elevate risk level.

_HIGH_RISK_ACTIONS = frozenset(
    {
        "delete",
        "purge",
        "revoke",
        "deactivate",
        "drop",
        "truncate",
        "approve",
        "release",
        "deploy",
        "promote",
    }
)
_CRITICAL_RISK_ACTIONS = frozenset(
    {
        "admin",
        "root",
        "sudo",
        "impersonate",
        "federation_trust",
    }
)
_SENSITIVE_RESOURCES = frozenset(
    {
        "production",
        "credentials",
        "secrets",
        "ca",
        "root_ca",
        "pki",
        "audit_log",
        "compliance",
        "billing",
    }
)

# Business hours (UTC) outside which elevated risk applies
_BUSINESS_HOURS_START = 7  # 07:00 UTC
_BUSINESS_HOURS_END = 19  # 19:00 UTC


@dataclass
class DynamicPolicyContext:
    risk_level: str = "low"  # low | medium | high | critical
    reliability_score: float = 1.0  # 0.0–1.0 from RL history (1.0 = no data = trust)
    audit_flagged: bool = False  # True if principal appears in recent deny events
    off_hours: bool = False  # True if request is outside business hours
    requires_quorum: bool = False  # derived from risk_level
    read_only_enforced: bool = False  # derived from low reliability_score

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_level": self.risk_level,
            "reliability_score": self.reliability_score,
            "audit_flagged": self.audit_flagged,
            "off_hours": self.off_hours,
            "requires_quorum": self.requires_quorum,
            "read_only_enforced": self.read_only_enforced,
        }


# ---------------------------------------------------------------------------
# Risk classification
# ---------------------------------------------------------------------------


def classify_risk(action: str, resource: str) -> str:
    """Classify request risk from action + resource keywords."""
    a = action.lower()
    r = resource.lower()

    if a in _CRITICAL_RISK_ACTIONS or any(
        kw in r for kw in ("root_ca", "federation_trust", "impersonate")
    ):
        return "critical"
    if a in _HIGH_RISK_ACTIONS or any(kw in r for kw in _SENSITIVE_RESOURCES):
        return "high"
    if "write" in a or "update" in a or "create" in a:
        return "medium"
    return "low"


def _is_off_hours() -> bool:
    hour = datetime.now(timezone.utc).hour
    return not (_BUSINESS_HOURS_START <= hour < _BUSINESS_HOURS_END)


# ---------------------------------------------------------------------------
# Reliability scoring from BellmanPolicy Q-table
# ---------------------------------------------------------------------------


def _reliability_from_policy(agent_type: str) -> float:
    """Look up the agent's avg reward from the BellmanPolicy Q-table.

    Returns 1.0 (full trust) when the policy or agent type is not found —
    unknown agents are given benefit of the doubt on first contact.
    """
    try:
        from broca.registry import get_registry

        registry = get_registry()
        runtime = getattr(registry, "_runtime", None)
        if runtime is None:
            return 1.0
        policy = getattr(runtime, "_policy", None)
        if policy is None:
            return 1.0
        # BellmanPolicy exposes reward_model.summary() which has avg_reward
        reward_summary = policy._reward_model.summary()
        avg = reward_summary.get("avg_reward", 1.0)
        # Normalize: reward range is [-2, 2], map to [0, 1]
        return max(0.0, min(1.0, (avg + 2.0) / 4.0))
    except Exception as exc:
        logger.debug("Reliability score unavailable: %s", exc)
        return 1.0


# ---------------------------------------------------------------------------
# Audit flag detection (Redis-based: were there recent denies for this subject?)
# ---------------------------------------------------------------------------


async def _is_audit_flagged(subject: str) -> bool:
    """Check if this principal appeared as a deny in the recent audit window (5 min)."""
    try:
        import redis.asyncio as aioredis

        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        r: aioredis.Redis = aioredis.from_url(redis_url, decode_responses=True)
        count = await r.get(f"audit:deny:{subject}")
        await r.aclose()
        return int(count or 0) >= 3  # 3+ denies in TTL window → flagged
    except Exception:
        return False


async def _record_audit_deny(subject: str) -> None:
    """Increment the deny counter for this subject (TTL 5 min)."""
    try:
        import redis.asyncio as aioredis

        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        r: aioredis.Redis = aioredis.from_url(redis_url, decode_responses=True)
        key = f"audit:deny:{subject}"
        await r.incr(key)
        await r.expire(key, 300)  # 5-minute sliding window
        await r.aclose()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def build_dynamic_context(
    principal: dict[str, Any],
    action: str,
    resource: str,
) -> DynamicPolicyContext:
    """Assemble DynamicPolicyContext from all signal sources."""
    risk_level = classify_risk(action, resource)
    off_hours = _is_off_hours()

    # Elevate risk for off-hours critical/high operations
    if off_hours and risk_level in ("high", "critical"):
        risk_level = "critical" if risk_level == "high" else risk_level

    subject = principal.get("sub", "")
    agent_type = principal.get("agent_type", "")
    principal_type = principal.get("principal_type", "user")

    reliability_score = (
        _reliability_from_policy(agent_type)
        if principal_type == "agent" and agent_type
        else 1.0
    )
    audit_flagged = await _is_audit_flagged(subject) if subject else False

    requires_quorum = risk_level in ("high", "critical") or audit_flagged
    # Low-reliability agents (< 0.3) get read-only enforcement
    read_only_enforced = reliability_score < 0.3 and principal_type == "agent"

    return DynamicPolicyContext(
        risk_level=risk_level,
        reliability_score=reliability_score,
        audit_flagged=audit_flagged,
        off_hours=off_hours,
        requires_quorum=requires_quorum,
        read_only_enforced=read_only_enforced,
    )
