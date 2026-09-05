"""Edge Policy Cache — signed policy/authority snapshots cached at the
edge, issued exclusively by the CognitiveOS control plane.

This is NOT a second policy engine. A `SignedPolicySnapshot` is the
control plane's OWN, already-computed OPA/GovernanceEngine verdict for
one specific (principal, action, resource) tuple, captured while
connected and carried forward so it can be consulted again without a
live round trip. The Rego policy itself is never re-evaluated at the
edge; only "is this previously-issued, still-valid verdict applicable to
the request in front of me right now" is evaluated locally
(kernel/edge/local_governance.py).

Proof mechanism: Ed25519 via kernel/identity.py's existing KeyManager/
sign_bytes/verify_bytes -- the SAME primitive kernel/delegation.py's
proof and every runtime-signed envelope in this codebase already use.
No new cryptography.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.edge.freshness import CacheProvenance, Freshness, classify_freshness
from src.monkey_brain.kernel.edge.local_store import EdgeLocalStore

logger = logging.getLogger("agentos.edge.policy_cache")

_NAMESPACE = "policy_snapshot"

# The control plane's own identity string used to sign snapshots -- a
# fixed, well-known signer identity (distinct from any actor/delegation
# identity) so an edge node only ever needs ONE public key to verify
# every snapshot it receives, regardless of which actor it covers.
CONTROL_PLANE_SIGNER_ID = "cognitiveos-control-plane"


class PolicySnapshotError(Exception):
    """Any failure here means DENY/escalate -- never fall back to an
    unsigned or unscoped decision."""


@dataclass(frozen=True)
class SignedPolicySnapshot:
    """One control-plane-issued, signed authorization verdict, scoped
    tightly enough to be safe to reuse without re-contacting OPA.

    Deliberately mirrors kernel/delegation.py::DelegationCredential's
    shape (signing_fields/signing_bytes/proof) rather than inventing a
    different signed-artifact convention."""
    snapshot_id: str = field(default_factory=lambda: uuid4().hex)
    principal: str = ""
    """The actor/workload this verdict was computed for -- a SPIFFE ID
    or trusted principal_id, never an agent-supplied string."""
    action: str = ""
    resource: str = ""
    audience: str = ""
    """Which node/runtime this snapshot may be consulted by -- empty
    means "any node acting as `principal`", matching build_opa_input's
    own audience-agnostic default; set when a snapshot was issued for a
    specific edge node only."""
    approval_mode: str = "DENY"
    """AUTO_APPROVE | HUMAN_APPROVAL_REQUIRED | DENY -- copied verbatim
    from the real OPA/GovernanceEngine decision this snapshot captures.
    HUMAN_APPROVAL_REQUIRED can never be locally satisfied by presence of
    this snapshot alone (Section 5's own invariant); see
    local_governance.py."""
    policy_rule: str = ""
    risk_level: str = ""
    issued_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    """Required, non-zero -- Section 3's "time-bounded". Deliberately
    short-lived; see DEFAULT_SNAPSHOT_TTL_SECONDS."""
    authority_epoch: int = 0
    """The control plane's epoch at issuance time. A local evaluator
    must compare this against the last epoch it has observed via sync
    (Section 7) -- a later epoch means something (a revocation, a policy
    change) may have superseded this snapshot even before expires_at."""
    proof: str = ""
    proof_alg: str = "ed25519"

    def __post_init__(self) -> None:
        if not self.principal or not self.action:
            raise PolicySnapshotError("principal and action are required")
        if self.approval_mode not in ("AUTO_APPROVE", "HUMAN_APPROVAL_REQUIRED", "DENY"):
            raise PolicySnapshotError(f"invalid approval_mode {self.approval_mode!r}")
        if self.expires_at <= self.issued_at:
            raise PolicySnapshotError("expires_at must be after issued_at (must be time-bounded)")

    def signing_fields(self) -> dict[str, Any]:
        """Every security-relevant field -- altering scope, audience,
        approval_mode, or expiry after issuance must invalidate the
        proof, exactly like DelegationCredential.signing_fields()."""
        return {
            "snapshot_id": self.snapshot_id,
            "principal": self.principal,
            "action": self.action,
            "resource": self.resource,
            "audience": self.audience,
            "approval_mode": self.approval_mode,
            "policy_rule": self.policy_rule,
            "risk_level": self.risk_level,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "authority_epoch": self.authority_epoch,
        }

    def signing_bytes(self) -> bytes:
        return json.dumps(self.signing_fields(), sort_keys=True, separators=(",", ":")).encode("utf-8")

    def to_dict(self) -> dict[str, Any]:
        d = self.signing_fields()
        d["proof"] = self.proof
        d["proof_alg"] = self.proof_alg
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SignedPolicySnapshot":
        return cls(
            snapshot_id=d.get("snapshot_id", ""), principal=d.get("principal", ""),
            action=d.get("action", ""), resource=d.get("resource", ""),
            audience=d.get("audience", ""), approval_mode=d.get("approval_mode", "DENY"),
            policy_rule=d.get("policy_rule", ""), risk_level=d.get("risk_level", ""),
            issued_at=float(d.get("issued_at", 0.0)), expires_at=float(d.get("expires_at", 0.0)),
            authority_epoch=int(d.get("authority_epoch", 0)),
            proof=d.get("proof", ""), proof_alg=d.get("proof_alg", "ed25519"),
        )

    def with_proof(self, proof: str) -> "SignedPolicySnapshot":
        from dataclasses import replace
        return replace(self, proof=proof)


DEFAULT_SNAPSHOT_TTL_SECONDS = 300.0
"""Short-lived by design (Section 3: "Prefer short-lived delegation
credentials for high-risk capabilities" applies equally here) -- an edge
node that loses connectivity for longer than this naturally falls back to
escalation/refusal for REQUIRES_AUTHORITY capabilities rather than
trusting an old verdict indefinitely."""


def issue_policy_snapshot(
    *, principal: str, action: str, resource: str, policy_decision: dict[str, Any],
    audience: str = "", authority_epoch: int = 0, ttl_seconds: float = DEFAULT_SNAPSHOT_TTL_SECONDS,
) -> SignedPolicySnapshot:
    """Called ONLY from the control plane, from the same real
    GovernanceEngine/OPA verdict ensure_governed's own _authorize()
    already computed (kernel/security_boundary.py) -- never from a
    fabricated or agent-asserted decision. Signs with the control
    plane's own Ed25519 key (kernel/identity.py), the same mechanism
    kernel/delegation.py already relies on."""
    from src.monkey_brain.kernel.identity import get_key_manager, sign_bytes

    snapshot = SignedPolicySnapshot(
        principal=principal, action=action, resource=resource, audience=audience,
        approval_mode=str(policy_decision.get("approval_mode") or ("DENY" if not policy_decision.get("allowed") else "AUTO_APPROVE")),
        policy_rule=str(policy_decision.get("policy_rule") or ""),
        risk_level=str(policy_decision.get("risk_level") or ""),
        expires_at=time.time() + ttl_seconds,
        authority_epoch=authority_epoch,
    )
    km = get_key_manager()
    private_key = km.get_or_create(CONTROL_PLANE_SIGNER_ID)
    proof = sign_bytes(snapshot.signing_bytes(), private_key)
    return snapshot.with_proof(proof)


def verify_policy_snapshot(
    snapshot: SignedPolicySnapshot, *, authenticated_principal: str, audience: str = "",
) -> tuple[bool, str]:
    """Returns (valid, reason). Fail-closed: any ambiguity is invalid."""
    from src.monkey_brain.kernel.identity import verify_bytes

    if snapshot.principal != authenticated_principal:
        return False, "snapshot principal does not match authenticated caller"
    if snapshot.audience and audience and snapshot.audience != audience:
        return False, "snapshot audience does not match this node"
    if snapshot.proof_alg != "ed25519" or not snapshot.proof:
        return False, "missing or unsupported proof"
    pub_pem = resolve_control_plane_public_key()
    if not pub_pem:
        return False, "control plane public key unavailable"
    if not verify_bytes(snapshot.signing_bytes(), snapshot.proof, pub_pem):
        return False, "signature invalid (forged or tampered)"
    return True, "ok"


def resolve_control_plane_public_key() -> str:
    """Distributed-verification-friendly resolution point (mirrors
    kernel/delegation.py::resolve_issuer_public_key_pem) -- an edge node
    that has synced the control plane's public key onto local disk could
    swap this for a pure-local lookup without changing
    verify_policy_snapshot's signature; today it resolves through the
    same local KeyManager an in-process control plane already uses."""
    from src.monkey_brain.kernel.identity import get_key_manager
    return get_key_manager().get_public_key_pem(CONTROL_PLANE_SIGNER_ID)


class EdgePolicyCache:
    """Durable storage + freshness-aware retrieval for signed policy
    snapshots, backed by EdgeLocalStore. Never itself decides
    ALLOW/DENY -- that is local_governance.py's job, using this cache's
    `get_valid` as one required input."""

    def __init__(self, store: EdgeLocalStore) -> None:
        self._store = store

    @staticmethod
    def _key(principal: str, action: str, resource: str) -> str:
        return f"{principal}:{action}:{resource}"

    def store_snapshot(self, snapshot: SignedPolicySnapshot) -> None:
        provenance = CacheProvenance(
            source="opa:control_plane_snapshot",
            version=snapshot.snapshot_id,
            observed_at=snapshot.issued_at,
            expires_at=snapshot.expires_at,
            authority_epoch=snapshot.authority_epoch,
            signature=snapshot.proof,
            freshness_requirement="requires_authority",
        )
        self._store.put(
            _NAMESPACE, self._key(snapshot.principal, snapshot.action, snapshot.resource),
            snapshot.to_dict(), provenance,
        )

    def get_valid(
        self, *, principal: str, action: str, resource: str, authenticated_principal: str,
        audience: str = "", current_authority_epoch: int | None = None,
    ) -> tuple[SignedPolicySnapshot | None, Freshness, str]:
        """Returns (snapshot_or_None, freshness, reason). A non-None
        snapshot is returned ONLY when it is both cryptographically
        valid and at least STALE_BUT_USABLE-fresh; local_governance.py
        still must not treat STALE_BUT_USABLE as equivalent to FRESH for
        an authority decision -- see its own docstring."""
        entry = self._store.get(_NAMESPACE, self._key(principal, action, resource))
        if entry is None:
            return None, Freshness.UNKNOWN, "no cached snapshot for this principal/action/resource"

        freshness = classify_freshness(entry.provenance, current_authority_epoch=current_authority_epoch)
        try:
            snapshot = SignedPolicySnapshot.from_dict(entry.value)
        except PolicySnapshotError as exc:
            return None, Freshness.UNKNOWN, f"corrupt cached snapshot: {exc}"

        valid, reason = verify_policy_snapshot(
            snapshot, authenticated_principal=authenticated_principal, audience=audience,
        )
        if not valid:
            return None, Freshness.UNKNOWN, reason

        if freshness in (Freshness.STALE_MUST_REFRESH, Freshness.UNKNOWN):
            return None, freshness, "cached snapshot is stale or its epoch has been superseded"

        return snapshot, freshness, "ok"
