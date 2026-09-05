"""Society Governance (Step 12.10) — introduces governance over the society.

Support:
    policies, permissions, trust, reputation,
    safety, compliance, auditing.

Governance constrains interaction, not cognition.

Naming note: this module's `SocietyGovernanceEngine` shares a name with a second,
unrelated class at `kernel/governance.py`, which evaluates runtime charters
and IS live in production (called from `/plan` /execute` /predict` /query`).
This one governs individual actors' permissions/trust/safety WITHIN a
Society Runtime — a different layer, different data model, owned by
`SocietyRuntime` (Step 12.10: moved here from being Planetary-Runtime-only,
matching Society's ownership of collective state established in Step
12.3). Do not import one expecting the other's behavior.

Scope correction (Society architecture review): `authorize()`/
`check_permission()` are NOT dormant — they are real, live inputs to
kernel/affiliations/graph.py's `resolve_communication` cascade, deciding
whether two actors may EXCHANGE MESSAGES at all. That is the full extent
of this engine's real authority in production today. It is NEVER
consulted by kernel/pipeline/action_executor.py or anything in
kernel/domains/ — capability-EXECUTION authority runs exclusively through
kernel/security_boundary.py::ensure_governed (SPIFFE + OPA + portable
delegation + approval). Do not wire this engine's authorize()/
check_permission() into a capability-authorization decision — that would
create a second, competing authority path this codebase does not
currently have and should not gain by accident.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class GovernanceLevel(Enum):
    GLOBAL = "global"
    ACTOR_TYPE = "actor_type"
    ACTOR_SPECIFIC = "actor_specific"
    CONTEXTUAL = "contextual"


class PolicyType(Enum):
    PERMISSION = "permission"
    RESTRICTION = "restriction"
    REQUIREMENT = "requirement"
    GUIDELINE = "guideline"
    SAFETY = "safety"
    COMPLIANCE = "compliance"


class ComplianceStatus(Enum):
    COMPLIANT = "compliant"
    NON_COMPLIANT = "non_compliant"
    UNDER_REVIEW = "under_review"
    EXEMPT = "exempt"


@dataclass(frozen=True)
class GovernancePolicy:
    """A governance policy constraining actor behavior."""
    policy_id: str = field(default_factory=lambda: uuid4().hex)
    name: str = ""
    description: str = ""
    policy_type: PolicyType = PolicyType.PERMISSION
    level: GovernanceLevel = GovernanceLevel.GLOBAL
    rules: tuple[str, ...] = ()
    scope: str = ""
    """Which actors this applies to."""
    enabled: bool = True
    priority: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Permission:
    """A specific permission granted to an actor."""
    permission_id: str = field(default_factory=lambda: uuid4().hex)
    actor_id: str = ""
    resource: str = ""
    action: str = ""
    granted_by: str = ""
    expires_at: float = 0.0
    """0.0 = never expires."""
    conditions: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuditEntry:
    """One entry in the governance audit log."""
    entry_id: str = field(default_factory=lambda: uuid4().hex)
    actor_id: str = ""
    action: str = ""
    policy_id: str = ""
    compliance_status: ComplianceStatus = ComplianceStatus.COMPLIANT
    details: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class TrustRecord:
    """A trust record for an actor."""
    actor_id: str = ""
    trust_score: float = 0.5
    """0.0 = completely untrusted, 1.0 = fully trusted."""
    evidence_count: int = 0
    last_evaluated: float = field(default_factory=time.time)
    factors: dict[str, float] = field(default_factory=dict)
    """Named trust factors: reliability, competence, honesty, etc."""


@dataclass(frozen=True)
class SafetyConstraint:
    """A safety constraint that must be satisfied."""
    constraint_id: str = field(default_factory=lambda: uuid4().hex)
    name: str = ""
    description: str = ""
    rule: str = ""
    severity: str = "high"
    """high, medium, low."""
    applies_to: tuple[str, ...] = ()
    """Actor types or IDs this applies to."""


class SocietyGovernanceEngine:
    """Manages governance over the society. Constrains interaction,
    not cognition."""

    def __init__(self) -> None:
        self._policies: dict[str, GovernancePolicy] = {}
        self._permissions: dict[str, Permission] = {}
        self._audit_log: list[AuditEntry] = []
        self._trust_records: dict[str, TrustRecord] = {}
        self._safety_constraints: dict[str, SafetyConstraint] = {}

    # ── Policies ─────────────────────────────────────────────────────────

    def add_policy(self, policy: GovernancePolicy) -> None:
        self._policies[policy.policy_id] = policy

    def remove_policy(self, policy_id: str) -> bool:
        if policy_id in self._policies:
            del self._policies[policy_id]
            return True
        return False

    def policies(self, *, policy_type: PolicyType | None = None,
                 enabled_only: bool = True) -> tuple[GovernancePolicy, ...]:
        result = self._policies.values()
        if enabled_only:
            result = [p for p in result if p.enabled]
        if policy_type is not None:
            result = [p for p in result if p.policy_type == policy_type]
        return tuple(result)

    def get_policy(self, policy_id: str) -> GovernancePolicy | None:
        return self._policies.get(policy_id)

    # ── Permissions ──────────────────────────────────────────────────────

    def grant_permission(self, permission: Permission) -> None:
        key = f"{permission.actor_id}:{permission.resource}:{permission.action}"
        self._permissions[key] = permission

    def check_permission(self, actor_id: str, resource: str, action: str) -> bool:
        key = f"{actor_id}:{resource}:{action}"
        perm = self._permissions.get(key)
        if perm is None:
            return False
        if perm.expires_at > 0 and time.time() > perm.expires_at:
            return False
        return True

    def authorize(self, actor_id: str, resource: str, action: str,
                  amount: float | None = None) -> bool:
        """Authorize an operation, including policy-defined amount limits.

        A permission answers *who may perform the action*.  A governance
        policy may additionally constrain *how much* (for example, cashier
        refunds up to $100).  The policy metadata convention keeps the
        governance engine domain-neutral while allowing store policies to
        deny an otherwise permitted $500 refund.
        """
        if not self.check_permission(actor_id, resource, action):
            return False
        for policy in self.policies():
            metadata = policy.metadata
            if metadata.get("resource") not in (None, resource):
                continue
            if metadata.get("action") not in (None, action):
                continue
            max_amount = metadata.get("max_amount")
            if amount is not None and max_amount is not None and amount > float(max_amount):
                return False
        return True

    def has_permission_entry(self, actor_id: str, resource: str, action: str) -> bool:
        """Whether this engine has ANY opinion (granted or expired) on this
        (actor, resource, action) — distinct from check_permission()'s
        granted/not-granted boolean. Society as Organizational Context
        refactor: lets SocietyActivationEngine's cross-society permission
        aggregation tell "this society explicitly has no entry" apart from
        "this society denies it," so it can defer to the next
        activated society in precedence order instead of stopping at the
        first one checked."""
        key = f"{actor_id}:{resource}:{action}"
        return key in self._permissions

    def revoke_permission(self, actor_id: str, resource: str, action: str) -> bool:
        key = f"{actor_id}:{resource}:{action}"
        if key in self._permissions:
            del self._permissions[key]
            return True
        return False

    def permissions_for(self, actor_id: str) -> tuple[Permission, ...]:
        return tuple(p for p in self._permissions.values() if p.actor_id == actor_id)

    def all_permissions(self) -> tuple[Permission, ...]:
        """Every granted Permission across every actor — for persistence
        (PlanetaryRuntime._save_societies), not per-actor resolution."""
        return tuple(self._permissions.values())

    # ── Trust ────────────────────────────────────────────────────────────

    def evaluate_trust(self, actor_id: str, score: float,
                       factors: dict[str, float] | None = None) -> TrustRecord:
        existing = self._trust_records.get(actor_id, TrustRecord(actor_id=actor_id))
        record = TrustRecord(
            actor_id=actor_id,
            trust_score=score,
            evidence_count=existing.evidence_count + 1,
            factors=dict(factors) if factors else dict(existing.factors),
        )
        self._trust_records[actor_id] = record
        return record

    def get_trust(self, actor_id: str) -> TrustRecord:
        return self._trust_records.get(actor_id, TrustRecord(actor_id=actor_id))

    def all_trust_records(self) -> tuple[TrustRecord, ...]:
        return tuple(self._trust_records.values())

    def restore_trust_record(self, record: TrustRecord) -> None:
        """Reinstate a previously-persisted TrustRecord verbatim (actor_id,
        score, evidence_count, factors, last_evaluated all preserved) —
        distinct from evaluate_trust(), which computes a NEW record
        relative to whatever this engine currently holds. Used only by
        PlanetaryRuntime's society-reload path (_load_societies), so a
        restart doesn't silently reset every actor's trust to the
        TrustRecord default."""
        self._trust_records[record.actor_id] = record

    # ── Audit ────────────────────────────────────────────────────────────

    def audit(self, actor_id: str, action: str, policy_id: str = "",
              compliance_status: ComplianceStatus = ComplianceStatus.COMPLIANT,
              details: str = "") -> AuditEntry:
        entry = AuditEntry(
            actor_id=actor_id, action=action, policy_id=policy_id,
            compliance_status=compliance_status, details=details,
        )
        self._audit_log.append(entry)
        return entry

    def restore_audit_entry(self, entry: AuditEntry) -> None:
        """Reinstate a previously-persisted AuditEntry verbatim, including
        its original timestamp — distinct from audit(), which always
        mints a fresh entry via time.time(). Used only by PlanetaryRuntime's
        society-reload path; callers must restore entries in original
        (oldest-first) order so audit_log()'s own log[-limit:] slicing
        stays chronological."""
        self._audit_log.append(entry)

    def audit_log(self, *, actor_id: str | None = None,
                  limit: int = 100) -> tuple[AuditEntry, ...]:
        log = self._audit_log
        if actor_id is not None:
            log = [e for e in log if e.actor_id == actor_id]
        return tuple(log[-limit:])

    # ── Safety ───────────────────────────────────────────────────────────

    def add_safety_constraint(self, constraint: SafetyConstraint) -> None:
        self._safety_constraints[constraint.constraint_id] = constraint

    def check_safety(self, actor_id: str, actor_type: str = "") -> tuple[SafetyConstraint, ...]:
        violations: list[SafetyConstraint] = []
        for constraint in self._safety_constraints.values():
            if not constraint.applies_to:
                continue
            if actor_id in constraint.applies_to or actor_type in constraint.applies_to:
                violations.append(constraint)
        return tuple(violations)

    def safety_constraints(self) -> tuple[SafetyConstraint, ...]:
        return tuple(self._safety_constraints.values())

    # ── Compliance ───────────────────────────────────────────────────────

    def check_compliance(self, actor_id: str) -> ComplianceStatus:
        actor_audit = [e for e in self._audit_log if e.actor_id == actor_id]
        if not actor_audit:
            return ComplianceStatus.UNDER_REVIEW
        if any(e.compliance_status == ComplianceStatus.NON_COMPLIANT for e in actor_audit):
            return ComplianceStatus.NON_COMPLIANT
        return ComplianceStatus.COMPLIANT
