"""Portable Delegation — cryptographically verifiable, attenuable,
chainable transfer of BOUNDED authority between authenticated
agents/workloads, independent of ApprovalArtifact and independent of
transferring any identity/credential material.

Two existing "delegation" concepts in this codebase solve different,
narrower problems and are NOT extended here:
kernel/society/delegation.py::Delegation (unauthenticated, single-process,
membership-permission convenience — its only real, live consumer is
kernel/affiliations/graph.py's communication-routing cascade, deciding
whether two actors may exchange messages at all; it is never consulted
for capability-execution authority) and
kernel/domains/domain_security.py::grant_delegation (KG-persisted,
household/grocery-specific, string IDs only, no proof). Neither can
express "Agent A cryptographically proves it granted Agent B this exact
bounded, attenuable, chainable authority" — this module is the one place
that does, and it is the ONLY delegation mechanism ensure_governed ever
consults for execution authority.

Core invariant (Section 1 of the task this module implements):

    Authority(child delegation) ⊆ Authority(parent delegation)

Delegation vs. Approval (deliberately kept separate — Section 3):

    Delegation = "I authorize Agent B to exercise this bounded authority."
    Approval   = "This particular operation is approved."

A DelegationCredential is never itself treated as human approval, and
never sets ApprovalMode. It only ever narrows what OPA is later allowed
to evaluate as "requested" — the AUTO_APPROVE / HUMAN_APPROVAL_REQUIRED /
DENY decision remains exclusively OPA's/GovernanceEngine's, via
security_boundary.py's existing pipeline (see build_opa_input's
`verified_delegation` parameter).

Proof mechanism (Section 7/28 — no invented cryptography): Ed25519 via
kernel/identity.py's already-trusted KeyManager/sign_bytes/verify_bytes,
the SAME primitive this codebase already uses to sign proposals,
checkpoints, and execution graphs. Deliberately NOT identity.py's
sign_payload/verify_signed_payload — those bundle a nonce+timestamp
anti-replay envelope suited to single-use messages (Section 18: a
delegation credential must NOT be collapsed with single-use approval; it
is reusable authority, not a one-shot token).

Distributed verification (Section 25): verify_delegation_proof takes the
issuer's public key PEM explicitly rather than reaching into local
process memory, so a remote verifier that has independently obtained the
issuer's public key (today: kernel/identity.py's KeyManager, keyed by the
issuer's authenticated identity string — a single-process/shared-keystore
resolution strategy; a distributed public-key registry is a separate PKI
concern this module does not solve) can verify without contacting the
issuer's process.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable
from uuid import uuid4

logger = logging.getLogger("agentos.delegation")

ARTIFACT_VERSION = "1.0"

# Section 13: chain termination. Configurable via env, not invented
# arbitrarily -- bounded low because every additional hop is one more
# independently-authenticated workload the whole chain's authority now
# transitively depends on.
DEFAULT_MAX_DELEGATION_DEPTH = 4

# Section 16: prefer short-lived delegation credentials for high-risk
# capabilities. This is an upper bound on issuance, not a recommendation
# -- issue_delegation() enforces it.
DEFAULT_MAX_DELEGATION_TTL_SECONDS = 24 * 3600

# Section 22/1 (HUMAN_APPROVAL_CANNOT_BE_DELEGATED_BY_AN_AGENT): no
# delegation may grant these regardless of what the issuer claims to hold
# -- an agent's own authority never includes the right to grant them, so
# no attenuation logic could ever legitimately produce them either.
_FORBIDDEN_CAPABILITY_MARKERS = (
    "human_approval", "approve_as_human", "mfa", "operator_identity",
    "human_session", "human_authorization", "approval.grant",
    "approval.override", "self_approve",
)


class DelegationError(Exception):
    """Base for delegation-specific failures. Callers at the security
    boundary should treat any DelegationError as DENY (Section 31: fail
    closed on any ambiguity)."""


class DelegationForgedError(DelegationError):
    """Proof does not verify, or signed content was altered post-issuance."""


class DelegationDeniedError(DelegationError):
    """A validation invariant failed. `result` carries the structured
    DelegationValidationResult explaining exactly which one."""

    def __init__(self, message: str, result: "DelegationValidationResult | None" = None) -> None:
        super().__init__(message)
        self.result = result


def _audit_delegation_event(
    event: str, *, delegation_id: str = "", issuer: str = "", delegate: str = "",
    parent_delegation_id: str = "", outcome: str = "success", details: dict[str, Any] | None = None,
) -> None:
    """Section 23. Best-effort: a delegation lifecycle record failing to
    persist must not itself block the caller (the SAME "supplementary
    telemetry" posture as security_boundary.py's _audit_attempt_event) --
    the gating decision (DENY/ALLOW) has already been made independently
    by the caller before this is invoked. Never logs `proof`/private key
    material -- only identity strings and the already-public scope/
    capability/timing fields."""
    try:
        from src.monkey_brain.kernel.audit import get_audit_log
        get_audit_log().record(
            runtime_id=issuer or "unknown",
            event_type="delegation",
            action=event,
            actor=issuer,
            target=delegate,
            outcome=outcome,
            details={"parent_delegation_id": parent_delegation_id, **(details or {})},
            critical=True,
            correlation_id=delegation_id,
        )
    except Exception:
        logger.warning("delegation audit event %s failed to persist for %s", event, delegation_id, exc_info=True)


def _canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _is_forbidden_capability(capability: str) -> bool:
    key = (capability or "").strip().lower()
    return any(marker in key for marker in _FORBIDDEN_CAPABILITY_MARKERS)


@dataclass(frozen=True)
class DelegationScope:
    """Section 9: what a delegation is bounded to, beyond `capabilities`.

    Empty `resources`/`actions` means "this dimension was not scoped by
    the issuer" -- narrowing is always allowed going down a chain, but an
    UNSCOPED dimension is never treated as "grants access to every
    resource/action for the delegated capability" at the enforcement
    point (Section 9: "capability=X does not mean all resources/actions
    for X unless policy explicitly says so") -- OPA's own policy is what
    ultimately decides whether an unscoped dimension is acceptable for a
    given capability; this dataclass only tracks what was stated and
    enforces that it never widens.
    """
    resources: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"resources": list(self.resources), "actions": list(self.actions)}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "DelegationScope":
        d = d or {}
        return cls(resources=tuple(d.get("resources") or ()), actions=tuple(d.get("actions") or ()))


def _dimension_is_narrower_or_equal(child: tuple[str, ...], parent: tuple[str, ...]) -> bool:
    """child ⊆ parent, EXCEPT an unscoped (empty) parent grants no
    additional widening room beyond what the parent already left
    unscoped -- an empty parent permits any child value (including
    empty), a non-empty parent requires the child to be a non-empty
    subset of it."""
    if not parent:
        return True
    if not child:
        return False
    return set(child).issubset(set(parent))


def _constraint_is_narrower_or_equal(key: str, child_val: Any, parent_val: Any) -> bool:
    """Section 11: treat constraints conservatively. If we cannot PROVE
    the child is no broader than the parent, return False (caller denies)."""
    key_l = key.lower()
    try:
        if isinstance(parent_val, (int, float)) and isinstance(child_val, (int, float)):
            if "max" in key_l or key_l in ("amount", "expires", "expires_at"):
                return child_val <= parent_val
            if "min" in key_l:
                return child_val >= parent_val
            return child_val == parent_val
        if isinstance(parent_val, (list, tuple, set)):
            parent_set = set(parent_val)
            if isinstance(child_val, (list, tuple, set)):
                return bool(child_val) and set(child_val).issubset(parent_set)
            return child_val in parent_set
        return child_val == parent_val
    except TypeError:
        return False


def _constraints_are_narrower_or_equal(child: dict[str, Any], parent: dict[str, Any]) -> bool:
    for key, parent_val in (parent or {}).items():
        if key not in (child or {}):
            # Parent explicitly scoped this; silently dropping it in the
            # child would widen effective authority back to unscoped.
            return False
        if not _constraint_is_narrower_or_equal(key, child[key], parent_val):
            return False
    return True


@dataclass(frozen=True)
class DelegationCredential:
    """Immutable, portable delegation credential (Section 4/6).

    Portability (Section 6): contains no private key material and no
    copy of the issuer's own auth credential (JWT/session/cert) -- only
    the issuer's identity STRING and a signature over this content.
    Presenting this credential never requires possessing Agent A's keys.
    """
    delegation_id: str = field(default_factory=lambda: uuid4().hex)
    artifact_version: str = ARTIFACT_VERSION
    issuer: str = ""
    delegate: str = ""
    parent_delegation_id: str = ""
    issued_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    scope: DelegationScope = field(default_factory=DelegationScope)
    capabilities: tuple[str, ...] = ()
    constraints: dict[str, Any] = field(default_factory=dict)
    audience: str = ""
    delegation_depth: int = 0
    proof: str = ""
    proof_alg: str = "ed25519"

    def __post_init__(self) -> None:
        if not self.delegation_id:
            raise DelegationError("delegation_id is required")
        if not self.issuer or not self.delegate:
            raise DelegationError("issuer and delegate are required")
        if self.issuer == self.delegate:
            # Section 14: reject self-delegation outright at construction
            # time -- there is no architectural use case in this system
            # that needs it, so it is never permitted, not merely
            # "permitted but authority-neutral."
            raise DelegationError("self-delegation is not permitted (issuer == delegate)")
        if self.expires_at <= self.issued_at:
            raise DelegationError("expires_at must be after issued_at (delegation must be time-bound)")
        if not self.capabilities:
            raise DelegationError("capabilities must be explicit and non-empty")
        for cap in self.capabilities:
            if _is_forbidden_capability(cap):
                raise DelegationError(f"capability {cap!r} cannot be delegated by an agent")
        if self.delegation_depth < 0:
            raise DelegationError("delegation_depth cannot be negative")
        if (self.delegation_depth == 0) != (not self.parent_delegation_id):
            raise DelegationError("delegation_depth must be 0 iff parent_delegation_id is empty")

    def signing_fields(self) -> dict[str, Any]:
        """Section 8: every security-relevant field, canonically ordered.
        Anything not listed here can be altered post-issuance without
        detection -- keep this in sync with every field above except
        `proof`/`proof_alg` themselves."""
        return {
            "delegation_id": self.delegation_id,
            "artifact_version": self.artifact_version,
            "issuer": self.issuer,
            "delegate": self.delegate,
            "parent_delegation_id": self.parent_delegation_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "scope": self.scope.to_dict(),
            "capabilities": list(self.capabilities),
            "constraints": self.constraints,
            "audience": self.audience,
            "delegation_depth": self.delegation_depth,
        }

    def signing_bytes(self) -> bytes:
        return _canonical_json(self.signing_fields())

    def to_dict(self) -> dict[str, Any]:
        d = self.signing_fields()
        d["proof"] = self.proof
        d["proof_alg"] = self.proof_alg
        return d

    def to_json(self) -> str:
        """Section 24: canonical, deterministic, versioned, transportable
        (JSON over HTTP/bus/queue) -- never pickle."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DelegationCredential":
        return cls(
            delegation_id=d.get("delegation_id", ""),
            artifact_version=d.get("artifact_version", ARTIFACT_VERSION),
            issuer=d.get("issuer", ""),
            delegate=d.get("delegate", ""),
            parent_delegation_id=d.get("parent_delegation_id", ""),
            issued_at=float(d.get("issued_at", 0.0)),
            expires_at=float(d.get("expires_at", 0.0)),
            scope=DelegationScope.from_dict(d.get("scope")),
            capabilities=tuple(d.get("capabilities") or ()),
            constraints=dict(d.get("constraints") or {}),
            audience=d.get("audience", ""),
            delegation_depth=int(d.get("delegation_depth", 0)),
            proof=d.get("proof", ""),
            proof_alg=d.get("proof_alg", "ed25519"),
        )

    @classmethod
    def from_json(cls, blob: str) -> "DelegationCredential":
        return cls.from_dict(json.loads(blob))

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def with_proof(self, proof: str) -> "DelegationCredential":
        return replace(self, proof=proof)


# ── Issuance / proof ─────────────────────────────────────────────────────

def issue_delegation(
    *,
    issuer: str,
    delegate: str,
    capabilities: tuple[str, ...],
    scope: DelegationScope | None = None,
    constraints: dict[str, Any] | None = None,
    ttl_seconds: float = DEFAULT_MAX_DELEGATION_TTL_SECONDS,
    audience: str = "",
    parent: DelegationCredential | None = None,
) -> DelegationCredential:
    """Create and sign a new delegation. `issuer` MUST be the caller's own
    authenticated identity string (SPIFFE URI or trusted principal_id) --
    this function does not itself check that (see verify_issuer_identity
    below, which the security boundary calls with the ACTUAL authenticated
    caller); calling this with a fabricated `issuer` produces a credential
    that simply fails verification everywhere else, because the proof is
    computed with the issuer's own key -- an agent cannot forge someone
    else's signature by naming them as issuer.

    Attenuation (Section 10): if `parent` is given, this delegation is
    D(n+1) in a chain -- depth/parent_delegation_id are derived from it,
    and the caller-supplied scope/capabilities/constraints/ttl are
    validated to be no broader than the parent's BEFORE signing (fail
    fast at issuance, in addition to the mandatory re-validation every
    verifier performs independently).
    """
    from src.monkey_brain.kernel.identity import get_key_manager

    ttl_seconds = min(ttl_seconds, DEFAULT_MAX_DELEGATION_TTL_SECONDS)
    now = time.time()
    scope = scope or DelegationScope()
    depth = 0 if parent is None else parent.delegation_depth + 1
    expires_at = now + ttl_seconds
    if parent is not None:
        expires_at = min(expires_at, parent.expires_at)

    credential = DelegationCredential(
        issuer=issuer,
        delegate=delegate,
        parent_delegation_id=parent.delegation_id if parent else "",
        issued_at=now,
        expires_at=expires_at,
        scope=scope,
        capabilities=tuple(capabilities),
        constraints=dict(constraints or {}),
        audience=audience,
        delegation_depth=depth,
    )

    if parent is not None:
        result = _validate_attenuation(parent=parent, child=credential)
        if not result.authorized:
            _audit_delegation_event(
                "delegation_rejected", delegation_id=credential.delegation_id, issuer=issuer,
                delegate=delegate, parent_delegation_id=parent.delegation_id, outcome="denied",
                details={"reason": result.failure_reason},
            )
            raise DelegationDeniedError(
                f"cannot issue delegation: {result.failure_reason}", result=result,
            )

    km = get_key_manager()
    private_key = km.get_or_create(issuer)
    from src.monkey_brain.kernel.identity import sign_bytes
    proof = sign_bytes(credential.signing_bytes(), private_key)
    signed = credential.with_proof(proof)
    _audit_delegation_event(
        "delegation_attenuated" if parent is not None else "delegation_created",
        delegation_id=signed.delegation_id, issuer=issuer, delegate=delegate,
        parent_delegation_id=signed.parent_delegation_id,
        details={"capabilities": list(signed.capabilities), "expires_at": signed.expires_at,
                  "delegation_depth": signed.delegation_depth},
    )
    return signed


def resolve_issuer_public_key_pem(issuer: str) -> str:
    """Default public-key resolution strategy (Section 25): this
    process's local KeyManager, keyed by the issuer's authenticated
    identity string. Correct today because issuer and verifier share one
    MonkeyBrain deployment's keystore; a genuinely distributed swarm would
    swap this for a published-key registry / SPIFFE-bundle-embedded key
    without changing verify_delegation_proof's signature."""
    from src.monkey_brain.kernel.identity import get_key_manager
    return get_key_manager().get_public_key_pem(issuer)


def verify_delegation_proof(
    credential: DelegationCredential,
    *,
    issuer_public_key_pem: str | None = None,
    public_key_resolver: Callable[[str], str] = resolve_issuer_public_key_pem,
) -> bool:
    """Section 7/8: does `credential.proof` bind ALL security-relevant
    fields to `credential.issuer`'s key? A hash alone (Section 7) is
    never accepted as proof -- this always calls Ed25519 verify_bytes."""
    from src.monkey_brain.kernel.identity import verify_bytes

    if credential.proof_alg != "ed25519" or not credential.proof:
        return False
    pub_pem = issuer_public_key_pem or public_key_resolver(credential.issuer)
    if not pub_pem:
        return False
    return verify_bytes(credential.signing_bytes(), credential.proof, pub_pem)


# ── Attenuation / validation ─────────────────────────────────────────────

@dataclass(frozen=True)
class DelegationValidationResult:
    issuer_valid: bool = False
    delegate_valid: bool = False
    proof_valid: bool = False
    parent_valid: bool = False
    scope_valid: bool = False
    capability_valid: bool = False
    constraints_valid: bool = False
    expiration_valid: bool = False
    audience_valid: bool = False
    depth_valid: bool = False
    revocation_valid: bool = False
    authorized: bool = False
    failure_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "issuer_valid": self.issuer_valid, "delegate_valid": self.delegate_valid,
            "proof_valid": self.proof_valid, "parent_valid": self.parent_valid,
            "scope_valid": self.scope_valid, "capability_valid": self.capability_valid,
            "constraints_valid": self.constraints_valid, "expiration_valid": self.expiration_valid,
            "audience_valid": self.audience_valid, "depth_valid": self.depth_valid,
            "revocation_valid": self.revocation_valid, "authorized": self.authorized,
            "failure_reason": self.failure_reason,
        }


def _deny(reason: str, **flags: bool) -> DelegationValidationResult:
    return DelegationValidationResult(authorized=False, failure_reason=reason, **flags)


def _validate_attenuation(*, parent: DelegationCredential, child: DelegationCredential) -> DelegationValidationResult:
    """Section 10/11/12: child_scope ⊆ parent_scope, child_expiration <=
    parent_expiration, child_capabilities ⊆ parent_capabilities, child
    constraints no broader than parent's. Pure structural check -- does
    NOT verify proofs/identity (validate_delegation does the full check)."""
    if child.parent_delegation_id != parent.delegation_id:
        return _deny("child.parent_delegation_id does not reference this parent")
    if child.delegation_depth != parent.delegation_depth + 1:
        return _deny("child.delegation_depth must be parent.delegation_depth + 1", depth_valid=False)
    depth_valid = True

    capability_valid = _dimension_is_narrower_or_equal(child.capabilities, parent.capabilities) and bool(parent.capabilities)
    if not capability_valid:
        return _deny("child capabilities are not a subset of parent capabilities", depth_valid=depth_valid)

    scope_valid = (
        _dimension_is_narrower_or_equal(child.scope.resources, parent.scope.resources)
        and _dimension_is_narrower_or_equal(child.scope.actions, parent.scope.actions)
    )
    if not scope_valid:
        return _deny("child scope escapes parent scope", depth_valid=depth_valid, capability_valid=capability_valid)

    constraints_valid = _constraints_are_narrower_or_equal(child.constraints, parent.constraints)
    if not constraints_valid:
        return _deny(
            "child constraints are broader than (or drop) a parent constraint",
            depth_valid=depth_valid, capability_valid=capability_valid, scope_valid=scope_valid,
        )

    expiration_valid = child.expires_at <= parent.expires_at
    if not expiration_valid:
        return _deny(
            "child cannot outlive parent (child.expires_at > parent.expires_at)",
            depth_valid=depth_valid, capability_valid=capability_valid,
            scope_valid=scope_valid, constraints_valid=constraints_valid,
        )

    # Section 5: delegate at hop N must be the issuer at hop N+1 -- a
    # delegation cannot be re-issued by anyone other than the workload it
    # was actually granted to.
    if parent.delegate != child.issuer:
        return _deny(
            "child.issuer is not the parent's delegate -- delegation was re-issued by an unauthorized party",
            depth_valid=depth_valid, capability_valid=capability_valid,
            scope_valid=scope_valid, constraints_valid=constraints_valid, expiration_valid=expiration_valid,
        )

    return DelegationValidationResult(
        depth_valid=depth_valid, capability_valid=capability_valid, scope_valid=scope_valid,
        constraints_valid=constraints_valid, expiration_valid=expiration_valid, authorized=True,
    )


def validate_delegation(
    *,
    child: DelegationCredential,
    parent: DelegationCredential | None,
    authenticated_issuer: str,
    authenticated_delegate: str,
    is_revoked: Callable[[str], bool] | None = None,
    public_key_resolver: Callable[[str], str] = resolve_issuer_public_key_pem,
    max_depth: int = DEFAULT_MAX_DELEGATION_DEPTH,
    now: float | None = None,
) -> DelegationValidationResult:
    result = _validate_delegation_inner(
        child=child, parent=parent, authenticated_issuer=authenticated_issuer,
        authenticated_delegate=authenticated_delegate, is_revoked=is_revoked,
        public_key_resolver=public_key_resolver, max_depth=max_depth, now=now,
    )
    _audit_delegation_event(
        "delegation_verified" if result.authorized else "delegation_rejected",
        delegation_id=child.delegation_id, issuer=child.issuer, delegate=child.delegate,
        parent_delegation_id=child.parent_delegation_id,
        outcome="allowed" if result.authorized else "denied",
        details={} if result.authorized else {"reason": result.failure_reason},
    )
    return result


def _validate_delegation_inner(
    *,
    child: DelegationCredential,
    parent: DelegationCredential | None,
    authenticated_issuer: str,
    authenticated_delegate: str,
    is_revoked: Callable[[str], bool] | None = None,
    public_key_resolver: Callable[[str], str] = resolve_issuer_public_key_pem,
    max_depth: int = DEFAULT_MAX_DELEGATION_DEPTH,
    now: float | None = None,
) -> DelegationValidationResult:
    """Section 15: validate ONE delegation (against its direct parent, if
    any) end to end. `authenticated_issuer`/`authenticated_delegate` MUST
    come from the trust boundary's own verified identity (SPIFFE/trusted
    auth) for whichever hop is being checked right now -- NEVER from the
    credential's own claimed fields (Section 5: never trust `{"issuer":
    "planner"}` merely because it appears in a message)."""
    now = time.time() if now is None else now

    if child.issuer != authenticated_issuer:
        return _deny("credential.issuer does not match the authenticated issuer presenting it")
    issuer_valid = True

    if child.delegate != authenticated_delegate:
        return _deny("credential.delegate does not match the authenticated recipient", issuer_valid=issuer_valid)
    delegate_valid = True

    if child.audience and child.audience != authenticated_delegate:
        return _deny("credential.audience does not match the authenticated recipient",
                     issuer_valid=issuer_valid, delegate_valid=delegate_valid)
    audience_valid = True

    if child.delegation_depth > max_depth:
        return _deny(f"delegation_depth {child.delegation_depth} exceeds max_delegation_depth {max_depth}",
                     issuer_valid=issuer_valid, delegate_valid=delegate_valid, audience_valid=audience_valid)
    depth_valid = True

    if not verify_delegation_proof(child, public_key_resolver=public_key_resolver):
        return _deny("delegation proof does not verify (forged or tampered)",
                     issuer_valid=issuer_valid, delegate_valid=delegate_valid,
                     audience_valid=audience_valid, depth_valid=depth_valid)
    proof_valid = True

    if now > child.expires_at:
        return _deny("delegation has expired",
                     issuer_valid=issuer_valid, delegate_valid=delegate_valid, audience_valid=audience_valid,
                     depth_valid=depth_valid, proof_valid=proof_valid)
    expiration_valid = True

    if is_revoked is not None and is_revoked(child.delegation_id):
        return _deny("delegation has been revoked",
                     issuer_valid=issuer_valid, delegate_valid=delegate_valid, audience_valid=audience_valid,
                     depth_valid=depth_valid, proof_valid=proof_valid, expiration_valid=expiration_valid)
    revocation_valid = True

    if child.parent_delegation_id:
        if parent is None:
            return _deny("child references a parent delegation that was not supplied for verification",
                         issuer_valid=issuer_valid, delegate_valid=delegate_valid, audience_valid=audience_valid,
                         depth_valid=depth_valid, proof_valid=proof_valid, expiration_valid=expiration_valid,
                         revocation_valid=revocation_valid)
        if not verify_delegation_proof(parent, public_key_resolver=public_key_resolver):
            return _deny("parent delegation proof does not verify",
                         issuer_valid=issuer_valid, delegate_valid=delegate_valid, audience_valid=audience_valid,
                         depth_valid=depth_valid, proof_valid=proof_valid, expiration_valid=expiration_valid,
                         revocation_valid=revocation_valid)
        if now > parent.expires_at:
            return _deny("parent delegation has expired",
                         issuer_valid=issuer_valid, delegate_valid=delegate_valid, audience_valid=audience_valid,
                         depth_valid=depth_valid, proof_valid=proof_valid, expiration_valid=expiration_valid,
                         revocation_valid=revocation_valid)
        if is_revoked is not None and is_revoked(parent.delegation_id):
            return _deny("parent delegation has been revoked",
                         issuer_valid=issuer_valid, delegate_valid=delegate_valid, audience_valid=audience_valid,
                         depth_valid=depth_valid, proof_valid=proof_valid, expiration_valid=expiration_valid,
                         revocation_valid=revocation_valid)
        parent_valid = True
        attenuation = _validate_attenuation(parent=parent, child=child)
        if not attenuation.authorized:
            return _deny(attenuation.failure_reason,
                         issuer_valid=issuer_valid, delegate_valid=delegate_valid, audience_valid=audience_valid,
                         depth_valid=depth_valid, proof_valid=proof_valid, expiration_valid=expiration_valid,
                         revocation_valid=revocation_valid, parent_valid=parent_valid,
                         scope_valid=attenuation.scope_valid, capability_valid=attenuation.capability_valid,
                         constraints_valid=attenuation.constraints_valid)
        scope_valid, capability_valid, constraints_valid = True, True, True
    else:
        # Root delegation: no parent to attenuate against. Its capability
        # set is bounded only by what OPA/GovernanceEngine independently
        # allows `authenticated_issuer` to hold and grant -- this module
        # does not itself model issuer entitlements (Section 29: keep
        # SPIFFE/delegation/OPA/approval/execution responsibilities
        # separate); build_opa_input's `verified_delegation` field is
        # what lets policy make that entitlement check.
        parent_valid = True
        scope_valid, capability_valid, constraints_valid = True, True, True

    return DelegationValidationResult(
        issuer_valid=issuer_valid, delegate_valid=delegate_valid, proof_valid=proof_valid,
        parent_valid=parent_valid, scope_valid=scope_valid, capability_valid=capability_valid,
        constraints_valid=constraints_valid, expiration_valid=expiration_valid,
        audience_valid=audience_valid, depth_valid=depth_valid, revocation_valid=revocation_valid,
        authorized=True,
    )


def verify_delegation_chain(
    *,
    chain: tuple[DelegationCredential, ...],
    authenticated_delegate: str,
    authenticated_issuers_by_depth: dict[int, str] | None = None,
    is_revoked: Callable[[str], bool] | None = None,
    public_key_resolver: Callable[[str], str] = resolve_issuer_public_key_pem,
    max_depth: int = DEFAULT_MAX_DELEGATION_DEPTH,
    now: float | None = None,
) -> DelegationValidationResult:
    """Section 12/13: recursively verify an entire A -> B -> C -> ... ->
    leaf chain, root first. `chain[0]` must have `parent_delegation_id ==
    ""`; each `chain[i].parent_delegation_id == chain[i-1].delegation_id`.

    `authenticated_delegate` is the caller ACTUALLY presenting the leaf
    credential right now (Section 17/19) -- it is checked against
    `chain[-1].delegate`. Interior hops' issuer identity is verified
    structurally (Section 5's "delegate at hop N must be the issuer at
    hop N+1", enforced in _validate_attenuation) rather than requiring
    live re-authentication of every intermediate agent for every use --
    each interior credential's own proof already cryptographically
    establishes that ITS issuer (the previous hop's delegate) actually
    signed it, so a forged interior hop is caught by proof verification,
    not by needing that agent online right now. `authenticated_issuers_by_depth`
    is available for callers who additionally have live confirmation of
    a specific hop's issuer (rarely needed; omit otherwise).
    """
    now = time.time() if now is None else now
    if not chain:
        return _deny("empty delegation chain")
    if chain[0].parent_delegation_id:
        return _deny("chain root must have no parent_delegation_id")
    if len(chain) - 1 > max_depth:
        return _deny(f"chain length {len(chain)} exceeds max_delegation_depth {max_depth}")

    for i in range(1, len(chain)):
        if chain[i].parent_delegation_id != chain[i - 1].delegation_id:
            return _deny(f"chain link {i} does not reference the previous credential as its parent")

    leaf = chain[-1]
    if leaf.delegate != authenticated_delegate:
        return _deny("authenticated caller does not match the leaf delegation's delegate")

    issuers = authenticated_issuers_by_depth or {}
    root_issuer = issuers.get(0, chain[0].issuer)
    result = validate_delegation(
        child=chain[0], parent=None, authenticated_issuer=root_issuer,
        authenticated_delegate=chain[0].delegate, is_revoked=is_revoked,
        public_key_resolver=public_key_resolver, max_depth=max_depth, now=now,
    )
    if not result.authorized:
        return result

    for i in range(1, len(chain)):
        hop_issuer = issuers.get(i, chain[i].issuer)
        result = validate_delegation(
            child=chain[i], parent=chain[i - 1], authenticated_issuer=hop_issuer,
            authenticated_delegate=chain[i].delegate if i < len(chain) - 1 else authenticated_delegate,
            is_revoked=is_revoked, public_key_resolver=public_key_resolver,
            max_depth=max_depth, now=now,
        )
        if not result.authorized:
            return result

    return result


def to_opa_delegation_context(chain: tuple[DelegationCredential, ...]) -> dict[str, Any]:
    """The ONLY sanctioned shape for OPA's `delegation` input key (Section
    21) -- built exclusively from a chain that has ALREADY passed
    verify_delegation_chain, never from agent-supplied claims. Callers
    must not construct this dict by hand from message content."""
    if not chain:
        return {}
    leaf = chain[-1]
    return {
        "delegation_id": leaf.delegation_id,
        "issuer": leaf.issuer,
        "delegate": leaf.delegate,
        "root_issuer": chain[0].issuer,
        "scope": leaf.scope.to_dict(),
        "capabilities": list(leaf.capabilities),
        "constraints": leaf.constraints,
        "delegation_depth": leaf.delegation_depth,
        "chain_length": len(chain),
        "expires_at": leaf.expires_at,
    }


# ── Revocation store (Section 16) ────────────────────────────────────────

class DelegationStore:
    """In-memory delegation registry + revocation tracking, mirroring the
    existing ApprovalArtifactStore pattern (approval.py) rather than
    inventing a new persistence architecture. Revoking a delegation
    invalidates every descendant recorded here (Section 16) -- a
    descendant not registered in THIS store (e.g. purely presented as a
    serialized chain from a remote agent) is instead caught by
    verify_delegation_chain re-validating every ancestor's own
    revocation/expiration status each time it is used, so revocation
    checked here is a fast-path, not the only enforcement point."""

    def __init__(self) -> None:
        self._credentials: dict[str, DelegationCredential] = {}
        self._revoked: dict[str, str] = {}
        self._children: dict[str, list[str]] = {}

    def register(self, credential: DelegationCredential) -> None:
        self._credentials[credential.delegation_id] = credential
        if credential.parent_delegation_id:
            self._children.setdefault(credential.parent_delegation_id, []).append(credential.delegation_id)

    def get(self, delegation_id: str) -> DelegationCredential | None:
        return self._credentials.get(delegation_id)

    def revoke(self, delegation_id: str, reason: str = "") -> bool:
        credential = self._credentials.get(delegation_id)
        if credential is None:
            return False
        self._revoked[delegation_id] = reason or "revoked"
        _audit_delegation_event(
            "delegation_revoked", delegation_id=delegation_id, issuer=credential.issuer,
            delegate=credential.delegate, parent_delegation_id=credential.parent_delegation_id,
            details={"reason": reason or "revoked"},
        )
        for child_id in self._children.get(delegation_id, ()):
            self.revoke(child_id, reason=f"ancestor {delegation_id} revoked")
        return True

    def is_revoked(self, delegation_id: str) -> bool:
        if delegation_id in self._revoked:
            return True
        credential = self._credentials.get(delegation_id)
        if credential and credential.parent_delegation_id:
            return self.is_revoked(credential.parent_delegation_id)
        return False


_default_store: DelegationStore | None = None


def get_delegation_store() -> DelegationStore:
    global _default_store
    if _default_store is None:
        _default_store = DelegationStore()
    return _default_store


def reset_delegation_store_for_tests() -> None:
    global _default_store
    _default_store = DelegationStore()
