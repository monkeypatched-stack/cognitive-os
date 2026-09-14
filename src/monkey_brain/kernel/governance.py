"""Governance — Runtime Charter and jurisdiction-based policy enforcement.

Every runtime has a Runtime Charter that defines its identity, owner,
type, jurisdiction, constitution, policies, capabilities, and trust
relationships.  The charter is the root of policy evaluation.

Naming note (Step 12.10 audit): this module's `GovernanceEngine` shares a
name with a second, unrelated class at `kernel/society/governance.py`.
This one — `get_governance_engine()` — is LIVE in production: it's called
directly from the `/plan`, `/execute`, `/predict`, and `/query` route
handlers to gate requests against a runtime's charter. The other one
governs individual actors' permissions/trust/safety WITHIN a Society Runtime
(a different layer, different data model, dormant in production). They are
never meant to be interchangeable — do not import one where the other is
expected. This module is not merged, renamed, or otherwise modified as part
of that audit; it is production-critical and explicitly left unchanged.
"""

from __future__ import annotations

import logging
import time
from src.monkey_brain.kernel.audit import get_audit_log
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("agentos.governance")
_UNCONFIGURED_WARNED = False


class RuntimeType(str, Enum):
    PERSONAL = "personal"
    COMMUNITY = "community"
    ENTERPRISE = "enterprise"
    INSTITUTION = "institution"
    GOVERNMENT = "government"


class Jurisdiction(str, Enum):
    US = "us"
    EU = "eu"
    UK = "uk"
    GLOBAL = "global"
    CUSTOM = "custom"


@dataclass
class RuntimeCharter:
    """The root governance document for a runtime.

    Defines identity, owner, type, jurisdiction, constitution,
    policies, capabilities, and trust relationships.
    """

    runtime_id: str = ""
    owner: str = ""
    runtime_type: RuntimeType = RuntimeType.PERSONAL
    jurisdiction: Jurisdiction = Jurisdiction.GLOBAL
    constitution: list[str] = field(default_factory=list)  # fundamental rules
    policies: dict[str, Any] = field(default_factory=dict)  # governance policies
    capabilities: list[str] = field(default_factory=list)
    trust_relationships: list[str] = field(default_factory=list)  # runtime_ids
    created_at: float = field(default_factory=time.time)
    version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "owner": self.owner,
            "runtime_type": self.runtime_type.value,
            "jurisdiction": self.jurisdiction.value,
            "constitution": self.constitution,
            "policies": self.policies,
            "capabilities": self.capabilities,
            "trust_relationships": self.trust_relationships,
            "created_at": self.created_at,
            "version": self.version,
        }


