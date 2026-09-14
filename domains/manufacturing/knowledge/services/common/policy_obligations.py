"""Policy obligations — what enforcement must happen beyond allow/deny.

The spec defines the policy agent as a PEP that enforces not just allow/deny
but also:
  header_inject     — add headers before forwarding (e.g. X-Tenant-ID, X-Trust-Level)
  response_redact   — mask or strip fields from the response (field-level redaction)
  route_override    — forward to a different target (e.g. sandbox instead of production)
  quorum_required   — block until N agents approve (multi-party auth for high-risk ops)
  rate_limit        — enforce a per-principal rate limit for this operation
  audit_force       — force an audit record regardless of normal sampling rate

OPA returns obligations as part of its result document:
  {
    "allow": true,
    "obligations": [
      {"type": "header_inject",   "headers": {"X-Tenant-ID": "acme"}},
      {"type": "response_redact", "fields": ["ssn", "credit_card"]},
      {"type": "route_override",  "target": "http://sandbox:8080"},
      {"type": "quorum_required", "min_approvers": 2, "timeout_seconds": 60}
    ]
  }

When OPA is not configured, obligations come from the local rule set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ObligationType(str, Enum):
    HEADER_INJECT = "header_inject"
    RESPONSE_REDACT = "response_redact"
    ROUTE_OVERRIDE = "route_override"
    QUORUM_REQUIRED = "quorum_required"
    RATE_LIMIT = "rate_limit"
    AUDIT_FORCE = "audit_force"


@dataclass
class PolicyObligation:
    type: ObligationType
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type.value, **self.params}


# ---------------------------------------------------------------------------
# Obligation extraction from OPA result
# ---------------------------------------------------------------------------


def extract_obligations(opa_result: dict[str, Any]) -> list[PolicyObligation]:
    """Parse obligations from an OPA result document."""
    raw = opa_result.get("obligations", [])
    obligations: list[PolicyObligation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        raw_type = item.get("type", "")
        try:
            otype = ObligationType(raw_type)
        except ValueError:
            continue
        params = {k: v for k, v in item.items() if k != "type"}
        obligations.append(PolicyObligation(type=otype, params=params))
    return obligations


# ---------------------------------------------------------------------------
# Local obligation derivation (no OPA fallback)
# ---------------------------------------------------------------------------


def derive_obligations(
    principal: dict[str, Any],
    action: str,
    resource: str,
    risk_level: str = "low",
) -> list[PolicyObligation]:
    """Derive obligations from local context when OPA is not configured.

    Risk sensitivity rules:
      high / critical → quorum_required (2 approvers, 60 s timeout)
      any agent       → inject X-Trust-Level and X-SPIFFE-ID headers
      read-only scope → response_redact PII fields
    """
    obligations: list[PolicyObligation] = []
    principal_type = principal.get("principal_type", "unknown")
    spiffe_id = principal.get("spiffe_id", "")
    trust_level = "high" if principal.get("mtls_verified") else "standard"

    # Always inject trust metadata headers so downstream services can act on them
    obligations.append(
        PolicyObligation(
            type=ObligationType.HEADER_INJECT,
            params={
                "headers": {
                    "X-Trust-Level": trust_level,
                    "X-Principal-Type": principal_type,
                    "X-SPIFFE-ID": spiffe_id or "",
                }
            },
        )
    )

    # High-risk operations require quorum approval
    if risk_level in ("high", "critical"):
        min_approvers = 3 if risk_level == "critical" else 2
        obligations.append(
            PolicyObligation(
                type=ObligationType.QUORUM_REQUIRED,
                params={"min_approvers": min_approvers, "timeout_seconds": 60},
            )
        )
        obligations.append(
            PolicyObligation(
                type=ObligationType.AUDIT_FORCE,
                params={"reason": f"risk_level:{risk_level}"},
            )
        )

    # Agents with restricted scopes (read-only) get PII redacted from responses
    scopes = set(principal.get("scopes") or [])
    if scopes and not any("write" in s or "admin" in s for s in scopes):
        obligations.append(
            PolicyObligation(
                type=ObligationType.RESPONSE_REDACT,
                params={
                    "fields": ["email", "phone", "ssn", "credit_card", "password_hash"]
                },
            )
        )

    return obligations
