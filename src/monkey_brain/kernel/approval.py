"""Runtime Approval Artifacts — explicit approval decisions for agent-to-agent operations.

Problem Solved:
    Agent-to-agent operations need explicit approval decisions:
    - AUTO_APPROVE: Policy permits, no human review needed
    - HUMAN_APPROVAL_REQUIRED: Policy requires explicit human approval
    - DENY: Operation forbidden, cannot execute

Solution:
    1. GovernanceEngine returns approval_mode alongside policy decision
    2. ApprovalArtifact captures full provenance of approval
    3. Approval validation gates execution before state mutation
    4. Approval is time-bound, scoped, and integrity-protected
    5. Agent cannot self-approve or manufacture approval artifacts

Architecture:
    - Approval is immutable once created
    - Approval source is distinguished (POLICY_AUTOMATIC vs HUMAN)
    - Approval cannot be inferred from silence or absence of rejection
    - Human approval requires explicit trusted approval event
    - Expired/revoked approvals prevent execution

Key Invariants:
    - Agents can REQUEST work but cannot APPROVE their own requests
    - Agent-to-agent communication is NOT automatically a human-approval event
    - Safe policy-permitted operations are automatically approved
    - Policy-defined high-risk operations escalate to HITL
    - Approval validation occurs at trusted execution boundary
    - Approval cannot bypass authentication or authorization
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable
from uuid import uuid4

logger = logging.getLogger("agentos.approval")


class ApprovalMode(str, Enum):
    """Explicit approval decision modes."""

    AUTO_APPROVE = "AUTO_APPROVE"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    DENY = "DENY"


class ApprovalSource(str, Enum):
    """Where the approval originated."""

    POLICY_AUTOMATIC = "POLICY_AUTOMATIC"
    HUMAN = "HUMAN"


class ApprovalStatus(str, Enum):
    """Current status of an approval."""

    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class ApprovalDecisionError(ValueError):
    """Raised when an ApprovalDecision is constructed with an invalid or
    untrustworthy field. Mirrors governance/approval_artifact.py's
    ApprovalArtifactError -- same repository convention, different module
    (that one is the DEVELOPMENT-PROCESS artifact; see ApprovalDecision's
    own docstring for why the two are never conflated)."""


@dataclass(frozen=True)
class ApprovalDecision:
    """The Approval Decision Contract: the single, canonical answer trusted
    governance gives to "what authorization path does this authenticated
    operation require?" -- AUTO_APPROVE, HUMAN_APPROVAL_REQUIRED, or DENY.

    Sits between policy evaluation and the runtime execution gate:

        authenticated request -> authorization -> OPA/governance policy
            -> ApprovalDecision -> ApprovalArtifact/HITL handoff
            -> runtime execution gate -> execution

    ApprovalDecision vs ApprovalArtifact (these are NOT interchangeable):
        ApprovalDecision  -- what approval path is required?      (this class)
        ApprovalArtifact  -- what specific authorization was       (above)
                             actually GRANTED?
    A HUMAN_APPROVAL_REQUIRED decision is a REQUEST for human review, never
    itself a grant -- only the authorized human-approval endpoint
    (api/routes/approval.py's /runtime-approvals/{id}/approve) may produce
    an ApprovalArtifact whose approval_source is HUMAN. Nothing on this
    class can transition a decision into an approval; there is
    deliberately no "approve()"/"grant()" method here.

    ApprovalDecision vs execution permission (these are NOT the same
    either): mode == AUTO_APPROVE means policy permits automatic approval
    -- it is not itself permission to execute. The runtime execution gate
    (kernel/security_boundary.py::run_governed_mutation) still requires
    authentication, scope validity, current state, durable audit, and
    idempotency to ALL independently hold. This class intentionally has no
    `execution_permitted` field -- that value can only be computed at the
    execution gate, by combining this decision with those other controls,
    never read directly off the decision alone.

    Immutable: frozen dataclass -- any attempted field assignment raises
    dataclasses.FrozenInstanceError. A changed decision is always a NEW
    ApprovalDecision from a NEW policy evaluation; there is no in-place
    transition (DENY never becomes AUTO_APPROVE by mutation, nor the
    reverse).

    This type is additive -- see kernel/governance.py::GovernanceEngine.
    evaluate() and kernel/security_boundary.py::_authorize(), both of
    which continue to return their existing plain-dict shape unchanged.
    from_policy_result() below is an ADAPTER around that existing dict,
    not a replacement for it, and nothing in security_boundary.py,
    capability_bus.py, action_executor.py, or the agent-to-agent
    capabilities (grocery.py) constructs or consumes this class yet --
    per this task's explicit scope, wiring it into any of those requires
    separate, later authorization.
    """

    mode: ApprovalMode
    operation_id: str
    """Stable per-request correlation identity. Reuses the SAME field
    name/semantics as SecurityOperation.operation_id (kernel/
    security_operation.py) and ApprovalArtifact.operation_id/
    correlation_id (this module) -- deliberately not a new identifier
    scheme (Section 19: "where an existing request ID/correlation ID
    exists, reuse it"). MUST be supplied by the caller from the actual
    originating request's own stable id; see this class's KNOWN
    LIMITATION note below about kernel/security_boundary.py's own
    ensure_governed()/run_governed_mutation() NOT currently threading one
    in from capability dispatch (new_operation_id() mints a fresh UUID
    per call there instead) -- a real, separate discovery finding, not
    something this contract's definition can silently fix by itself."""
    principal: str
    """The AUTHENTICATED principal (kernel/trusted_auth.py::
    TrustedAuthEvidence.principal_id) that is bound to this decision.
    Never an agent-self-reported id, never read from agent/LLM-controlled
    request content -- see TRUST BOUNDARY table in this module's tests."""
    operation: str
    """The capability/action being evaluated (e.g. "capability.Payment"),
    matching the `action` argument GovernanceEngine.evaluate()/
    ensure_governed() already take."""
    scope: str
    """The resource/scope this decision covers (matches the existing
    `resource` argument). A decision for one scope never implies
    authorization for a different scope or a materially different
    operation -- see `covers()` below."""
    policy_rule: str
    """Which named policy rule produced this decision (e.g.
    "high_risk_action", "runtime_blocked", "default_allow") -- matches
    GovernanceEngine.evaluate()'s own "policy_rule" field."""
    reason: str
    """Deterministic, human-readable reason -- never fabricated when
    absent (empty string, not a guessed explanation)."""
    risk_level: str
    """LOW | MEDIUM | HIGH | CRITICAL -- matches GovernanceEngine.evaluate()."""
    policy_decision: str = ""
    """The full raw policy result, stringified -- same convention as
    ApprovalArtifact.policy_decision/SecurityOperation.policy_decision
    (both already plain strings, not structured dicts, in this codebase)."""
    policy_revision: str = ""
    """OPA bundle/policy version, when available. KNOWN LIMITATION:
    neither OPA client implementation in this repo (packages/cerebellum/
    .../opa_client.py, or its inline mirror in services/common/opa.py)
    currently queries or exposes a bundle/policy revision from OPA's
    Status/Bundle API -- there is nothing real to put here yet. Left ""
    rather than a fabricated version string (Section 8: "if policy
    provenance cannot safely be captured, document the limitation
    instead of fabricating it")."""
    decided_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ApprovalMode):
            raise ApprovalDecisionError(f"mode must be an ApprovalMode, got {type(self.mode)!r}")
        missing = [
            name
            for name, value in (
                ("operation_id", self.operation_id),
                ("principal", self.principal),
                ("operation", self.operation),
            )
            if not value or not str(value).strip()
        ]
        if missing:
            raise ApprovalDecisionError(f"missing required field(s): {', '.join(missing)}")

    def covers(self, *, operation_id: str, principal: str, operation: str, scope: str = "") -> bool:
        """Whether this decision may be relied on for the given request.

        All four must match -- request binding (Section 19), principal
        binding (Section 18), and scope binding (Section 17) combined into
        one check: same operation_id AND same principal AND same
        operation AND (if a scope is given) the same scope. A decision for
        request A never covers request B; a decision scoped to principal A
        never covers principal B; a decision for operation/scope X never
        covers a materially different operation/scope Y.

        Pure function over this decision's own fields -- not consumed by
        any runtime call site yet (Section 28: no wiring in this task).
        """
        return (
            self.operation_id == operation_id
            and self.principal == principal
            and self.operation == operation
            and (not scope or self.scope == scope)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "operation_id": self.operation_id,
            "principal": self.principal,
            "operation": self.operation,
            "scope": self.scope,
            "policy_rule": self.policy_rule,
            "reason": self.reason,
            "risk_level": self.risk_level,
            "policy_decision": self.policy_decision,
            "policy_revision": self.policy_revision,
            "decided_at": self.decided_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalDecision":
        try:
            return cls(
                mode=ApprovalMode(data["mode"]),
                operation_id=data["operation_id"],
                principal=data["principal"],
                operation=data["operation"],
                scope=data.get("scope", ""),
                policy_rule=data.get("policy_rule", ""),
                reason=data.get("reason", ""),
                risk_level=data.get("risk_level", ""),
                policy_decision=data.get("policy_decision", ""),
                policy_revision=data.get("policy_revision", ""),
                decided_at=data.get("decided_at", time.time()),
            )
        except KeyError as exc:
            raise ApprovalDecisionError(f"decision missing required field: {exc}") from exc
        except ValueError as exc:
            raise ApprovalDecisionError(f"decision schema invalid: {exc}") from exc

    @classmethod
    def from_policy_result(
        cls,
        *,
        operation_id: str,
        principal: str,
        operation: str,
        scope: str,
        result: dict[str, Any],
    ) -> "ApprovalDecision":
        """Construct the canonical decision from whatever GovernanceEngine.
        evaluate()/_authorize() already return -- an ADAPTER around the
        existing dict shape (Section 27: prefer adapters over a parallel
        approval system), not a replacement for it. GovernanceEngine.
        evaluate() itself is untouched by this.

        TRUST BOUNDARY: operation_id/principal/operation/scope are always
        taken from the explicit keyword arguments a trusted caller
        supplies -- NEVER read out of `result`, even if an agent-poisoned
        `extra`/context dict caused similarly-named keys to end up inside
        it (kernel/trusted_auth.py::strip_untrusted_security_signals
        already strips known-dangerous keys before `result` is built, but
        this method does not additionally trust `result` for identity
        fields regardless, as defense in depth).
        """
        allowed = bool(result.get("allowed", False))
        mode = ApprovalMode(result.get("approval_mode", "AUTO_APPROVE" if allowed else "DENY"))
        return cls(
            mode=mode,
            operation_id=operation_id,
            principal=principal,
            operation=operation,
            scope=scope,
            policy_rule=result.get("policy_rule", "") or ("default_allow" if allowed else "denied"),
            reason=result.get("reason", ""),
            risk_level=result.get("risk_level", "LOW" if allowed else "HIGH"),
            policy_decision=str(result),
            policy_revision=result.get("policy_revision", ""),
        )

    @classmethod
    def deny(
        cls,
        *,
        operation_id: str,
        principal: str,
        operation: str,
        scope: str = "",
        reason: str = "",
        policy_rule: str = "fail_closed",
    ) -> "ApprovalDecision":
        """Explicit fail-closed constructor for the failure-semantics
        matrix (Section 14): missing/invalid authentication, OPA
        unavailable, policy error, and invalid scope all resolve to a
        DENY decision built here -- never to AUTO_APPROVE, and never by
        silently defaulting `mode` in a general-purpose constructor.
        `principal` may be "unknown" for a genuinely unauthenticated
        request -- an empty ApprovalDecision.principal is still rejected
        by __post_init__, so callers must pass an explicit placeholder
        rather than have one fabricated here.
        """
        if not principal or not principal.strip():
            raise ApprovalDecisionError("deny() requires an explicit principal (e.g. 'unknown'), not empty")
        return cls(
            mode=ApprovalMode.DENY,
            operation_id=operation_id,
            principal=principal,
            operation=operation,
            scope=scope,
            policy_rule=policy_rule,
            reason=reason,
            risk_level="CRITICAL",
            policy_decision=reason,
        )


@dataclass(frozen=True)
class ApprovalArtifact:
    """Immutable approval artifact — records full provenance of execution authorization.

    Key property: Cannot be created by agents. Only by trusted governance engine or
    authorized human approval mechanism.
    """

    # Identity
    approval_id: str = field(default_factory=lambda: uuid4().hex)
    operation_id: str = ""

    # Approval decision
    approval_mode: ApprovalMode = ApprovalMode.AUTO_APPROVE
    approval_source: ApprovalSource = ApprovalSource.POLICY_AUTOMATIC
    approval_status: ApprovalStatus = ApprovalStatus.ACTIVE

    # Who/what approved this
    requesting_principal: str = ""
    """The authenticated principal requesting the operation."""
    approving_principal: str = ""
    """For HUMAN approvals, the authenticated human. For POLICY_AUTOMATIC, the runtime."""

    # What was approved
    target_operation: str = ""
    """The operation being approved (e.g., 'capability.read_customer_record')."""
    target_resource: str = ""
    """The resource being operated on."""
    operation_class: str = ""
    """Classification: mutation | query | proposal | etc."""

    # Scope and constraints
    scope: dict[str, Any] = field(default_factory=dict)
    """Fine-grained scope: which capabilities, resources, constraints."""
    constraints: dict[str, Any] = field(default_factory=dict)
    """Additional constraints: max_attempts, rate_limits, etc."""

    # Policy decision
    policy_rule: str = ""
    """Which OPA rule matched (for POLICY_AUTOMATIC)."""
    policy_decision: str = ""
    """Full policy decision text."""
    policy_revision: str = ""
    """OPA policy version/revision."""
    risk_level: str = ""
    """Risk classification: LOW, MEDIUM, HIGH, CRITICAL."""

    # Time bounds
    approved_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    """0.0 means no expiration. Expired approvals prevent execution."""

    # Metadata and provenance
    correlation_id: str = ""
    """Links this approval to the request/audit trail."""
    audit_entry_id: str = ""
    """Reference to audit log entry for this approval."""

    # Integrity
    signature: str = ""
    """HMAC signature for integrity verification."""

    # State
    revoked_at: float | None = None
    revocation_reason: str = ""

    created_at: float = field(default_factory=time.time)

    def is_valid(self) -> bool:
        """Check if approval is currently valid (not expired, not revoked)."""
        if self.approval_status == ApprovalStatus.REVOKED:
            return False
        if self.approval_status == ApprovalStatus.EXPIRED:
            return False
        if self.approval_status == ApprovalStatus.SUPERSEDED:
            return False
        now = time.time()
        if self.expires_at > 0.0 and now > self.expires_at:
            return False
        return True

    def matches_scope(self, operation: str, resource: str) -> bool:
        """Verify this approval covers the requested operation and resource."""
        if self.target_operation and self.target_operation != operation:
            return False
        if self.target_resource and self.target_resource != resource:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for storage."""
        return {
            "approval_id": self.approval_id,
            "operation_id": self.operation_id,
            "approval_mode": self.approval_mode.value,
            "approval_source": self.approval_source.value,
            "approval_status": self.approval_status.value,
            "requesting_principal": self.requesting_principal,
            "approving_principal": self.approving_principal,
            "target_operation": self.target_operation,
            "target_resource": self.target_resource,
            "operation_class": self.operation_class,
            "scope": dict(self.scope),
            "constraints": dict(self.constraints),
            "policy_rule": self.policy_rule,
            "policy_decision": self.policy_decision,
            "policy_revision": self.policy_revision,
            "risk_level": self.risk_level,
            "approved_at": self.approved_at,
            "expires_at": self.expires_at,
            "correlation_id": self.correlation_id,
            "audit_entry_id": self.audit_entry_id,
            "signature": self.signature,
            "revoked_at": self.revoked_at,
            "revocation_reason": self.revocation_reason,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApprovalArtifact:
        """Deserialize from dictionary."""
        return cls(
            approval_id=data.get("approval_id", ""),
            operation_id=data.get("operation_id", ""),
            approval_mode=ApprovalMode(data.get("approval_mode", "AUTO_APPROVE")),
            approval_source=ApprovalSource(data.get("approval_source", "POLICY_AUTOMATIC")),
            approval_status=ApprovalStatus(data.get("approval_status", "ACTIVE")),
            requesting_principal=data.get("requesting_principal", ""),
            approving_principal=data.get("approving_principal", ""),
            target_operation=data.get("target_operation", ""),
            target_resource=data.get("target_resource", ""),
            operation_class=data.get("operation_class", ""),
            scope=data.get("scope", {}),
            constraints=data.get("constraints", {}),
            policy_rule=data.get("policy_rule", ""),
            policy_decision=data.get("policy_decision", ""),
            policy_revision=data.get("policy_revision", ""),
            risk_level=data.get("risk_level", ""),
            approved_at=data.get("approved_at", time.time()),
            expires_at=data.get("expires_at", 0.0),
            correlation_id=data.get("correlation_id", ""),
            audit_entry_id=data.get("audit_entry_id", ""),
            signature=data.get("signature", ""),
            revoked_at=data.get("revoked_at"),
            revocation_reason=data.get("revocation_reason", ""),
            created_at=data.get("created_at", time.time()),
        )


class ApprovalArtifactStore:
    """In-memory approval artifact store with optional MongoDB persistence.

    Approvals are immutable once created. Store manages:
    - Creation of automatic approvals (from trusted governance engine)
    - Creation of human approvals (from authorized approval endpoint)
    - Validation of approvals before execution
    - Revocation of approvals
    - Cleanup of expired approvals
    """

    def __init__(self) -> None:
        self._artifacts: dict[str, ApprovalArtifact] = {}
        self._operation_to_approvals: dict[str, list[str]] = {}
        self._principal_to_approvals: dict[str, list[str]] = {}

    def create(self, artifact: ApprovalArtifact) -> ApprovalArtifact:
        """Create a new approval artifact.

        Artifacts are immutable. This method stores the artifact and
        establishes the indexes for validation.

        Args:
            artifact: ApprovalArtifact to store

        Returns:
            The stored artifact

        Raises:
            ValueError: If approval_id already exists (duplicate)
        """
        if artifact.approval_id in self._artifacts:
            raise ValueError(f"Approval {artifact.approval_id} already exists (immutable)")

        self._artifacts[artifact.approval_id] = artifact

        # Index by operation for quick lookup
        if artifact.operation_id:
            self._operation_to_approvals.setdefault(artifact.operation_id, []).append(artifact.approval_id)

        # Index by requesting principal for audit trail
        if artifact.requesting_principal:
            self._principal_to_approvals.setdefault(artifact.requesting_principal, []).append(artifact.approval_id)

        logger.debug(
            "Created approval %s: mode=%s, source=%s, principal=%s, operation=%s",
            artifact.approval_id,
            artifact.approval_mode.value,
            artifact.approval_source.value,
            artifact.requesting_principal,
            artifact.operation_id,
        )

        return artifact

    def get(self, approval_id: str) -> ApprovalArtifact | None:
        """Retrieve an approval artifact by ID."""
        return self._artifacts.get(approval_id)

    def get_for_operation(self, operation_id: str) -> list[ApprovalArtifact]:
        """Retrieve all approvals for an operation."""
        approval_ids = self._operation_to_approvals.get(operation_id, [])
        return [self._artifacts[aid] for aid in approval_ids if aid in self._artifacts]

    def get_for_principal(self, principal_id: str) -> list[ApprovalArtifact]:
        """Retrieve all approvals created by/for a principal."""
        approval_ids = self._principal_to_approvals.get(principal_id, [])
        return [self._artifacts[aid] for aid in approval_ids if aid in self._artifacts]

    def list_pending(self) -> list[ApprovalArtifact]:
        """Retrieve every artifact currently awaiting a human decision."""
        return [
            artifact
            for artifact in self._artifacts.values()
            if artifact.approval_mode == ApprovalMode.HUMAN_APPROVAL_REQUIRED
            and artifact.approval_status == ApprovalStatus.ACTIVE
        ]

    def validate(
        self,
        approval_id: str,
        operation: str = "",
        resource: str = "",
    ) -> tuple[bool, str]:
        """Validate that an approval is valid and matches the operation.

        Returns:
            (is_valid, reason_if_invalid)
        """
        artifact = self.get(approval_id)
        if artifact is None:
            return False, "approval not found"

        if not artifact.is_valid():
            if artifact.approval_status == ApprovalStatus.REVOKED:
                return False, f"approval revoked: {artifact.revocation_reason}"
            if artifact.approval_status == ApprovalStatus.EXPIRED:
                return False, "approval expired"
            if artifact.expires_at > 0.0 and time.time() > artifact.expires_at:
                return False, "approval expired"
            return False, "approval not valid"

        if operation and not artifact.matches_scope(operation, resource):
            return False, "approval scope mismatch"

        return True, ""

    def revoke(self, approval_id: str, reason: str = "") -> bool:
        """Revoke an approval (prevent further use).

        Revocation is permanent and immutable.
        """
        artifact = self.get(approval_id)
        if artifact is None:
            logger.warning("Cannot revoke: approval %s not found", approval_id)
            return False

        if artifact.approval_status == ApprovalStatus.REVOKED:
            logger.debug("Approval %s already revoked", approval_id)
            return False

        # Create a new artifact with REVOKED status (immutable replace)
        revoked_artifact = ApprovalArtifact(
            approval_id=artifact.approval_id,
            operation_id=artifact.operation_id,
            approval_mode=artifact.approval_mode,
            approval_source=artifact.approval_source,
            approval_status=ApprovalStatus.REVOKED,
            requesting_principal=artifact.requesting_principal,
            approving_principal=artifact.approving_principal,
            target_operation=artifact.target_operation,
            target_resource=artifact.target_resource,
            operation_class=artifact.operation_class,
            scope=artifact.scope,
            constraints=artifact.constraints,
            policy_rule=artifact.policy_rule,
            policy_decision=artifact.policy_decision,
            policy_revision=artifact.policy_revision,
            risk_level=artifact.risk_level,
            approved_at=artifact.approved_at,
            expires_at=artifact.expires_at,
            correlation_id=artifact.correlation_id,
            audit_entry_id=artifact.audit_entry_id,
            signature=artifact.signature,
            revoked_at=time.time(),
            revocation_reason=reason,
            created_at=artifact.created_at,
        )

        self._artifacts[approval_id] = revoked_artifact
        logger.info("Revoked approval %s: %s", approval_id, reason)
        return True

    def cleanup_expired(self) -> int:
        """Remove expired approvals from store (optional cleanup).

        Returns:
            Number of approvals cleaned up
        """
        now = time.time()
        expired_ids = [
            aid for aid, artifact in self._artifacts.items() if artifact.expires_at > 0.0 and now > artifact.expires_at
        ]

        for aid in expired_ids:
            if aid in self._artifacts:
                del self._artifacts[aid]

        if expired_ids:
            logger.info("Cleaned up %d expired approvals", len(expired_ids))

        return len(expired_ids)

    def summary(self) -> dict[str, Any]:
        """Return summary statistics."""
        total = len(self._artifacts)
        active = sum(1 for a in self._artifacts.values() if a.is_valid())
        by_mode = {}
        for artifact in self._artifacts.values():
            mode = artifact.approval_mode.value
            by_mode[mode] = by_mode.get(mode, 0) + 1

        return {
            "total_approvals": total,
            "active_approvals": active,
            "expired_or_revoked": total - active,
            "by_mode": by_mode,
        }


class MongoApprovalArtifactStore(ApprovalArtifactStore):
    """MongoDB-backed approval artifact store with async persistence.

    Extends ApprovalArtifactStore with durable persistence to MongoDB.
    In-memory indexes are maintained for fast lookup; MongoDB is the durable layer.

    Collection: ``approval_artifacts`` — one document per approval, indexed on:
    - _id: approval_id (primary key)
    - operation_id: for querying approvals for an operation
    - requesting_principal: for audit trail
    - approval_status: for filtering valid/revoked/expired
    - expires_at: for cleanup queries
    """

    def __init__(self, mongo_client: Any, db_name: str = "monkeybrain") -> None:
        """Initialize MongoDB-backed approval store.

        Args:
            mongo_client: motor.motor_asyncio.AsyncIOMotorClient or similar
            db_name: MongoDB database name
        """
        super().__init__()
        self._db = mongo_client[db_name]
        self._col = self._db["approval_artifacts"]
        self._initialized = False

    async def initialize(self) -> None:
        """Create indexes for efficient queries. Call once at startup."""
        if self._initialized:
            return

        # Index for operation lookup
        await self._col.create_index("operation_id")

        # Index for principal audit trail
        await self._col.create_index("requesting_principal")

        # Compound index for expiration cleanup
        await self._col.create_index([("approval_status", 1), ("expires_at", 1)])

        # Index for approval validation by ID (primary)
        await self._col.create_index("approval_id", unique=True)

        logger.info("Initialized MongoDB approval artifact store indexes")
        self._initialized = True

    async def create(self, artifact: ApprovalArtifact) -> ApprovalArtifact:
        """Create and persist a new approval artifact.

        Stores the artifact in both in-memory indexes and MongoDB.
        """
        # Create in memory first (validates no duplicate)
        super().create(artifact)

        # Persist to MongoDB
        try:
            doc = artifact.to_dict()
            doc["_id"] = artifact.approval_id
            await self._col.update_one(
                {"_id": artifact.approval_id},
                {"$set": doc},
                upsert=True,
            )
            logger.debug("Persisted approval %s to MongoDB", artifact.approval_id)
        except Exception as exc:
            logger.error(
                "Failed to persist approval %s to MongoDB: %s",
                artifact.approval_id,
                exc,
            )
            # Still return the artifact (in-memory store is valid)
            # but log the error for operational visibility

        return artifact

    async def load(self, approval_id: str) -> ApprovalArtifact | None:
        """Load an approval from MongoDB (bypassing in-memory cache).

        Useful for retrieving approvals created in other processes.
        """
        try:
            doc = await self._col.find_one({"_id": approval_id})
            if doc is None:
                return None
            doc.pop("_id", None)
            artifact = ApprovalArtifact.from_dict(doc)
            # Add to in-memory cache
            self._artifacts[approval_id] = artifact
            if artifact.operation_id:
                self._operation_to_approvals.setdefault(artifact.operation_id, []).append(approval_id)
            if artifact.requesting_principal:
                self._principal_to_approvals.setdefault(artifact.requesting_principal, []).append(approval_id)
            return artifact
        except Exception as exc:
            logger.error("Failed to load approval %s from MongoDB: %s", approval_id, exc)
            return None

    async def list_for_operation(self, operation_id: str) -> list[ApprovalArtifact]:
        """Retrieve all approvals for an operation from MongoDB."""
        try:
            cursor = self._col.find({"operation_id": operation_id})
            results = []
            async for doc in cursor:
                doc.pop("_id", None)
                results.append(ApprovalArtifact.from_dict(doc))
            return results
        except Exception as exc:
            logger.error("Failed to list approvals for operation %s: %s", operation_id, exc)
            return []

    async def list_for_principal(self, principal_id: str) -> list[ApprovalArtifact]:
        """Retrieve all approvals for a principal from MongoDB."""
        try:
            cursor = self._col.find({"requesting_principal": principal_id})
            results = []
            async for doc in cursor:
                doc.pop("_id", None)
                results.append(ApprovalArtifact.from_dict(doc))
            return results
        except Exception as exc:
            logger.error("Failed to list approvals for principal %s: %s", principal_id, exc)
            return []

    async def revoke(self, approval_id: str, reason: str = "") -> bool:
        """Revoke an approval and persist the revocation."""
        # Revoke in memory
        success = super().revoke(approval_id, reason)

        if not success:
            return False

        # Persist revocation to MongoDB
        try:
            revoked_artifact = self._artifacts[approval_id]
            doc = revoked_artifact.to_dict()
            doc["_id"] = approval_id
            await self._col.update_one(
                {"_id": approval_id},
                {"$set": doc},
                upsert=True,
            )
            logger.info("Persisted revocation of approval %s to MongoDB", approval_id)
        except Exception as exc:
            logger.error(
                "Failed to persist revocation of approval %s to MongoDB: %s",
                approval_id,
                exc,
            )

        return True

    async def cleanup_expired(self) -> int:
        """Remove expired approvals from both in-memory and MongoDB."""
        now = time.time()

        # Clean from MongoDB
        try:
            mongo_result = await self._col.delete_many(
                {
                    "approval_status": "EXPIRED",
                }
            )
            deleted_count = mongo_result.deleted_count
        except Exception as exc:
            logger.error("Failed to cleanup expired approvals from MongoDB: %s", exc)
            deleted_count = 0

        # Also delete by expires_at timestamp
        try:
            mongo_result = await self._col.delete_many(
                {
                    "expires_at": {"$gt": 0.0, "$lt": now},
                    "approval_status": {"$ne": "EXPIRED"},  # Don't double-delete
                }
            )
            deleted_count += mongo_result.deleted_count
        except Exception as exc:
            logger.error("Failed to cleanup expired approvals by timestamp from MongoDB: %s", exc)

        # Clean from in-memory cache
        memory_deleted = super().cleanup_expired()

        if deleted_count > 0 or memory_deleted > 0:
            logger.info(
                "Cleaned up %d expired approvals (MongoDB: %d, memory: %d)",
                deleted_count + memory_deleted,
                deleted_count,
                memory_deleted,
            )

        return deleted_count + memory_deleted


# Global singleton instances
_approval_store: ApprovalArtifactStore | None = None
_mongo_approval_store: MongoApprovalArtifactStore | None = None


def get_approval_store() -> ApprovalArtifactStore:
    """Get or create the global approval artifact store.

    If MongoDB is configured, returns the MongoDB-backed store.
    Otherwise returns the in-memory store.
    """
    global _approval_store, _mongo_approval_store

    # Try to use MongoDB store if client is available
    try:
        from src.monkey_brain.runtime.routers import get_mongo_client

        mongo_client = get_mongo_client()
        if mongo_client is not None:
            if _mongo_approval_store is None:
                _mongo_approval_store = MongoApprovalArtifactStore(mongo_client)
            return _mongo_approval_store
    except Exception:
        pass

    # Fall back to in-memory store
    if _approval_store is None:
        _approval_store = ApprovalArtifactStore()
    return _approval_store


def get_mongo_approval_store() -> MongoApprovalArtifactStore | None:
    """Get the MongoDB-backed approval store if available."""
    global _mongo_approval_store
    try:
        from src.monkey_brain.runtime.routers import get_mongo_client

        mongo_client = get_mongo_client()
        if mongo_client is not None:
            if _mongo_approval_store is None:
                _mongo_approval_store = MongoApprovalArtifactStore(mongo_client)
            return _mongo_approval_store
    except Exception:
        pass
    return None


def create_approval_artifact_from_policy(
    operation_id: str,
    action: str,
    resource: str,
    policy_decision: dict[str, Any],
    requesting_principal: str,
    operation_class: str = "mutation",
) -> ApprovalArtifact:
    """Create an approval artifact from a governance policy decision.

    This is called after policy evaluation in run_governed_mutation()
    to generate an immutable approval record.

    Args:
        operation_id: Unique operation ID from security operation ledger
        action: The operation being performed (e.g., 'capability.execute')
        resource: The resource being operated on
        policy_decision: Result from GovernanceEngine.evaluate() with approval fields
        requesting_principal: Authenticated principal requesting the operation
        operation_class: Classification (mutation, query, proposal, etc.)

    Returns:
        ApprovalArtifact with approval_mode set by policy
    """
    approval_mode = ApprovalMode(policy_decision.get("approval_mode", "AUTO_APPROVE"))
    risk_level = policy_decision.get("risk_level", "LOW")
    policy_rule = policy_decision.get("policy_rule", "")

    # AUTO_APPROVE: approving_principal is the runtime itself
    # HUMAN_APPROVAL_REQUIRED: approving_principal will be set when human approves
    # DENY: artifact is still created for audit trail, but is never valid
    approving_principal = "runtime:governance" if approval_mode == ApprovalMode.AUTO_APPROVE else ""

    # Set expiration based on approval mode and risk level
    # AUTO_APPROVE with LOW risk: 24 hours
    # AUTO_APPROVE with MEDIUM/HIGH: 6 hours
    # HUMAN_APPROVAL_REQUIRED: 30 days (or until approved)
    now = time.time()
    if approval_mode == ApprovalMode.AUTO_APPROVE:
        ttl_seconds = 21600 if risk_level in ("MEDIUM", "HIGH") else 86400
        expires_at = now + ttl_seconds
    elif approval_mode == ApprovalMode.HUMAN_APPROVAL_REQUIRED:
        expires_at = now + (30 * 86400)  # 30 days for humans to review
    else:  # DENY
        expires_at = now + 3600  # 1 hour, not really used

    artifact = ApprovalArtifact(
        approval_id=f"appr_{uuid4().hex[:16]}",
        operation_id=operation_id,
        approval_mode=approval_mode,
        approval_source=ApprovalSource.POLICY_AUTOMATIC,
        approval_status=(ApprovalStatus.ACTIVE if approval_mode != ApprovalMode.DENY else ApprovalStatus.ACTIVE),
        requesting_principal=requesting_principal,
        approving_principal=approving_principal,
        target_operation=action,
        target_resource=resource,
        operation_class=operation_class,
        scope={},  # Empty for now; can be populated from policy_decision
        constraints={},  # Can be populated from policy_decision
        policy_rule=policy_rule,
        policy_decision=str(policy_decision),
        policy_revision=policy_decision.get("policy_revision", ""),
        risk_level=risk_level,
        approved_at=now,
        expires_at=expires_at,
        correlation_id=operation_id,
        audit_entry_id="",  # Will be linked to audit log later
        signature="",  # Can be computed if needed for integrity
    )

    return artifact


def prevent_self_approval(
    requesting_principal: str,
    approving_principal: str,
) -> tuple[bool, str]:
    """Prevent agents from approving their own requests.

    This is a critical invariant: agents can REQUEST work and PROPOSE work,
    but cannot APPROVE their own requests. This prevents agents from
    circumventing human-approval requirements or escalation policies.

    Args:
        requesting_principal: The principal who requested the operation
        approving_principal: The principal who would approve it

    Returns:
        (is_valid, reason_if_invalid) where is_valid=True means NO self-approval detected
    """
    if not requesting_principal or not approving_principal:
        # Empty principal means not set yet (e.g., AUTO_APPROVE hasn't been approved by a human)
        return True, ""

    if requesting_principal == approving_principal:
        reason = f"self-approval prevented: {requesting_principal} cannot approve their own request"
        logger.warning(reason)
        return False, reason

    return True, ""


def validate_approval_for_execution(
    operation_id: str,
    approval_mode: str,
    action: str = "",
    resource: str = "",
) -> tuple[bool, str]:
    """Validate that an operation is approved for execution.

    This gate is called just before MUTATION in _execute_attempt_pipeline.

    Rules:
    - AUTO_APPROVE: always permitted (no artifact check needed)
    - HUMAN_APPROVAL_REQUIRED: must have an active, approved, non-expired artifact
    - DENY: never permitted

    Also validates:
    - Approval is not expired (expires_at timestamp)
    - Approval scope matches the operation (if scope is constrained)
    - Approval correlation_id matches operation_id (freshness/binding — Task 3)

    Args:
        operation_id: The operation being executed
        approval_mode: From the policy decision (AUTO_APPROVE | HUMAN_APPROVAL_REQUIRED | DENY)
        action: The operation being performed (for scope validation)
        resource: The resource being operated on (for scope validation)

    Returns:
        (is_valid, reason_if_invalid)
    """
    if approval_mode == "AUTO_APPROVE":
        return True, ""

    if approval_mode == "DENY":
        return False, "operation denied by policy"

    if approval_mode == "HUMAN_APPROVAL_REQUIRED":
        # Must have an active, approved, non-expired artifact
        store = get_approval_store()
        artifacts = store.get_for_operation(operation_id)

        if not artifacts:
            return False, "no approval found for operation"

        # Find an active, approved, non-expired artifact
        last_error = "operation awaiting human approval"
        for artifact in artifacts:
            # Check if valid (not expired, not revoked)
            if not artifact.is_valid():
                if artifact.approval_status == ApprovalStatus.REVOKED:
                    last_error = f"approval revoked: {artifact.revocation_reason}"
                elif artifact.approval_status == ApprovalStatus.EXPIRED:
                    last_error = "approval expired"
                elif artifact.expires_at > 0.0 and time.time() > artifact.expires_at:
                    last_error = "approval expired"
                continue

            # Check if approved (approval_source == HUMAN means it's been approved)
            if artifact.approval_source != ApprovalSource.HUMAN:
                continue

            # CRITICAL: Validate request freshness via correlation_id (Task 3: gap fix)
            # Approval must be bound to this specific operation_id (prevents replay across operations)
            if artifact.correlation_id and artifact.correlation_id != operation_id:
                last_error = (
                    f"approval correlation mismatch: approval for {artifact.correlation_id}, executing {operation_id}"
                )
                continue

            # CRITICAL: Prevent self-approval
            is_not_self_approval, reason = prevent_self_approval(
                artifact.requesting_principal,
                artifact.approving_principal,
            )
            if not is_not_self_approval:
                return False, reason

            # Validate scope if constrained
            if action and resource:
                if not artifact.matches_scope(action, resource):
                    return False, "approval scope does not cover this operation"

            # All checks passed
            return True, ""

        # No valid human approval found
        return False, last_error

    return False, f"unknown approval mode: {approval_mode}"


def reset_approval_store() -> None:
    """Reset the global approval store (for testing)."""
    global _approval_store, _mongo_approval_store
    _approval_store = None
    _mongo_approval_store = None


async def execute_with_approval(
    operation_id: str,
    approval_id: str,
    action: str,
    resource: str,
    mutate: Callable[[], Awaitable[Any]] | Callable[[], Any],
) -> Any:
    """Execute an operation using a pre-approved approval artifact.

    This is called when a human has already approved an operation via
    POST /runtime-approvals/{approval_id}/approve, and now the operation
    should proceed with mutation.

    Args:
        operation_id: The operation ID to execute
        approval_id: The approval artifact ID (must be approved by human)
        action: The operation action (for audit)
        resource: The resource being operated on (for audit)
        mutate: The mutation function to execute

    Returns:
        Result from mutate()

    Raises:
        ValidationError: If approval is invalid, expired, or not human-approved
        SecurityBoundaryDenied: If approval check fails
    """
    from src.monkey_brain.kernel.security_boundary import SecurityBoundaryDenied

    store = get_approval_store()
    artifact = store.get(approval_id)

    if not artifact:
        raise SecurityBoundaryDenied(
            f"approval not found: {approval_id}",
            stage="APPROVAL_RETRIEVAL",
        )

    if artifact.operation_id != operation_id:
        logger.warning(
            "Approval mismatch: approval_id=%s is for operation %s, not %s",
            approval_id,
            artifact.operation_id,
            operation_id,
        )
        raise SecurityBoundaryDenied(
            "approval does not match operation",
            stage="APPROVAL_VALIDATION",
        )

    # Validate approval is still valid (not expired, not revoked)
    if not artifact.is_valid():
        logger.warning(
            "Approval invalid: approval_id=%s (revoked=%s, expired=%s)",
            approval_id,
            artifact.revoked_at is not None,
            artifact.expires_at < time.time() if artifact.expires_at else False,
        )
        raise SecurityBoundaryDenied(
            "approval is invalid, expired, or revoked",
            stage="APPROVAL_VALIDATION",
        )

    # Validate approval was granted by human (not automatic)
    if artifact.approval_source != ApprovalSource.HUMAN:
        logger.warning(
            "Approval not human-approved: approval_id=%s (source=%s)",
            approval_id,
            artifact.approval_source.value,
        )
        raise SecurityBoundaryDenied(
            "approval was not granted by human",
            stage="APPROVAL_SOURCE_VALIDATION",
        )

    # Validate scope if applicable
    if not artifact.matches_scope(action, resource):
        logger.warning(
            "Approval scope mismatch: approval_id=%s (target=%s:%s, actual=%s:%s)",
            approval_id,
            artifact.target_operation,
            artifact.target_resource,
            action,
            resource,
        )
        raise SecurityBoundaryDenied(
            "approval scope does not match operation",
            stage="APPROVAL_SCOPE_VALIDATION",
        )

    logger.info(
        "Executing operation %s with human-approved artifact %s",
        operation_id,
        approval_id,
    )

    # Execute the mutation
    result = mutate()
    if hasattr(result, "__await__"):
        return await result  # type: ignore
    return result
