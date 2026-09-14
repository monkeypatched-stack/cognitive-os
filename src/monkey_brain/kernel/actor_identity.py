"""Per-Actor Cell identity — layered on top of the existing SPIFFE/SPIRE
process identity and the existing Portable Delegation machinery
(kernel/delegation.py), per docs/ACTOR_CELL_ARCHITECTURE.md Section J.

Problem this closes: kernel/workload_identity.py's WorkloadIdentityProvider
issues one SVID per PROCESS, never per actor. The two production call sites
that bind trusted auth for an actor answering a request (actor_runtime.py's
per-Pod POST /execute, kernel/domains/grocery.py's NATS inbox handler) fall
back to evidence_for_service(f"actor-runtime:{actor_id}") -- a self-asserted
string with no cryptographic binding to actor_id at all. Nothing today stops
a credential minted/used in one Actor Cell's context from being presented
for a different actor_id.

Design (Section J, option (b) -- the option the user picked over a SPIRE
server-side per-actor registration entry): the process keeps its ONE
SPIFFE-issued identity. On top of it, mint a short-lived, cryptographically
signed DelegationCredential (issuer = the process's own authenticated
identity string, delegate = actor_id) using kernel/delegation.py's existing
issue_delegation/validate_delegation -- the SAME mechanism already used
elsewhere in this codebase for bounded, attenuable, chainable authority. No
SPIRE server-side changes, no new registration entries: this module never
touches kernel/workload_identity.py or SPIRE itself.

Restart behavior: nothing here is persisted. An Actor Cell restart simply
calls mint_actor_cell_identity again and gets a fresh credential -- "identity
survives/reinitializes correctly across Actor Cell restart" falls out of the
credential being cheap to re-mint, not out of any saved state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.monkey_brain.kernel.delegation import (
    DelegationCredential,
    DelegationError,
    DelegationValidationResult,
    issue_delegation,
    to_opa_delegation_context,
    validate_delegation,
)

DEFAULT_ACTOR_CELL_CAPABILITIES: tuple[str, ...] = ("actor.tick",)
DEFAULT_ACTOR_CELL_TTL_SECONDS = 900.0  # 15 minutes -- short-lived per Section 16 of delegation.py


class ActorIdentityError(Exception):
    """Raised when a presented Actor Cell credential does not authorize the
    actor_id it is being used for. Callers must treat this as DENY (fail
    closed), the same posture kernel/delegation.py documents for any
    DelegationError."""


def mint_actor_cell_identity(
    actor_id: str,
    *,
    issuer: str,
    capabilities: tuple[str, ...] = DEFAULT_ACTOR_CELL_CAPABILITIES,
    ttl_seconds: float = DEFAULT_ACTOR_CELL_TTL_SECONDS,
) -> DelegationCredential:
    """Mint a fresh, signed DelegationCredential scoping `issuer` (the
    process's own authenticated identity -- its verified SPIFFE ID when
    available, else the same self-asserted service evidence string already
    used at the call sites this replaces) down to exactly `actor_id`.

    This is the Actor Cell's identity: `delegate` is bound at signing time
    and cannot be altered without invalidating the proof (kernel/delegation.
    py's Ed25519 proof over signing_fields(), which includes `delegate`) --
    so a credential minted for one actor_id can never be silently
    repurposed for another one.
    """
    return issue_delegation(
        issuer=issuer,
        delegate=actor_id,
        capabilities=capabilities,
        audience=actor_id,
        ttl_seconds=ttl_seconds,
    )


def verify_actor_cell_identity(
    credential: DelegationCredential,
    *,
    actor_id: str,
    authenticated_issuer: str,
) -> DelegationValidationResult:
    """The actual enforcement point: is `credential` valid AND actually
    delegated to `actor_id` by the currently-authenticated issuer?

    kernel.delegation.validate_delegation already rejects when
    credential.delegate != authenticated_delegate (Section 5: "NEVER trust
    the credential's own claimed fields") -- this is what makes "Actor A
    cannot authenticate as Actor B" hold: presenting A's credential while
    claiming to answer for B fails here, not because of anything new, but
    because that check already exists and is already tested.
    """
    return validate_delegation(
        child=credential,
        parent=None,
        authenticated_issuer=authenticated_issuer,
        authenticated_delegate=actor_id,
    )


def bind_actor_cell_trusted_auth(
    actor_id: str,
    credential: DelegationCredential,
    *,
    authenticated_issuer: str,
) -> dict[str, Any]:
    """Verify `credential` actually authorizes `actor_id`, then return the
    sanctioned OPA delegation-context dict (kernel.delegation.
    to_opa_delegation_context) for the caller to pass into
    ensure_governed(..., verified_delegation=...).

    Raises ActorIdentityError (fail closed) if verification fails -- callers
    must not fall back to unscoped/self-asserted evidence on failure.
    """
    result = verify_actor_cell_identity(credential, actor_id=actor_id, authenticated_issuer=authenticated_issuer)
    if not result.authorized:
        raise ActorIdentityError(
            f"Actor Cell credential does not authorize actor_id={actor_id!r}: {result.failure_reason}"
        )
    return to_opa_delegation_context((credential,))


@dataclass
class ActorCellIdentityCache:
    """Per-process cache of one Actor Cell's minted credential, so it is
    minted once (at Cell start) and re-verified (cheap, no signing) on every
    subsequent request rather than re-signed every call. One instance per
    ActorRuntime process -- NOT shared across actor_ids; a process hosting
    more than one Actor Cell must hold one of these per actor_id."""

    actor_id: str
    credential: DelegationCredential | None = None

    def ensure(
        self,
        *,
        issuer: str,
        capabilities: tuple[str, ...] = DEFAULT_ACTOR_CELL_CAPABILITIES,
        ttl_seconds: float = DEFAULT_ACTOR_CELL_TTL_SECONDS,
    ) -> DelegationCredential:
        if self.credential is None or self.credential.is_expired or self.credential.issuer != issuer:
            self.credential = mint_actor_cell_identity(
                self.actor_id,
                issuer=issuer,
                capabilities=capabilities,
                ttl_seconds=ttl_seconds,
            )
        return self.credential

    def bind_trusted_auth(self, *, authenticated_issuer: str) -> dict[str, Any]:
        if self.credential is None:
            raise ActorIdentityError(f"no Actor Cell credential minted yet for actor_id={self.actor_id!r}")
        return bind_actor_cell_trusted_auth(self.actor_id, self.credential, authenticated_issuer=authenticated_issuer)
