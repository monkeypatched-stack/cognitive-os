"""Trusted authentication evidence — never taken from agent/LLM payloads.

MFA and authentication state are bound from verified JWT claims or service
credentials. Agents cannot set these fields.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Mapping

MFA_SATISFIED = "satisfied"
MFA_NOT_SATISFIED = "not_satisfied"
MFA_UNKNOWN = "unknown"
MFA_NOT_REQUIRED = "not_required"

_VALID_MFA = frozenset({MFA_SATISFIED, MFA_NOT_SATISFIED, MFA_UNKNOWN, MFA_NOT_REQUIRED})

# Agent-supplied keys that must never be treated as security evidence.
UNTRUSTED_SECURITY_SIGNAL_KEYS = frozenset(
    {
        "mfa_enforced",
        "mfa_status",
        "mfa_satisfied",
        "authenticated",
        "token_valid",
        "mfa_required",
        "trusted_auth",
        "authorized",
        "authorization",
        "is_admin",
        "role",
        "roles",
        "permissions",
        "policy_approval",
        "governance_approval",
        "governance_allowed",
        "opa_allow",
        "audit_authority",
        "execution_authority",
        # SPIFFE/SPIRE workload identity layer: an agent claiming its own
        # spiffe_id/trust_domain/verified flag in message content must never
        # be treated as if mTLS/the Workload API actually attested it -- see
        # kernel/workload_identity.py's module docstring and
        # evidence_from_spiffe() below, the only sanctioned construction path.
        "spiffe_id",
        "spiffe_verified",
        "sender_spiffe_id",
        "recipient_spiffe_id",
        # Portable Delegation: an agent's own claim about what authority it
        # was delegated (or that it holds delegation at all) must never reach
        # OPA as if trusted -- only build_opa_input's `verified_delegation`
        # keyword (populated exclusively from a chain that has already passed
        # kernel/delegation.py::verify_delegation_chain) may set the
        # `delegation` input key. See kernel/delegation.py::
        # to_opa_delegation_context, the only sanctioned constructor for it.
        "delegation",
        "delegation_id",
        "delegation_chain",
    }
)

_current: ContextVar["TrustedAuthEvidence | None"] = ContextVar("trusted_auth", default=None)


@dataclass(frozen=True)
class TrustedAuthEvidence:
    authenticated: bool
    token_valid: bool
    principal_id: str
    principal_type: str  # human | service | unknown
    mfa_status: str
    session_id: str = ""
    permissions: tuple[str, ...] = ()
    spiffe_id: str = ""
    """The verified SPIFFE URI (spiffe://<trust-domain>/agent/<id>) this
    evidence is bound to, when the principal authenticated via a real
    workload identity (kernel/workload_identity.py). "" when this evidence
    came from a non-SPIFFE source (human JWT, X-User-ID dev bypass,
    internal-service-token) -- absence here is not itself a security
    problem, most existing evidence legitimately has none."""
    spiffe_verified: bool = False
    """True only when spiffe_id came from a real X.509-SVID (WorkloadIdentity.
    is_cryptographically_verified), never from the guarded local-dev
    SPIFFE_ID env override. A caller that specifically needs cryptographic
    proof (not just "some spiffe_id string is present") must check this,
    not merely `bool(spiffe_id)`."""

    def mfa_satisfied(self) -> bool:
        return self.mfa_status == MFA_SATISFIED

    def to_opa_auth(self) -> dict[str, Any]:
        from src.monkey_brain.kernel.production_gates import mfa_required

        required = mfa_required() and self.principal_type != "service"
        status = self.mfa_status if self.mfa_status in _VALID_MFA else MFA_UNKNOWN
        return {
            "authenticated": self.authenticated,
            "token_valid": self.token_valid,
            "principal": self.principal_id,
            "principal_type": self.principal_type,
            "mfa_status": status,
            "mfa_required": required,
            "mfa_satisfied": status == MFA_SATISFIED,
            "session_id": self.session_id,
            "agent_attested_mfa": False,
            "spiffe_id": self.spiffe_id,
            "spiffe_verified": self.spiffe_verified,
        }


def normalize_mfa_status(value: Any) -> str:
    if value is None:
        return MFA_UNKNOWN
    text = str(value).strip().lower()
    if text in _VALID_MFA:
        return text
    if text in ("true", "1", "yes"):
        return MFA_SATISFIED
    if text in ("false", "0", "no"):
        return MFA_NOT_SATISFIED
    return MFA_UNKNOWN


def evidence_from_jwt(payload: Mapping[str, Any]) -> TrustedAuthEvidence:
    principal = str(payload.get("sub") or payload.get("user_id") or "")
    mfa_status = normalize_mfa_status(payload.get("mfa_status"))
    permissions = payload.get("permissions") or []
    perms = tuple(p if isinstance(p, str) else str(p.get("permission_id", "")) for p in permissions if p)
    return TrustedAuthEvidence(
        authenticated=bool(principal),
        token_valid=True,
        principal_id=principal,
        principal_type="human",
        mfa_status=mfa_status,
        session_id=str(payload.get("jti") or ""),
        permissions=perms,
    )


def evidence_for_service(principal_id: str) -> TrustedAuthEvidence:
    return TrustedAuthEvidence(
        authenticated=True,
        token_valid=True,
        principal_id=principal_id,
        principal_type="service",
        mfa_status=MFA_NOT_REQUIRED,
    )


def evidence_from_spiffe(identity: "Any") -> TrustedAuthEvidence:
    """The ONLY sanctioned way to bind a SPIFFE workload identity into
    trusted evidence. `identity` must be a kernel.workload_identity.
    WorkloadIdentity -- constructed exclusively from WorkloadIdentityProvider.
    get_current_identity(), never from request JSON, a message's claimed
    sender, an agent_id string, or LLM output (see that module's own
    docstring). principal_id is the full SPIFFE URI itself, not a
    human-friendly name parsed out of it -- callers that want the short
    agent-id component can use WorkloadIdentity.path_segment().

    Deliberately typed as Any here (not WorkloadIdentity) to avoid a
    circular import (workload_identity.py has no need to import
    trusted_auth.py back) -- duck-types on spiffe_id/is_cryptographically_
    verified, both real fields/properties whatever WorkloadIdentity's
    module defines.
    """
    return TrustedAuthEvidence(
        authenticated=True,
        token_valid=True,
        principal_id=identity.spiffe_id,
        principal_type="service",
        mfa_status=MFA_NOT_REQUIRED,
        spiffe_id=identity.spiffe_id,
        spiffe_verified=bool(getattr(identity, "is_cryptographically_verified", False)),
    )


def unauthenticated_evidence() -> TrustedAuthEvidence:
    return TrustedAuthEvidence(
        authenticated=False,
        token_valid=False,
        principal_id="",
        principal_type="unknown",
        mfa_status=MFA_UNKNOWN,
    )


def bind_trusted_auth(evidence: TrustedAuthEvidence) -> None:
    _current.set(evidence)


def get_trusted_auth() -> TrustedAuthEvidence:
    return _current.get() or unauthenticated_evidence()


def _strip_untrusted_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return strip_untrusted_security_signals(value)
    if isinstance(value, list):
        return [_strip_untrusted_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_strip_untrusted_value(v) for v in value)
    return value


def strip_untrusted_security_signals(
    signals: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Drop agent-attested security properties (recursively)."""
    if not signals:
        return {}
    out: dict[str, Any] = {}
    for key, value in signals.items():
        if key in UNTRUSTED_SECURITY_SIGNAL_KEYS:
            continue
        out[key] = _strip_untrusted_value(value)
    return out


def mfa_allows_operation(evidence: TrustedAuthEvidence | None = None) -> bool:
    from src.monkey_brain.kernel.production_gates import mfa_required

    ev = evidence or get_trusted_auth()
    if ev.principal_type == "service":
        return ev.authenticated and ev.token_valid
    if not mfa_required():
        return True
    return ev.mfa_status == MFA_SATISFIED