class GovernanceEngine:
    """Evaluates governance decisions against real OPA policy
    (opa/policies/agentos_governance.rego), not an in-memory stub.

    register_charter()/get_charter() are kept for the RuntimeCharter data
    shape's own backward compatibility (some unrelated dead code still
    imports RuntimeCharter from this module), but evaluate() no longer
    reads self._charters at all — the previous version's in-memory
    per-process dict had no real provisioning path (register_charter()
    has zero callers in production; no charter is ever created at boot;
    there is no API to make one), so "governance" was either always
    "not configured" (allow everything) or, in an earlier version, always
    "no_charter" (deny everything for every runtime — a fail-closed
    control with no provisioning path, which is an outage, not security;
    see tests/security/test_governance_gate.py). Real decisions now come
    from OPA's data layer, which — unlike this dict — IS provisionable at
    runtime without a code deploy (PUT /v1/data/agentos/governance/...).
    """

    def __init__(self) -> None:
        self._charters: dict[str, RuntimeCharter] = {}
        self._decisions: list[dict[str, Any]] = []
        self._max_decisions = 10000

    def register_charter(self, charter: RuntimeCharter) -> None:
        self._charters[charter.runtime_id] = charter

    def _record_and_return_decision(self, runtime_id: str, action: str, decision: dict[str, Any]) -> dict[str, Any]:
        """Helper: record policy decision to audit log and in-memory decisions, then return.

        This ensures all policy decisions are persisted durably, even early-return error paths.
        """
        self._decisions.append(
            {
                "runtime_id": runtime_id,
                "action": action,
                **decision,
                "timestamp": time.time(),
            }
        )
        if len(self._decisions) > self._max_decisions:
            self._decisions = self._decisions[-self._max_decisions :]

        # Persist to durable audit log (Task 2: enforce gap fix)
        try:
            get_audit_log().record_policy_decision(runtime_id, action, decision)
        except Exception as exc:
            # Log but don't fail — audit persistence failure shouldn't block policy evaluation
            logger.error("Failed to persist policy decision to audit log: %s", exc)

        return decision

    def get_charter(self, runtime_id: str) -> RuntimeCharter | None:
        return self._charters.get(runtime_id)

    def is_configured(self) -> bool:
        """Whether OPA is actually configured (OPA_URL set) — this is now
        the real signal for "is governance in use," not whether anyone
        has called the dead register_charter() API."""
        import os

        return bool(os.getenv("OPA_URL", "").strip())

    async def evaluate(self, runtime_id: str, action: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Evaluate whether an action is permitted, via OPA's real
        opa/policies/agentos_governance.rego policy (package agentos.governance).

        Returns:
            {
                "allowed": bool,
                "reason": str,
                "violations": list,
                "approval_mode": str,  # AUTO_APPROVE | HUMAN_APPROVAL_REQUIRED | DENY
                "approval_source": str,  # POLICY_AUTOMATIC | HUMAN
                "risk_level": str,  # LOW | MEDIUM | HIGH | CRITICAL
                "policy_rule": str,  # OPA rule that matched
                "requires_hitl": bool,  # Whether human-in-the-loop is required
            }

        All fields are present in response; new fields reflect OPA output or sensible defaults.

        default_allow=True: when OPA_URL is unset or OPA is unreachable,
        this behaves exactly like the old "not configured" case — allow,
        don't fail-closed with no provisioning path. Once OPA is actually
        reachable, the policy itself defaults to deny internally and only
        allows through explicit rules (see the .rego file), so a
        misconfigured-but-reachable OPA fails closed, not silently open.

        When OPA_URL is unset or OPA is unreachable, governed actions deny
        unless COGNITIVEOS_ALLOW_INSECURE_DEV_MODE is set. default_allow is
        False: unknown ≠ allow.
        """
        from src.monkey_brain.kernel.production_gates import (
            insecure_dev_mode,
            require_opa,
        )
        from src.monkey_brain.kernel.trusted_auth import (
            get_trusted_auth,
            strip_untrusted_security_signals,
        )

        trusted = get_trusted_auth().to_opa_auth()
        ctx = strip_untrusted_security_signals(dict(context or {}))
        ctx["trusted_auth"] = trusted
        ctx["auth"] = trusted

        if require_opa() and not self.is_configured():
            decision = {
                "allowed": False,
                "reason": "opa_required_but_not_configured",
                "violations": [
                    {
                        "rule": "opa_required_but_not_configured",
                        "type": "production_gate",
                    }
                ],
                "approval_mode": "DENY",
                "approval_source": "POLICY_AUTOMATIC",
                "risk_level": "CRITICAL",
                "policy_rule": "opa_required_but_not_configured",
                "requires_hitl": False,
            }
            return self._record_and_return_decision(runtime_id, action, decision)
        try:
            from services.common.opa import evaluate_full
        except Exception as exc:
            if insecure_dev_mode() and not require_opa():
                logger.warning(
                    "Governance: OPA client unavailable, allowing (insecure-dev): %s",
                    exc,
                )
                decision = {
                    "allowed": True,
                    "reason": "opa_client_unavailable",
                    "violations": [],
                    "approval_mode": "AUTO_APPROVE",
                    "approval_source": "POLICY_AUTOMATIC",
                    "risk_level": "LOW",
                    "policy_rule": "default_allow_insecure_dev",
                    "requires_hitl": False,
                }
                return self._record_and_return_decision(runtime_id, action, decision)
            logger.error("Governance: OPA client unavailable, denying: %s", exc)
            decision = {
                "allowed": False,
                "reason": "opa_client_unavailable",
                "violations": [{"rule": "opa_unavailable", "type": "fail_closed"}],
                "approval_mode": "DENY",
                "approval_source": "POLICY_AUTOMATIC",
                "risk_level": "CRITICAL",
                "policy_rule": "opa_unavailable",
                "requires_hitl": False,
            }
            return self._record_and_return_decision(runtime_id, action, decision)

        input_data = {
            "runtime_id": runtime_id,
            "action": action,
            "context": ctx,
            "auth": trusted,
        }
        try:
            result = await evaluate_full("agentos/governance", input_data, default_allow=False)
        except Exception as exc:
            if insecure_dev_mode() and not require_opa():
                logger.warning(
                    "Governance: OPA evaluation failed, allowing (insecure-dev): %s",
                    exc,
                )
                decision = {
                    "allowed": True,
                    "reason": "opa_evaluation_failed",
                    "violations": [],
                    "approval_mode": "AUTO_APPROVE",
                    "approval_source": "POLICY_AUTOMATIC",
                    "risk_level": "LOW",
                    "policy_rule": "default_allow_insecure_dev",
                    "requires_hitl": False,
                }
                return self._record_and_return_decision(runtime_id, action, decision)
            logger.error("Governance: OPA evaluation failed, denying: %s", exc)
            decision = {
                "allowed": False,
                "reason": "opa_unavailable",
                "violations": [{"rule": "opa_unavailable", "type": "fail_closed"}],
                "approval_mode": "DENY",
                "approval_source": "POLICY_AUTOMATIC",
                "risk_level": "CRITICAL",
                "policy_rule": "opa_unavailable",
                "requires_hitl": False,
            }
            return self._record_and_return_decision(runtime_id, action, decision)

        allowed = bool(result.get("allowed", False))
        if result.get("source") == "skip":
            if require_opa() or not insecure_dev_mode():
                allowed = False
                reason = "opa_required_but_not_configured"
                approval_mode = "DENY"
                risk_level = "CRITICAL"
                policy_rule = "opa_required_but_not_configured"
            else:
                global _UNCONFIGURED_WARNED
                if not _UNCONFIGURED_WARNED:
                    _UNCONFIGURED_WARNED = True
                    logger.warning(
                        "Governance is NOT configured — OPA_URL is unset; allowing only because "
                        "COGNITIVEOS_ALLOW_INSECURE_DEV_MODE is set."
                    )
                reason = "governance_not_configured"
                allowed = True
                approval_mode = "AUTO_APPROVE"
                risk_level = "LOW"
                policy_rule = "default_allow_insecure_dev"
        else:
            reason = "" if allowed else (result.get("reason") or "denied by policy")
            # Extract approval decision from OPA result
            approval_mode = result.get("approval_mode", "AUTO_APPROVE" if allowed else "DENY")
            risk_level = result.get("risk_level", "LOW" if allowed else "HIGH")
            policy_rule = result.get("policy_rule", reason or "policy_evaluation")

        requires_hitl = approval_mode == "HUMAN_APPROVAL_REQUIRED"
        approval_source = "POLICY_AUTOMATIC"  # OPA-determined approvals are always policy-automatic

        decision = {
            "allowed": allowed,
            "reason": reason,
            "violations": [] if allowed else [{"rule": policy_rule, "type": "opa"}],
            "approval_mode": approval_mode,
            "approval_source": approval_source,
            "risk_level": risk_level,
            "policy_rule": policy_rule,
            "requires_hitl": requires_hitl,
        }
        return self._record_and_return_decision(runtime_id, action, decision)

    def audit_decisions(self, runtime_id: str | None = None, limit: int = 100) -> list[dict]:
        """Return recent governance decisions, optionally filtered by runtime."""
        decisions = self._decisions
        if runtime_id:
            decisions = [d for d in decisions if d["runtime_id"] == runtime_id]
        return decisions[-limit:]


_default_engine: GovernanceEngine | None = None


def get_governance_engine() -> GovernanceEngine:
    global _default_engine
    if _default_engine is None:
        _default_engine = GovernanceEngine()
    return _default_engine
